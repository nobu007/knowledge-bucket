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
