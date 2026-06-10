"""Tests for index module: SQLite FTS5 indexing and search."""

import os
import sqlite3
import subprocess
import tempfile
from unittest.mock import patch

from kb.core import ensure_dirs
from kb.index import (
    _cleanup_stale,
    _get_meta,
    _retry_locked,
    _set_meta,
    build_index,
    index_document,
    index_path,
    init_db,
    parse_front_matter,
    reindex_document,
    repair_index,
    search_index,
    sync_index,
    verify_index,
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

    def test_no_duplicates_on_rebuild(self):
        """build_index clears FTS before re-indexing, so no duplicates."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "test.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")

            build_index(tmp)
            build_index(tmp)

            db = index_path(tmp)
            conn = init_db(db)
            rows = conn.execute(
                "SELECT count(*) FROM docs WHERE docs MATCH 'Body'"
            ).fetchone()
            conn.close()
            assert rows[0] == 1

    def test_records_head_in_git_repo(self):
        """build_index stores HEAD so subsequent sync_index can use git-diff."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            _git_commit(tmp, "initial")

            build_index(tmp)

            db = index_path(tmp)
            conn = init_db(db)
            stored = _get_meta(conn, "last_indexed_commit")
            conn.close()
            assert stored == _git_head(tmp)

    def test_sync_after_build_uses_git_diff(self):
        """After build_index records HEAD, sync_index uses git-diff (returns 0)."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            _git_commit(tmp, "c1")

            build_index(tmp)
            # sync_index should detect no change via git-diff
            assert sync_index(tmp) == 0


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


class TestCleanupStale:
    def test_removes_ghost_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            # Index two docs
            index_document(conn, "doc1", "Alive", None, "web",
                           os.path.join("records", "doc", "aa", "bb", "alive.md"),
                           "Still here")
            index_document(conn, "doc2", "Ghost", None, "web",
                           os.path.join("records", "doc", "cc", "dd", "ghost.md"),
                           "Deleted from disk")
            # Create only one file on disk
            alive_path = os.path.join(
                tmp, "records", "doc", "aa", "bb", "alive.md"
            )
            os.makedirs(os.path.dirname(alive_path), exist_ok=True)
            with open(alive_path, "w") as f:
                f.write("---\nid: doc1\ntitle: Alive\n---\n\nStill here\n")

            removed = _cleanup_stale(conn, tmp)
            assert removed == 1
            assert len(
                conn.execute(
                    "SELECT id FROM docs WHERE id = ?", ("doc2",)
                ).fetchall()
            ) == 0
            assert len(
                conn.execute(
                    "SELECT id FROM docs WHERE id = ?", ("doc1",)
                ).fetchall()
            ) == 1
            conn.close()

    def test_sync_index_removes_ghosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = index_path(tmp)
            conn = init_db(db)
            # Insert a ghost entry (no file on disk)
            index_document(
                conn, "ghost1", "Phantom", None, "web",
                os.path.join("records", "doc", "xx", "yy", "phantom.md"),
                "Does not exist",
            )
            conn.close()

            # sync_index should clean up the ghost
            added = sync_index(tmp)
            assert added == 0

            conn = init_db(db)
            assert len(
                conn.execute(
                    "SELECT id FROM docs WHERE id = ?", ("ghost1",)
                ).fetchall()
            ) == 0
            conn.close()


class TestKvMeta:
    def test_get_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            assert _get_meta(conn, "last_indexed_commit") is None
            _set_meta(conn, "last_indexed_commit", "abc123")
            assert _get_meta(conn, "last_indexed_commit") == "abc123"
            conn.close()

    def test_upsert(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "index.db")
            conn = init_db(db)
            _set_meta(conn, "k", "v1")
            _set_meta(conn, "k", "v2")
            assert _get_meta(conn, "k") == "v2"
            conn.close()


def _init_git_repo(path):
    """Create a minimal git repo in path for testing."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True, check=True,
    )


def _git_commit(path, msg="commit"):
    """Stage all and commit in the git repo at path."""
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", msg, "--allow-empty"],
        cwd=path, capture_output=True, check=True,
    )


def _git_head(path):
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path, capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


class TestGitDiffSync:
    def test_fallback_no_git(self):
        """Without git, sync_index still works via full file walk."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "test.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Hello\n---\n\nBody\n")

            added = sync_index(tmp)
            assert added == 1

    def test_stores_head_after_first_sync(self):
        """After a full walk in a git repo, last_indexed_commit is stored."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "test.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Hello\n---\n\nBody\n")
            _git_commit(tmp, "initial")

            sync_index(tmp)

            db = index_path(tmp)
            conn = init_db(db)
            stored = _get_meta(conn, "last_indexed_commit")
            conn.close()
            assert stored == _git_head(tmp)

    def test_incremental_sync_detects_new_file(self):
        """After adding a file and committing, sync picks it up via git diff."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)

            # First file
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: First\n---\n\nAlpha\n")
            _git_commit(tmp, "first")

            # Initial sync
            assert sync_index(tmp) == 1

            # Add second file
            with open(os.path.join(doc_dir, "b.md"), "w") as f:
                f.write("---\nid: d2\ntitle: Second\n---\n\nBeta\n")
            _git_commit(tmp, "second")

            # Incremental sync should find the new file
            added = sync_index(tmp)
            assert added == 1

            # Verify both docs are searchable
            db = index_path(tmp)
            conn = init_db(db)
            assert len(search_index(conn, "Alpha")) == 1
            assert len(search_index(conn, "Beta")) == 1
            conn.close()

    def test_incremental_sync_detects_modification(self):
        """Modified file content is re-indexed via git diff."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            doc_path = os.path.join(doc_dir, "a.md")
            with open(doc_path, "w") as f:
                f.write("---\nid: d1\ntitle: V1\n---\n\nOriginal content\n")
            _git_commit(tmp, "v1")

            sync_index(tmp)

            # Modify the file
            with open(doc_path, "w") as f:
                f.write("---\nid: d1\ntitle: V2\n---\n\nUpdated content\n")
            _git_commit(tmp, "v2")

            added = sync_index(tmp)
            assert added == 1  # re-indexed

            db = index_path(tmp)
            conn = init_db(db)
            assert len(search_index(conn, "Original")) == 0
            assert len(search_index(conn, "Updated")) == 1
            conn.close()

    def test_incremental_sync_detects_deletion(self):
        """Deleted file is removed from FTS index via git diff."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)

            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Keep\n---\n\nAlpha\n")
            with open(os.path.join(doc_dir, "b.md"), "w") as f:
                f.write("---\nid: d2\ntitle: Delete\n---\n\nBeta\n")
            _git_commit(tmp, "both")

            sync_index(tmp)

            # Delete one file
            os.remove(os.path.join(doc_dir, "b.md"))
            _git_commit(tmp, "remove b")

            sync_index(tmp)

            db = index_path(tmp)
            conn = init_db(db)
            assert len(search_index(conn, "Alpha")) == 1
            assert len(search_index(conn, "Beta")) == 0
            conn.close()

    def test_no_changes_returns_zero(self):
        """When HEAD hasn't moved, sync_index returns 0 quickly."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Hello\n---\n\nBody\n")
            _git_commit(tmp, "initial")

            sync_index(tmp)  # first sync
            assert sync_index(tmp) == 0  # no change

    def test_head_recorded_after_full_walk(self):
        """When no last_indexed_commit exists, full walk runs and HEAD is stored."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            _git_commit(tmp, "c1")

            added = sync_index(tmp)
            assert added == 1

            db = index_path(tmp)
            conn = init_db(db)
            head = _get_meta(conn, "last_indexed_commit")
            conn.close()
            assert head is not None

            # Second sync should use git diff path (returns 0)
            assert sync_index(tmp) == 0


class TestVerifyIndex:
    def test_consistent_index(self):
        """verify_index reports no issues when index matches disk."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            build_index(tmp)

            report = verify_index(tmp)
            assert report["ghost_entries"] == []
            assert report["missing_entries"] == []
            assert report["stale_head"] is False

    def test_detects_ghost_entries(self):
        """verify_index detects FTS entries whose files are missing."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = index_path(tmp)
            conn = init_db(db)
            index_document(conn, "ghost1", "Phantom", None, "web",
                           os.path.join("records", "doc", "xx", "yy", "phantom.md"),
                           "Does not exist")
            conn.close()

            report = verify_index(tmp)
            assert "ghost1" in report["ghost_entries"]
            assert report["missing_entries"] == []

    def test_detects_missing_entries(self):
        """verify_index detects files on disk not in FTS."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            # Create empty DB so verify doesn't bail early
            db = index_path(tmp)
            conn = init_db(db)
            conn.close()

            report = verify_index(tmp)
            assert report["ghost_entries"] == []
            assert len(report["missing_entries"]) == 1
            assert report["missing_entries"][0][0] == "d1"

    def test_no_db_returns_error(self):
        """verify_index returns error when no index database exists."""
        with tempfile.TemporaryDirectory() as tmp:
            report = verify_index(tmp)
            assert "error" in report

    def test_detects_stale_head(self):
        """verify_index detects stale last_indexed_commit."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            build_index(tmp)
            # Set a fake HEAD that doesn't exist
            db = index_path(tmp)
            conn = init_db(db)
            _set_meta(conn, "last_indexed_commit", "deadbeef" * 5)
            conn.close()

            report = verify_index(tmp)
            assert report["stale_head"] is True


class TestRepairIndex:
    def test_removes_ghosts(self):
        """repair_index removes FTS entries for missing files."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            db = index_path(tmp)
            conn = init_db(db)
            index_document(conn, "ghost1", "Phantom", None, "web",
                           os.path.join("records", "doc", "xx", "yy", "phantom.md"),
                           "Does not exist")
            conn.close()

            report = repair_index(tmp)
            assert report["ghosts_removed"] == 1
            assert report["missing_indexed"] == 0

    def test_indexes_missing_files(self):
        """repair_index adds files that are on disk but not in FTS."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")

            report = repair_index(tmp)
            assert report["ghosts_removed"] == 0
            assert report["missing_indexed"] == 1

            # Verify it's now searchable
            conn = init_db(index_path(tmp))
            results = search_index(conn, "Body")
            conn.close()
            assert len(results) == 1

    def test_fixes_stale_head(self):
        """repair_index clears stale last_indexed_commit."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            build_index(tmp)
            db = index_path(tmp)
            conn = init_db(db)
            _set_meta(conn, "last_indexed_commit", "deadbeef" * 5)
            conn.close()

            report = repair_index(tmp)
            assert report["stale_head_fixed"] is True

    def test_no_issues_to_repair(self):
        """repair_index reports zeros when index is healthy."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            build_index(tmp)

            report = repair_index(tmp)
            assert report["ghosts_removed"] == 0
            assert report["missing_indexed"] == 0
            assert report["stale_head_fixed"] is False

    def test_verify_after_repair_passes(self):
        """After repair, verify_index reports no issues."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            # Create a file
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")
            # Add a ghost
            db = index_path(tmp)
            conn = init_db(db)
            index_document(conn, "ghost1", "Phantom", None, "web",
                           os.path.join("records", "doc", "xx", "yy", "phantom.md"),
                           "Ghost")
            conn.close()

            repair_index(tmp)
            report = verify_index(tmp)
            assert report["ghost_entries"] == []
            assert report["missing_entries"] == []
            assert report["stale_head"] is False


class TestBatchPerformance:
    """Phase 6.2: verify batch INSERT optimization works correctly with bulk data."""

    def _create_bulk_docs(self, root, n):
        """Create n documents under root/records/doc/."""
        doc_dir = os.path.join(root, "records", "doc", "ab", "cd")
        os.makedirs(doc_dir, exist_ok=True)
        for i in range(n):
            with open(os.path.join(doc_dir, f"doc{i:05d}.md"), "w") as f:
                f.write(f"---\nid: d{i:05d}\ntitle: Doc {i}\n---\n\nContent for doc {i}\n")

    def test_build_index_bulk(self):
        """build_index handles 1000 docs correctly with batch optimization."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            self._create_bulk_docs(tmp, 100)

            count = build_index(tmp)
            assert count == 100

            db = index_path(tmp)
            conn = init_db(db)
            rows = conn.execute("SELECT COUNT(*) FROM docs").fetchone()
            conn.close()
            assert rows[0] == 100

    def test_sync_index_bulk_fallback(self):
        """sync_index fallback path handles bulk docs."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            self._create_bulk_docs(tmp, 50)

            added = sync_index(tmp)
            assert added == 50

            db = index_path(tmp)
            conn = init_db(db)
            rows = conn.execute("SELECT COUNT(*) FROM docs").fetchone()
            conn.close()
            assert rows[0] == 50

    def test_repair_index_bulk_missing(self):
        """repair_index handles bulk missing files."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            self._create_bulk_docs(tmp, 50)

            report = repair_index(tmp)
            assert report["missing_indexed"] == 50

            db = index_path(tmp)
            conn = init_db(db)
            rows = conn.execute("SELECT COUNT(*) FROM docs").fetchone()
            conn.close()
            assert rows[0] == 50

    def test_build_index_no_duplicates_bulk(self):
        """build_index still prevents duplicates on rebuild with bulk data."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            self._create_bulk_docs(tmp, 50)

            build_index(tmp)
            build_index(tmp)

            db = index_path(tmp)
            conn = init_db(db)
            rows = conn.execute("SELECT COUNT(*) FROM docs").fetchone()
            conn.close()
            assert rows[0] == 50

    def test_search_after_bulk_index(self):
        """FTS search works correctly after bulk index."""
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            for i in range(20):
                with open(os.path.join(doc_dir, f"doc{i:05d}.md"), "w") as f:
                    body = f"Content about topic{i % 5}\n"
                    f.write(f"---\nid: d{i:05d}\ntitle: Doc {i}\n---\n\n{body}")

            build_index(tmp)
            db = index_path(tmp)
            conn = init_db(db)
            results = search_index(conn, "topic0")
            assert len(results) == 4  # doc0, doc5, doc10, doc15
            conn.close()


# --- SQLite lock retry (Phase 6.1) ---


class TestRetryLocked:
    """Test _retry_locked decorator behavior on database locked errors."""

    def test_succeeds_immediately(self):
        call_count = 0

        @_retry_locked
        def ok_fn():
            nonlocal call_count
            call_count += 1
            return "success"

        assert ok_fn() == "success"
        assert call_count == 1

    def test_retries_on_locked_then_succeeds(self):
        call_count = 0

        @_retry_locked
        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "recovered"

        assert flaky_fn() == "recovered"
        assert call_count == 3

    def test_raises_after_max_retries(self):
        @_retry_locked
        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        try:
            always_locked()
        except sqlite3.OperationalError as e:
            assert "locked" in str(e)
        else:
            raise AssertionError("Expected OperationalError")

    def test_does_not_retry_non_locked_errors(self):
        call_count = 0

        @_retry_locked
        def other_error():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("no such table: docs")

        try:
            other_error()
        except sqlite3.OperationalError as e:
            assert "no such table" in str(e)
        assert call_count == 1


class TestBuildIndexRetry:
    """Verify build_index retries on database locked."""

    @patch("kb.index.init_db")
    def test_build_index_retries_on_locked(self, mock_init_db):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc", "ab", "cd")
            os.makedirs(doc_dir, exist_ok=True)
            with open(os.path.join(doc_dir, "a.md"), "w") as f:
                f.write("---\nid: d1\ntitle: Test\n---\n\nBody\n")

            # First call raises locked, second succeeds
            real_conn = init_db(index_path(tmp))
            mock_init_db.side_effect = [
                sqlite3.OperationalError("database is locked"),
                real_conn,
            ]
            # build_index calls init_db once; the decorator retries the whole function
            count = build_index(tmp)
            assert count == 1
