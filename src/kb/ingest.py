"""Ingest pipeline: process inbox files into proper records."""

import datetime
import os
import re

from .core import DOC_DIR, INBOX_DIR, RECORDS_DIR, generate_ulid, shard_path
from .dedup import (
    check_duplicate,
    compute_content_hash,
    generate_source_key,
    init_sources_table,
    register_source,
)
from .index import index_path, init_db

_URL_RE = re.compile(r"https?://\S+")
_SUPPORTED_EXT = {".md", ".txt", ".url"}


def _classify_file(filename: str, content: str) -> tuple[str, str | None, str]:
    """Return (title, source_url, source_type) for an inbox file."""
    base, ext = os.path.splitext(filename)
    lines = content.strip().splitlines()

    first_line = lines[0].strip() if lines else ""
    url_match = _URL_RE.match(first_line)

    if ext == ".url" or (ext == ".txt" and url_match):
        return base, first_line, "web"

    if url_match:
        body_without_url = content.strip()[len(first_line):].strip()
        title = base if not body_without_url else body_without_url.splitlines()[0].strip()
        if not title:
            title = base
        return title, first_line, "web"

    title = first_line if first_line else base
    if title.startswith("# "):
        title = title[2:].strip()
    if not title:
        title = base
    return title, None, "memo"


def _body_from_content(content: str, source_url: str | None) -> str:
    """Extract body text, stripping URL line if it was used as source."""
    if not source_url:
        return content.strip() + "\n"

    lines = content.strip().splitlines()
    first = lines[0].strip() if lines else ""
    if _URL_RE.match(first) and first == source_url:
        body = "\n".join(lines[1:]).strip()
    else:
        body = content.strip()
    return body + "\n" if body else ""


def ingest_file(root: str, filepath: str) -> str | None:
    """Process a single inbox file into a record.

    Returns the ULID of the new or updated document, or None if skipped.
    If a duplicate source_key exists and content_hash matches, skips the file.
    If a duplicate source_key exists but content changed, updates the existing
    document in-place (GOAL.md section 18).
    """
    basename = os.path.basename(filepath)
    _, ext = os.path.splitext(basename)
    if ext.lower() not in _SUPPORTED_EXT:
        return None

    try:
        with open(filepath) as f:
            content = f.read()
    except OSError:
        return None

    if not content.strip():
        return None

    title, source_url, source_type = _classify_file(basename, content)
    body = _body_from_content(content, source_url)

    # Generate ULID first so memo source_key is unique
    ulid = generate_ulid()

    # Generate source_key and content_hash for dedup
    source_key = generate_source_key(
        source_type, source_url=source_url, title=title, doc_ulid=ulid,
    )
    content_hash = compute_content_hash(body)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Check for duplicates via sources table
    db_path = index_path(root)
    conn = init_db(db_path)
    init_sources_table(conn)
    try:
        existing = check_duplicate(conn, source_key)
        if existing:
            if existing["content_hash"] == content_hash:
                # Exact duplicate — skip
                conn.close()
                os.remove(filepath)
                return None
            # Content changed — update existing document in-place (GOAL.md section 18)
            existing_ulid = existing["last_doc_id"]
            existing_rel = shard_path(existing_ulid)
            existing_abs = os.path.join(root, RECORDS_DIR, DOC_DIR, existing_rel)
            if os.path.exists(existing_abs):
                with open(existing_abs) as f:
                    text = f.read()
                # Update the updated_at line in front matter
                updated_text = re.sub(
                    r"^updated:.*$", f"updated: {now}", text,
                    count=1, flags=re.MULTILINE,
                )
                # Update content_hash in front matter (GOAL.md section 6)
                updated_text = re.sub(
                    r"^content_hash:.*$", f"content_hash: sha256:{content_hash}",
                    updated_text, count=1, flags=re.MULTILINE,
                )
                # Replace body (everything after the second ---)
                fm_end = updated_text.find("---\n", 4)
                if fm_end >= 0:
                    updated_text = updated_text[: fm_end + 4] + "\n" + body
                with open(existing_abs, "w") as f:
                    f.write(updated_text)
                register_source(
                    conn, source_key, source_url, existing_ulid, content_hash, now,
                )
                conn.close()
                os.remove(filepath)
                return existing_ulid
            # Existing file missing — fall through to create new
    except Exception:
        conn.close()
        raise
    rel = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel)

    front_matter = f"---\nid: {ulid}\ntitle: {title}\nsource_type: {source_type}\n"
    front_matter += f"source_key: {source_key}\n"
    front_matter += f"content_hash: sha256:{content_hash}\n"
    front_matter += f"created: {now}\nupdated: {now}\n"
    if source_url:
        front_matter += f"source: {source_url}\n"
    front_matter += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)

    # Register source for future dedup
    register_source(conn, source_key, source_url, ulid, content_hash, now)
    conn.close()

    os.remove(filepath)
    return ulid


def ingest_inbox(root: str) -> list[str]:
    """Process all files in inbox/ and return list of created ULIDs."""
    inbox_dir = os.path.join(root, INBOX_DIR)
    if not os.path.isdir(inbox_dir):
        return []

    ingested: list[str] = []
    for fn in sorted(os.listdir(inbox_dir)):
        if fn == ".gitkeep":
            continue
        filepath = os.path.join(inbox_dir, fn)
        if not os.path.isfile(filepath):
            continue
        result = ingest_file(root, filepath)
        if result is not None:
            ingested.append(result)

    return ingested
