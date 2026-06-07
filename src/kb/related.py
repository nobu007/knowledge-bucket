"""Related-document discovery via shared concepts."""

import sqlite3

from .graph import compute_df, get_active_graph_terms


def build_doc_edges(conn: sqlite3.Connection, top_k: int = 10) -> int:
    """Generate document-document edges based on shared concepts.

    For each document with active graph terms (df >= 2), find other documents
    sharing the same concepts and create edges ranked by shared IDF weight.
    Returns the number of edges created.
    """
    compute_df(conn)

    docs = conn.execute(
        "SELECT DISTINCT doc_id FROM doc_concepts"
    ).fetchall()

    edges = 0
    for (doc_id,) in docs:
        terms = get_active_graph_terms(conn, doc_id, max_terms=5)
        if not terms:
            continue

        concept_ids = [t["concept_id"] for t in terms]
        placeholders = ",".join("?" * len(concept_ids))

        # Find documents sharing these concepts, excluding self
        candidates = conn.execute(
            f"SELECT dc.doc_id, COUNT(*) as shared, "
            f"  SUM(1.0 / MAX(c.df, 1)) as idf_sum "
            f"FROM doc_concepts dc "
            f"JOIN concepts c ON c.concept_id = dc.concept_id "
            f"WHERE dc.concept_id IN ({placeholders}) "
            f"  AND dc.doc_id != ? "
            f"  AND c.is_stop = 0 "
            f"GROUP BY dc.doc_id "
            f"ORDER BY idf_sum DESC "
            f"LIMIT ?",
            (*concept_ids, doc_id, top_k),
        ).fetchall()

        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        for target_id, _shared, idf_sum in candidates:
            conn.execute(
                "INSERT OR REPLACE INTO edges "
                "(src_id, dst_id, edge_type, weight, updated_at) "
                "VALUES (?, ?, 'related', ?, ?)",
                (doc_id, target_id, idf_sum, now),
            )
            edges += 1

    conn.commit()
    return edges


def find_related(conn: sqlite3.Connection, doc_id: str,
                 limit: int = 10) -> list[dict]:
    """Find documents related to the given document via edges."""
    rows = conn.execute(
        "SELECT e.dst_id, e.weight, d.title, d.source "
        "FROM edges e "
        "LEFT JOIN docs d ON d.id = e.dst_id "
        "WHERE e.src_id = ? AND e.edge_type = 'related' "
        "ORDER BY e.weight DESC "
        "LIMIT ?",
        (doc_id, limit),
    ).fetchall()

    results = []
    for r in rows:
        entry = {"doc_id": r[0], "weight": r[1], "title": r[2] or ""}
        if r[3]:
            entry["source"] = r[3]
        results.append(entry)

    # Also return the shared concepts for context
    terms = get_active_graph_terms(conn, doc_id)
    if results and terms:
        results[0]["_query_terms"] = [t["concept_id"] for t in terms]

    return results
