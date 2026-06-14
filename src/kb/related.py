"""Related-document discovery via shared concepts and concept co-occurrence."""

import sqlite3

from .graph import compute_df, compute_hub_threshold, get_active_graph_terms


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


def build_concept_edges(conn: sqlite3.Connection, min_cooccurrence: int = 2,
                        top_k: int = 20) -> int:
    """Generate concept→concept co-occurrence edges per GOAL.md section 12.

    Two concepts co-occur when they appear in the same document. The edge
    weight is the number of documents where both appear. Hub concepts and
    stop concepts are excluded.

    Returns the number of edges created.
    """
    hub_threshold = compute_hub_threshold(conn)

    # Eligible concepts: df in [2, hub_threshold] and not a stop word. This
    # keeps the same filters as the prior SQL self-join while letting us build
    # the co-occurrence map in Python instead of an O(K^2) database join.
    eligible = {
        cid for (cid,) in conn.execute(
            "SELECT concept_id FROM concepts "
            "WHERE is_stop = 0 AND df >= 2 AND df <= ?",
            (hub_threshold,),
        ).fetchall()
    }

    # One pass over doc_concepts: {concept_id: set(doc_ids)}.
    concept_docs: dict[str, set[str]] = {}
    for doc_id, concept_id in conn.execute(
        "SELECT doc_id, concept_id FROM doc_concepts"
    ).fetchall():
        if concept_id in eligible:
            concept_docs.setdefault(concept_id, set()).add(doc_id)

    # For each pair of concepts sharing >=1 document, weight = shared-doc count.
    pairs: list[tuple[str, str, int]] = []
    eligible_concepts = sorted(concept_docs)
    for i, c1 in enumerate(eligible_concepts):
        docs1 = concept_docs[c1]
        for c2 in eligible_concepts[i + 1:]:
            shared = len(docs1 & concept_docs[c2])
            if shared >= min_cooccurrence:
                # Normalize ordering so c1 < c2 (guaranteed by sorted iteration).
                pairs.append((c1, c2, shared))

    # Order by descending co-occurrence (strongest pairs first); concept-id
    # pair as a deterministic tiebreak for stable top_k selection.
    pairs.sort(key=lambda p: (-p[2], p[0], p[1]))

    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()

    # Keep only top_k per concept
    concept_edge_count: dict[str, int] = {}
    edges = 0
    for c1, c2, cooc in pairs:
        if concept_edge_count.get(c1, 0) >= top_k:
            continue
        if concept_edge_count.get(c2, 0) >= top_k:
            continue

        conn.execute(
            "INSERT OR REPLACE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) "
            "VALUES (?, ?, 'cooccurrence', ?, ?)",
            (c1, c2, float(cooc), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) "
            "VALUES (?, ?, 'cooccurrence', ?, ?)",
            (c2, c1, float(cooc), now),
        )
        concept_edge_count[c1] = concept_edge_count.get(c1, 0) + 1
        concept_edge_count[c2] = concept_edge_count.get(c2, 0) + 1
        edges += 2  # bidirectional

    conn.commit()
    return edges


def find_cooccurring_concepts(conn: sqlite3.Connection,
                              concept_id: str, limit: int = 10) -> list[dict]:
    """Find concepts that co-occur with the given concept."""
    rows = conn.execute(
        "SELECT e.dst_id, e.weight, c.label, c.df "
        "FROM edges e "
        "JOIN concepts c ON c.concept_id = e.dst_id "
        "WHERE e.src_id = ? AND e.edge_type = 'cooccurrence' "
        "ORDER BY e.weight DESC "
        "LIMIT ?",
        (concept_id, limit),
    ).fetchall()
    return [
        {"concept_id": r[0], "cooccurrence": int(r[1]), "label": r[2], "df": r[3]}
        for r in rows
    ]
