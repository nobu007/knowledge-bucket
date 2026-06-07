"""Tests for sync module: incremental indexing and full sync pipeline."""

import os
import subprocess
import tempfile

from kb.core import ensure_dirs
from kb.index import build_index, index_path, search_index, sync_index
from kb.sync import _git, sync


def _write_doc(doc_dir: str, ulid: str, title: str, body: str) -> str:
    """Helper: write a minimal record doc and return its path."""
    import hashlib

    h = hashlib.sha256(ulid.encode()).hexdigest()
    shard = os.path.join(doc_dir, h[0:2], h[2:4])
    os.makedirs(shard, exist_ok=True)
    path = os.path.join(shard, f"{ulid}.md")
    with open(path, "w") as f:
        f.write(f"---\nid: {ulid}\ntitle: {title}\nsource_type: web\n---\n\n{body}\n")
    return path


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init", path], check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "Test"],
                   check=True, capture_output=True)


class TestSyncIndex:
    def test_adds_new_docs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc")

            _write_doc(doc_dir, "doc001", "First", "alpha beta")
            build_index(tmp)

            _write_doc(doc_dir, "doc002", "Second", "gamma delta")
            added = sync_index(tmp)
            assert added == 1

    def test_no_new_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc")

            _write_doc(doc_dir, "doc001", "First", "alpha beta")
            build_index(tmp)

            added = sync_index(tmp)
            assert added == 0

    def test_sync_then_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc")

            _write_doc(doc_dir, "doc001", "First", "alpha beta")
            build_index(tmp)
            _write_doc(doc_dir, "doc002", "Graph RAG", "graph retrieval augmented")
            sync_index(tmp)

            db = index_path(tmp)
            import sqlite3
            conn = sqlite3.connect(db)
            results = search_index(conn, "graph retrieval")
            assert len(results) == 1
            assert results[0]["id"] == "doc002"
            conn.close()

    def test_creates_db_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            doc_dir = os.path.join(tmp, "records", "doc")

            _write_doc(doc_dir, "doc001", "Hello", "world")

            assert not os.path.exists(index_path(tmp))
            added = sync_index(tmp)
            assert added == 1
            assert os.path.exists(index_path(tmp))


class TestSync:
    def test_sync_with_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)

            doc_dir = os.path.join(tmp, "records", "doc")
            _write_doc(doc_dir, "doc001", "Test Doc", "some content")

            report = sync(tmp)
            assert report["ingested"] == 0
            assert report["indexed"] >= 1
            assert report["committed"] is True

    def test_sync_no_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)

            # Make an initial commit so git is happy
            subprocess.run(["git", "-C", tmp, "add", "."], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", tmp, "commit", "-m", "init", "--allow-empty"],
                check=True, capture_output=True,
            )

            report = sync(tmp)
            assert report["committed"] is False

    def test_sync_ingests_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)

            inbox_dir = os.path.join(tmp, "inbox")
            with open(os.path.join(inbox_dir, "test-note.txt"), "w") as f:
                f.write("This is a test memo about machine learning\n")

            report = sync(tmp)
            assert report["ingested"] == 1
            assert report["indexed"] >= 1
            assert report["committed"] is True
            assert not os.path.exists(os.path.join(inbox_dir, "test-note.txt"))

    def test_sync_with_custom_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            ensure_dirs(tmp)

            doc_dir = os.path.join(tmp, "records", "doc")
            _write_doc(doc_dir, "doc001", "Doc", "content")

            report = sync(tmp, message="kb: ingest 1 item")
            assert report["committed"] is True

            log = subprocess.run(["git", "-C", tmp, "log", "--oneline", "-1"],
                                 capture_output=True, text=True, check=True)
            assert "kb: ingest 1 item" in log.stdout


class TestGitHelper:
    def test_git_no_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _git("status", cwd=tmp, check=False)
            assert r.returncode != 0
