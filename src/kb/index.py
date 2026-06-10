"""SQLite FTS5 full-text search index for knowledge bucket documents."""

import os
import re
import sqlite3
import subprocess

from .core import DOC_DIR, RECORDS_DIR

INDEX_DIR = ".kb"
INDEX_FILENAME = "index.db"

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n*(.*)", re.DOTALL)


def index_path(root: str) -> str:
    return os.path.join(root, INDEX_DIR, INDEX_FILENAME)


def init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
            id UNINDEXED,
            title,
            source,
            source_type UNINDEXED,
            rel_path UNINDEXED,
            content
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM kv_meta WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO kv_meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def parse_front_matter(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def _read_doc(filepath: str) -> tuple[dict, str] | None:
    try:
        with open(filepath) as f:
            text = f.read()
    except OSError:
        return None
    meta, body = parse_front_matter(text)
    if "id" not in meta:
        return None
    return meta, body


def index_document(conn: sqlite3.Connection, doc_id: str, title: str,
                   source: str | None, source_type: str, rel_path: str,
                   content: str) -> None:
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, title, source or "", source_type, rel_path, content),
    )
    conn.commit()


def build_index(root: str) -> int:
    db_path = index_path(root)
    conn = init_db(db_path)
    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    count = 0
    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            result = _read_doc(abs_path)
            if result is None:
                continue
            meta, body = result
            rel = os.path.relpath(abs_path, root)
            index_document(
                conn,
                doc_id=meta["id"],
                title=meta.get("title", ""),
                source=meta.get("source"),
                source_type=meta.get("source_type", "web"),
                rel_path=rel,
                content=body,
            )
            count += 1
    conn.close()
    return count


def _cleanup_stale(conn: sqlite3.Connection, root: str) -> int:
    """Remove FTS entries whose files no longer exist on disk. Returns count removed."""
    rows = conn.execute("SELECT id, rel_path FROM docs").fetchall()
    removed = 0
    for doc_id, rel_path in rows:
        abs_path = os.path.join(root, rel_path)
        if not os.path.isfile(abs_path):
            conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
            removed += 1
    if removed:
        conn.commit()
    return removed


def sync_index(root: str) -> int:
    """Incrementally index using git-diff when possible, falling back to full walk.

    Per GOAL.md section 14: track ``last_indexed_commit`` in SQLite, use
    ``git diff --name-status`` for O(changes) instead of O(N) file walk.
    """
    db_path = index_path(root)
    conn = init_db(db_path)

    # Try git-diff-based sync
    diff_result = _git_diff_sync(conn, root)
    if diff_result is not None:
        conn.close()
        return diff_result

    # Fallback: full file walk
    _cleanup_stale(conn, root)
    existing = {r[0] for r in conn.execute("SELECT id FROM docs").fetchall()}
    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    added = 0
    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            result = _read_doc(abs_path)
            if result is None:
                continue
            meta, body = result
            if meta["id"] in existing:
                continue
            rel = os.path.relpath(abs_path, root)
            index_document(
                conn,
                doc_id=meta["id"],
                title=meta.get("title", ""),
                source=meta.get("source"),
                source_type=meta.get("source_type", "web"),
                rel_path=rel,
                content=body,
            )
            existing.add(meta["id"])
            added += 1

    # Record HEAD after full walk so next sync can be diff-based
    head = _git_head(root)
    if head:
        _set_meta(conn, "last_indexed_commit", head)

    conn.close()
    return added


def _git_head(root: str) -> str | None:
    """Get current git HEAD commit hash, or None if not a git repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True, check=True,
        )
        return r.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _git_diff_sync(conn: sqlite3.Connection, root: str) -> int | None:
    """Attempt git-diff-based incremental sync. Returns count or None to fall back."""
    last_commit = _get_meta(conn, "last_indexed_commit")
    head = _git_head(root)
    if not head or not last_commit:
        return None

    # If HEAD hasn't moved, nothing to do
    if head == last_commit:
        _cleanup_stale(conn, root)
        return 0

    # Check that last_commit still exists (could be lost after force-push)
    try:
        subprocess.run(
            ["git", "cat-file", "-t", last_commit],
            cwd=root, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return None

    # Get changed files under records/doc
    try:
        r = subprocess.run(
            ["git", "diff", "--name-status", last_commit, head, "--",
             os.path.join(RECORDS_DIR, DOC_DIR)],
            cwd=root, capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return None

    if not r.stdout.strip():
        # No changes in records/doc, just update HEAD pointer
        _set_meta(conn, "last_indexed_commit", head)
        _cleanup_stale(conn, root)
        return 0

    processed = 0
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, rel_path = parts
        if not rel_path.endswith(".md"):
            continue
        abs_path = os.path.join(root, rel_path)

        if status == "D":
            # File deleted — remove from FTS by rel_path lookup
            rows = conn.execute(
                "SELECT id FROM docs WHERE rel_path = ?", (rel_path,)
            ).fetchall()
            for (doc_id,) in rows:
                conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
            conn.commit()
        else:
            # Added (A) or Modified (M) — reindex
            if os.path.isfile(abs_path):
                result = _read_doc(abs_path)
                if result:
                    meta, body = result
                    # Delete old entry if exists (for Modified)
                    conn.execute(
                        "DELETE FROM docs WHERE id = ?", (meta["id"],)
                    )
                    conn.commit()
                    index_document(
                        conn,
                        doc_id=meta["id"],
                        title=meta.get("title", ""),
                        source=meta.get("source"),
                        source_type=meta.get("source_type", "web"),
                        rel_path=rel_path,
                        content=body,
                    )
                    processed += 1

    _cleanup_stale(conn, root)
    _set_meta(conn, "last_indexed_commit", head)
    return processed


def reindex_document(conn: sqlite3.Connection, doc_id: str, filepath: str, root: str) -> bool:
    """Re-index a single document in FTS (delete old + insert new).

    Used when a document is updated in-place (GOAL.md section 18) so that
    search results reflect the new content immediately.
    """
    result = _read_doc(filepath)
    if result is None:
        conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
        conn.commit()
        return False
    meta, body = result
    rel = os.path.relpath(filepath, root)
    conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    conn.execute(
        "INSERT INTO docs (id, title, source, source_type, rel_path, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, meta.get("title", ""), meta.get("source", ""),
         meta.get("source_type", "web"), rel, body),
    )
    conn.commit()
    return True


def search_index(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, source, source_type, rel_path, "
        "  snippet(docs, 5, '>>>', '<<<', '...', 40) AS snippet "
        "FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "source": r[2],
            "source_type": r[3], "rel_path": r[4], "snippet": r[5],
        }
        for r in rows
    ]
