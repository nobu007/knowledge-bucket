"""Graph construction: concept extraction, normalization, df/idf, edges."""

import os
import sqlite3
from datetime import UTC, datetime

import yaml

from .core import CONFIG_DIR, DOC_DIR, RECORDS_DIR
from .index import _FM_RE, index_path, init_db


def init_graph_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS concepts (
            concept_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'concept',
            df INTEGER NOT NULL DEFAULT 0,
            is_stop INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS doc_concepts (
            doc_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'primary',
            weight REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY (doc_id, concept_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS edges (
            src_id TEXT NOT NULL,
            dst_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (src_id, dst_id, edge_type)
        ) WITHOUT ROWID;
    """)
    conn.commit()


def load_aliases(root: str) -> dict[str, str]:
    path = os.path.join(root, CONFIG_DIR, "aliases.yml")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("aliases", {}) if data else {}


def load_stop_concepts(root: str) -> set[str]:
    path = os.path.join(root, CONFIG_DIR, "stop_concepts.yml")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        data = yaml.safe_load(f)
    return set(data.get("stop_concepts", [])) if data else set()


def normalize_concept(raw: str, aliases: dict[str, str]) -> str:
    key = raw.strip().lower()
    return aliases.get(key, key)


def _parse_front_matter_yaml(text: str) -> tuple[dict, str]:
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, m.group(2)


def _extract_concepts(meta: dict) -> list[str]:
    raw = meta.get("concepts")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [c.strip() for c in raw.split(",") if c.strip()]
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append(item.strip())
            elif isinstance(item, dict):
                cid = item.get("id", "")
                if cid.startswith("concept:"):
                    cid = cid[len("concept:"):]
                if cid:
                    result.append(cid.strip())
        return result
    return []


def _read_doc_concepts(filepath: str) -> tuple[str, list[str]] | None:
    try:
        with open(filepath) as f:
            text = f.read()
    except OSError:
        return None
    meta, _body = _parse_front_matter_yaml(text)
    doc_id = meta.get("id")
    if not doc_id:
        return None
    concepts = _extract_concepts(meta)
    return str(doc_id), concepts


def build_graph(root: str) -> dict:
    """Build concept graph from all documents. Returns stats."""
    db_path = index_path(root)
    conn = init_db(db_path)
    init_graph_tables(conn)

    aliases = load_aliases(root)
    stop = load_stop_concepts(root)

    conn.execute("DELETE FROM doc_concepts")
    conn.execute("DELETE FROM edges")

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    docs_processed = 0
    concepts_seen: dict[str, str] = {}

    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            result = _read_doc_concepts(abs_path)
            if result is None:
                continue
            doc_id, raw_concepts = result
            docs_processed += 1

            for raw_c in raw_concepts:
                concept_id = normalize_concept(raw_c, aliases)
                if concept_id in stop:
                    continue
                concepts_seen[concept_id] = concept_id
                conn.execute(
                    "INSERT OR IGNORE INTO doc_concepts "
                    "(doc_id, concept_id, role, weight) VALUES (?, ?, 'primary', 1.0)",
                    (doc_id, concept_id),
                )

    now = datetime.now(UTC).isoformat()
    for cid, label in concepts_seen.items():
        conn.execute(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 0, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET label=excluded.label",
            (cid, label, now),
        )

    # Mark stop concepts in DB
    for sc in stop:
        conn.execute(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 1, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET is_stop=1",
            (sc, sc, now),
        )

    compute_df(conn)
    conn.close()

    return {"docs_processed": docs_processed, "concepts_found": len(concepts_seen)}


def compute_df(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE concepts SET df = COALESCE("
        "  (SELECT COUNT(*) FROM doc_concepts "
        "   WHERE doc_concepts.concept_id = concepts.concept_id), 0)"
    )
    conn.commit()


def get_active_graph_terms(conn: sqlite3.Connection, doc_id: str,
                           max_terms: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT c.concept_id, c.label, c.df, dc.weight "
        "FROM doc_concepts dc "
        "JOIN concepts c ON c.concept_id = dc.concept_id "
        "WHERE dc.doc_id = ? AND c.df >= 2 AND c.is_stop = 0 "
        "ORDER BY dc.weight DESC, c.df ASC "
        "LIMIT ?",
        (doc_id, max_terms),
    ).fetchall()
    return [
        {"concept_id": r[0], "label": r[1], "df": r[2], "weight": r[3]}
        for r in rows
    ]
