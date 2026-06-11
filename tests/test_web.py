"""Tests for the local web UI module."""

import os

import pytest

from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path
from kb.graph import init_graph_tables
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


def _setup_graph(root, doc_id, source_type="web", concepts=None):
    from datetime import UTC, datetime

    from kb.index import index_path

    db = index_path(root)
    conn = init_db(db)
    init_graph_tables(conn)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
        "VALUES (?, ?, 0, 0.5, ?)",
        (doc_id, source_type, now),
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
        assert "/categories" in rules
        assert "/categories/<source_type>" in rules
        assert "/concepts" in rules
        assert "/concepts/<concept_id>" in rules
        assert "/graph" in rules
        assert "/api/graph" in rules

    def test_app_stores_kb_root(self, kb):
        app = create_app(kb)
        assert app.config["KB_ROOT"] == kb


class TestCategoriesPage:
    def test_empty_categories(self, client):
        resp = client.get("/categories")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Categories" in html
        assert "No categories yet" in html

    def test_categories_with_data(self, kb, client):
        doc_id, _ = _add_doc(kb, title="A paper", source_type="paper")
        _index_doc(kb, doc_id, "A paper", source_type="paper")
        _setup_graph(kb, doc_id, source_type="paper")

        resp = client.get("/categories")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "paper" in html


class TestCategoryDetail:
    def test_category_with_docs(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Test Web Page", source_type="web")
        _index_doc(kb, doc_id, "Test Web Page", source_type="web")
        _setup_graph(kb, doc_id, source_type="web")

        resp = client.get("/categories/web")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Test Web Page" in html

    def test_category_empty(self, client):
        resp = client.get("/categories/memo")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No documents" in html


class TestConceptsPage:
    def test_empty_concepts(self, client):
        resp = client.get("/concepts")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Concepts" in html
        assert "No concepts yet" in html

    def test_concepts_with_data(self, kb, client):
        doc_id, _ = _add_doc(kb, title="RAG doc", concepts=["rag", "llm"])
        _index_doc(kb, doc_id, "RAG doc")
        _setup_graph(kb, doc_id, concepts=["rag", "llm"])

        resp = client.get("/concepts")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "rag" in html
        assert "llm" in html


class TestConceptDetail:
    def test_concept_with_docs(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Graph RAG", concepts=["graph-rag"])
        _index_doc(kb, doc_id, "Graph RAG")
        _setup_graph(kb, doc_id, concepts=["graph-rag"])

        resp = client.get("/concepts/graph-rag")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "graph-rag" in html
        assert "Graph RAG" in html

    def test_concept_no_docs(self, client):
        resp = client.get("/concepts/nonexistent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No documents" in html

    def test_concept_shows_cooccurring(self, kb, client):
        from kb.graph import compute_df
        from kb.index import index_path
        from kb.related import build_concept_edges

        # Both docs share rag and llm so both concepts reach df=2
        d1, _ = _add_doc(kb, title="RAG Overview", concepts=["rag", "llm"])
        d2, _ = _add_doc(kb, title="RAG Pipeline", concepts=["rag", "llm"])
        _index_doc(kb, d1, "RAG Overview")
        _index_doc(kb, d2, "RAG Pipeline")
        _setup_graph(kb, d1, concepts=["rag", "llm"])
        _setup_graph(kb, d2, concepts=["rag", "llm"])

        # Build co-occurrence edges
        db = index_path(kb)
        conn = init_db(db)
        try:
            compute_df(conn)
            build_concept_edges(conn, min_cooccurrence=1)
        finally:
            conn.close()

        resp = client.get("/concepts/rag")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Co-occurring Concepts" in html
    def test_graph_page_renders(self, client):
        resp = client.get("/graph")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Concept Graph" in html
        assert "d3" in html

    def test_graph_page_empty(self, client):
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["nodes"] == []
        assert data["links"] == []

    def test_graph_page_with_data(self, kb, client):
        doc_id, _ = _add_doc(kb, title="RAG Overview", concepts=["rag", "llm"])
        _index_doc(kb, doc_id, "RAG Overview")
        _setup_graph(kb, doc_id, concepts=["rag", "llm"])

        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["nodes"]) >= 3  # 1 doc + 2 concepts
        assert len(data["links"]) >= 2  # doc connected to both concepts
        concept_nodes = [n for n in data["nodes"] if n["type"] == "concept"]
        doc_nodes = [n for n in data["nodes"] if n["type"] == "doc"]
        assert any(n["label"] == "rag" for n in concept_nodes)
        assert any(n["label"] == "RAG Overview" for n in doc_nodes)


class TestRecentPage:
    def test_recent_empty(self, client):
        resp = client.get("/recent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Recent Documents" in html
        assert "No documents yet" in html

    def test_recent_with_docs(self, kb, client):
        d1, _ = _add_doc(kb, title="First doc")
        d2, _ = _add_doc(kb, title="Second doc")
        _index_doc(kb, d1, "First doc")
        _index_doc(kb, d2, "Second doc")

        resp = client.get("/recent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Second doc" in html
        assert "First doc" in html

    def test_api_recent(self, kb, client):
        d1, _ = _add_doc(kb, title="Doc A")
        d2, _ = _add_doc(kb, title="Doc B")
        _index_doc(kb, d1, "Doc A")
        _index_doc(kb, d2, "Doc B")

        resp = client.get("/api/recent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["docs"]) == 2
        # ULIDs are time-sortable so Doc B (added second) comes first
        assert data["docs"][0]["title"] == "Doc B"
        assert data["docs"][1]["title"] == "Doc A"

    def test_api_recent_limit(self, kb, client):
        for i in range(5):
            d, _ = _add_doc(kb, title=f"Doc {i}")
            _index_doc(kb, d, f"Doc {i}")

        resp = client.get("/api/recent?limit=2")
        data = resp.get_json()
        assert len(data["docs"]) == 2

    def test_homepage_shows_recent_when_no_query(self, kb, client):
        d, _ = _add_doc(kb, title="Homepage Recent Doc")
        _index_doc(kb, d, "Homepage Recent Doc")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Recent Documents" in html
        assert "Homepage Recent Doc" in html
        assert "View more recent documents" in html

    def test_homepage_no_recent_when_searching(self, kb, client):
        d, _ = _add_doc(kb, title="Should not show recent")
        _index_doc(kb, d, "Should not show recent")

        resp = client.get("/?q=test")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Recent Documents" not in html


class TestPagination:
    def test_recent_pagination_default_page(self, kb, client):
        d, _ = _add_doc(kb, title="Only doc")
        _index_doc(kb, d, "Only doc")

        resp = client.get("/recent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Only doc" in html
        # Single doc shouldn't show pagination
        assert "Page 1 of 1" not in html

    def test_recent_pagination_multi_page(self, kb, client):
        for i in range(25):
            d, _ = _add_doc(kb, title=f"Doc {i:03d}")
            _index_doc(kb, d, f"Doc {i:03d}")

        # Page 1: first 20 docs
        resp = client.get("/recent")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Page 1 of 2" in html
        assert "Next" in html

        # Page 2: remaining 5 docs
        resp2 = client.get("/recent?page=2")
        assert resp2.status_code == 200
        html2 = resp2.data.decode()
        assert "Page 2 of 2" in html2
        assert "Prev" in html2
        assert "Next" not in html2

    def test_recent_pagination_out_of_range(self, kb, client):
        for i in range(5):
            d, _ = _add_doc(kb, title=f"Doc {i}")
            _index_doc(kb, d, f"Doc {i}")

        # Page beyond data should return empty doc list but valid page
        resp = client.get("/recent?page=99")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No documents" in html or "doc-item" not in html

    def test_homepage_recent_pagination(self, kb, client):
        for i in range(25):
            d, _ = _add_doc(kb, title=f"Home doc {i:03d}")
            _index_doc(kb, d, f"Home doc {i:03d}")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Page 1 of 2" in html
        assert "Next" in html

        resp2 = client.get("/?page=2")
        assert resp2.status_code == 200
        html2 = resp2.data.decode()
        assert "Page 2 of 2" in html2

    def test_homepage_search_pagination(self, kb, client):
        for i in range(25):
            d, _ = _add_doc(
                kb, title=f"RAG paper {i:03d}",
                body=f"Retrieval augmented generation {i}",
            )
            _index_doc(
                kb, d, f"RAG paper {i:03d}",
                content=f"Retrieval augmented generation {i}",
            )

        resp = client.get("/?q=RAG")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Page 1 of" in html
        assert "Next" in html

        resp2 = client.get("/?q=RAG&page=2")
        assert resp2.status_code == 200
        html2 = resp2.data.decode()
        assert "Page 2 of" in html2
        assert "Prev" in html2


class TestSorting:
    def test_recent_sort_by_date(self, kb, client):
        d1, _ = _add_doc(kb, title="First doc")
        d2, _ = _add_doc(kb, title="Second doc")
        _index_doc(kb, d1, "First doc")
        _index_doc(kb, d2, "Second doc")

        resp = client.get("/recent?sort=date")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Sort by:" in html
        # Default sort is date (by ULID DESC), second doc appears first
        assert html.index("Second doc") < html.index("First doc")

    def test_recent_sort_by_importance(self, kb, client):
        d1, _ = _add_doc(kb, title="Low importance")
        d2, _ = _add_doc(kb, title="High importance")
        _index_doc(kb, d1, "Low importance")
        _index_doc(kb, d2, "High importance")
        _setup_graph(kb, d1, source_type="web")
        _setup_graph(kb, d2, source_type="web")

        # Set different importance values
        from kb.index import index_path
        db = index_path(kb)
        conn = init_db(db)
        conn.execute("UPDATE doc_stats SET importance = 0.2 WHERE doc_id = ?", (d1,))
        conn.execute("UPDATE doc_stats SET importance = 0.9 WHERE doc_id = ?", (d2,))
        conn.commit()
        conn.close()

        resp = client.get("/recent?sort=importance")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.index("High importance") < html.index("Low importance")
        assert "importance:" in html

    def test_recent_sort_by_type(self, kb, client):
        d1, _ = _add_doc(kb, title="Web doc", source_type="web")
        d2, _ = _add_doc(kb, title="Paper doc", source_type="paper")
        _index_doc(kb, d1, "Web doc", source_type="web")
        _index_doc(kb, d2, "Paper doc", source_type="paper")

        resp = client.get("/recent?sort=type")
        assert resp.status_code == 200
        html = resp.data.decode()
        # "paper" comes before "web" alphabetically
        assert html.index("Paper doc") < html.index("Web doc")

    def test_recent_sort_default_is_date(self, kb, client):
        d1, _ = _add_doc(kb, title="First doc")
        d2, _ = _add_doc(kb, title="Second doc")
        _index_doc(kb, d1, "First doc")
        _index_doc(kb, d2, "Second doc")

        resp = client.get("/recent")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Without sort param, defaults to date (ULID DESC)
        assert html.index("Second doc") < html.index("First doc")

    def test_recent_sort_invalid_ignored(self, kb, client):
        d, _ = _add_doc(kb, title="Doc")
        _index_doc(kb, d, "Doc")

        resp = client.get("/recent?sort=invalid")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Doc" in html

    def test_homepage_sort_by_importance(self, kb, client):
        d1, _ = _add_doc(kb, title="Low imp doc")
        d2, _ = _add_doc(kb, title="High imp doc")
        _index_doc(kb, d1, "Low imp doc")
        _index_doc(kb, d2, "High imp doc")
        _setup_graph(kb, d1, source_type="web")
        _setup_graph(kb, d2, source_type="web")

        from kb.index import index_path
        db = index_path(kb)
        conn = init_db(db)
        conn.execute("UPDATE doc_stats SET importance = 0.1 WHERE doc_id = ?", (d1,))
        conn.execute("UPDATE doc_stats SET importance = 0.8 WHERE doc_id = ?", (d2,))
        conn.commit()
        conn.close()

        resp = client.get("/?sort=importance")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Sort by:" in html
        assert html.index("High imp doc") < html.index("Low imp doc")


class TestDocEdit:
    def test_edit_form_renders(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Editable doc", body="Original content.")
        resp = client.get(f"/doc/{doc_id}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Edit: Editable doc" in html
        assert "memo" in html
        assert "rating" in html
        assert "Save" in html

    def test_edit_form_not_found(self, client):
        resp = client.get("/doc/DOESNOTEXIST/edit")
        assert resp.status_code == 404

    def test_edit_post_saves_memo(self, kb, client):
        doc_id, rel = _add_doc(kb, title="Memo test", body="Content.")
        resp = client.post(f"/doc/{doc_id}/edit", data={
            "memo": "My important note",
            "rating": "",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/doc/{doc_id}")

        # Verify front matter was updated
        doc_path = os.path.join(kb, "records", "doc", os.path.dirname(rel), f"{doc_id}.md")
        with open(doc_path) as f:
            text = f.read()
        assert "My important note" in text
        assert "memo:" in text
        assert "Content." in text

    def test_edit_post_saves_rating(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Rating test", body="Content.")
        resp = client.post(f"/doc/{doc_id}/edit", data={
            "memo": "",
            "rating": "0.85",
        })
        assert resp.status_code == 302

        # Check the detail page shows the rating
        resp2 = client.get(f"/doc/{doc_id}")
        assert resp2.status_code == 200
        html = resp2.data.decode()
        assert "0.85" in html

    def test_edit_post_round_trip(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Round trip", body="Body text.")
        # Save memo + rating
        client.post(f"/doc/{doc_id}/edit", data={
            "memo": "First note",
            "rating": "0.7",
        })
        # Re-open edit form — should show saved values
        resp = client.get(f"/doc/{doc_id}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "First note" in html
        assert "0.7" in html

        # Update memo, clear rating
        client.post(f"/doc/{doc_id}/edit", data={
            "memo": "Updated note",
            "rating": "",
        })
        resp2 = client.get(f"/doc/{doc_id}")
        assert resp2.status_code == 200
        html2 = resp2.data.decode()
        assert "Updated note" in html2
        assert "Rating" not in html2

    def test_doc_detail_shows_edit_link(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Has edit link")
        resp = client.get(f"/doc/{doc_id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f"/doc/{doc_id}/edit" in html
        assert "Edit" in html

    def test_edit_invalid_rating_ignored(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Bad rating")
        resp = client.post(f"/doc/{doc_id}/edit", data={
            "memo": "Note",
            "rating": "not-a-number",
        })
        assert resp.status_code == 302
        # Rating should not be saved
        resp2 = client.get(f"/doc/{doc_id}")
        html = resp2.data.decode()
        assert "not-a-number" not in html


class TestDarkMode:
    def test_css_variables_present(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "--kb-text:" in html
        assert "--kb-bg:" in html
        assert "--kb-link:" in html
        assert "--kb-border:" in html

    def test_prefers_color_scheme_dark(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "@media (prefers-color-scheme: dark)" in html
        assert "--kb-text: #e5e5e5;" in html
        assert "--kb-bg: #18181b;" in html

    def test_data_theme_dark_selector(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert '[data-theme="dark"]' in html

    def test_theme_toggle_button(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert 'onclick="toggleTheme()"' in html
        assert "theme-toggle" in html

    def test_toggle_js_present(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "localStorage" in html
        assert "toggleTheme" in html
        assert "kb-theme" in html

    def test_theme_injected_on_all_pages(self, kb, client):
        doc_id, _ = _add_doc(kb, title="Theme check")
        _index_doc(kb, doc_id, "Theme check")
        pages = [
            "/",
            f"/doc/{doc_id}",
            f"/doc/{doc_id}/edit",
            "/recent",
            "/categories",
            "/concepts",
            "/graph",
            "/health",
            "/collections",
        ]
        for url in pages:
            resp = client.get(url)
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "--kb-text:" in html, f"Missing CSS vars on {url}"
            assert "toggleTheme" in html, f"Missing toggle JS on {url}"

    def test_uses_var_refs_not_hardcoded_colors(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        # Body text should use var() not hardcoded color
        assert "var(--kb-text)" in html
        assert "var(--kb-link)" in html
        assert "var(--kb-border)" in html
