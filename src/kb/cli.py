"""CLI entry point for Knowledge Bucket commands."""

import datetime
import json
import os
import sqlite3
import sys

import click

from .analyzer import build_analysis_prompt
from .concepts import generate_concept_note, suggest_concept_notes
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
from .health import compute_health
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
    click.echo(f"Scored {report['importance_scored']} document(s) for importance")

    # Also build document-document edges
    db = index_path(root)
    conn = sqlite3.connect(db)
    try:
        edges = build_doc_edges(conn)
    finally:
        conn.close()
    click.echo(f"Created {edges} document-document edge(s)")


@main.command("concepts")
@click.argument("subcommand", default="suggest")
@click.option("--min-df", default=2, type=int, help="Minimum document frequency")
@click.option("--generate", is_flag=True, help="Generate note files for candidates")
def concepts_cmd(subcommand: str, min_df: int, generate: bool):
    """Manage concept notes. Subcommands: suggest."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if subcommand != "suggest":
        click.echo(f"Unknown concepts subcommand: {subcommand}. Use 'suggest'.", err=True)
        raise SystemExit(1)

    db = index_path(root)
    if not os.path.exists(db):
        click.echo("No index found. Run 'kb graph build' first.", err=True)
        raise SystemExit(1)

    conn = sqlite3.connect(db)
    try:
        candidates = suggest_concept_notes(conn, root, min_df=min_df)
    finally:
        conn.close()

    if not candidates:
        click.echo("No concept note candidates found.")
        return

    click.echo(f"Found {len(candidates)} concept note candidate(s):")
    generated = 0
    for c in candidates:
        click.echo(f"  {c['label']} (df={c['df']})")
        if c["doc_titles"]:
            for t in c["doc_titles"][:3]:
                click.echo(f"    - {t}")
        if generate:
            rel = generate_concept_note(
                root, c["concept_id"], c["label"], c["df"], c["doc_titles"],
            )
            generated += 1
            click.echo(f"    -> generated: {rel}")

    if generate and generated:
        click.echo(f"Generated {generated} concept note(s)")


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


@main.command("add-paper")
@click.argument("paper_ref")
@click.option("--content", "-c", default=None, help="Notes or content text (or pipe via stdin)")
def add_paper(paper_ref: str, content: str | None):
    """Add a paper by arXiv URL/ID, DOI, or title."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .parsers.paper import parse_paper

    if content is None and not sys.stdin.isatty():
        content = sys.stdin.read()

    click.echo(f"Fetching {paper_ref}...", err=True)
    try:
        paper_data = parse_paper(paper_ref, content=content)
    except (ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    ulid = generate_ulid()
    rel_path = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    front_matter = f"""\
---
id: {ulid}
title: {paper_data['title']}
source_type: {paper_data['source_type']}
created: {now}
updated: {now}
"""
    if paper_data["source_url"]:
        front_matter += f"source: {paper_data['source_url']}\n"
    meta = paper_data.get("metadata", {})
    if meta.get("authors"):
        front_matter += "paper_authors:\n"
        for a in meta["authors"]:
            front_matter += f"  - {a}\n"
    if meta.get("arxiv_id"):
        front_matter += f"arxiv_id: {meta['arxiv_id']}\n"
    if meta.get("doi"):
        front_matter += f"doi: {meta['doi']}\n"
    if meta.get("published"):
        front_matter += f"paper_published: {meta['published']}\n"
    front_matter += "---\n\n"

    body = paper_data["body"]
    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)
        if body and not body.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")
    click.echo(f"  title: {paper_data['title']}")


@main.command("add-pdf")
@click.argument("pdf_path")
@click.option("--source", "-s", default=None, help="URL where the raw PDF is stored externally")
@click.option("--content", "-c", default=None, help="Notes or content text (or pipe via stdin)")
def add_pdf(pdf_path: str, source: str | None, content: str | None):
    """Add a PDF document by extracting text and metadata from a local file."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .parsers.pdf import parse_pdf

    if content is None and not sys.stdin.isatty():
        content = sys.stdin.read()

    abs_pdf = os.path.abspath(pdf_path)
    click.echo(f"Parsing {abs_pdf}...", err=True)
    try:
        pdf_data = parse_pdf(abs_pdf, source_url=source, content=content)
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    ulid = generate_ulid()
    rel_path = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    front_matter = f"""\
---
id: {ulid}
title: {pdf_data['title']}
source_type: {pdf_data['source_type']}
created: {now}
updated: {now}
"""
    if pdf_data["source_url"]:
        front_matter += f"source: {pdf_data['source_url']}\n"
    meta = pdf_data.get("metadata", {})
    if meta.get("page_count"):
        front_matter += f"pdf_pages: {meta['page_count']}\n"
    if meta.get("author"):
        front_matter += f"pdf_author: {meta['author']}\n"
    front_matter += "---\n\n"

    body = pdf_data["body"]
    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)
        if body and not body.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")
    click.echo(f"  title: {pdf_data['title']}")
    click.echo(f"  pages: {meta.get('page_count', '?')}")


@main.command("add-repo")
@click.argument("url")
def add_repo(url: str):
    """Fetch a GitHub repo by URL and add it as a git_repo document."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .parsers.repo import parse_repo

    click.echo(f"Fetching {url}...", err=True)
    try:
        repo_data = parse_repo(url)
    except (ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    ulid = generate_ulid()
    rel_path = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    front_matter = f"""\
---
id: {ulid}
title: {repo_data['title']}
source_type: {repo_data['source_type']}
created: {now}
updated: {now}
source: {repo_data['source_url']}
"""
    meta = repo_data.get("metadata", {})
    if meta.get("language"):
        front_matter += f"repo_language: {meta['language']}\n"
    if meta.get("stars"):
        front_matter += f"repo_stars: {meta['stars']}\n"
    if meta.get("topics"):
        front_matter += "repo_topics:\n"
        for t in meta["topics"]:
            front_matter += f"  - {t}\n"
    front_matter += "---\n\n"

    body = repo_data["body"]
    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)
        if body and not body.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")
    click.echo(f"  title: {repo_data['title']}")


@main.command()
@click.argument("doc_id")
@click.option("--raw-json", is_flag=True, help="Output raw analysis prompt as JSON")
def analyze(doc_id: str, raw_json: bool):
    """Build an analysis prompt for DOC_ID. Output is a ready-to-send prompt."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    # Find the document by scanning records/doc/
    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    found_path = None
    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if fn == f"{doc_id}.md":
                found_path = os.path.join(dirpath, fn)
                break
        if found_path:
            break

    if not found_path:
        click.echo(f"Document not found: {doc_id}", err=True)
        raise SystemExit(1)

    from .graph import _parse_front_matter_yaml

    with open(found_path) as f:
        text = f.read()
    meta, body = _parse_front_matter_yaml(text)

    title = meta.get("title", doc_id)
    source_type = meta.get("source_type", "web")
    source_url = meta.get("source")

    prompt = build_analysis_prompt(source_type, title, body.strip(), source_url)

    if raw_json:
        click.echo(json.dumps({
            "doc_id": doc_id,
            "source_type": source_type,
            "prompt": prompt,
        }, indent=2))
    else:
        click.echo(prompt)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def health(as_json: bool):
    """Show graph health metrics and quality indicators."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    report = compute_health(root)
    if "error" in report:
        click.echo(report["error"], err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    ov = report["overview"]
    click.echo("=== Knowledge Bucket Health ===")
    click.echo()
    click.echo(f"Documents:    {ov['total_documents']}")
    click.echo(f"Concepts:     {ov['total_concepts']}")
    click.echo(f"Edges:        {ov['total_edges']}")
    click.echo(f"Orphan docs:  {ov['orphan_documents']} (no concepts)")
    click.echo(f"Isolated docs:{ov['isolated_documents']} (no edges)")
    click.echo()

    m = report["metrics"]
    click.echo("--- Metrics ---")
    click.echo(f"Avg concepts/doc: {m['avg_concepts_per_doc']}")
    click.echo(f"Avg edges/doc:    {m['avg_edges_per_doc']}")
    click.echo(f"Connectivity:     {m['connectivity_ratio']:.1%}")
    click.echo()

    click.echo("--- Source Types ---")
    for st, count in report["source_types"].items():
        click.echo(f"  {st}: {count}")
    click.echo()

    click.echo("--- Importance Distribution ---")
    dist = report["importance_distribution"]
    click.echo(f"  High (>=0.7):    {dist['high']}")
    click.echo(f"  Medium (0.4-0.7):{dist['medium']}")
    click.echo(f"  Low (0.0-0.4):   {dist['low']}")
    click.echo(f"  Unscored:        {dist['unscored']}")
    click.echo()

    if report["top_concepts"]:
        click.echo("--- Top Concepts ---")
        for c in report["top_concepts"][:10]:
            click.echo(f"  {c['label']} (df={c['df']})")
        click.echo()

    if report["concepts_missing_notes"] > 0:
        click.echo(f"Concepts missing notes (df>=2): {report['concepts_missing_notes']}")


@main.command()
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", "-p", default=5000, type=int, help="Bind port")
@click.option("--debug", is_flag=True, help="Enable Flask debug mode")
def serve(host: str, port: int, debug: bool):
    """Start local web UI for browsing the knowledge bucket."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .web import create_app

    app = create_app(root)
    click.echo(f"Starting Knowledge Bucket UI at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
