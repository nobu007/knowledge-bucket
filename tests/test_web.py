"""Tests for the local web UI module."""

import os

import pytest

from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path
from kb.index import index_document, init_db
from kb.web import create_app


@pytest.fixture
def kb(tmp_path):
    root = str(tmp_path)
    ensure_dirs(root)
    return root


@pytest.fixture
def app(kb):
    app = create_app(kb)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _add_doc(root, title="Test Document", source_type="web", source=None,
             concepts=None, body="Some content here."):
    ulid = generate_ulid()
    rel = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel)

    fm = f"---\nid: {ulid}\ntitle: {title}\nsource_type: {source_type}\n"
    if source:
        fm += f"source: {source}\n"
    if concepts:
        fm += "concepts:\n"
        for c in concepts:
            fm += f"  - {c}\n"
    fm += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(fm)
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")

    return ulid, rel


def _index_doc(root, doc_id, title, source_type="web", source=None, content="Some content here."):
    from kb.index import index_path
    db = index_path(root)
    conn = init_db(db)
    rel = f"records/doc/{doc_id[:2]}/{doc_id}.md"
    index_document(
        conn, doc_id, title, source, source_type, rel, content,
    )
    conn.close()


class TestIndexPage:
    def test_empty_search(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Knowledge Bucket" in html

    def test_search_with_results(self, kb, client):
        doc_id, _ = _add_doc(
            kb, title="RAG systems overview",
            body="Retrieval augmented generation",
        )
        _index_doc(
            kb, doc_id, "RAG systems overview",
            content="Retrieval augmented generation",
        )

        resp = client.get("/?q=RAG")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "RAG systems overview" in html

    def test_search_no_results(self, client):
        resp = client.get("/?q=nonexistent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No results found" in html


class TestDocDetail:
    def test_doc_found(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Test Paper", source_type="paper",
                             source="https://arxiv.org/abs/2401.0001",
                             body="Abstract of the paper.")

        resp = client.get(f"/doc/{doc_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Test Paper" in html
        assert "Abstract of the paper" in html
        assert "paper" in html

    def test_doc_not_found(self, client):
        resp = client.get("/doc/DOESNOTEXIST")
        assert resp.status_code == 404

    def test_doc_with_concepts(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Graph RAG paper",
                             concepts=["graph-rag", "retrieval-augmented-generation"])

        resp = client.get(f"/doc/{doc_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "graph-rag" in html
        assert "retrieval-augmented-generation" in html


class TestApiEndpoints:
    def test_api_search(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Vector databases", body="Vector search with embeddings")
        _index_doc(kb, doc_id, "Vector databases", content="Vector search with embeddings")

        resp = client.get("/api/search?q=vector")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["title"] == "Vector databases"

    def test_api_search_empty_query(self, client):
        resp = client.get("/api/search?q=")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["results"] == []

    def test_api_stats(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Stats test doc")
        _index_doc(kb, doc_id, "Stats test doc")

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["docs"] >= 1


class TestCreateApp:
    def test_app_has_routes(self, kb):
        app = create_app(kb)
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/" in rules
        assert "/doc/<doc_id>" in rules
        assert "/api/search" in rules
        assert "/api/stats" in rules

    def test_app_stores_kb_root(self, kb):
        app = create_app(kb)
        assert app.config["KB_ROOT"] == kb
