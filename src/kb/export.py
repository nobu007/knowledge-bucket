"""Export knowledge bucket data to Parquet format for external analysis."""

import os
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

from .graph import init_graph_tables
from .index import index_path

TABLES = {
    "documents": "SELECT id, title, source, source_type FROM docs",
    "concepts": (
        "SELECT concept_id, label, kind, df, is_stop, created_at FROM concepts"
    ),
    "doc_concepts": "SELECT doc_id, concept_id, role, weight FROM doc_concepts",
    "edges": "SELECT src_id, dst_id, edge_type, weight, updated_at FROM edges",
    "doc_stats": (
        "SELECT doc_id, source_type, has_source, importance, updated_at "
        "FROM doc_stats"
    ),
}


def export_parquet(root: str, output_dir: str | None = None) -> dict:
    """Export all graph tables to Parquet files.

    Returns a dict mapping table name -> row count.
    """
    db_path = index_path(root)
    if not os.path.exists(db_path):
        raise FileNotFoundError("No index database found. Run 'kb index' first.")

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(db_path), "exports")
    os.makedirs(output_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        init_graph_tables(conn)
        results = {}
        for name, query in TABLES.items():
            cursor = conn.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            arrays = []
            for i, col in enumerate(columns):
                col_values = [row[i] for row in rows]
                arrays.append(pa.array(col_values))

            table = pa.table(
                {col: arrays[i] for i, col in enumerate(columns)}
            )
            pq.write_table(table, os.path.join(output_dir, f"{name}.parquet"))
            results[name] = len(rows)
        return results
    finally:
        conn.close()
