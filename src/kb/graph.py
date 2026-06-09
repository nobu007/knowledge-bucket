"""Graph construction: concept extraction, normalization, df/idf, edges."""

import math
import os
import sqlite3
from datetime import UTC, datetime

import yaml

from .core import CONFIG_DIR, DOC_DIR, RECORDS_DIR
from .dedup import init_sources_table
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
        CREATE TABLE IF NOT EXISTS doc_stats (
            doc_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL DEFAULT 'web',
            has_source INTEGER NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        );
    """)
    init_sources_table(conn)
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


def load_user_interests(root: str) -> set[str]:
    path = os.path.join(root, CONFIG_DIR, "kb.yml")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data:
        return set()
    interests = data.get("user_interests", [])
    return set(interests) if interests else set()


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


def _read_doc_info(filepath: str) -> dict | None:
    try:
        with open(filepath) as f:
            text = f.read()
    except OSError:
        return None
    meta, _body = _parse_front_matter_yaml(text)
    doc_id = meta.get("id")
    if not doc_id:
        return None
    return {
        "doc_id": str(doc_id),
        "concepts": _extract_concepts(meta),
        "source_type": str(meta.get("source_type", "web")),
        "has_source": bool(meta.get("source")),
    }


def build_graph(root: str) -> dict:
    """Build concept graph from all documents. Returns stats."""
    db_path = index_path(root)
    conn = init_db(db_path)
    init_graph_tables(conn)

    aliases = load_aliases(root)
    stop = load_stop_concepts(root)

    conn.execute("DELETE FROM doc_concepts")
    conn.execute("DELETE FROM edges")
    conn.execute("DELETE FROM doc_stats")

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    docs_processed = 0
    concepts_seen: dict[str, str] = {}
    now = datetime.now(UTC).isoformat()

    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            info = _read_doc_info(abs_path)
            if info is None:
                continue
            docs_processed += 1
            doc_id = info["doc_id"]

            conn.execute(
                "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
                "VALUES (?, ?, ?, 0.0, ?)",
                (doc_id, info["source_type"], int(info["has_source"]), now),
            )

            for raw_c in info["concepts"]:
                concept_id = normalize_concept(raw_c, aliases)
                if concept_id in stop:
                    continue
                concepts_seen[concept_id] = concept_id
                conn.execute(
                    "INSERT OR IGNORE INTO doc_concepts "
                    "(doc_id, concept_id, role, weight) VALUES (?, ?, 'primary', 1.0)",
                    (doc_id, concept_id),
                )

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
    scored = compute_importance(conn)
    conn.close()

    return {
        "docs_processed": docs_processed,
        "concepts_found": len(concepts_seen),
        "importance_scored": scored,
    }


def compute_hub_threshold(conn: sqlite3.Connection) -> int:
    """Compute dynamic hub threshold per GOAL.md section 10.

    hub_threshold = min(5000, max(50, floor(0.002 * N)))
    Concepts with df > threshold are too common for doc-doc edges.
    """
    n = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    return min(5000, max(50, int(0.002 * n)))


def score_graph_term(
    concept_id: str,
    label: str,
    df: int,
    weight: float,
    total_docs: int,
    hub_threshold: int,
    user_interests: set[str] | None = None,
) -> float:
    """Multi-factor scoring for active graph term selection (GOAL.md section 11).

    score = 0.40 * AI weight
          + 0.25 * normalized IDF
          + 0.15 * technical term boost
          + 0.10 * compound term boost
          + 0.10 * user interest match
          - generic penalty (df/N)
          - hub penalty ((df/threshold)^2)
    """
    ai_score = min(1.0, max(0.0, weight))

    if total_docs > 0:
        idf = math.log(1 + total_docs / (df + 1))
        max_idf = math.log(1 + total_docs)
        idf_norm = idf / max_idf if max_idf > 0 else 0.0
    else:
        idf_norm = 0.0

    tech_boost = 0.0
    if "-" in concept_id:
        tech_boost += 0.5
    if any(c.isupper() for c in label):
        tech_boost += 0.5
    tech_boost = min(1.0, tech_boost)

    segments = concept_id.split("-")
    compound_boost = min(1.0, len(segments) / 3.0)

    interest_score = 1.0 if user_interests and concept_id in user_interests else 0.0

    generic_penalty = (df / total_docs) if total_docs > 0 else 0.0
    hub_penalty = (df / hub_threshold) ** 2 if hub_threshold > 0 else 0.0

    score = (
        0.40 * ai_score
        + 0.25 * idf_norm
        + 0.15 * tech_boost
        + 0.10 * compound_boost
        + 0.10 * interest_score
        - generic_penalty
        - hub_penalty
    )
    return max(0.0, score)


def compute_df(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE concepts SET df = COALESCE("
        "  (SELECT COUNT(*) FROM doc_concepts "
        "   WHERE doc_concepts.concept_id = concepts.concept_id), 0)"
    )
    conn.commit()


def get_active_graph_terms(conn: sqlite3.Connection, doc_id: str,
                           max_terms: int = 5,
                           hub_threshold: int | None = None,
                           user_interests: set[str] | None = None) -> list[dict]:
    if hub_threshold is None:
        hub_threshold = compute_hub_threshold(conn)
    total_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    rows = conn.execute(
        "SELECT c.concept_id, c.label, c.df, dc.weight "
        "FROM doc_concepts dc "
        "JOIN concepts c ON c.concept_id = dc.concept_id "
        "WHERE dc.doc_id = ? AND c.df >= 2 AND c.is_stop = 0 "
        "  AND c.df <= ? ",
        (doc_id, hub_threshold),
    ).fetchall()
    scored = []
    for concept_id, label, df, weight in rows:
        s = score_graph_term(
            concept_id, label, df, weight,
            total_docs, hub_threshold, user_interests,
        )
        scored.append({
            "concept_id": concept_id,
            "label": label,
            "df": df,
            "weight": weight,
            "score": s,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_terms]


_SOURCE_TYPE_WEIGHTS: dict[str, float] = {
    "paper": 1.0,
    "pdf": 0.8,
    "git_repo": 0.7,
    "repo": 0.7,
    "web": 0.4,
    "video": 0.3,
    "memo": 0.0,
}


def estimate_importance(
    n_concepts: int,
    avg_inv_df: float,
    source_type: str = "web",
    has_source: bool = False,
) -> float:
    """Heuristic importance score in [0.0, 1.0].

    Based on concept count, concept rarity, source type weight,
    and whether the document references an external source.
    """
    concept_score = min(1.0, n_concepts / 3.0)
    rarity_score = min(1.0, avg_inv_df * 2.0)
    type_score = _SOURCE_TYPE_WEIGHTS.get(source_type, 0.0)
    source_score = 1.0 if has_source else 0.0
    raw = (0.40 * concept_score + 0.30 * rarity_score
           + 0.15 * type_score + 0.15 * source_score)
    return round(min(1.0, max(0.0, raw)), 2)


def compute_importance(conn: sqlite3.Connection) -> int:
    """Compute importance for all documents in doc_stats that have concepts.

    Returns the number of documents scored.
    """
    now = datetime.now(UTC).isoformat()
    rows = conn.execute(
        "SELECT dc.doc_id, "
        "  COUNT(DISTINCT dc.concept_id) as n_concepts, "
        "  AVG(CASE WHEN c.df > 0 THEN 1.0 / c.df ELSE 1.0 END) as avg_inv_df "
        "FROM doc_concepts dc "
        "JOIN concepts c ON c.concept_id = dc.concept_id "
        "WHERE c.is_stop = 0 "
        "GROUP BY dc.doc_id"
    ).fetchall()

    scored = 0
    for doc_id, n_concepts, avg_inv_df in rows:
        stat = conn.execute(
            "SELECT source_type, has_source FROM doc_stats WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        source_type = stat[0] if stat else "web"
        has_source = bool(stat[1]) if stat else False

        imp = estimate_importance(
            n_concepts, avg_inv_df or 0.0, source_type, has_source,
        )
        conn.execute(
            "UPDATE doc_stats SET importance = ?, updated_at = ? "
            "WHERE doc_id = ?",
            (imp, now, doc_id),
        )
        scored += 1

    conn.commit()
    return scored
