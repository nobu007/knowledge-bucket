"""Ingest pipeline: process inbox files into proper records."""

import os
import re

from .core import DOC_DIR, INBOX_DIR, RECORDS_DIR, generate_ulid, shard_path

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

    Returns the ULID of the new document, or None if skipped.
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

    ulid = generate_ulid()
    rel = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel)

    import datetime

    now = datetime.datetime.now(datetime.UTC).isoformat()
    front_matter = f"---\nid: {ulid}\ntitle: {title}\nsource_type: {source_type}\n"
    front_matter += f"created: {now}\nupdated: {now}\n"
    if source_url:
        front_matter += f"source: {source_url}\n"
    front_matter += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)

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
