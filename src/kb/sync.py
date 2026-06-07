"""kb sync: pull, ingest, index, stage, commit, push."""

import os
import subprocess

from .index import sync_index
from .ingest import ingest_inbox


def _git(*args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _has_staged_changes(cwd: str) -> bool:
    r = _git("diff", "--cached", "--quiet", cwd=cwd, check=False)
    return r.returncode != 0


def sync(root: str, message: str | None = None) -> dict:
    """Run the full sync pipeline. Returns a summary dict."""
    report: dict = {
        "pulled": False, "ingested": 0, "indexed": 0,
        "committed": False, "pushed": False,
    }

    # 1. git pull --rebase (best-effort; skip if no remote)
    pull = _git("pull", "--rebase", cwd=root, check=False)
    report["pulled"] = pull.returncode == 0

    # 2. sync index (after pull, new records may exist)
    report["indexed"] += sync_index(root)

    # 3. ingest inbox
    ingested = ingest_inbox(root)
    report["ingested"] = len(ingested)

    # 4. sync index again (new docs from ingest)
    report["indexed"] += sync_index(root)

    # 5-6. stage and commit (only directories that exist)
    for d in ("records", "config", "prompts"):
        if os.path.isdir(os.path.join(root, d)):
            _git("add", d, cwd=root, check=False)
    if _has_staged_changes(root):
        msg = message or "kb: sync"
        _git("commit", "-m", msg, cwd=root)
        report["committed"] = True

    # 7. git push
    push = _git("push", cwd=root, check=False)
    report["pushed"] = push.returncode == 0

    return report
