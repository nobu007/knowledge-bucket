"""Tests for graph module: concept extraction, normalization, df/idf."""

import os
import sqlite3
import tempfile

from kb.core import ensure_dirs
from kb.graph import (
    build_graph,
    compute_df,
    estimate_importance,
    get_active_graph_terms,
    init_graph_tables,
    load_aliases,
    load_stop_concepts,
    normalize_concept,
)
from kb.index import index_path, init_db


def _make_doc(root, doc_id, title, concepts=None, source_type="web",
              source=None):
    """Helper: create a minimal document under root/records/doc/."""
    from kb.core import DOC_DIR, RECORDS_DIR

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, "ab", "cd")
    os.makedirs(doc_dir, exist_ok=True)
    path = os.path.join(doc_dir, f"{doc_id}.md")
    fm = f"---\nid: {doc_id}\ntitle: {title}\nsource_type: {source_type}\n"
    if source:
        fm += f"source: {source}\n"
    if concepts:
        fm += "concepts:\n"
        for c in concepts:
            fm += f"  - {c}\n"
    fm += "---\n\nBody text.\n"
    with open(path, "w") as f:
        f.write(fm)
    return path


def _make_config(root, aliases_yaml=None, stop_yaml=None):
    from kb.core import CONFIG_DIR

    cfg_dir = os.path.join(root, CONFIG_DIR)
    os.makedirs(cfg_dir, exist_ok=True)
    if aliases_yaml is not None:
        with open(os.path.join(cfg_dir, "aliases.yml"), "w") as f:
            f.write(aliases_yaml)
    if stop_yaml is not None:
        with open(os.path.join(cfg_dir, "stop_concepts.yml"), "w") as f:
            f.write(stop_yaml)


class TestNormalizeConcept:
    def test_alias_match(self):
        aliases = {"rag": "retrieval-augmented-generation"}
        assert normalize_concept("RAG", aliases) == "retrieval-augmented-generation"

    def test_no_alias(self):
        assert normalize_concept("graph-rag", {}) == "graph-rag"

    def test_case_insensitive(self):
        aliases = {"rag": "retrieval-augmented-generation"}
        assert normalize_concept("RAG", aliases) == "retrieval-augmented-generation"


class TestLoadAliases:
    def test_loads_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_config(tmp, aliases_yaml="aliases:\n  rag: retrieval-augmented-generation\n")
            aliases = load_aliases(tmp)
            assert aliases["rag"] == "retrieval-augmented-generation"

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            aliases = load_aliases(tmp)
            assert aliases == {}


class TestLoadStopConcepts:
    def test_loads_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_config(tmp, stop_yaml="stop_concepts:\n  - ai\n  - python\n")
            stop = load_stop_concepts(tmp)
            assert "ai" in stop
            assert "python" in stop

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = load_stop_concepts(tmp)
            assert stop == set()


class TestInitGraphTables:
    def test_creates_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = sqlite3.connect(db)
            init_graph_tables(conn)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            assert "concepts" in tables
            assert "doc_concepts" in tables
            assert "edges" in tables
            conn.close()

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = sqlite3.connect(db)
            init_graph_tables(conn)
            init_graph_tables(conn)
            count = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
            assert count == 0
            conn.close()


class TestBuildGraph:
    def test_extracts_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp)
            _make_doc(tmp, "doc1", "Doc One", ["rag", "knowledge-graph"])
            _make_doc(tmp, "doc2", "Doc Two", ["graph-rag", "rag"])

            report = build_graph(tmp)
            assert report["docs_processed"] == 2
            assert report["concepts_found"] == 3  # rag, knowledge-graph, graph-rag

            db = index_path(tmp)
            conn = sqlite3.connect(db)
            rag_df = conn.execute(
                "SELECT df FROM concepts WHERE concept_id='rag'"
            ).fetchone()
            assert rag_df[0] == 2  # appears in both docs
            conn.close()

    def test_alias_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp, aliases_yaml="aliases:\n  rag: retrieval-augmented-generation\n")
            _make_doc(tmp, "doc1", "Doc One", ["rag"])
            _make_doc(tmp, "doc2", "Doc Two", ["retrieval-augmented-generation"])

            report = build_graph(tmp)
            assert report["concepts_found"] == 1

            db = index_path(tmp)
            conn = sqlite3.connect(db)
            df = conn.execute(
                "SELECT df FROM concepts WHERE concept_id='retrieval-augmented-generation'"
            ).fetchone()
            assert df[0] == 2
            conn.close()

    def test_stop_concept_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp, stop_yaml="stop_concepts:\n  - ai\n")
            _make_doc(tmp, "doc1", "Doc One", ["rag", "ai"])

            report = build_graph(tmp)
            assert report["concepts_found"] == 1  # only rag, not ai

    def test_no_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp)
            _make_doc(tmp, "doc1", "No Concepts")

            report = build_graph(tmp)
            assert report["docs_processed"] == 1
            assert report["concepts_found"] == 0


class TestComputeDf:
    def test_recomputes_df(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            init_graph_tables(conn)
            conn.execute(
                "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
                "VALUES ('rag', 'RAG', 0, 0, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d1', 'rag', 'primary', 1.0)"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d2', 'rag', 'primary', 1.0)"
            )
            conn.commit()
            compute_df(conn)
            df = conn.execute("SELECT df FROM concepts WHERE concept_id='rag'").fetchone()
            assert df[0] == 2
            conn.close()


class TestGetActiveGraphTerms:
    def test_filters_low_df(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            init_graph_tables(conn)
            conn.execute(
                "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
                "VALUES ('rag', 'RAG', 1, 0, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO concepts (concept_id, label, df, is_stop, created_at) "
                "VALUES ('kg', 'Knowledge Graph', 3, 0, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d1', 'rag', 'primary', 1.0)"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d1', 'kg', 'primary', 1.0)"
            )
            conn.commit()

            terms = get_active_graph_terms(conn, "d1")
            assert len(terms) == 1
            assert terms[0]["concept_id"] == "kg"
            conn.close()


class TestEstimateImportance:
    def test_zero_concepts(self):
        assert estimate_importance(0, 0.0, "memo", False) == 0.0

    def test_max_concepts_web_no_source(self):
        # 3 concepts, avg_inv_df=0.5, web, no source
        imp = estimate_importance(3, 0.5, "web", False)
        # concept_score=1.0, rarity=1.0, type=0.4, source=0.0
        # = 0.40*1 + 0.30*1 + 0.15*0.4 + 0.15*0 = 0.76
        assert imp == 0.76

    def test_paper_with_source(self):
        # 3 concepts, avg_inv_df=1.0, paper, has source
        imp = estimate_importance(3, 1.0, "paper", True)
        # concept=1.0, rarity=1.0, type=1.0, source=1.0
        # = 0.40 + 0.30 + 0.15 + 0.15 = 1.0
        assert imp == 1.0

    def test_memo_low(self):
        # 1 concept, avg_inv_df=0.1, memo, no source
        imp = estimate_importance(1, 0.1, "memo", False)
        # concept=1/3≈0.33, rarity=0.2, type=0.0, source=0.0
        # = 0.40*0.33 + 0.30*0.2 = 0.133 + 0.06 = 0.19
        assert imp == 0.19

    def test_capped_at_one(self):
        imp = estimate_importance(10, 2.0, "paper", True)
        assert imp <= 1.0


class TestComputeImportance:
    def test_scores_docs_with_concepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp)
            _make_doc(tmp, "doc1", "Doc One", ["rag", "knowledge-graph"],
                      source_type="paper", source="https://example.com")
            _make_doc(tmp, "doc2", "Doc Two", ["graph-rag", "rag"])
            _make_doc(tmp, "doc3", "No Concepts")

            report = build_graph(tmp)
            assert report["importance_scored"] == 2  # doc3 has no concepts

            db = index_path(tmp)
            conn = sqlite3.connect(db)
            imp1 = conn.execute(
                "SELECT importance FROM doc_stats WHERE doc_id='doc1'"
            ).fetchone()
            imp2 = conn.execute(
                "SELECT importance FROM doc_stats WHERE doc_id='doc2'"
            ).fetchone()
            imp3 = conn.execute(
                "SELECT importance FROM doc_stats WHERE doc_id='doc3'"
            ).fetchone()
            assert imp1[0] > imp2[0]  # paper+source > web+no-source
            assert imp3[0] == 0.0  # no concepts
            conn.close()

    def test_doc_stats_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            _make_config(tmp)
            _make_doc(tmp, "doc1", "Doc One", ["rag"],
                      source_type="web", source="https://example.com")

            build_graph(tmp)
            db = index_path(tmp)
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT source_type, has_source FROM doc_stats WHERE doc_id='doc1'"
            ).fetchone()
            assert row[0] == "web"
            assert row[1] == 1
            conn.close()
