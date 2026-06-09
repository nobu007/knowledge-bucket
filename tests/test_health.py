"""Tests for the graph health module."""

import os

import pytest

from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path
from kb.graph import init_graph_tables
from kb.health import compute_health
from kb.index import index_document, init_db


@pytest.fixture
def kb(tmp_path):
    root = str(tmp_path)
    ensure_dirs(root)
    return root


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

    return ulid


def _index_doc(root, doc_id, title, source_type="web", source=None, content="content"):
    from kb.index import index_path
    db = index_path(root)
    conn = init_db(db)
    rel = f"records/doc/{doc_id[:2]}/{doc_id}.md"
    index_document(conn, doc_id, title, source, source_type, rel, content)
    conn.close()


def _setup_graph(root, doc_id, source_type="web", concepts=None, importance=0.5):
    from datetime import UTC, datetime

    from kb.index import index_path

    db = index_path(root)
    conn = init_db(db)
    init_graph_tables(conn)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
        "VALUES (?, ?, 0, ?, ?)",
        (doc_id, source_type, importance, now),
    )
    if concepts:
        for c in concepts:
            conn.execute(
                "INSERT OR IGNORE INTO concepts "
                "(concept_id, label, kind, df, is_stop, created_at) "
                "VALUES (?, ?, 'concept', 1, 0, ?)",
                (c, c, now),
            )
            conn.execute(
                "INSERT INTO doc_concepts (doc_id, concept_id, role, weight) "
                "VALUES (?, ?, 'primary', 1.0)",
                (doc_id, c),
            )
    conn.commit()
    conn.close()


class TestComputeHealth:
    def test_no_index_returns_error(self, kb):
        report = compute_health(kb)
        assert "error" in report

    def test_empty_index(self, kb):
        _index_doc(kb, "testid001", "Empty", content="empty")
        report = compute_health(kb)
        assert report["overview"]["total_documents"] == 1
        assert report["overview"]["total_concepts"] == 0
        assert report["overview"]["total_edges"] == 0
        assert report["overview"]["orphan_documents"] == 1
        assert report["overview"]["isolated_documents"] == 1
        assert report["overview"]["hub_threshold"] == 50

    def test_with_graph_data(self, kb):
        doc_id = _add_doc(kb, title="RAG Paper", source_type="paper",
                          source="https://arxiv.org/abs/2401.0001",
                          concepts=["rag", "llm"])
        _index_doc(kb, doc_id, "RAG Paper", source_type="paper",
                   source="https://arxiv.org/abs/2401.0001", content="RAG with LLM")
        _setup_graph(kb, doc_id, source_type="paper", concepts=["rag", "llm"])

        report = compute_health(kb)
        ov = report["overview"]
        assert ov["total_documents"] == 1
        assert ov["total_concepts"] == 2
        assert ov["orphan_documents"] == 0
        assert report["source_types"]["paper"] == 1
        assert len(report["top_concepts"]) == 2

    def test_importance_distribution(self, kb):
        doc1 = _add_doc(kb, title="High", source_type="paper")
        _index_doc(kb, doc1, "High", source_type="paper")
        _setup_graph(kb, doc1, source_type="paper", importance=0.8)

        doc2 = _add_doc(kb, title="Low", source_type="memo")
        _index_doc(kb, doc2, "Low", source_type="memo")
        _setup_graph(kb, doc2, source_type="memo", importance=0.0)

        report = compute_health(kb)
        dist = report["importance_distribution"]
        assert dist["high"] == 1
        assert dist["unscored"] >= 0

    def test_connectivity_metrics(self, kb):
        doc1 = _add_doc(kb, title="Doc A", concepts=["rag"])
        _index_doc(kb, doc1, "Doc A")
        _setup_graph(kb, doc1, concepts=["rag"])

        report = compute_health(kb)
        m = report["metrics"]
        assert m["connectivity_ratio"] == 0.0
        assert m["avg_concepts_per_doc"] == 1.0

    def test_concepts_missing_notes(self, kb):
        doc1 = _add_doc(kb, title="Doc", concepts=["rag", "llm"])
        _index_doc(kb, doc1, "Doc")
        _setup_graph(kb, doc1, concepts=["rag", "llm"])

        # Set df >= 2 for one concept to qualify for note check
        from kb.index import index_path
        db = index_path(kb)
        conn = init_db(db)
        init_graph_tables(conn)
        conn.execute("UPDATE concepts SET df = 2 WHERE concept_id = 'rag'")
        conn.commit()
        conn.close()

        report = compute_health(kb)
        # rag has df=2 and no note file
        assert report["concepts_missing_notes"] >= 1

    def test_hub_concepts_detected(self, kb):
        doc1 = _add_doc(kb, title="Doc", concepts=["rag", "common-term"])
        _index_doc(kb, doc1, "Doc")
        _setup_graph(kb, doc1, concepts=["rag", "common-term"])

        # Set common-term df above threshold (50 for N=1)
        from kb.index import index_path
        db = index_path(kb)
        conn = init_db(db)
        init_graph_tables(conn)
        conn.execute("UPDATE concepts SET df = 100 WHERE concept_id = 'common-term'")
        conn.execute("UPDATE concepts SET df = 2 WHERE concept_id = 'rag'")
        conn.commit()
        conn.close()

        report = compute_health(kb)
        hub_ids = [c["id"] for c in report["hub_concepts"]]
        assert "common-term" in hub_ids
        assert "rag" not in hub_ids


class TestHealthWebUI:
    @pytest.fixture
    def client(self, kb):
        from kb.web import create_app
        app = create_app(kb)
        app.config["TESTING"] = True
        return app.test_client()

    def test_health_page_error(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Health" in html

    def test_api_health_error(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "error" in data

    def test_health_page_with_data(self, kb):
        from kb.web import create_app

        doc_id = _add_doc(kb, title="Test", concepts=["x"])
        _index_doc(kb, doc_id, "Test")
        _setup_graph(kb, doc_id, concepts=["x"])

        app = create_app(kb)
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/health")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Documents" in html
        assert "Metrics" in html

    def test_api_health_with_data(self, kb):
        from kb.web import create_app

        doc_id = _add_doc(kb, title="Test", concepts=["x"])
        _index_doc(kb, doc_id, "Test")
        _setup_graph(kb, doc_id, concepts=["x"])

        app = create_app(kb)
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "overview" in data
        assert data["overview"]["total_documents"] == 1
        assert "metrics" in data

    def test_health_route_registered(self, kb):
        from kb.web import create_app

        app = create_app(kb)
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/health" in rules
        assert "/api/health" in rules
