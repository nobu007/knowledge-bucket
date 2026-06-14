"""CLI entry point for Knowledge Bucket commands."""

import datetime
import json
import os
import sqlite3
import sys

import click

from .analyzer import (
    analyze_documents_parallel,
    build_analysis_prompt,
    find_docs_without_analysis,
    get_api_key,
)
from .concepts import generate_concept_note, suggest_concept_notes
from .core import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    DEFAULT_ALIASES,
    DEFAULT_CONFIG,
    DEFAULT_STOP_CONCEPTS,
    DEFAULT_TAXONOMY,
    DOC_DIR,
    RECORDS_DIR,
    ensure_dirs,
    generate_ulid,
    kb_root,
    shard_path,
    yaml_scalar,
)
from .dedup import compute_content_hash, find_doc_by_source_key, generate_source_key
from .embeddings import build_embeddings, embedding_search
from .graph import build_graph, load_taxonomy, resolve_virtual_collection
from .health import compute_health
from .index import build_index, index_path, repair_index, search_index, sync_index, verify_index
from .ingest import ingest_inbox
from .related import build_concept_edges, build_doc_edges, find_cooccurring_concepts, find_related
from .storage import get_raw, save_raw
from .sync import sync
from .vectors import build_vectors, semantic_search

from .generate import generate as generate_dataset


@click.group()
@click.version_option(package_name="kb-tools")
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

    taxonomy_path = os.path.join(target, CONFIG_DIR, "taxonomy.yml")
    if not os.path.exists(taxonomy_path):
        with open(taxonomy_path, "w") as f:
            f.write(DEFAULT_TAXONOMY)

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
@click.option("--save-raw", "do_save_raw", is_flag=True,
              help="Save raw content to S3/R2 and record raw_ref in front matter")
def add(title: str, source: str | None, content: str | None, doc_type: str,
        concepts: str | None, do_save_raw: bool):
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

    raw_ref = None
    if do_save_raw and content:
        raw_ref = save_raw(root, ulid, content.encode())

    content_hash = compute_content_hash(content)

    front_matter = f"""\
---
id: {ulid}
title: {yaml_scalar(title)}
source_type: {doc_type}
source_key: {generate_source_key(doc_type, source_url=source, title=title, doc_ulid=ulid)}
content_hash: sha256:{content_hash}
created: {now}
updated: {now}
"""

    if source:
        front_matter += f"source: {source}\n"

    if raw_ref:
        front_matter += f"raw_ref: {raw_ref}\n"

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

    # Index immediately so the new doc is searchable without a separate command.
    sync_index(root)


@main.command()
@click.option("--analyze", "do_analyze", is_flag=True,
              help="Run LLM analysis on ingested documents")
@click.option("--workers", "-w", type=int, default=1,
              help="Parallel analysis workers when --analyze is set")
def ingest(do_analyze: bool, workers: int):
    """Process inbox files into records and rebuild the search index."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    ingested = ingest_inbox(root)

    if not ingested:
        click.echo("No files to ingest.")
        return

    count = sync_index(root)
    click.echo(f"Ingested {len(ingested)} file(s), indexed {count} new document(s)")

    if do_analyze:
        if not get_api_key():
            click.echo("Warning: ai-hub-agent-proxy not found (set KB_AGENT_PROXY), "
                       "skipping analysis", err=True)
            return
        paths, ulid_map = [], {}
        for ulid in ingested:
            doc_path = _find_doc_path(root, ulid)
            if doc_path:
                paths.append(doc_path)
                ulid_map[doc_path] = ulid
        analyzed, failures = analyze_documents_parallel(paths, workers=workers)
        for p, err in failures:
            click.echo(f"Analysis failed for {ulid_map.get(p, '?')}: {err}", err=True)
        click.echo(f"Analyzed {analyzed}/{len(paths)} document(s) "
                   f"({workers} worker(s))")
        # Re-index so FTS/graph reflect the new summary/concepts/concepts front
        # matter written by the analysis step.
        if analyzed:
            sync_index(root)


@main.command()
@click.option("--rebuild", is_flag=True, help="Drop and rebuild index from scratch")
@click.option("--sync", "do_sync", is_flag=True, help="Incrementally add new documents only")
@click.option("--verify", is_flag=True, help="Check index consistency")
@click.option("--repair", is_flag=True, help="Repair index inconsistencies")
def index(rebuild: bool, do_sync: bool, verify: bool, repair: bool):
    """Build, rebuild, or incrementally sync the SQLite FTS search index."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if verify:
        report = verify_index(root)
        if "error" in report:
            click.echo(report["error"], err=True)
            raise SystemExit(1)
        ok = True
        if report["ghost_entries"]:
            ok = False
            n = len(report["ghost_entries"])
            click.echo(f"Ghost entries (in index, missing on disk): {n}")
            for gid in report["ghost_entries"][:10]:
                click.echo(f"  {gid}")
        if report["missing_entries"]:
            ok = False
            n = len(report["missing_entries"])
            click.echo(f"Missing entries (on disk, not in index): {n}")
            for mid, mrel in report["missing_entries"][:10]:
                click.echo(f"  {mid} ({mrel})")
        if report["stale_head"]:
            ok = False
            click.echo("Stale last_indexed_commit: HEAD reference points to non-existent commit")
        if ok:
            click.echo("Index is consistent")
        raise SystemExit(0 if ok else 1)

    if repair:
        report = repair_index(root)
        click.echo(f"Removed {report['ghosts_removed']} ghost entry(ies)")
        click.echo(f"Indexed {report['missing_indexed']} missing document(s)")
        if report["stale_head_fixed"]:
            click.echo("Fixed stale last_indexed_commit reference")
        return

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
@click.option("--semantic", is_flag=True, help="Use TF-IDF semantic search")
def search(query: str, limit: int, semantic: bool):
    """Search documents using full-text search."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if semantic:
        # Prefer embedding index over TF-IDF when available
        emb_path = os.path.join(root, ".kb", "embeddings.npz")
        vec_path = os.path.join(root, ".kb", "vectors.npz")
        try:
            if os.path.exists(emb_path):
                results = embedding_search(root, query, limit)
            elif os.path.exists(vec_path):
                results = semantic_search(root, query, limit)
            else:
                click.echo(
                    "No vector index found. Run 'kb vectorize' first.",
                    err=True,
                )
                raise SystemExit(1)
        except FileNotFoundError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

        if not results:
            click.echo("No results found.")
            return

        # Enrich with title from FTS index
        db = index_path(root)
        titles: dict[str, str] = {}
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            try:
                for row in conn.execute("SELECT id, title FROM docs").fetchall():
                    titles[row[0]] = row[1]
            finally:
                conn.close()

        for r in results:
            title = titles.get(r["id"], r["id"])
            click.echo(f"[{r['id']}] {title} (score: {r['score']:.4f})")
        return

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
@click.option("--full", is_flag=True,
              help="Force full rebuild instead of git-diff incremental build")
def graph_cmd(subcommand: str, full: bool):
    """Build the concept graph from document metadata."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if subcommand != "build":
        click.echo(f"Unknown graph subcommand: {subcommand}. Use 'build'.", err=True)
        raise SystemExit(1)

    report = build_graph(root, full=full)
    mode = "incremental" if report.get("incremental") else "full"
    click.echo(f"Processed {report['docs_processed']} document(s) [{mode}]")
    if "docs_deleted" in report and report["docs_deleted"]:
        click.echo(f"Removed {report['docs_deleted']} deleted document(s)")
    click.echo(f"Found {report['concepts_found']} unique concept(s)")
    click.echo(f"Found {report['entities_found']} unique entit(ies)")
    click.echo(f"Scored {report['importance_scored']} document(s) for importance")

    if "entity_edges" in report:
        click.echo(f"Created {report['entity_edges']} document-entity edge(s)")
        click.echo(f"Created {report['source_edges']} document-source edge(s)")

    # Also build document-document edges and concept co-occurrence edges.
    # These are global and always recomputed (cheap once per-doc rows are set
    # and the concept_id index exists).
    db = index_path(root)
    conn = sqlite3.connect(db)
    try:
        doc_edges = build_doc_edges(conn)
        concept_edges = build_concept_edges(conn)
    finally:
        conn.close()
    click.echo(f"Created {doc_edges} document-document edge(s)")
    click.echo(f"Created {concept_edges} concept co-occurrence edge(s)")


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

    paper_skey = generate_source_key(
        paper_data["source_type"],
        source_url=paper_data.get("source_url"),
        title=paper_data["title"],
        doc_ulid=ulid,
    )
    front_matter = f"""\
---
id: {ulid}
title: {yaml_scalar(paper_data['title'])}
source_type: {paper_data['source_type']}
source_key: {paper_skey}
content_hash: sha256:{compute_content_hash(paper_data['body'])}
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

    # Index immediately so the new doc is searchable without a separate command.
    sync_index(root)
    click.echo(f"  title: {yaml_scalar(paper_data['title'])}")


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

    pdf_skey = generate_source_key(
        pdf_data["source_type"],
        source_url=pdf_data.get("source_url"),
        title=pdf_data["title"],
        doc_ulid=ulid,
    )
    front_matter = f"""\
---
id: {ulid}
title: {yaml_scalar(pdf_data['title'])}
source_type: {pdf_data['source_type']}
source_key: {pdf_skey}
content_hash: sha256:{compute_content_hash(pdf_data['body'])}
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

    # Index immediately so the new doc is searchable without a separate command.
    sync_index(root)
    click.echo(f"  title: {yaml_scalar(pdf_data['title'])}")
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

    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Compute source_key first; repo source_key is URL-derived and independent
    # of the ulid, so we can detect an existing doc before minting a new id.
    repo_skey = generate_source_key(
        repo_data["source_type"],
        source_url=repo_data.get("source_url"),
        doc_ulid="probe",
    )
    existing = find_doc_by_source_key(root, repo_skey)
    if existing:
        abs_path = existing
        with open(abs_path) as _f:
            _old = _f.read()
        ulid = _old.split("id:", 1)[1].split("\n", 1)[0].strip()
        rel_path = os.path.relpath(abs_path, os.path.join(root, RECORDS_DIR, DOC_DIR))
        created_line = ""
        for _l in _old.split("\n"):
            if _l.startswith("created:"):
                created_line = _l
                break
        click.echo(f"Updating existing doc for {repo_skey}", err=True)
    else:
        ulid = generate_ulid()
        rel_path = shard_path(ulid)
        abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
        os.makedirs(abs_dir, exist_ok=True)
        abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)
        created_line = f"created: {now}"

    front_matter = f"""\
---
id: {ulid}
title: {yaml_scalar(repo_data['title'])}
source_type: {repo_data['source_type']}
source_key: {repo_skey}
content_hash: sha256:{compute_content_hash(repo_data['body'])}
{created_line}
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

    # Index immediately so the new doc is searchable without a separate command.
    sync_index(root)
    click.echo(f"  title: {yaml_scalar(repo_data['title'])}")


@main.command("add-video")
@click.argument("url")
@click.option("--content", default=None, help="Personal notes about the video")
def add_video(url: str, content: str | None):
    """Fetch a YouTube video by URL and add it as a video document."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .parsers.video import parse_video

    click.echo(f"Fetching {url}...", err=True)
    try:
        video_data = parse_video(url, content=content)
    except (ValueError, RuntimeError) as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    ulid = generate_ulid()
    rel_path = shard_path(ulid)
    abs_dir = os.path.join(root, RECORDS_DIR, DOC_DIR, os.path.dirname(rel_path))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(root, RECORDS_DIR, DOC_DIR, rel_path)

    now = datetime.datetime.now(datetime.UTC).isoformat()

    video_skey = generate_source_key(
        video_data["source_type"],
        source_url=video_data.get("source_url"),
        doc_ulid=ulid,
    )
    front_matter = f"""\
---
id: {ulid}
title: {yaml_scalar(video_data['title'])}
source_type: {video_data['source_type']}
source_key: {video_skey}
content_hash: sha256:{compute_content_hash(video_data['body'])}
created: {now}
updated: {now}
source: {video_data['source_url']}
"""
    meta = video_data.get("metadata", {})
    if meta.get("video_id"):
        front_matter += f"video_id: {meta['video_id']}\n"
    if meta.get("channel"):
        front_matter += f"video_channel: {meta['channel']}\n"
    if meta.get("platform"):
        front_matter += f"video_platform: {meta['platform']}\n"
    front_matter += "---\n\n"

    body = video_data["body"]
    with open(abs_path, "w") as f:
        f.write(front_matter)
        f.write(body)
        if body and not body.endswith("\n"):
            f.write("\n")

    click.echo(f"Added: {ulid}")
    click.echo(f"  path: {os.path.join(RECORDS_DIR, DOC_DIR, rel_path)}")

    # Index immediately so the new doc is searchable without a separate command.
    sync_index(root)
    click.echo(f"  title: {yaml_scalar(video_data['title'])}")


@main.command()
@click.argument("doc_id")
@click.option("--full", is_flag=True, help="Show full body without truncation")
def show(doc_id: str, full: bool):
    """Display metadata and body of document DOC_ID."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

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

    click.echo(f"ID:          {meta.get('id', doc_id)}")
    click.echo(f"Title:       {meta.get('title', '(untitled)')}")
    click.echo(f"Source type: {meta.get('source_type', 'unknown')}")
    if meta.get("source"):
        click.echo(f"Source:      {meta['source']}")
    click.echo(f"Created:     {meta.get('created', 'unknown')}")
    click.echo(f"Updated:     {meta.get('updated', 'unknown')}")

    concepts = meta.get("concepts", [])
    if isinstance(concepts, str):
        concepts = [c.strip() for c in concepts.split(",") if c.strip()]
    if concepts:
        click.echo(f"Concepts:    {', '.join(concepts)}")

    importance = meta.get("importance")
    if importance is not None:
        click.echo(f"Importance:  {importance}")

    click.echo()
    if full:
        click.echo(body.rstrip())
    else:
        lines = body.strip().split("\n")
        if len(lines) > 20:
            click.echo("\n".join(lines[:20]))
            click.echo(f"... ({len(lines) - 20} more lines, use --full to show)")
        else:
            click.echo(body.rstrip())


@main.command("concept")
@click.argument("concept_id")
def concept_cmd(concept_id: str):
    """Display concept metadata, associated documents, and note."""
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
        row = conn.execute(
            "SELECT concept_id, label, kind, df, is_stop FROM concepts "
            "WHERE concept_id = ?",
            (concept_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        click.echo(f"Concept not found: {concept_id}", err=True)
        raise SystemExit(1)

    click.echo(f"ID:       {row[0]}")
    click.echo(f"Label:    {row[1]}")
    click.echo(f"Kind:     {row[2]}")
    click.echo(f"DF:       {row[3]}")
    if row[4]:
        click.echo("Stop:     yes")

    # Show associated documents
    conn = sqlite3.connect(db)
    try:
        docs = conn.execute(
            "SELECT dc.doc_id, d.title "
            "FROM doc_concepts dc "
            "LEFT JOIN docs d ON d.id = dc.doc_id "
            "WHERE dc.concept_id = ? "
            "ORDER BY dc.weight DESC "
            "LIMIT 20",
            (concept_id,),
        ).fetchall()
    finally:
        conn.close()

    if docs:
        click.echo()
        click.echo(f"Documents ({len(docs)}):")
        for doc_id, title in docs:
            label = title or doc_id
            click.echo(f"  [{doc_id}] {label}")

    # Show co-occurring concepts
    conn = sqlite3.connect(db)
    try:
        cooc = find_cooccurring_concepts(conn, concept_id, limit=10)
    finally:
        conn.close()
    if cooc:
        click.echo()
        click.echo(f"Co-occurring concepts ({len(cooc)}):")
        for c in cooc:
            click.echo(f"  {c['label']} (co-occurrence: {c['cooccurrence']}, df={c['df']})")

    # Show concept note if it exists
    concept_dir = os.path.join(root, RECORDS_DIR, "concept")
    note_path = os.path.join(concept_dir, f"{concept_id}.md")
    if os.path.exists(note_path):
        with open(note_path) as f:
            note = f.read().strip()
        click.echo()
        click.echo("--- Concept Note ---")
        click.echo(note)


@main.command()
def collections():
    """List virtual collections defined in config/taxonomy.yml."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    taxonomy = load_taxonomy(root)
    if not taxonomy:
        click.echo("No virtual collections defined. Edit config/taxonomy.yml.")
        return

    db = index_path(root)
    if not os.path.exists(db):
        click.echo("No index found. Run 'kb graph build' first.", err=True)
        raise SystemExit(1)

    conn = sqlite3.connect(db)
    try:
        for name, cdef in taxonomy.items():
            docs = resolve_virtual_collection(conn, cdef)
            label = cdef.get("label", name)
            click.echo(f"  {name} ({label}): {len(docs)} document(s)")
    finally:
        conn.close()


@main.command()
@click.argument("doc_id", required=False)
@click.option("--raw-json", is_flag=True, help="Output raw analysis prompt as JSON")
@click.option("--retry-failed", is_flag=True,
              help="Re-analyze documents missing analysis.confidence")
@click.option("--workers", "-w", type=int, default=1,
              help="Parallel analysis workers (agent subprocesses). "
                   "Cuts wall-clock ~Nx for bulk analysis.")
def analyze(doc_id: str | None, raw_json: bool, retry_failed: bool, workers: int):
    """Build an analysis prompt for DOC_ID or retry failed analyses."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    if retry_failed:
        if not get_api_key():
            click.echo("Error: ai-hub-agent-proxy not found (set KB_AGENT_PROXY)", err=True)
            raise SystemExit(1)
        docs = find_docs_without_analysis(root)
        if not docs:
            click.echo("No documents need re-analysis")
            return
        paths = [p for _ulid, p in docs]
        ulids = {p: u for u, p in docs}
        analyzed, failures = analyze_documents_parallel(paths, workers=workers)
        for p, err in failures:
            click.echo(f"Failed: {ulids.get(p, '?')}: {err}", err=True)
        click.echo(f"Re-analyzed {analyzed}/{len(docs)} document(s) "
                   f"({workers} worker(s))")
        return

    if not doc_id:
        click.echo("Error: DOC_ID is required (or use --retry-failed)", err=True)
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
    click.echo(f"Edges:        {ov['total_edges']} (doc-doc)")
    click.echo(f"Concept edges:{ov['concept_edges']} (co-occurrence)")
    click.echo(f"Orphan docs:  {ov['orphan_documents']} (no concepts)")
    click.echo(f"Isolated docs:{ov['isolated_documents']} (no edges)")
    click.echo(f"Hub threshold:{ov['hub_threshold']} (df > threshold = hub)")
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

    if report["hub_concepts"]:
        click.echo("--- Hub Concepts (df > threshold, excluded from edges) ---")
        for c in report["hub_concepts"][:10]:
            click.echo(f"  {c['label']} (df={c['df']})")
        click.echo()

    if report["concepts_missing_notes"] > 0:
        click.echo(f"Concepts missing notes (df>=2): {report['concepts_missing_notes']}")


@main.command()
def doctor():
    """Run data-quality checks on the bucket. Exits non-zero if any fail.

    Checks: front-matter YAML parse, required fields, duplicate source_keys,
    analysis coverage, concept sanity, index consistency. Designed to run
    before/after bulk ingestion to catch corruption early.
    """
    from .core import DOC_DIR, RECORDS_DIR
    from .graph import _read_doc_info

    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    docs = []
    for dp, _dn, fns in os.walk(doc_dir):
        for fn in fns:
            if fn.endswith(".md"):
                docs.append(os.path.join(dp, fn))

    problems = 0
    click.echo(f"=== kb doctor: {len(docs)} document(s) ===")

    # QC1: front-matter parse
    bad = [f for f in docs if _read_doc_info(f) is None]
    if bad:
        problems += len(bad)
        click.echo(f"[FAIL] QC1 front-matter parse: {len(bad)} unparseable")
        for f in bad[:5]:
            click.echo(f"         {os.path.basename(f)[:20]}")
    else:
        click.echo("[ok]   QC1 front-matter parse: all docs valid")

    # QC2: required fields
    required = ("id:", "title:", "source_type:", "source_key:", "content_hash:")
    miss = []
    for f in docs:
        text = open(f).read()
        if any(r not in text for r in required):
            miss.append(f)
    if miss:
        problems += len(miss)
        click.echo(f"[FAIL] QC2 required fields: {len(miss)} missing fields")
    else:
        click.echo("[ok]   QC2 required fields: all present")

    # QC3: duplicate source_keys
    keys: dict[str, list[str]] = {}
    for f in docs:
        text = open(f).read()
        for line in text.split("\n"):
            if line.startswith("source_key:"):
                keys.setdefault(line.split(":", 1)[1].strip(), []).append(f)
                break
    dups = {k: v for k, v in keys.items() if len(v) > 1}
    if dups:
        problems += len(dups)
        click.echo(f"[FAIL] QC3 duplicate source_keys: {len(dups)}")
        for k, v in dups.items():
            click.echo(f"         {k}")
    else:
        click.echo("[ok]   QC3 duplicate source_keys: none")

    # QC4: analysis coverage
    unanalyzed = []
    for f in docs:
        text = open(f).read()
        if "confidence:" not in text:
            unanalyzed.append(f)
    if unanalyzed:
        click.echo(f"[warn] QC4 analysis coverage: {len(unanalyzed)}/{len(docs)} unanalyzed")
    else:
        click.echo("[ok]   QC4 analysis coverage: all analyzed")

    # QC5: index consistency
    try:
        report = verify_index(root)
        ghosts = len(report.get("ghost_entries", []))
        missing = len(report.get("missing_entries", []))
        if ghosts or missing:
            problems += ghosts + missing
            click.echo(f"[FAIL] QC5 index consistency: {ghosts} ghost, {missing} missing")
        else:
            click.echo("[ok]   QC5 index consistency: clean")
    except Exception as e:
        click.echo(f"[warn] QC5 index consistency: {e}")

    if problems:
        click.echo(f"\n{problems} problem(s) found")
        raise SystemExit(1)
    click.echo("\nAll checks passed")


@main.command()
@click.option("--concept", default=None, help="Filter docs whose front matter mentions this concept (substring)")
@click.option("--type", "source_type", default=None, help="Filter by source_type (web|paper|git_repo|memo|pdf|video)")
@click.option("--pairs", "-n", type=int, default=5, show_default=True, help="Instruction pairs to generate per doc")
@click.option("--format", "fmt", type=click.Choice(["openai", "alpaca"]), default="openai", show_default=True)
@click.option("--limit", type=int, default=None, help="Max docs to process")
@click.option("--output", "-o", default=None, help="Output JSONL path (default .kb/training/sft-<tag>.jsonl)")
def generate(concept: str | None, source_type: str | None, pairs: int, fmt: str,
             limit: int | None, output: str | None):
    """Generate domain training data (SFT JSONL) from analyzed docs via the agent proxy."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)
    if not get_api_key():
        click.echo("Error: ai-hub-agent-proxy not found (set KB_AGENT_PROXY)", err=True)
        raise SystemExit(1)
    report = generate_dataset(root, concept=concept, source_type=source_type,
                              n_pairs=pairs, fmt=fmt, limit=limit, output=output)
    if report["docs"] == 0:
        click.echo("No docs matched the filter.")
        return
    click.echo(f"Processed {report['docs']} doc(s) → {report['pairs']} pair(s) "
               f"[{fmt}] (skipped {report['duplicates_skipped']} dupes)")
    click.echo(f"Output: {report['output']}")


@main.command()
@click.option("--interval", "-i", default=300, show_default=True,
              help="Seconds between maintenance cycles")
@click.option("--analyze", "do_analyze", is_flag=True,
              help="Analyze newly-ingested docs each cycle (needs KB_AGENT_PROXY)")
@click.option("--once", is_flag=True,
              help="Run a single maintenance cycle and exit (for cron)")
@click.option("--engine", default="embedding",
              type=click.Choice(["tfidf", "embedding", "local"]),
              help="Vector engine to keep fresh")
def watch(interval: int, do_analyze: bool, once: bool, engine: str):
    """Auto-ingest inbox + keep index/graph/vectors fresh on a timer.

    Each cycle: ingest inbox → sync FTS index → rebuild graph (incremental) →
    rebuild vectors if docs changed. Run as a foreground daemon, or with --once
    for a cron job. Ctrl-C to stop.
    """
    import time as _time

    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    def cycle() -> dict:
        ts = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
        ingested = ingest_inbox(root) or []
        n_idx = sync_index(root)
        graph = build_graph(root)  # incremental via git-diff
        # Rebuild vectors only if the doc count changed since last cycle.
        n_vec = 0
        try:
            from .embeddings import build_embeddings
            n_vec = build_embeddings(root, engine=engine).get("docs_vectorized", 0)
        except Exception as e:  # vectorize is best-effort in the loop
            click.echo(f"[{ts}] vectorize skipped: {e}", err=True)
        analyzed = 0
        if do_analyze and ingested and get_api_key():
            paths = [_find_doc_path(root, u) for u in ingested]
            analyzed, _fail = analyze_documents_parallel(
                [p for p in paths if p], workers=4)
            if analyzed:
                sync_index(root)
        return {"ts": ts, "ingest": len(ingested), "idx": n_idx,
                "docs": graph.get("docs_processed", 0), "vec": n_vec, "an": analyzed}

    if once:
        r = cycle()
        click.echo(f"[{r['ts']}] ingest={r['ingest']} idx={r['idx']} "
                   f"docs={r['docs']} vec={r['vec']} analyzed={r['an']}")
        return

    click.echo(f"Watching {root} every {interval}s (Ctrl-C to stop)...")
    while True:
        r = cycle()
        click.echo(f"[{r['ts']}] ingest={r['ingest']} idx={r['idx']} "
                   f"docs={r['docs']} vec={r['vec']} analyzed={r['an']}")
        _time.sleep(interval)


@main.command()
@click.option("--engine", default="tfidf",
              type=click.Choice(["tfidf", "embedding", "openai", "local"]),
              help="Engine: tfidf (default), embedding/openai, local (hash)")
def vectorize(engine: str):
    """Build vector index for semantic search."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    try:
        if engine == "tfidf":
            report = build_vectors(root)
        else:
            # "embedding"/"local" → local sentence-transformers model;
            # "openai" → OpenAI API. See embeddings._get_engine.
            report = build_embeddings(root, engine=engine)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)

    label = report.get("engine", "tfidf")
    dim_info = f", dim={report['dim']}" if "dim" in report else ""
    click.echo(f"Vectorized {report['docs_vectorized']} document(s) [{label}{dim_info}]")


@main.command()
@click.argument("doc_id")
def raw(doc_id: str):
    """Retrieve and display the raw data stored for DOC_ID."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    # Find document file
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
    meta, _body = _parse_front_matter_yaml(text)

    raw_ref = meta.get("raw_ref")
    if not raw_ref:
        click.echo(f"No raw data stored for {doc_id}", err=True)
        raise SystemExit(1)

    try:
        data = get_raw(root, raw_ref)
    except (RuntimeError, ValueError) as e:
        click.echo(f"Error retrieving raw data: {e}", err=True)
        raise SystemExit(1)

    click.echo(data.decode(errors="replace"))


@main.command()
@click.option("--output", "-o", default=None, help="Output directory (default: .kb/exports)")
def export(output: str | None):
    """Export graph data to Parquet files for external analysis."""
    root = kb_root()
    if root is None:
        click.echo("Not in a knowledge bucket. Run 'kb init' first.", err=True)
        raise SystemExit(1)

    from .export import export_parquet

    try:
        results = export_parquet(root, output_dir=output)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)

    out_dir = output or os.path.join(root, ".kb", "exports")
    click.echo(f"Exported to {out_dir}:")
    for name, count in results.items():
        click.echo(f"  {name}: {count} rows")


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


def _find_doc_path(root: str, ulid: str) -> str | None:
    """Resolve ULID to absolute document path."""
    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if fn == f"{ulid}.md":
                return os.path.join(dirpath, fn)
    return None
