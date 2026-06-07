"""Tests for related module: document-document edges and kb related."""

import os
import tempfile

from kb.graph import init_graph_tables
from kb.index import init_db
from kb.related import build_doc_edges, find_related


def _setup_graph_db(tmp):
    """Create a DB with docs, concepts, and doc_concepts for testing."""
    db = os.path.join(tmp, "index.db")
    conn = init_db(db)
    init_graph_tables(conn)

    # Insert docs into FTS
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d1', 'RAG Guide', '', 'web', 'd1.md', 'About RAG')"
    )
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d2', 'Graph RAG', '', 'web', 'd2.md', 'About GraphRAG')"
    )
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES ('d3', 'Cooking', '', 'web', 'd3.md', 'About cooking')"
    )
    conn.commit()

    # Concepts
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('rag', 'RAG', 2, 0, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('graph-rag', 'GraphRAG', 1, 0, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
        "VALUES ('cooking', 'Cooking', 1, 0, '2026-01-01')"
    )
    conn.commit()

    # doc_concepts
    for doc_id, concepts in [
        ("d1", ["rag"]),
        ("d2", ["rag", "graph-rag"]),
        ("d3", ["cooking"]),
    ]:
        for c in concepts:
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, c),
            )
    conn.commit()
    return conn


class TestBuildDocEdges:
    def test_creates_edges_for_shared_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            edges = build_doc_edges(conn)
            # d1 and d2 share "rag", so there should be edges between them
            assert edges >= 2  # d1→d2 and d2→d1

            row = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type='related'"
            ).fetchone()
            assert row[0] >= 2
            conn.close()

    def test_no_self_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            build_doc_edges(conn)
            self_edges = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE src_id = dst_id"
            ).fetchone()[0]
            assert self_edges == 0
            conn.close()

    def test_isolated_doc_no_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            build_doc_edges(conn)
            d3_edges = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE src_id='d3' OR dst_id='d3'"
            ).fetchone()[0]
            assert d3_edges == 0
            conn.close()


class TestFindRelated:
    def test_returns_related_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            build_doc_edges(conn)
            results = find_related(conn, "d1")
            assert len(results) >= 1
            assert any(r["doc_id"] == "d2" for r in results)
            conn.close()

    def test_no_related(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            build_doc_edges(conn)
            results = find_related(conn, "d3")
            assert len(results) == 0
            conn.close()

    def test_respects_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_graph_db(tmp)
            build_doc_edges(conn)
            results = find_related(conn, "d1", limit=0)
            assert len(results) == 0
            conn.close()
