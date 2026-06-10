"""Tests for virtual collections: taxonomy loading and collection resolution."""

import os
import sqlite3
import tempfile

from kb.core import ensure_dirs
from kb.graph import init_graph_tables, load_taxonomy, resolve_virtual_collection
from kb.index import index_document, init_db


class TestLoadTaxonomy:
    def test_loads_virtual_collections(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            tax_path = os.path.join(tmp, "config", "taxonomy.yml")
            with open(tax_path, "w") as f:
                f.write(
                    "virtual_collections:\n"
                    "  ai_agents:\n"
                    "    label: AI Agents\n"
                    "    include_concepts:\n"
                    "      - concept:ai-agent\n"
                    "  papers:\n"
                    "    label: Papers\n"
                    "    include_types:\n"
                    "      - paper\n"
                )
            result = load_taxonomy(tmp)
            assert "ai_agents" in result
            assert result["ai_agents"]["label"] == "AI Agents"
            assert "papers" in result
            assert result["papers"]["include_types"] == ["paper"]

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = load_taxonomy(tmp)
            assert result == {}

    def test_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            tax_path = os.path.join(tmp, "config", "taxonomy.yml")
            with open(tax_path, "w") as f:
                f.write("")
            result = load_taxonomy(tmp)
            assert result == {}

    def test_no_virtual_collections_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            tax_path = os.path.join(tmp, "config", "taxonomy.yml")
            with open(tax_path, "w") as f:
                f.write("other_key: value\n")
            result = load_taxonomy(tmp)
            assert result == {}


class TestResolveVirtualCollection:
    def _setup_db(self, tmp: str) -> sqlite3.Connection:
        db = os.path.join(tmp, "index.db")
        conn = init_db(db)
        init_graph_tables(conn)
        return conn

    def test_by_source_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            index_document(conn, "d1", "Paper A", None, "paper", "p1.md", "body")
            index_document(conn, "d2", "Web B", None, "web", "p2.md", "body")
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d1', 'paper', 0, 0.8, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d2', 'web', 0, 0.3, '2026-01-01')"
            )
            conn.commit()

            cdef = {"include_types": ["paper"]}
            docs = resolve_virtual_collection(conn, cdef)
            assert len(docs) == 1
            assert docs[0]["id"] == "d1"
            assert docs[0]["importance"] == 0.8
            conn.close()

    def test_by_concept(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            index_document(conn, "d1", "RAG Doc", None, "web", "p1.md", "body")
            conn.execute(
                "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
                "VALUES ('rag', 'RAG', 'concept', 1, 0, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d1', 'rag', 'primary', 1.0)"
            )
            conn.commit()

            cdef = {"include_concepts": ["concept:rag"]}
            docs = resolve_virtual_collection(conn, cdef)
            assert len(docs) == 1
            assert docs[0]["id"] == "d1"
            conn.close()

    def test_concept_without_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            index_document(conn, "d1", "Doc", None, "web", "p1.md", "body")
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d1', 'graph-rag', 'primary', 1.0)"
            )
            conn.commit()

            cdef = {"include_concepts": ["graph-rag"]}
            docs = resolve_virtual_collection(conn, cdef)
            assert len(docs) == 1
            conn.close()

    def test_both_concepts_and_types_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            index_document(conn, "d1", "Paper", None, "paper", "p1.md", "body")
            index_document(conn, "d2", "Web", None, "web", "p2.md", "body")
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d1', 'paper', 0, 0.5, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d2', 'web', 0, 0.9, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES ('d2', 'rag', 'primary', 1.0)"
            )
            conn.commit()

            cdef = {
                "include_types": ["paper"],
                "include_concepts": ["rag"],
            }
            docs = resolve_virtual_collection(conn, cdef)
            ids = {d["id"] for d in docs}
            assert ids == {"d1", "d2"}
            conn.close()

    def test_no_match_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            cdef = {"include_types": ["nonexistent"]}
            docs = resolve_virtual_collection(conn, cdef)
            assert docs == []
            conn.close()

    def test_empty_definition_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            cdef = {}
            docs = resolve_virtual_collection(conn, cdef)
            assert docs == []
            conn.close()

    def test_sorted_by_importance_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._setup_db(tmp)
            index_document(conn, "d1", "Low", None, "paper", "p1.md", "body")
            index_document(conn, "d2", "High", None, "paper", "p2.md", "body")
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d1', 'paper', 0, 0.2, '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES ('d2', 'paper', 0, 0.9, '2026-01-01')"
            )
            conn.commit()

            cdef = {"include_types": ["paper"]}
            docs = resolve_virtual_collection(conn, cdef)
            assert len(docs) == 2
            assert docs[0]["id"] == "d2"
            assert docs[1]["id"] == "d1"
            conn.close()
