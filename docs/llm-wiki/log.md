# GoalDev Log

Append durable decisions and completed goal progress here.

## 2026-06-07: Phase 1 foundation committed

- **Commit**: `7abfedc` feat(kb): implement kb init and kb add with ULID + hash-sharded paths
- **What**: Python package scaffold (`src/kb/`), `kb init` (creates dirs + config), `kb add` (ULID + sha256 shard + front matter Markdown). 15 tests, lint clean.
- **Decisions**:
  - Monotonic ULID: same-ms calls increment random component instead of re-sampling
  - `kb_root()` uses `os.path.realpath()` to handle macOS `/var` → `/private/var` symlink
  - `kb.yml` is the root marker (walks up from cwd)
