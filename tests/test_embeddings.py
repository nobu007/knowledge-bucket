"""Tests for embedding-based vector index."""

import os
from unittest.mock import patch

import numpy as np
import pytest

from kb.core import ensure_dirs
from kb.embeddings import (
    LocalHashEngine,
    OpenAIEngine,
    _engine_cache,
    _get_engine,
    build_embeddings,
    embedding_search,
    embeddings_path,
)
from kb.index import index_document, index_path, init_db


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


class TestLocalHashEngine:
    def test_returns_correct_count(self):
        eng = LocalHashEngine(dim=128)
        vecs = eng.embed_texts(["hello world", "foo bar"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 128

    def test_normalized(self):
        eng = LocalHashEngine(dim=64)
        vecs = eng.embed_texts(["test input text"])
        norm = sum(v * v for v in vecs[0]) ** 0.5
        assert abs(norm - 1.0) < 1e-5

    def test_empty_input(self):
        eng = LocalHashEngine()
        assert eng.embed_texts([]) == []

    def test_dim_property(self):
        eng = LocalHashEngine(dim=256)
        assert eng.dim == 256


class TestGetEngine:
    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            _get_engine("nonexistent")

    def test_local(self):
        eng = _get_engine("hash")
        assert isinstance(eng, LocalHashEngine)

    def test_openai(self):
        eng = _get_engine("openai")
        assert isinstance(eng, OpenAIEngine)


class TestBuildEmbeddings:
    def test_no_index_raises(self, kb):
        with pytest.raises(FileNotFoundError, match="No index"):
            build_embeddings(kb, engine="hash")

    def test_empty_index(self, kb):
        db = index_path(kb)
        conn = init_db(db)
        conn.close()
        result = build_embeddings(kb, engine="hash")
        assert result["docs_vectorized"] == 0

    def test_creates_file(self, kb):
        _index_doc(kb, "id001", "Test", content="hello world")
        result = build_embeddings(kb, engine="hash")
        assert result["docs_vectorized"] == 1
        assert os.path.isfile(embeddings_path(kb))

    def test_vector_shape(self, kb):
        for i in range(5):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}", content=f"document content number {i}")
        result = build_embeddings(kb, engine="hash", dim=128)
        assert result["dim"] == 128

        data = np.load(embeddings_path(kb))
        assert data["vectors"].shape == (5, 128)
        assert len(data["ids"]) == 5

    def test_vectors_normalized(self, kb):
        for i in range(3):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}",
                       content=f"machine learning neural network deep learning {i}")
        build_embeddings(kb, engine="hash", dim=64)

        data = np.load(embeddings_path(kb))
        norms = np.linalg.norm(data["vectors"], axis=1)
        for norm in norms:
            assert abs(norm - 1.0) < 1e-5 or norm == 0.0

    def test_engine_in_result(self, kb):
        _index_doc(kb, "id001", "Test", content="test")
        result = build_embeddings(kb, engine="hash")
        assert result["engine"] == "hash"


class TestEmbeddingSearch:
    def test_no_index_raises(self, kb):
        with pytest.raises(FileNotFoundError, match="Embedding index"):
            embedding_search(kb, "test")

    def test_returns_results(self, kb):
        _index_doc(kb, "id001", "GraphRAG",
                   content="graph rag retrieval augmented generation knowledge graph")
        _index_doc(kb, "id002", "Cooking",
                   content="cooking recipe kitchen food meal preparation")
        build_embeddings(kb, engine="hash", dim=64)

        results = embedding_search(kb, "graph rag")
        assert len(results) > 0
        for r in results:
            assert "id" in r
            assert "score" in r
            assert r["id"] in ("id001", "id002")

    def test_scores_between_zero_and_one(self, kb):
        _index_doc(kb, "id001", "Test", content="hello world")
        build_embeddings(kb, engine="hash", dim=64)

        results = embedding_search(kb, "hello")
        if results:
            assert 0 < results[0]["score"] <= 1.0

    def test_limit(self, kb):
        for i in range(10):
            _index_doc(kb, f"id{i:03d}", f"Doc {i}",
                       content=f"document content text about topic {i}")
        build_embeddings(kb, engine="hash", dim=64)

        results = embedding_search(kb, "document", limit=3)
        assert len(results) <= 3


class TestOpenAIEngineMocked:
    def test_embed_texts_calls_api(self):
        engine = OpenAIEngine(api_key="test-key", base_url="https://example.com/v1")

        mock_response = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3], "index": 0},
                {"embedding": [0.4, 0.5, 0.6], "index": 1},
            ]
        }

        class MockResp:
            def read(self):
                import json
                return json.dumps(mock_response).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with patch("kb.embeddings.urllib.request.urlopen", return_value=MockResp()):
            vecs = engine.embed_texts(["hello", "world"])
            assert len(vecs) == 2
            assert vecs[0] == [0.1, 0.2, 0.3]
            assert engine.dim == 3

    def test_empty_input(self):
        engine = OpenAIEngine()
        assert engine.embed_texts([]) == []

    def test_dim_unknown_before_first_call(self):
        engine = OpenAIEngine()
        with pytest.raises(RuntimeError, match="dim unknown"):
            _ = engine.dim


class TestSentenceTransformerCache:
    """The engine cache must return the same instance for a given model name
    so repeated embedding_search() calls don't reload the model."""

    def test_same_instance_for_same_model(self):
        import sys
        import types

        # Build stub modules so __init__ runs without a real model download.
        fake_st = types.ModuleType("sentence_transformers")

        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                self.model_name = model_name
                self.device = device

            def get_embedding_dimension(self):
                return 8

            def encode(self, texts, **kwargs):
                return [[0.0] * 8 for _ in texts]

        fake_st.SentenceTransformer = FakeSentenceTransformer

        fake_torch = types.ModuleType("torch")
        _false = types.SimpleNamespace(is_available=lambda: False)
        fake_torch.backends = types.SimpleNamespace(mps=_false, cuda=_false)
        fake_torch.cuda = _false

        orig_st = sys.modules.get("sentence_transformers")
        orig_torch = sys.modules.get("torch")
        sys.modules["sentence_transformers"] = fake_st
        sys.modules["torch"] = fake_torch
        _engine_cache.clear()
        try:
            from kb.embeddings import SentenceTransformerEngine

            first = SentenceTransformerEngine(model="bge-m3-test")
            second = SentenceTransformerEngine(model="bge-m3-test")
            assert first is second
            # Cache is keyed by model name.
            assert "bge-m3-test" in _engine_cache
            assert _engine_cache["bge-m3-test"] is first
        finally:
            _engine_cache.clear()
            if orig_st is not None:
                sys.modules["sentence_transformers"] = orig_st
            else:
                sys.modules.pop("sentence_transformers", None)
            if orig_torch is not None:
                sys.modules["torch"] = orig_torch
            else:
                sys.modules.pop("torch", None)
