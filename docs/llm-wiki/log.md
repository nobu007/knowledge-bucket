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
