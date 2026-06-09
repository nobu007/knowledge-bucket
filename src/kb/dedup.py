"""Deduplication: source_key generation, content_hash, sources table."""

import hashlib
import sqlite3
import urllib.parse


def generate_source_key(
    source_type: str,
    source_url: str | None = None,
    title: str | None = None,
    doc_ulid: str | None = None,
) -> str:
    """Generate a deduplication key per GOAL.md section 17.

    Rules by source_type:
      - web:    url:<canonical_url>  (UTM params stripped)
      - paper:  doi:<doi> | arxiv:<id> | paper:<title_hash>
      - git_repo / repo: repo:github.com/owner/name
      - memo:   memo:<ulid>
    """
    if source_type in ("git_repo", "repo"):
        return _repo_source_key(source_url or "")

    if source_type == "paper":
        return _paper_source_key(source_url, title)

    if source_type in ("web", "video", "pdf"):
        if source_url:
            return f"url:{_canonicalize_url(source_url)}"
        if title:
            return f"paper:{_title_hash(title)}"
        return f"memo:{doc_ulid or 'unknown'}"

    # Default: memo
    return f"memo:{doc_ulid or 'unknown'}"


def _repo_source_key(url: str) -> str:
    """Extract repo:github.com/owner/name from URL."""
    # Handle owner/repo shorthand
    parts = url.strip().rstrip("/").split("/")
    if len(parts) == 2 and "://" not in url:
        return f"repo:github.com/{parts[0]}/{parts[1]}"

    # Handle full URLs
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    # Remove trailing .git
    if path.endswith(".git"):
        path = path[:-4]
    host = parsed.hostname or "github.com"
    return f"repo:{host}/{path}"


def _paper_source_key(source_url: str | None, title: str | None) -> str:
    """Generate paper source key: doi, arxiv, or title hash."""
    if source_url:
        lower = source_url.lower()
        # DOI
        if "doi.org/" in lower or lower.startswith("doi:"):
            doi = source_url.split("doi.org/")[-1].split("doi:")[-1].strip()
            return f"doi:{doi}"

        # arXiv
        if "arxiv.org" in lower or lower.startswith("arxiv:"):
            arxiv_id = _extract_arxiv_id(source_url)
            if arxiv_id:
                return f"arxiv:{arxiv_id}"

    if title:
        return f"paper:{_title_hash(title)}"

    return "paper:unknown"


def _extract_arxiv_id(text: str) -> str | None:
    """Extract arXiv ID from URL or bare ID."""
    import re

    # Bare ID: 2401.12345 or 2301.01234v2
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", text)
    if m:
        return m.group(1)

    # Old-style: hep-th/9901001
    m = re.search(r"([a-z-]+/\d{7})", text)
    if m:
        return m.group(1)

    return None


def _canonicalize_url(url: str) -> str:
    """Normalize URL: lowercase host, strip UTM params, remove fragment."""
    parsed = urllib.parse.urlparse(url)

    # Strip UTM and common tracking params
    query_params = urllib.parse.parse_qsl(parsed.query)
    clean_params = [
        (k, v) for k, v in query_params
        if not k.lower().startswith("utm_")
        and k.lower() not in ("ref", "source", "fbclid", "gclid")
    ]

    query = urllib.parse.urlencode(clean_params) if clean_params else ""
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")

    return urllib.parse.urlunparse(
        (parsed.scheme, host, path, parsed.params, query, "")
    )


def _title_hash(title: str) -> str:
    """SHA-256 hash of normalized title for dedup."""
    normalized = title.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def compute_content_hash(body: str) -> str:
    """SHA-256 hash of document body for update detection (section 18)."""
    return hashlib.sha256(body.encode()).hexdigest()


def init_sources_table(conn: sqlite3.Connection) -> None:
    """Create the sources table per GOAL.md section 13."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            source_key TEXT PRIMARY KEY,
            canonical_url TEXT,
            first_doc_id TEXT NOT NULL,
            last_doc_id TEXT NOT NULL,
            content_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def check_duplicate(conn: sqlite3.Connection, source_key: str) -> dict | None:
    """Check if a source_key already exists. Returns source record or None."""
    row = conn.execute(
        "SELECT source_key, canonical_url, first_doc_id, last_doc_id, "
        "       content_hash, created_at, updated_at "
        "FROM sources WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if not row:
        return None
    return {
        "source_key": row[0],
        "canonical_url": row[1],
        "first_doc_id": row[2],
        "last_doc_id": row[3],
        "content_hash": row[4],
        "created_at": row[5],
        "updated_at": row[6],
    }


def register_source(
    conn: sqlite3.Connection,
    source_key: str,
    canonical_url: str | None,
    doc_id: str,
    content_hash: str,
    now: str,
) -> None:
    """Insert or update a source record."""
    existing = check_duplicate(conn, source_key)
    if existing:
        conn.execute(
            "UPDATE sources SET last_doc_id = ?, content_hash = ?, updated_at = ? "
            "WHERE source_key = ?",
            (doc_id, content_hash, now, source_key),
        )
    else:
        conn.execute(
            "INSERT INTO sources (source_key, canonical_url, first_doc_id, last_doc_id, "
            "                     content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_key, canonical_url, doc_id, doc_id, content_hash, now, now),
        )
    conn.commit()
