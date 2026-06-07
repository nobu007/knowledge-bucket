"""Graph health metrics for knowledge bucket."""

import os
import sqlite3

from .core import RECORDS_DIR
from .graph import init_graph_tables
from .index import index_path


def compute_health(root: str) -> dict:
    """Compute graph health metrics from the index database.

    Returns a dict with overview stats, distributions, and quality indicators.
    """
    db_path = index_path(root)
    if not os.path.exists(db_path):
        return {"error": "No index database found. Run 'kb graph build' first."}

    conn = sqlite3.connect(db_path)
    try:
        return _gather_metrics(conn, root)
    finally:
        conn.close()


def _gather_metrics(conn: sqlite3.Connection, root: str) -> dict:
    init_graph_tables(conn)
    # Overview counts
    total_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    total_concepts = conn.execute(
        "SELECT COUNT(*) FROM concepts WHERE is_stop = 0"
    ).fetchone()[0]
    total_edges = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE edge_type = 'related'"
    ).fetchone()[0]

    # Orphan documents: docs with no concepts
    orphan_docs = conn.execute(
        "SELECT COUNT(*) FROM docs d "
        "WHERE d.id NOT IN (SELECT DISTINCT doc_id FROM doc_concepts)"
    ).fetchone()[0]

    # Isolated docs: docs with no edges to other docs
    isolated_docs = conn.execute(
        "SELECT COUNT(*) FROM docs d "
        "WHERE d.id NOT IN (SELECT DISTINCT src_id FROM edges WHERE edge_type = 'related') "
        "AND d.id NOT IN (SELECT DISTINCT dst_id FROM edges WHERE edge_type = 'related')"
    ).fetchone()[0]

    # Source type breakdown
    source_types = {}
    for row in conn.execute(
        "SELECT source_type, COUNT(*) FROM docs GROUP BY source_type ORDER BY COUNT(*) DESC"
    ).fetchall():
        source_types[row[0]] = row[1]

    # Importance distribution
    importance_buckets = {"high": 0, "medium": 0, "low": 0, "unscored": 0}
    for row in conn.execute("SELECT importance FROM doc_stats").fetchall():
        imp = row[0]
        if imp >= 0.7:
            importance_buckets["high"] += 1
        elif imp >= 0.4:
            importance_buckets["medium"] += 1
        elif imp > 0.0:
            importance_buckets["low"] += 1
        else:
            importance_buckets["unscored"] += 1
    importance_buckets["unscored"] += max(0, total_docs - conn.execute(
        "SELECT COUNT(*) FROM doc_stats"
    ).fetchone()[0])

    # Top concepts by document frequency
    top_concepts = []
    for row in conn.execute(
        "SELECT concept_id, label, df FROM concepts "
        "WHERE is_stop = 0 AND df > 0 "
        "ORDER BY df DESC LIMIT 20"
    ).fetchall():
        top_concepts.append({"id": row[0], "label": row[1], "df": row[2]})

    # Concepts with high df but no note file
    concept_dir = os.path.join(root, RECORDS_DIR, "concept")
    concepts_missing_notes = 0
    for row in conn.execute(
        "SELECT concept_id FROM concepts WHERE is_stop = 0 AND df >= 2"
    ).fetchall():
        note_path = os.path.join(concept_dir, f"{row[0]}.md")
        if not os.path.exists(note_path):
            concepts_missing_notes += 1

    # Average concepts per document
    avg_concepts = conn.execute(
        "SELECT AVG(cnt) FROM (SELECT COUNT(*) as cnt FROM doc_concepts GROUP BY doc_id)"
    ).fetchone()[0] or 0.0

    # Average edges per document
    avg_edges = conn.execute(
        "SELECT AVG(cnt) FROM (SELECT COUNT(*) as cnt FROM edges "
        "WHERE edge_type = 'related' GROUP BY src_id)"
    ).fetchone()[0] or 0.0

    # Connectivity ratio: docs that have at least one edge
    connected_docs = total_docs - isolated_docs
    connectivity_ratio = connected_docs / total_docs if total_docs > 0 else 0.0

    return {
        "overview": {
            "total_documents": total_docs,
            "total_concepts": total_concepts,
            "total_edges": total_edges,
            "orphan_documents": orphan_docs,
            "isolated_documents": isolated_docs,
        },
        "source_types": source_types,
        "importance_distribution": importance_buckets,
        "top_concepts": top_concepts,
        "concepts_missing_notes": concepts_missing_notes,
        "metrics": {
            "avg_concepts_per_doc": round(avg_concepts, 2),
            "avg_edges_per_doc": round(avg_edges, 2),
            "connectivity_ratio": round(connectivity_ratio, 3),
        },
    }
