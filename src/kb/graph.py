"""Graph construction: concept extraction, normalization, df/idf, edges."""

import math
import os
import sqlite3
import subprocess
from datetime import UTC, datetime

import yaml

from .core import CONFIG_DIR, DOC_DIR, RECORDS_DIR
from .dedup import init_sources_table
from .index import _FM_RE, _get_meta, _set_meta, index_path, init_db


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
        -- Secondary indexes: the PK on doc_concepts is (doc_id, concept_id), so a
        -- lookup BY concept_id (the dominant access path: edge building, df recomputation,
        -- active-graph-term selection, co-occurrence) is not served by the PK and would
        -- otherwise full-scan. These convert scans into seeks at 10k+ docs.
        CREATE INDEX IF NOT EXISTS idx_doc_concepts_concept ON doc_concepts(concept_id);
        CREATE INDEX IF NOT EXISTS idx_edges_dst_type ON edges(dst_id, edge_type);
        CREATE INDEX IF NOT EXISTS idx_edges_src_type ON edges(src_id, edge_type);
        CREATE INDEX IF NOT EXISTS idx_concepts_df ON concepts(df);
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


def load_taxonomy(root: str) -> dict[str, dict]:
    """Load virtual collections from config/taxonomy.yml (GOAL.md section 19).

    Returns dict mapping collection name -> {label, include_concepts, include_types}.
    """
    path = os.path.join(root, CONFIG_DIR, "taxonomy.yml")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data:
        return {}
    return data.get("virtual_collections", {}) or {}


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
    if isinstance(raw, dict):
        result = []
        for key in ("primary", "candidates"):
            items = raw.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    result.append(item.strip())
                elif isinstance(item, dict):
                    cid = item.get("id", "")
                    if cid.startswith("concept:"):
                        cid = cid[len("concept:"):]
                    if cid:
                        result.append(cid.strip())
        return result
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


def _extract_entities(meta: dict) -> list[dict]:
    """Extract entity list from front matter concepts.entities (GOAL.md section 6).

    Returns list of dicts with 'entity_id' and 'label' keys.
    """
    raw = meta.get("concepts")
    if not isinstance(raw, dict):
        return []
    entities_raw = raw.get("entities", [])
    if not isinstance(entities_raw, list):
        return []
    result = []
    for item in entities_raw:
        if isinstance(item, dict):
            eid = item.get("id", "")
            label = item.get("label", eid)
            if eid:
                result.append({"entity_id": eid, "label": label})
        elif isinstance(item, str):
            result.append({"entity_id": item, "label": item})
    return result


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
        "entities": _extract_entities(meta),
        "source_type": str(meta.get("source_type", "web")),
        "has_source": bool(meta.get("source")),
        "source_key": str(meta.get("source_key", "")) if meta.get("source_key") else "",
    }


def _extract_doc_rows(abs_path, aliases, stop, now):
    """Extract per-doc rows from one Markdown file.

    Returns (info, doc_stats_row, doc_concept_rows, entity_edge_rows,
    source_edge_rows, concepts_seen, entities_seen) or None if unparseable.
    Shared by the full and incremental build paths.
    """
    info = _read_doc_info(abs_path)
    if info is None:
        return None
    doc_id = info["doc_id"]
    doc_concepts = []
    concepts_seen = {}
    entities_seen = {}
    entity_edges = []
    for raw_c in info["concepts"]:
        concept_id = normalize_concept(raw_c, aliases)
        if concept_id in stop:
            continue
        concepts_seen[concept_id] = concept_id
        doc_concepts.append((doc_id, concept_id, "primary", 1.0))
    for ent in info["entities"]:
        eid = ent["entity_id"]
        entities_seen[eid] = ent["label"]
        entity_edges.append((doc_id, eid, "entity", 1.0, now))
    src_key = info.get("source_key", "")
    source_edges = [(doc_id, src_key, "source", 1.0, now)] if src_key else []
    doc_stats = (doc_id, info["source_type"], int(info["has_source"]), 0.0, now)
    return info, doc_stats, doc_concepts, entity_edges, source_edges, concepts_seen, entities_seen


def _apply_doc_rows(conn, doc_stats, doc_concepts, entity_edges, source_edges,
                    concepts_seen, entities_seen, now):
    """Upsert one doc's extracted rows (incremental path)."""
    conn.execute(
        "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(doc_id) DO UPDATE SET source_type=excluded.source_type, "
        "has_source=excluded.has_source, updated_at=excluded.updated_at",
        doc_stats,
    )
    if doc_concepts:
        conn.executemany(
            "INSERT OR IGNORE INTO doc_concepts "
            "(doc_id, concept_id, role, weight) VALUES (?, ?, ?, ?)",
            doc_concepts,
        )
    if entity_edges:
        conn.executemany(
            "INSERT OR IGNORE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) VALUES (?, ?, ?, ?, ?)",
            entity_edges,
        )
    if source_edges:
        conn.executemany(
            "INSERT OR IGNORE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) VALUES (?, ?, ?, ?, ?)",
            source_edges,
        )
    if concepts_seen:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 0, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET label=excluded.label",
            [(cid, label, now) for cid, label in concepts_seen.items()],
        )
    if entities_seen:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'entity', 0, 0, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET label=excluded.label",
            [(eid, label, now) for eid, label in entities_seen.items()],
        )


def _delete_doc_rows(conn, doc_id):
    """Remove all per-doc graph rows (incremental path: changed/deleted docs)."""
    conn.execute("DELETE FROM doc_concepts WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM doc_stats WHERE doc_id = ?", (doc_id,))
    conn.execute(
        "DELETE FROM edges WHERE src_id = ? OR dst_id = ?", (doc_id, doc_id),
    )


def build_graph(root: str, full: bool = False) -> dict:
    """Build concept graph from documents. Returns stats.

    By default incremental: uses ``git diff`` against the last build commit to
    process only changed documents (mirrors sync_index). Falls back to a full
    rebuild when ``full=True``, when no previous commit is recorded, or when git
    is unavailable. df, importance, and global edges are recomputed every build
    (cheap once the per-doc rows are in place and indexes exist).
    """
    from .index import _git_head

    db_path = index_path(root)
    conn = init_db(db_path)
    init_graph_tables(conn)

    aliases = load_aliases(root)
    stop = load_stop_concepts(root)
    now = datetime.now(UTC).isoformat()

    # Try incremental unless full rebuild requested.
    changed = None if full else _git_changed_docs(conn, root)
    if changed is None:
        return _build_graph_full(conn, root, aliases, stop, now)
    return _build_graph_incremental(conn, root, aliases, stop, now, changed)


def _git_changed_docs(conn, root):
    """Return (added_or_modified_paths, deleted_doc_ids) or None to fall back.

    Falls back to None (→ full rebuild) when git is missing, no previous build
    commit is recorded, or the recorded commit no longer exists.
    """
    from .index import _git_head

    last = _get_meta(conn, "last_graph_build_commit")
    head = _git_head(root)
    if not head or not last or head == last:
        return None if not head or not last else ([], [])

    try:
        subprocess.run(
            ["git", "cat-file", "-t", last],
            cwd=root, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    try:
        r = subprocess.run(
            ["git", "diff", "--name-status", last, head, "--",
             os.path.join(RECORDS_DIR, DOC_DIR)],
            cwd=root, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    changed_paths, deleted_ids = [], []
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, rel_path = parts
        if not rel_path.endswith(".md"):
            continue
        if status.startswith("D"):
            deleted_ids.append(os.path.splitext(os.path.basename(rel_path))[0])
        else:
            changed_paths.append(os.path.join(root, rel_path))
    return changed_paths, deleted_ids


def _build_graph_incremental(conn, root, aliases, stop, now, changed):
    """Rebuild only changed docs' per-doc rows, then recompute globals."""
    from .index import _git_head

    changed_paths, deleted_ids = changed
    docs_processed = 0
    all_concepts: dict[str, str] = {}
    all_entities: dict[str, str] = {}

    # Remove deleted docs entirely
    for doc_id in deleted_ids:
        _delete_doc_rows(conn, doc_id)

    # Re-extract changed/added docs: drop old rows first, then insert fresh
    for abs_path in changed_paths:
        if not os.path.isfile(abs_path):
            continue
        extracted = _extract_doc_rows(abs_path, aliases, stop, now)
        if extracted is None:
            continue
        info, doc_stats, doc_concepts, entity_edges, source_edges, c_seen, e_seen = extracted
        _delete_doc_rows(conn, info["doc_id"])
        _apply_doc_rows(conn, doc_stats, doc_concepts, entity_edges, source_edges,
                        c_seen, e_seen, now)
        all_concepts.update(c_seen)
        all_entities.update(e_seen)
        docs_processed += 1

    if stop:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 1, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET is_stop=1",
            [(sc, sc, now) for sc in stop],
        )

    conn.commit()
    _set_meta(conn, "last_graph_build_commit", _git_head(root))
    compute_df(conn)
    scored = compute_importance(conn)
    conn.close()
    return {
        "docs_processed": docs_processed,
        "docs_deleted": len(deleted_ids),
        "concepts_found": len(all_concepts),
        "entities_found": len(all_entities),
        "importance_scored": scored,
        "incremental": True,
    }


def _build_graph_full(conn, root, aliases, stop, now):
    """Full rebuild: wipe and re-extract every document."""
    from .index import _git_head

    conn.execute("DELETE FROM doc_concepts")
    conn.execute("DELETE FROM edges")
    conn.execute("DELETE FROM doc_stats")

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    docs_processed = 0
    concepts_seen: dict[str, str] = {}
    entities_seen: dict[str, str] = {}  # entity_id -> label
    entity_edges = 0
    source_edges = 0

    doc_stats_rows = []
    doc_concept_rows = []
    entity_edge_rows = []
    source_edge_rows = []

    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            extracted = _extract_doc_rows(abs_path, aliases, stop, now)
            if extracted is None:
                continue
            info, doc_stats, doc_concepts, entity_e, source_e, c_seen, e_seen = extracted
            docs_processed += 1
            doc_stats_rows.append(doc_stats)
            doc_concept_rows.extend(doc_concepts)
            entity_edge_rows.extend(entity_e)
            source_edge_rows.extend(source_e)
            entity_edges += len(entity_e)
            source_edges += len(source_e)
            concepts_seen.update(c_seen)
            entities_seen.update(e_seen)

    # Batch insert all collected rows
    if doc_stats_rows:
        conn.executemany(
            "INSERT INTO doc_stats (doc_id, source_type, has_source, importance, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            doc_stats_rows,
        )
    if doc_concept_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO doc_concepts "
            "(doc_id, concept_id, role, weight) VALUES (?, ?, ?, ?)",
            doc_concept_rows,
        )
    if entity_edge_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            entity_edge_rows,
        )
    if source_edge_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO edges "
            "(src_id, dst_id, edge_type, weight, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            source_edge_rows,
        )

    # Insert concepts in batch
    concept_rows = [(cid, label, now) for cid, label in concepts_seen.items()]
    if concept_rows:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 0, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET label=excluded.label",
            concept_rows,
        )

    # Insert entities into concepts table with kind='entity'
    entity_rows = [(eid, label, now) for eid, label in entities_seen.items()]
    if entity_rows:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'entity', 0, 0, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET label=excluded.label",
            entity_rows,
        )

    # Mark stop concepts in DB
    if stop:
        conn.executemany(
            "INSERT INTO concepts (concept_id, label, kind, df, is_stop, created_at) "
            "VALUES (?, ?, 'concept', 0, 1, ?) "
            "ON CONFLICT(concept_id) DO UPDATE SET is_stop=1",
            [(sc, sc, now) for sc in stop],
        )

    conn.commit()
    head = _git_head(root)
    if head:
        _set_meta(conn, "last_graph_build_commit", head)
    compute_df(conn)
    scored = compute_importance(conn)
    conn.close()

    return {
        "docs_processed": docs_processed,
        "concepts_found": len(concepts_seen),
        "entities_found": len(entities_seen),
        "entity_edges": entity_edges,
        "source_edges": source_edges,
        "importance_scored": scored,
        "incremental": False,
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

    Single aggregation query joined to doc_stats (no per-doc SELECT), then one
    bulk UPDATE via executemany. Returns the number of documents scored.
    """
    now = datetime.now(UTC).isoformat()
    rows = conn.execute(
        "SELECT dc.doc_id, "
        "  COUNT(DISTINCT dc.concept_id) as n_concepts, "
        "  AVG(CASE WHEN c.df > 0 THEN 1.0 / c.df ELSE 1.0 END) as avg_inv_df, "
        "  ds.source_type, ds.has_source "
        "FROM doc_concepts dc "
        "JOIN concepts c ON c.concept_id = dc.concept_id "
        "JOIN doc_stats ds ON ds.doc_id = dc.doc_id "
        "WHERE c.is_stop = 0 "
        "GROUP BY dc.doc_id"
    ).fetchall()

    updates = []
    for doc_id, n_concepts, avg_inv_df, source_type, has_source in rows:
        imp = estimate_importance(
            n_concepts, avg_inv_df or 0.0, source_type or "web", bool(has_source),
        )
        updates.append((imp, now, doc_id))

    if updates:
        conn.executemany(
            "UPDATE doc_stats SET importance = ?, updated_at = ? "
            "WHERE doc_id = ?",
            updates,
        )
        conn.commit()
    return len(updates)


def resolve_virtual_collection(conn: sqlite3.Connection,
                               collection_def: dict) -> list[dict]:
    """Resolve a virtual collection definition to matching documents.

    Supports include_concepts (concept IDs) and include_types (source types).
    If both are specified, documents matching either criterion are included.
    Returns list of dicts with id, title, source, importance.
    """
    init_graph_tables(conn)
    include_concepts = collection_def.get("include_concepts", []) or []
    include_types = collection_def.get("include_types", []) or []

    # Strip concept: prefix if present
    concept_ids = []
    for c in include_concepts:
        cid = c if isinstance(c, str) else c.get("id", "")
        if cid.startswith("concept:"):
            cid = cid[len("concept:"):]
        if cid:
            concept_ids.append(cid)

    doc_ids: set[str] = set()

    if concept_ids:
        placeholders = ",".join("?" * len(concept_ids))
        rows = conn.execute(
            f"SELECT DISTINCT dc.doc_id FROM doc_concepts dc "
            f"WHERE dc.concept_id IN ({placeholders})",
            concept_ids,
        ).fetchall()
        doc_ids.update(r[0] for r in rows)

    if include_types:
        placeholders = ",".join("?" * len(include_types))
        rows = conn.execute(
            f"SELECT doc_id FROM doc_stats "
            f"WHERE source_type IN ({placeholders})",
            include_types,
        ).fetchall()
        doc_ids.update(r[0] for r in rows)

    if not doc_ids:
        return []

    doc_list = sorted(doc_ids)
    placeholders = ",".join("?" * len(doc_list))
    rows = conn.execute(
        f"SELECT d.id, d.title, d.source, COALESCE(ds.importance, 0.0) "
        f"FROM docs d "
        f"LEFT JOIN doc_stats ds ON ds.doc_id = d.id "
        f"WHERE d.id IN ({placeholders}) "
        f"ORDER BY COALESCE(ds.importance, 0.0) DESC, d.title ASC",
        doc_list,
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "source": r[2], "importance": r[3]}
        for r in rows
    ]
