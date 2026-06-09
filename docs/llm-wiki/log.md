# GoalDev Log

Append durable decisions and completed goal progress here.

## 2026-06-07: Phase 1 foundation committed

- **Commit**: `7abfedc` feat(kb): implement kb init and kb add with ULID + hash-sharded paths
- **What**: Python package scaffold (`src/kb/`), `kb init` (creates dirs + config), `kb add` (ULID + sha256 shard + front matter Markdown). 15 tests, lint clean.
- **Decisions**:
  - Monotonic ULID: same-ms calls increment random component instead of re-sampling
  - `kb_root()` uses `os.path.realpath()` to handle macOS `/var` → `/private/var` symlink
  - `kb.yml` is the root marker (walks up from cwd)

## 2026-06-07: SQLite FTS5 index + kb index + kb search

- **Commit**: `99cb466` feat(kb): add SQLite FTS5 index with kb index and kb search commands
- **What**: `src/kb/index.py` (FTS5 virtual table, front-matter parser, build_index, search_index), `kb index [--rebuild]`, `kb search QUERY`. 12 new tests, 27 total pass, lint clean.
- **Decisions**:
  - Standalone FTS5 table (no content= sync) — simpler, rebuild is cheap
  - Index DB at `.kb/index.db`, gitignored (regeneratable cache per GOAL.md)
  - `search_index()` function name avoids clash with CLI `search` command

## 2026-06-07: kb sync + incremental index — Phase 1 complete

- **Commit**: `pending` feat(kb): add kb sync command and incremental index sync
- **What**: `src/kb/sync.py` (git pull/ingest/index/stage/commit/push pipeline), `sync_index()` in `index.py` (incremental FTS update skipping already-indexed IDs), `kb index --sync` flag, `kb sync` CLI command. 9 new tests, 52 total pass, lint clean.
- **Decisions**:
  - `sync_index()` queries existing IDs from FTS table and skips them — avoids full rebuild on each sync
  - `kb sync` does best-effort pull/push (no error on missing remote) — works in local-only repos
  - Only stages directories that actually exist (`records`, `config`, `prompts`)
- **Phase 1 status**: All items complete (init, add, ingest, index, search, sync)

## 2026-06-07: Phase 2 — concept graph pipeline

- **Commits**: `e951952` + `3ebd1a2`
- **What**:
  - `src/kb/graph.py`: graph tables (concepts, doc_concepts, edges), concept extraction from YAML front matter, alias normalization (`config/aliases.yml`), stop concept filtering (`config/stop_concepts.yml`), df computation, active graph term selection
  - `src/kb/related.py`: `build_doc_edges()` generates document-document edges via shared concept IDF, `find_related()` queries related docs
  - CLI: `kb graph build` (concepts + edges), `kb related DOC_ID`, `kb add --concepts`
  - `kb init` now creates default `aliases.yml` and `stop_concepts.yml`
  - 14 new tests, 73 total pass, lint clean
- **Decisions**:
  - Graph tables share the same SQLite DB as FTS (`.kb/index.db`) — one regeneratable cache
  - Concepts parsed from front matter using PyYAML (backward compatible with Phase 1 simple format)
  - Supports both comma-separated (`concepts: a, b`) and YAML list (`concepts:\n  - a`) formats
  - Active graph terms require df >= 2 before used for edges — prevents singleton concepts from creating spurious links
  - Edge weight = sum of (1/df) for shared concepts — rarer concepts contribute more weight
- **Phase 2 status**: All items complete (concept normalization, aliases, stop_concepts, df/idf, active graph terms, document-document edges, kb related)

## 2026-06-07: Phase 3 — importance estimation

- **Commit**: `a3ea792` feat(kb): add heuristic importance estimation for documents (Phase 3)
- **What**:
  - `doc_stats` table in graph DB: stores per-document `source_type`, `has_source`, `importance` (0.0–1.0)
  - `estimate_importance()`: heuristic scoring based on concept count (40%), concept rarity/avg-IDF (30%), source type weight (15%), external source presence (15%)
  - `compute_importance()`: batch scores all documents with concepts
  - `build_graph()` now populates `doc_stats` and computes importance in one pass
  - `_read_doc_concepts()` renamed to `_read_doc_info()` returning dict with source metadata
  - CLI `kb graph build` prints importance scoring count
  - 7 new tests (5 unit + 2 integration), 80 total pass, lint clean
- **Decisions**:
  - Importance is heuristic-only for now (no AI); formula weights concept connectivity and rarity most heavily
  - Source type weights: paper=1.0, pdf=0.8, repo=0.7, web=0.4, memo=0.0
  - Docs without concepts get importance=0.0 (not scored)
  - `doc_stats` table separate from FTS `docs` virtual table (FTS columns are immutable)
- **Phase 3 status**: importance estimation complete. Remaining: source-type prompts, Git repo/paper/PDF parsing, concept note auto-generation

## 2026-06-07: Phase 3 — concept note auto-generation

- **Commit**: `da65d56` feat(kb): add concept note suggestion and generation (Phase 3)
- **What**: `src/kb/concepts.py` with `suggest_concept_notes()` (finds concepts with df >= min_df lacking a `.md` in `records/concept/`) and `generate_concept_note()` (creates stub note). CLI `kb concepts suggest [--min-df N] [--generate]`.
- **Decisions**:
  - Concept notes are stubs (label, df, representative doc titles) — human or AI fills in details later
  - Suggestion threshold defaults to min_df=2 (same as active graph term threshold)
- **Phase 3 status**: concept notes complete. Remaining: source-type prompts, Git repo/paper/PDF parsing

## 2026-06-07: Phase 3 — analyzer prompt framework

- **Commit**: `518364b` feat(kb): add analyzer prompt framework with source-type templates (Phase 3)
- **What**:
  - `prompts/` directory: base prompt (`analyzer_base.md`) + 5 source-type-specific templates (web, paper, repo, pdf, memo)
  - `src/kb/analyzer.py`: prompt loading by source type (`load_prompt`), analysis request building (`build_analysis_prompt`), body formatting with metadata (`format_body_for_analysis`), response parsing (`parse_analysis_response` → `AnalysisResult` dataclass), front matter update builder (`build_front_matter_update`)
  - CLI: `kb analyze DOC_ID [--raw-json]` — generates ready-to-send analysis prompt
  - 25 new tests, 113 total pass, lint clean
- **Decisions**:
  - `git_repo` maps to same prompt as `repo`; `video` maps to `web` as fallback
  - Prompt files are plain Markdown (no template engine needed) — simple and versionable
  - AnalysisResult uses dataclasses for type-safe access; supports both dict and string concept formats in JSON
  - Front matter update uses `concept:` prefix for concept IDs (matches GOAL.md schema)
- **Phase 3 status**: source-type prompts complete. Remaining: Git repo/paper/PDF parsing

## 2026-06-07: Phase 3 — Git repo parser

- **Commit**: `6862f5b` feat(kb): add Git repo parser and kb add-repo command (Phase 3)
- **What**:
  - `src/kb/parsers/repo.py`: GitHub repo metadata extraction via `gh api` (description, language, stars, topics), README fetching via base64 decode, structured `parse_repo()` returning title/source/body/metadata dict
  - `kb add-repo URL` CLI command: fetches repo, creates `git_repo` document with `repo_language`, `repo_stars`, `repo_topics` in front matter
  - URL parser handles https, ssh, and `owner/repo` shorthand
- **Decisions**:
  - Uses `gh` CLI subprocess rather than Python HTTP library — no new dependencies, leverages existing auth
  - `parsers/` package for extensibility (paper, PDF parsers will go here)
  - Repo metadata stored in front matter for indexability; README goes in body
- **Phase 3 status**: source-type prompts + Git repo parsing complete. Remaining: paper parsing, PDF parsing

## 2026-06-07: Phase 3 — paper parser

- **Commit**: `71045f6` feat(kb): add paper parser with arXiv and DOI support (Phase 3)
- **What**: `src/kb/parsers/paper.py` with arXiv API and CrossRef DOI API support. Handles arXiv URLs/IDs (new and old style), DOI URLs/bare DOIs, and raw paper titles. CLI `kb add-paper REF`.
- **Decisions**:
  - Uses stdlib `urllib.request` and `xml.etree` — no new HTTP/XML dependencies
  - `_classify_input()` tries arXiv first, then DOI, then raw fallback
- **Phase 3 status**: paper parsing complete. Remaining: PDF parsing

## 2026-06-07: Phase 3 — PDF parser (Phase 3 complete)

- **Commit**: `abb5185` feat(kb): add PDF parser with text extraction and kb add-pdf command (Phase 3)
- **What**:
  - `src/kb/parsers/pdf.py`: text and metadata extraction from local PDF files via `pypdf`
  - `kb add-pdf PATH [--source URL] [--content NOTES]` CLI command
  - Extracts title, author, page count, producer from PDF metadata; falls back to filename
  - Extracts text up to 5000 chars (per GOAL.md: no full text in Git)
  - Creates `pdf` source_type documents with `pdf_pages`, `pdf_author` front matter fields
  - 11 new tests, 169 total pass, lint clean
- **Decisions**:
  - Uses `pypdf` (pure Python, no binary deps) for PDF text extraction
  - Text truncated at 5000 chars per GOAL.md policy (no full text stored in Git)
  - Optional `--source` flag for external storage URL (S3/R2) where raw PDF is kept
  - PDF metadata (title, author) extracted from embedded PDF info dict; filename used as fallback
- **Phase 3 status**: ALL COMPLETE. Importance estimation, concept notes, source-type prompts, Git repo/paper/PDF parsing all done

## 2026-06-07: Phase 4 — local web UI

- **Commits**: `ca65747`, `772b315`, `76e45c1`
- **What**:
  - `src/kb/web.py`: Flask app with search page, document detail, related documents, categories, concept browser, interactive concept graph visualization
  - `/api/search`, `/api/stats`, `/api/graph` JSON endpoints
  - `/graph` page: D3.js force-directed graph (loaded from CDN) showing concept nodes (sized by df) and document nodes with draggable interaction, tooltips, and click-through to detail pages
  - Navigation bar on all pages with links to Search, Categories, Concepts, Graph
  - Concept tags on doc detail pages link to concept detail pages
  - `kb serve` CLI command
  - 191 total tests pass, lint clean
- **Decisions**:
  - D3.js loaded from CDN (no npm/JS build step)
  - Graph limited to top 50 concepts by df and 100 connected docs for performance
  - Node IDs prefixed with `c:` or `d:` to avoid collisions between concept and doc IDs
- **Phase 4 status**: ALL COMPLETE. Search, document detail, related docs, category browse, concept browse, concept graph visualization all done

## 2026-06-07: Phase 5 — graph health dashboard

- **What**:
  - `src/kb/health.py`: `compute_health()` gathers overview stats (total docs/concepts/edges, orphan docs, isolated docs), source type breakdown, importance distribution (high/medium/low/unscored), top 20 concepts by df, concepts missing notes (df>=2), and metrics (avg concepts/doc, avg edges/doc, connectivity ratio)
  - `kb health [--json]` CLI command: human-readable or JSON output
  - Web UI: `/health` dashboard page with stat cards, source type bar chart, importance distribution, metrics grid, top concepts; `/api/health` JSON endpoint
  - All navbar links updated to include Health
  - 11 new tests, 202 total pass, lint clean
- **Decisions**:
  - Health module calls `init_graph_tables()` to ensure tables exist even on empty DBs
  - Orphan docs = no concepts; isolated docs = no edges; both are quality indicators
  - Concepts missing notes counted only for df>=2 (same threshold as active graph terms)
  - Connectivity ratio = fraction of docs that have at least one edge
- **Phase 5 status**: graph health dashboard complete. Remaining: vector index, Parquet export, S3/R2 raw storage, shard repo, batch reanalysis

## 2026-06-09: Phase 5 — Parquet export

- **Commit**: `138ad03` feat(kb): add Parquet export command for external analysis (Phase 5)
- **What**:
  - `src/kb/export.py`: exports all 5 graph tables (documents, concepts, doc_concepts, edges, doc_stats) from SQLite to Parquet files using pyarrow
  - `kb export [-o DIR]` CLI command; default output `.kb/exports/`
  - `pyarrow>=14.0` added as optional `[export]` dependency
  - 6 new tests, 208 total pass, lint clean
- **Decisions**:
  - pyarrow is optional dependency (not all users need Parquet export)
  - Export writes one `.parquet` file per table; column types inferred by pyarrow
  - Default output to `.kb/exports/` (regeneratable cache, not committed to Git)
- **Phase 5 status**: graph health dashboard + Parquet export complete. Remaining: vector index, S3/R2 raw storage, shard repo, batch reanalysis

## 2026-06-09: Phase 5 — TF-IDF vector index for semantic search

- **Commit**: `fa1e76c` feat(kb): add TF-IDF vector index for semantic search (Phase 5)
- **What**:
  - `src/kb/vectors.py`: hash-based fixed-dimension (4096) TF-IDF vectors with L2 normalization and cosine similarity search
  - `kb vectorize` CLI command to build vector index from FTS-indexed documents
  - `kb search --semantic` flag for semantic search mode (enriched with titles from FTS index)
  - `numpy>=1.24` added as optional `[vector]` dependency
  - 12 new tests, 220 total pass, lint clean
- **Decisions**:
  - Hash-based vectorization (MD5 of token → bucket index) avoids vocabulary management and keeps fixed-size vectors
  - Smoothed IDF formula `log(1 + N/(df+1))` prevents zero-IDF with few documents
  - numpy is optional dependency — vectorize and semantic search give clear error without it
  - Vectors stored as `.npz` in `.kb/` (regeneratable cache, gitignored)
- **Phase 5 status**: graph health dashboard + Parquet export + vector index complete. Remaining: S3/R2 raw storage, shard repo, batch reanalysis

## 2026-06-09: CLI spec gap — kb show

- **Commit**: `826f83e` feat(kb): add kb show command to display document metadata and body
- **What**:
  - `kb show <doc_id>` CLI command: finds document file, parses front matter, displays id/title/source_type/source/created/updated/concepts/importance/body
  - Body truncated at 20 lines by default; `--full` flag shows entire body
  - Uses existing `_parse_front_matter_yaml` from `graph.py` and same file-walk lookup as `kb analyze`
  - 6 new tests, 226 total pass, lint clean
- **Decisions**:
  - Same O(files) walk as `kb analyze` — acceptable at current scale, index-based lookup can be added later
  - Concepts field supports both comma-separated string and YAML list formats
- **Spec gap remaining**: `kb concept <concept_id>` still missing from GOAL.md section 15

## 2026-06-09: CLI spec gap — kb concept

- **Commit**: `0eeb3be` feat(kb): add kb concept command to display concept metadata and documents
- **What**:
  - `kb concept <concept_id>` CLI command: looks up concept in SQLite `concepts` table, displays id/label/kind/df/stop status
  - Lists associated documents (up to 20) with titles from FTS index
  - Shows concept note from `records/concept/<concept_id>.md` if it exists
  - 5 new tests, 231 total pass, lint clean
- **Decisions**:
  - Reuses existing SQLite schema (concepts, doc_concepts, docs tables)
  - Documents limited to 20 results; concept note displayed inline below separator
- **GOAL.md section 15 CLI spec status**: all commands now implemented (init, add, ingest, index, search, show, related, concept, export, sync)
