"""CLI entry point: kb init, kb add, kb ingest, kb search, kb sync."""

import datetime
import os
import sys

import click

from .core import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    DEFAULT_CONFIG,
    DOC_DIR,
    RECORDS_DIR,
    ensure_dirs,
    generate_ulid,
    kb_root,
    shard_path,
)


@click.group()
def main():
    """Knowledge Bucket - Git-backed knowledge management."""


@main.command()
@click.argument("path", default=".")
def init(path: str):
    """Initialize a new knowledge bucket at PATH (default: current directory)."""
    target = os.path.abspath(path)
    cfg_path = os.path.join(target, CONFIG_DIR, CONFIG_FILENAME)

    if os.path.exists(cfg_path):
        click.echo(f"Already initialized: {cfg_path}", err=True)
        raise SystemExit(1)

    ensure_dirs(target)

    with open(cfg_path, "w") as f:
        f.write(DEFAULT_CONFIG)

    # Ensure .gitkeep in inbox so git tracks the empty dir
    gitkeep = os.path.join(target, "inbox", ".gitkeep")
    if not os.path.exists(gitkeep):
        with open(gitkeep, "w") as f:
            f.write("")

    click.echo(f"Initialized knowledge bucket at {target}")


@main.command()
@click.option("--title", "-t", required=True, help="Document title")
@click.option("--source", "-s", default=None, help="Source URL or reference")
@click.option("--content", "-c", default=None, help="Content text (or pipe via stdin)")
@click.option("--type", "doc_type", default="web", help="Source type: web|paper|repo|memo|pdf")
def add(title: str, source: str | None, content: str | None, doc_type: str):
    """Add a new document to the knowledge bucket."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    ulid = generate_ulid()
    rel_path = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
    os.makedirs(abs_dir, exist_ok=True)

    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Read content from stdin if not provided
    if content is None and not sys.stdin.isatty():
        content = sys.stdin.read()
    elif content is None:
        content = ""

    front_matter = f"""\
---
id: {ulid}
title: {title}
source_type: {doc_type}
created: {now}
updated: {now}
"""

    if source:
        front_matter += f"source: {source}\n"

    front_matter += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(content)
        if content and not content.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")


if __name__ == "__main__":
    main()
