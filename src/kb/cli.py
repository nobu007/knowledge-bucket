"""CLI entry point: kb init, kb add, kb ingest, kb index, kb search, kb sync."""

import datetime
import os
import sqlite3
import sys

import click

from .core import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    DEFAULT_ALIASES,
    DEFAULT_CONFIG,
    DEFAULT_STOP_CONCEPTS,
    DOC_DIR,
    RECORDS_DIR,
    ensure_dirs,
    generate_ulid,
    kb_root,
    shard_path,
)
from .graph import build_graph
from .index import build_index, index_path, search_index, sync_index
from .ingest import ingest_inbox
from .related import build_doc_edges, find_related
from .sync import sync


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

    aliases_path = os.path.join(target, CONFIG_DIR, "aliases.yml")
    if not os.path.exists(aliases_path):
        with open(aliases_path, "w") as f:
            f.write(DEFAULT_ALIASES)

    stop_path = os.path.join(target, CONFIG_DIR, "stop_concepts.yml")
    if not os.path.exists(stop_path):
        with open(stop_path, "w") as f:
            f.write(DEFAULT_STOP_CONCEPTS)

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
@click.option("--concepts", default=None, help="Comma-separated concept slugs")
def add(title: str, source: str | None, content: str | None, doc_type: str,
        concepts: str | None):
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

    if concepts:
        front_matter += "concepts:\n"
        for c in concepts.split(","):
            c = c.strip()
            if c:
                front_matter += f"  - {c}\n"

    front_matter += "---\n\n"

    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(content)
        if content and not content.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")


@main.command()
def ingest():
    """Process inbox files into records and rebuild the search index."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    ingested = ingest_inbox(root)

    if not ingested:
        click.echo("No files to ingest.")
        return

    count = build_index(root)
    click.echo(f"Ingested {len(ingested)} file(s), indexed {count} document(s) total")


@main.command()
@click.option("--rebuild", is_flag=True, help="Drop and rebuild index from scratch")
@click.option("--sync", "do_sync", is_flag=True, help="Incrementally add new documents only")
def index(rebuild: bool, do_sync: bool):
    """Build, rebuild, or incrementally sync the SQLite FTS search index."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if rebuild:
        db = index_path(root)
        if os.path.exists(db):
            os.remove(db)

    if do_sync:
        count = sync_index(root)
        click.echo(f"Synced {count} new document(s) into index")
    else:
        count = build_index(root)
        click.echo(f"Indexed {count} document(s)")


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, help="Max results")
def search(query: str, limit: int):
    """Search documents using full-text search."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    db = index_path(root)
    if not os.path.exists(db):
        click.echo("No index found. Run 'kb index' first.", err=True)
        raise SystemExit(1)

    conn = sqlite3.connect(db)
    try:
        results = search_index(conn, query, limit)
    finally:
        conn.close()

    if not results:
        click.echo("No results found.")
        return

    for r in results:
        click.echo(f"[{r['id']}] {r['title']}")
        if r["source"]:
            click.echo(f"  source: {r['source']}")
        click.echo(f"  path: {r['rel_path']}")
        click.echo(f"  {r['snippet']}")
        click.echo()


@main.command("sync")
@click.option("--message", "-m", default=None, help="Commit message (default: 'kb: sync')")
def sync_cmd(message: str | None):
    """Pull, ingest, index, stage, commit, and push."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    report = sync(root, message=message)

    click.echo(f"Pulled: {report['pulled']}")
    click.echo(f"Ingested: {report['ingested']} file(s)")
    click.echo(f"Indexed: {report['indexed']} new document(s)")
    if report["committed"]:
        click.echo("Committed changes")
    else:
        click.echo("No changes to commit")
    click.echo(f"Pushed: {report['pushed']}")


@main.command("graph")
@click.argument("subcommand", default="build")
def graph_cmd(subcommand: str):
    """Build the concept graph from document metadata."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if subcommand != "build":
        click.echo(f"Unknown graph subcommand: {subcommand}. Use 'build'.", err=True)
        raise SystemExit(1)

    report = build_graph(root)
    click.echo(f"Processed {report['docs_processed']} document(s)")
    click.echo(f"Found {report['concepts_found']} unique concept(s)")

    # Also build document-document edges
    db = index_path(root)
    conn = sqlite3.connect(db)
    try:
        edges = build_doc_edges(conn)
    finally:
        conn.close()
    click.echo(f"Created {edges} document-document edge(s)")


@main.command()
@click.argument("doc_id")
@click.option("--limit", "-n", default=10, help="Max results")
def related(doc_id: str, limit: int):
    """Find documents related to DOC_ID via shared concepts."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    db = index_path(root)
    if not os.path.exists(db):
        click.echo("No index found. Run 'kb graph build' first.", err=True)
        raise SystemExit(1)

    conn = sqlite3.connect(db)
    try:
        results = find_related(conn, doc_id, limit)
    finally:
        conn.close()

    if not results:
        click.echo(f"No related documents found for {doc_id}")
        return

    click.echo(f"Related to {doc_id}:")
    for r in results:
        click.echo(f"  [{r['doc_id']}] {r['title']} (weight: {r['weight']:.2f})")
        if "source" in r:
            click.echo(f"    source: {r['source']}")


if __name__ == "__main__":
    main()
