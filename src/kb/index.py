"""SQLite FTS5 full-text search index for knowledge bucket documents."""

import os
import re
import sqlite3

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
    conn.commit()
    return conn


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


def sync_index(root: str) -> int:
    """Incrementally index new documents (skip already-indexed IDs)."""
    db_path = index_path(root)
    conn = init_db(db_path)
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
    conn.close()
    return added


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
