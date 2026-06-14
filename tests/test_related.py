"""Tests for related module: document-document edges, concept co-occurrence, and kb related."""

import os
import tempfile

from kb.graph import init_graph_tables
from kb.index import init_db
from kb.related import (
    build_concept_edges,
    build_doc_edges,
    find_cooccurring_concepts,
    find_related,
)


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


def _setup_cooc_db(tmp):
    """Create a DB with concepts that co-occur across multiple documents."""
    db = os.path.join(tmp, "index.db")
    conn = init_db(db)
    init_graph_tables(conn)

    # Insert 5 docs into FTS
    for i in range(1, 6):
        conn.execute(
            "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
            f"VALUES ('d{i}', 'Doc {i}', '', 'web', 'd{i}.md', 'Content {i}')"
        )
    conn.commit()

    # Concepts with df >= 2 (required for co-occurrence)
    concepts = [
        ("rag", "RAG", 3),
        ("graph-rag", "GraphRAG", 2),
        ("knowledge-graph", "Knowledge Graph", 2),
        ("embedding", "Embedding", 1),  # df=1, should be excluded
        ("machine-learning", "Machine Learning", 1),  # df=1
    ]
    for cid, label, df in concepts:
        conn.execute(
            "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
            "VALUES (?, ?, ?, 0, '2026-01-01')",
            (cid, label, df),
        )
    conn.commit()

    # doc_concepts: rag+graph-rag co-occur in d1,d2; rag+knowledge-graph in d1,d3
    assignments = {
        "d1": ["rag", "graph-rag", "knowledge-graph"],
        "d2": ["rag", "graph-rag"],
        "d3": ["rag", "knowledge-graph"],
        "d4": ["embedding"],
        "d5": ["machine-learning"],
    }
    for doc_id, cids in assignments.items():
        for c in cids:
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, c),
            )
    conn.commit()
    return conn


class TestBuildConceptEdges:
    def test_creates_cooccurrence_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            edges = build_concept_edges(conn, min_cooccurrence=2)
            # rag+graph-rag co-occur in d1,d2 (2 docs)
            # rag+knowledge-graph co-occur in d1,d3 (2 docs)
            assert edges >= 4  # 2 pairs × 2 bidirectional

            row = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type='cooccurrence'"
            ).fetchone()
            assert row[0] >= 4
            conn.close()

    def test_excludes_low_df_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            # embedding and machine-learning have df=1, should not appear
            emb_edges = conn.execute(
                "SELECT COUNT(*) FROM edges "
                "WHERE (src_id='embedding' OR dst_id='embedding') "
                "AND edge_type='cooccurrence'"
            ).fetchone()[0]
            assert emb_edges == 0
            conn.close()

    def test_min_cooccurrence_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            # With min_cooccurrence=3, no pairs qualify (max cooc is 2)
            edges = build_concept_edges(conn, min_cooccurrence=3)
            assert edges == 0
            conn.close()

    def test_bidirectional_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            # rag→graph-rag and graph-rag→rag should both exist
            fwd = conn.execute(
                "SELECT weight FROM edges "
                "WHERE src_id='rag' AND dst_id='graph-rag' AND edge_type='cooccurrence'"
            ).fetchone()
            bwd = conn.execute(
                "SELECT weight FROM edges "
                "WHERE src_id='graph-rag' AND dst_id='rag' AND edge_type='cooccurrence'"
            ).fetchone()
            assert fwd is not None
            assert bwd is not None
            assert fwd[0] == bwd[0]
            conn.close()

    def test_top_k_limits_edges_per_concept(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            # With top_k=1, each concept gets at most 1 edge partner
            build_concept_edges(conn, min_cooccurrence=2, top_k=1)
            rag_count = conn.execute(
                "SELECT COUNT(*) FROM edges "
                "WHERE src_id='rag' AND edge_type='cooccurrence'"
            ).fetchone()[0]
            assert rag_count <= 1
            conn.close()


def _setup_cooc_db_three_plus(tmp):
    """Create a DB where two concepts co-occur in 3+ documents."""
    db = os.path.join(tmp, "index.db")
    conn = init_db(db)
    init_graph_tables(conn)

    # 6 docs: rag + graph-rag share d1..d4 (4 docs); rag + embedding share d1..d3 (3)
    for i in range(1, 7):
        conn.execute(
            "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
            f"VALUES ('d{i}', 'Doc {i}', '', 'web', 'd{i}.md', 'Content {i}')"
        )
    conn.commit()

    concepts = [
        ("rag", "RAG", 5),
        ("graph-rag", "GraphRAG", 4),
        ("embedding", "Embedding", 3),
        ("cooking", "Cooking", 1),  # df=1, excluded
    ]
    for cid, label, df in concepts:
        conn.execute(
            "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
            "VALUES (?, ?, ?, 0, '2026-01-01')",
            (cid, label, df),
        )
    conn.commit()

    assignments = {
        "d1": ["rag", "graph-rag", "embedding"],
        "d2": ["rag", "graph-rag", "embedding"],
        "d3": ["rag", "graph-rag", "embedding"],
        "d4": ["rag", "graph-rag"],
        "d5": ["rag"],
        "d6": ["cooking"],
    }
    for doc_id, cids in assignments.items():
        for c in cids:
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, c),
            )
    conn.commit()
    return conn


class TestBuildConceptEdgesThreePlusShared:
    def test_weight_reflects_three_plus_shared_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db_three_plus(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            # rag+graph-rag co-occur in d1,d2,d3,d4 -> weight 4
            row = conn.execute(
                "SELECT weight FROM edges "
                "WHERE src_id='rag' AND dst_id='graph-rag' "
                "AND edge_type='cooccurrence'"
            ).fetchone()
            assert row is not None
            assert row[0] == 4.0
            conn.close()

    def test_min_cooccurrence_three_filters_lower_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db_three_plus(tmp)
            # min_cooccurrence=3 keeps rag+graph-rag (4) and rag+embedding (3),
            # but nothing else.
            edges = build_concept_edges(conn, min_cooccurrence=3)
            assert edges >= 4  # at least one pair, bidirectional

            emb = conn.execute(
                "SELECT weight FROM edges "
                "WHERE src_id='rag' AND dst_id='embedding' "
                "AND edge_type='cooccurrence'"
            ).fetchone()
            assert emb is not None
            assert emb[0] == 3.0
            conn.close()

    def test_higher_cooc_ranked_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db_three_plus(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            # rag's top neighbor should be graph-rag (4 shared) over embedding (3)
            top = conn.execute(
                "SELECT dst_id FROM edges "
                "WHERE src_id='rag' AND edge_type='cooccurrence' "
                "ORDER BY weight DESC LIMIT 1"
            ).fetchone()
            assert top[0] == "graph-rag"
            conn.close()


class TestFindCooccurringConcepts:
    def test_returns_cooccurring_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            results = find_cooccurring_concepts(conn, "rag")
            assert len(results) >= 1
            assert any(r["concept_id"] == "graph-rag" for r in results)
            assert all("label" in r and "df" in r for r in results)
            conn.close()

    def test_no_cooccurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = _setup_cooc_db(tmp)
            build_concept_edges(conn, min_cooccurrence=2)
            results = find_cooccurring_concepts(conn, "embedding")
            assert len(results) == 0
            conn.close()
