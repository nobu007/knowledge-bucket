"""Tests for graph module: concept extraction, normalization, df/idf."""

import os
import sqlite3
import tempfile

from kb.core import ensure_dirs
from kb.graph import (
    build_graph,
    compute_df,
    get_active_graph_terms,
    init_graph_tables,
    load_aliases,
    load_stop_concepts,
    normalize_concept,
)
from kb.index import index_path, init_db


def _make_doc(root, doc_id, title, concepts=None, source_type="web"):
    """Helper: create a minimal document under root/records/doc/."""
    from kb.core import DOC_DIR, RECORDS_DIR

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, "ab", "cd")
    os.makedirs(doc_dir, exist_ok=True)
    path = os.path.join(doc_dir, f"{doc_id}.md")
    fm = f"---\nid: {doc_id}\ntitle: {title}\nsource_type: {source_type}\n"
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
