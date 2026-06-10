"""Tests for index module: SQLite FTS5 indexing and search."""

import os
import tempfile

from kb.core import ensure_dirs
from kb.index import (
    build_index,
    index_document,
    index_path,
    init_db,
    parse_front_matter,
    reindex_document,
    search_index,
)


class TestParseFrontMatter:
    def test_standard(self):
        text = "---\nid: 01K2Z9P7Y8QWERTY1234567890\ntitle: Test\n---\n\nBody here"
        meta, body = parse_front_matter(text)
        assert meta["id"] == "01K2Z9P7Y8QWERTY1234567890"
        assert meta["title"] == "Test"
        assert "Body here" in body

    def test_no_front_matter(self):
        meta, body = parse_front_matter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"

    def test_with_source(self):
        text = "---\nid: abc\ntitle: T\nsource: https://example.com\n---\ncontent"
        meta, body = parse_front_matter(text)
        assert meta["source"] == "https://example.com"


class TestInitDb:
    def test_creates_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            assert os.path.exists(db)
            conn.close()

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            init_db(db)
            init_db(db)  # should not raise
            assert os.path.exists(db)


class TestIndexDocument:
    def test_insert_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            index_document(conn, "id1", "RAG Guide", None, "web", "doc.md",
                           "Retrieval augmented generation is a technique")
            results = search_index(conn, "retrieval")
            assert len(results) == 1
            assert results[0]["id"] == "id1"
            conn.close()


class TestBuildIndex:
    def test_indexes_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "test.md"), "w") as f:
                f.write("---\nid: testdoc001\ntitle: Hello World\n---\n\nSome content here\n")

            count = build_index(tmp)
            assert count == 1

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            count = build_index(tmp)
            assert count == 0


class TestSearch:
    def test_finds_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            index_document(conn, "id1", "Graph RAG", None, "web", "doc.md",
                           "Graph-based retrieval augmented generation")
            index_document(conn, "id2", "Unrelated", None, "web", "doc2.md",
                           "Something about cooking")
            results = search_index(conn, "retrieval")
            assert len(results) == 1
            assert results[0]["id"] == "id1"
            conn.close()

    def test_no_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            index_document(conn, "id1", "Test", None, "web", "doc.md", "hello")
            results = search_index(conn, "nonexistent_xyz")
            assert len(results) == 0
            conn.close()

    def test_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            for i in range(10):
                index_document(conn, f"id{i}", f"Doc {i}", None, "web",
                               f"doc{i}.md", "machine learning stuff")
            results = search_index(conn, "machine", limit=3)
            assert len(results) == 3
            conn.close()


class TestIndexPath:
    def test_path(self):
        assert index_path("/tmp/bucket") == "/tmp/bucket/.kb/index.db"


class TestReindexDocument:
    def test_updates_fts_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)

            # Create a doc file
            doc_file = os.path.join(tmp, "doc.md")
            with open(doc_file, "w") as f:
                f.write("---\nid: doc1\ntitle: Test\n---\n\nOriginal content\n")
            index_document(conn, "doc1", "Test", None, "web", "doc.md",
                           "Original content")

            # Verify original is indexed
            results = search_index(conn, "Original")
            assert len(results) == 1

            # Update file on disk
            with open(doc_file, "w") as f:
                f.write("---\nid: doc1\ntitle: Updated\n---\n\nNew content here\n")

            # Reindex
            reindex_document(conn, "doc1", doc_file, tmp)

            # Old content gone, new content findable
            assert len(search_index(conn, "Original")) == 0
            results = search_index(conn, "New content")
            assert len(results) == 1
            assert results[0]["title"] == "Updated"
            conn.close()

    def test_removes_fts_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            index_document(conn, "doc1", "Test", None, "web", "doc.md",
                           "Some content")

            # Reindex with nonexistent file
            result = reindex_document(conn, "doc1", "/nonexistent/path.md", tmp)
            assert result is False
            assert len(search_index(conn, "Some")) == 0
            conn.close()
