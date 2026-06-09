"""Tests for deduplication: source_key, content_hash, sources table."""

import sqlite3

from kb.dedup import (
    check_duplicate,
    compute_content_hash,
    generate_source_key,
    init_sources_table,
    register_source,
)

# --- generate_source_key ---


class TestGenerateSourceKey:
    def test_web_url(self):
        key = generate_source_key("web", source_url="https://example.com/article")
        assert key == "url:https://example.com/article"

    def test_web_strips_utm(self):
        key = generate_source_key("web", source_url="https://example.com/a?utm_source=x&ref=foo")
        assert key == "url:https://example.com/a"

    def test_web_lowercase_host(self):
        key = generate_source_key("web", source_url="https://EXAMPLE.COM/Path")
        assert key == "url:https://example.com/Path"

    def test_web_strips_fragment(self):
        key = generate_source_key("web", source_url="https://example.com/a#section")
        assert key == "url:https://example.com/a"

    def test_paper_doi(self):
        key = generate_source_key("paper", source_url="https://doi.org/10.1234/test")
        assert key == "doi:10.1234/test"

    def test_paper_doi_prefix(self):
        key = generate_source_key("paper", source_url="doi:10.5678/example")
        assert key == "doi:10.5678/example"

    def test_paper_arxiv(self):
        key = generate_source_key("paper", source_url="https://arxiv.org/abs/2401.12345")
        assert key == "arxiv:2401.12345"

    def test_paper_arxiv_bare(self):
        key = generate_source_key("paper", source_url="arxiv:2301.01234v2")
        assert key == "arxiv:2301.01234v2"

    def test_paper_title_fallback(self):
        key = generate_source_key("paper", title="Attention Is All You Need")
        assert key.startswith("paper:")
        assert len(key) == len("paper:") + 16

    def test_repo_full_url(self):
        key = generate_source_key("repo", source_url="https://github.com/owner/repo")
        assert key == "repo:github.com/owner/repo"

    def test_repo_ssh_url(self):
        key = generate_source_key("git_repo", source_url="git@github.com:owner/repo.git")
        # urlparse handles this as path
        assert key.startswith("repo:")

    def test_repo_shorthand(self):
        key = generate_source_key("repo", source_url="owner/repo")
        assert key == "repo:github.com/owner/repo"

    def test_repo_strips_git_suffix(self):
        key = generate_source_key("repo", source_url="https://github.com/owner/repo.git")
        assert key == "repo:github.com/owner/repo"

    def test_memo(self):
        key = generate_source_key("memo", doc_ulid="01K2Z9P7Y8QWERTY1234567890")
        assert key == "memo:01K2Z9P7Y8QWERTY1234567890"

    def test_memo_no_ulid(self):
        key = generate_source_key("memo")
        assert key == "memo:unknown"

    def test_web_no_url_falls_to_memo(self):
        key = generate_source_key("web", doc_ulid="ABC123")
        assert key == "memo:ABC123"


# --- compute_content_hash ---


class TestComputeContentHash:
    def test_deterministic(self):
        h = compute_content_hash("hello world")
        assert h == compute_content_hash("hello world")

    def test_different_content(self):
        assert compute_content_hash("a") != compute_content_hash("b")

    def test_sha256_length(self):
        h = compute_content_hash("test")
        assert len(h) == 64

    def test_empty_string(self):
        h = compute_content_hash("")
        assert len(h) == 64


# --- sources table ---


class TestSourcesTable:
    def test_init_creates_table(self):
        conn = sqlite3.connect(":memory:")
        init_sources_table(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sources'"
        ).fetchall()
        assert len(rows) == 1
        conn.close()

    def test_init_idempotent(self):
        conn = sqlite3.connect(":memory:")
        init_sources_table(conn)
        init_sources_table(conn)  # should not raise
        conn.close()

    def test_check_duplicate_none(self):
        conn = sqlite3.connect(":memory:")
        init_sources_table(conn)
        result = check_duplicate(conn, "url:https://example.com")
        assert result is None
        conn.close()

    def test_register_and_check(self):
        conn = sqlite3.connect(":memory:")
        init_sources_table(conn)
        register_source(
            conn, "url:https://example.com", "https://example.com",
            "doc1", "hash1", "2026-01-01T00:00:00",
        )
        result = check_duplicate(conn, "url:https://example.com")
        assert result is not None
        assert result["source_key"] == "url:https://example.com"
        assert result["first_doc_id"] == "doc1"
        assert result["last_doc_id"] == "doc1"
        assert result["content_hash"] == "hash1"
        conn.close()

    def test_register_updates_last_doc(self):
        conn = sqlite3.connect(":memory:")
        init_sources_table(conn)
        register_source(
            conn, "url:https://example.com", "https://example.com",
            "doc1", "hash1", "2026-01-01T00:00:00",
        )
        register_source(
            conn, "url:https://example.com", "https://example.com",
            "doc2", "hash2", "2026-01-02T00:00:00",
        )
        result = check_duplicate(conn, "url:https://example.com")
        assert result["first_doc_id"] == "doc1"
        assert result["last_doc_id"] == "doc2"
        assert result["content_hash"] == "hash2"
        conn.close()


# --- integration: dedup in ingest ---


class TestIngestDedup:
    def test_duplicate_skipped(self, tmp_path):
        from kb.core import ensure_dirs
        from kb.ingest import ingest_file

        root = str(tmp_path)
        ensure_dirs(root)

        # Create inbox dir
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)

        # First file
        f1 = inbox / "test1.txt"
        f1.write_text("https://example.com/article\nBody content here\n")
        result1 = ingest_file(root, str(f1))
        assert result1 is not None

        # Second file with same URL and content — should be skipped
        f2 = inbox / "test2.txt"
        f2.write_text("https://example.com/article\nBody content here\n")
        result2 = ingest_file(root, str(f2))
        assert result2 is None  # skipped as duplicate

    def test_different_content_accepted(self, tmp_path):
        from kb.core import ensure_dirs
        from kb.ingest import ingest_file

        root = str(tmp_path)
        ensure_dirs(root)

        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)

        # First file
        f1 = inbox / "test1.txt"
        f1.write_text("https://example.com/article\nOriginal content\n")
        result1 = ingest_file(root, str(f1))
        assert result1 is not None

        # Second file with same URL but different content — accepted
        f2 = inbox / "test2.txt"
        f2.write_text("https://example.com/article\nUpdated content here\n")
        result2 = ingest_file(root, str(f2))
        assert result2 is not None

    def test_memo_no_dedup(self, tmp_path):
        from kb.core import ensure_dirs
        from kb.ingest import ingest_file

        root = str(tmp_path)
        ensure_dirs(root)

        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)

        # Two memo files — both accepted (different memo: ULIDs)
        f1 = inbox / "memo1.txt"
        f1.write_text("Just a quick note\n")
        result1 = ingest_file(root, str(f1))
        assert result1 is not None

        f2 = inbox / "memo2.txt"
        f2.write_text("Just a quick note\n")
        result2 = ingest_file(root, str(f2))
        assert result2 is not None  # memos get unique ULID keys
