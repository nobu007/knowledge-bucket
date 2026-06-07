"""Tests for ingest module: inbox processing pipeline."""

import os
import tempfile

from kb.core import DOC_DIR, INBOX_DIR, RECORDS_DIR, ensure_dirs
from kb.ingest import _body_from_content, _classify_file, ingest_file, ingest_inbox


class TestClassifyFile:
    def test_url_file(self):
        title, url, stype = _classify_file("example-article.url", "https://example.com/article")
        assert title == "example-article"
        assert url == "https://example.com/article"
        assert stype == "web"

    def test_txt_with_url(self):
        title, url, stype = _classify_file("my-note.txt", "https://example.com\nSome notes")
        assert url == "https://example.com"
        assert stype == "web"

    def test_plain_memo(self):
        title, url, stype = _classify_file("random-thoughts.txt", "This is a memo about stuff")
        assert title == "This is a memo about stuff"
        assert url is None
        assert stype == "memo"

    def test_md_with_heading(self):
        title, url, stype = _classify_file("notes.md", "# My Notes\n\nSome content")
        assert title == "My Notes"
        assert url is None
        assert stype == "memo"

    def test_md_with_url_first_line(self):
        title, url, stype = _classify_file(
            "article.md", "https://example.com/paper\n\nGreat article about RAG"
        )
        assert url == "https://example.com/paper"
        assert stype == "web"


class TestBodyFromContent:
    def test_strips_source_url(self):
        body = _body_from_content("https://example.com\nActual content", "https://example.com")
        assert body.startswith("Actual content")
        assert "https://example.com" not in body

    def test_no_url(self):
        body = _body_from_content("Just some content\nMore text", None)
        assert "Just some content" in body


class TestIngestFile:
    def test_ingests_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "test-note.txt")
            with open(inbox_path, "w") as f:
                f.write("This is a test note about RAG systems")

            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None
            assert len(ulid) == 26
            assert not os.path.exists(inbox_path)

    def test_ingests_url_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "example.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article")

            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None
            assert not os.path.exists(inbox_path)

            # Verify record was created with source
            from kb.core import shard_path
            rel = shard_path(ulid)
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, rel)
            assert os.path.exists(doc_path)
            with open(doc_path) as f:
                content = f.read()
            assert "source: https://example.com/article" in content

    def test_skips_unsupported_ext(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "image.png")
            with open(inbox_path, "wb") as f:
                f.write(b"\x89PNG")

            result = ingest_file(tmp, inbox_path)
            assert result is None
            assert os.path.exists(inbox_path)

    def test_skips_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "empty.txt")
            with open(inbox_path, "w") as f:
                f.write("")

            result = ingest_file(tmp, inbox_path)
            assert result is None
            assert os.path.exists(inbox_path)

    def test_skips_gitkeep(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, ".gitkeep")
            with open(inbox_path, "w") as f:
                f.write("")

            result = ingest_file(tmp, inbox_path)
            assert result is None


class TestIngestInbox:
    def test_batch_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)

            for i in range(3):
                with open(os.path.join(tmp, INBOX_DIR, f"note{i}.txt"), "w") as f:
                    f.write(f"Note number {i} about machine learning")

            ingested = ingest_inbox(tmp)
            assert len(ingested) == 3

            # All inbox files should be removed
            remaining = [
                f for f in os.listdir(os.path.join(tmp, INBOX_DIR)) if f != ".gitkeep"
            ]
            assert len(remaining) == 0

    def test_empty_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            ingested = ingest_inbox(tmp)
            assert ingested == []

    def test_mixed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)

            # Valid file
            with open(os.path.join(tmp, INBOX_DIR, "good.txt"), "w") as f:
                f.write("Good content")
            # Unsupported file
            with open(os.path.join(tmp, INBOX_DIR, "bad.jpg"), "wb") as f:
                f.write(b"\xff\xd8")
            # Empty file
            with open(os.path.join(tmp, INBOX_DIR, "empty.txt"), "w") as f:
                f.write("")

            ingested = ingest_inbox(tmp)
            assert len(ingested) == 1

            # bad.jpg and empty.txt remain (empty.txt wasn't removed by ingest_file
            # since it returns None, but the loop just skips it)
            remaining = [
                f for f in os.listdir(os.path.join(tmp, INBOX_DIR)) if f != ".gitkeep"
            ]
            assert "bad.jpg" in remaining

    def test_records_searchable_after_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)

            with open(os.path.join(tmp, INBOX_DIR, "rag-note.txt"), "w") as f:
                f.write("Retrieval augmented generation is a powerful technique")

            ingested = ingest_inbox(tmp)
            assert len(ingested) == 1

            # Build index and verify searchable
            from kb.index import build_index, init_db, search_index

            build_index(tmp)
            db_path = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db_path)
            results = search_index(conn, "retrieval")
            assert len(results) == 1
            conn.close()
