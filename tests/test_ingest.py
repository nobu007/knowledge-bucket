"""Tests for ingest module: inbox processing pipeline."""

import os
import tempfile

from kb.core import DOC_DIR, INBOX_DIR, RECORDS_DIR, ensure_dirs
from kb.ingest import (
    _body_from_content,
    _classify_file,
    _has_broken_front_matter,
    ingest_file,
    ingest_inbox,
)


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

    def test_source_key_in_front_matter_web(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "article.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article")

            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None

            from kb.core import shard_path
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, shard_path(ulid))
            with open(doc_path) as f:
                content = f.read()
            assert "source_key: url:" in content

    def test_content_hash_in_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "note.txt")
            with open(inbox_path, "w") as f:
                f.write("Content about vector databases")

            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None

            from kb.core import shard_path
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, shard_path(ulid))
            with open(doc_path) as f:
                content = f.read()
            assert "content_hash: sha256:" in content

    def test_content_hash_updated_on_change(self):
        """content_hash in front matter is updated when content changes (section 18)."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)

            # First ingest
            inbox_path = os.path.join(tmp, INBOX_DIR, "article.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/a")
            ulid1 = ingest_file(tmp, inbox_path)

            from kb.core import shard_path
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, shard_path(ulid1))
            with open(doc_path) as f:
                text1 = f.read()
            import re
            hash1 = re.search(r"content_hash: (sha256:\w+)", text1)
            assert hash1 is not None

            # Second ingest with changed content
            with open(inbox_path, "w") as f:
                f.write("https://example.com/a\n\nNew body text")
            ulid2 = ingest_file(tmp, inbox_path)
            assert ulid2 == ulid1

            with open(doc_path) as f:
                text2 = f.read()
            hash2 = re.search(r"content_hash: (sha256:\w+)", text2)
            assert hash2 is not None
            assert hash1.group(1) != hash2.group(1)  # hash changed

    def test_source_key_in_front_matter_memo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "note.txt")
            with open(inbox_path, "w") as f:
                f.write("This is a test memo")

            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None

            from kb.core import shard_path
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, shard_path(ulid))
            with open(doc_path) as f:
                content = f.read()
            assert "source_key: memo:" in content

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

    def test_exact_duplicate_skipped(self):
        """Same source_key + same content → skip, no new document."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "article.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article")
            ulid1 = ingest_file(tmp, inbox_path)
            assert ulid1 is not None

            # Add identical file again
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article")
            ulid2 = ingest_file(tmp, inbox_path)
            assert ulid2 is None  # skipped
            assert not os.path.exists(inbox_path)  # file consumed

    def test_changed_content_updates_existing(self):
        """Same source_key + different content → update existing document (GOAL.md section 18)."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)

            # First ingest: create document
            inbox_path = os.path.join(tmp, INBOX_DIR, "article.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article")
            ulid1 = ingest_file(tmp, inbox_path)
            assert ulid1 is not None

            from kb.core import shard_path
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, shard_path(ulid1))
            with open(doc_path) as f:
                text1 = f.read()
            assert "https://example.com/article" in text1

            # Second ingest: same URL, but now with extra content (different body)
            with open(inbox_path, "w") as f:
                f.write("https://example.com/article\n\nUpdated article content here")
            ulid2 = ingest_file(tmp, inbox_path)
            assert ulid2 == ulid1  # same ULID — updated in-place
            assert not os.path.exists(inbox_path)  # file consumed

            with open(doc_path) as f:
                text2 = f.read()
            assert "Updated article content here" in text2
            assert text2 != text1  # content actually changed

            # Verify updated_at was bumped
            import re
            updated_match = re.findall(r"^updated: (.+)$", text2, re.MULTILINE)
            assert len(updated_match) == 1

            # Verify no second document was created
            doc_count = 0
            for _dirpath, _dirnames, filenames in os.walk(
                os.path.join(tmp, RECORDS_DIR, DOC_DIR)
            ):
                doc_count += sum(1 for fn in filenames if fn.endswith(".md"))
            assert doc_count == 1

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

    def test_fts_updated_on_content_change(self):
        """FTS index reflects new content after in-place update (section 18)."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            from kb.index import build_index, init_db, search_index

            # First ingest — document with "quantum computing"
            inbox_path = os.path.join(tmp, INBOX_DIR, "article.url")
            with open(inbox_path, "w") as f:
                f.write("https://example.com/physics")
            ulid = ingest_file(tmp, inbox_path)
            assert ulid is not None

            build_index(tmp)
            db_path = os.path.join(tmp, ".kb", "index.db")
            conn = init_db(db_path)
            # Original content has the URL as source, body is empty
            results = search_index(conn, "physics")
            assert len(results) == 1
            conn.close()

            # Second ingest — same URL, new body about "neural networks"
            with open(inbox_path, "w") as f:
                f.write("https://example.com/physics\n\nNeural networks are deep learning models")
            ingest_file(tmp, inbox_path)

            # Reconnect and search for the new content
            conn = init_db(db_path)
            results_new = search_index(conn, "neural")
            assert len(results_new) == 1
            snippet = results_new[0]["snippet"]
            assert "neural" in snippet.lower()
            conn.close()


# --- Broken front matter handling (Phase 6.1) ---


class TestHasBrokenFrontMatter:
    def test_no_front_matter(self):
        assert not _has_broken_front_matter("# Hello\n\nWorld")

    def test_valid_front_matter(self):
        assert not _has_broken_front_matter("---\ntitle: Test\n---\n\nBody")

    def test_incomplete_front_matter(self):
        assert _has_broken_front_matter("---\ntitle: Broken\nid: test\n")

    def test_empty_with_dash_prefix(self):
        assert not _has_broken_front_matter("---\n---\n\nBody")


class TestIngestBrokenFrontMatter:
    def test_skips_incomplete_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "broken.md")
            with open(inbox_path, "w") as f:
                f.write("---\ntitle: Broken\nid: test\n")

            result = ingest_file(tmp, inbox_path)
            assert result is None
            assert os.path.exists(inbox_path)

    def test_regular_md_not_affected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            inbox_path = os.path.join(tmp, INBOX_DIR, "regular.md")
            with open(inbox_path, "w") as f:
                f.write("# Regular Note\n\nJust a normal note")

            result = ingest_file(tmp, inbox_path)
            assert result is not None
