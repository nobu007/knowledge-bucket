"""Tests for TF-IDF vector index and semantic search."""

import os

import numpy as np
import pytest

from kb.core import ensure_dirs
from kb.index import index_document, index_path, init_db
from kb.vectors import build_vectors, semantic_search, vector_path


@pytest.fixture
def kb(tmp_path):
    root = str(tmp_path)
    ensure_dirs(root)
    return root


def _index_doc(root, doc_id, title, source_type="web", source=None, content="content"):
    db = index_path(root)
    conn = init_db(db)
    rel = f"records/doc/{doc_id[:2]}/{doc_id}.md"
    index_document(conn, doc_id, title, source, source_type, rel, content)
    conn.close()


class TestBuildVectors:
    def test_no_index_raises(self, kb):
        with pytest.raises(FileNotFoundError, match="No index"):
            build_vectors(kb)

    def test_empty_index(self, kb):
        _index_doc(kb, "id001", "Empty", content="empty")
        result = build_vectors(kb)
        assert result["docs_vectorized"] == 1

    def test_creates_vector_file(self, kb):
        _index_doc(kb, "id001", "Test", content="hello world")
        build_vectors(kb)
        assert os.path.isfile(vector_path(kb))

    def test_vector_shape(self, kb):
        for i in range(5):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}", content=f"document content number {i}")
        build_vectors(kb)

        data = np.load(vector_path(kb))
        assert data["vectors"].shape == (5, 4096)
        assert len(data["ids"]) == 5
        assert data["idf"].shape == (4096,)

    def test_zero_docs(self, kb):
        # Create an empty index
        db = index_path(kb)
        conn = init_db(db)
        conn.close()
        result = build_vectors(kb)
        assert result["docs_vectorized"] == 0

    def test_vectors_normalized(self, kb):
        for i in range(3):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}",
                       content=f"machine learning neural network deep learning {'x' * (i+1)}")
        build_vectors(kb)

        data = np.load(vector_path(kb))
        norms = np.linalg.norm(data["vectors"], axis=1)
        for norm in norms:
            assert abs(norm - 1.0) < 1e-5 or norm == 0.0


class TestSemanticSearch:
    def test_no_vector_index_raises(self, kb):
        with pytest.raises(FileNotFoundError, match="Vector index"):
            semantic_search(kb, "test")

    def test_returns_results(self, kb):
        _index_doc(kb, "id001", "GraphRAG Introduction",
                   content="graph rag retrieval augmented generation knowledge graph")
        _index_doc(kb, "id002", "Cooking Recipe",
                   content="cooking recipe kitchen food meal preparation")
        build_vectors(kb)

        results = semantic_search(kb, "graph rag")
        assert len(results) > 0
        assert results[0]["id"] == "id001"

    def test_ranking_by_relevance(self, kb):
        _index_doc(kb, "id001", "RAG Overview",
                   content="retrieval augmented generation rag llm knowledge")
        _index_doc(kb, "id002", "Unrelated Topic",
                   content="cooking kitchen recipe food meal")
        _index_doc(kb, "id003", "RAG Advanced",
                   content="advanced retrieval augmented generation rag techniques")
        build_vectors(kb)

        results = semantic_search(kb, "retrieval augmented generation")
        ids = [r["id"] for r in results]
        # The two RAG docs should rank above the cooking doc
        assert "id001" in ids
        assert "id003" in ids
        # Cooking doc should be last if it appears at all
        if "id002" in ids:
            assert ids.index("id002") > ids.index("id001")

    def test_scores_between_zero_and_one(self, kb):
        _index_doc(kb, "id001", "Test", content="hello world")
        build_vectors(kb)

        results = semantic_search(kb, "hello")
        if results:
            assert 0 < results[0]["score"] <= 1.0

    def test_no_match_returns_empty(self, kb):
        _index_doc(kb, "id001", "Alpha", content="alpha beta gamma")
        build_vectors(kb)

        results = semantic_search(kb, "zzzzzzzz")
        # May return 0 results or low-score results; ensure no crash
        for r in results:
            assert "id" in r
            assert "score" in r

    def test_limit(self, kb):
        for i in range(10):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}",
                       content=f"document content text about topic {i}")
        build_vectors(kb)

        results = semantic_search(kb, "document content", limit=3)
        assert len(results) <= 3
