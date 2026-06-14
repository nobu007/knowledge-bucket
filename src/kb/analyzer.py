"""Analyzer framework: prompt loading, analysis request building, response parsing.

LLM calls go through ai-hub-agent-proxy (Claude Code primary, OpenCode fallback)
via subprocess — NOT a raw chat-completions API. Configure the proxy path with
the KB_AGENT_PROXY env var; otherwise it is auto-detected from common locations.
"""

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from importlib import resources

from .core import yaml_scalar

_SOURCE_TYPE_FILES: dict[str, str] = {
    "web": "analyzer_web.md",
    "paper": "analyzer_paper.md",
    "repo": "analyzer_repo.md",
    "git_repo": "analyzer_repo.md",
    "pdf": "analyzer_pdf.md",
    "memo": "analyzer_memo.md",
    "video": "analyzer_web.md",
}

_BASE_FILE = "analyzer_base.md"


def prompts_dir() -> str:
    """Return filesystem path to bundled prompts directory."""
    return str(resources.files("kb").joinpath("prompts"))


def load_base_prompt() -> str:
    return resources.files("kb.prompts").joinpath(_BASE_FILE).read_text(encoding="utf-8")


def load_prompt(source_type: str) -> str:
    filename = _SOURCE_TYPE_FILES.get(source_type, "analyzer_web.md")
    return resources.files("kb.prompts").joinpath(filename).read_text(encoding="utf-8")


def build_analysis_prompt(source_type: str, title: str, body: str,
                          source_url: str | None = None) -> str:
    base = load_base_prompt()
    specific = load_prompt(source_type)

    header = f"Title: {title}\n"
    if source_url:
        header += f"Source: {source_url}\n"
    header += "\n"

    return f"{base}\n\n---\n\n{specific}\n\n---\n\n## 入力\n\n{header}{body}"


def format_body_for_analysis(source_type: str, raw_content: str,
                             metadata: dict | None = None) -> str:
    parts: list[str] = []
    if source_type == "paper" and metadata:
        if metadata.get("authors"):
            parts.append(f"Authors: {metadata['authors']}")
        if metadata.get("doi"):
            parts.append(f"DOI: {metadata['doi']}")
        if metadata.get("arxiv_id"):
            parts.append(f"arXiv: {metadata['arxiv_id']}")
    elif source_type in ("repo", "git_repo") and metadata:
        if metadata.get("description"):
            parts.append(f"Description: {metadata['description']}")
        if metadata.get("language"):
            parts.append(f"Language: {metadata['language']}")
    if parts:
        return "\n".join(parts) + "\n\n" + raw_content
    return raw_content


@dataclass
class ConceptRef:
    id: str
    label: str


@dataclass
class EntityRef:
    id: str
    label: str


@dataclass
class AnalysisResult:
    title: str = ""
    summary: str = ""
    why_important: str = ""
    key_points: list[str] = field(default_factory=list)
    primary_concepts: list[ConceptRef] = field(default_factory=list)
    candidate_concepts: list[ConceptRef] = field(default_factory=list)
    display_tags: list[str] = field(default_factory=list)
    entities: list[EntityRef] = field(default_factory=list)
    confidence: float = 0.0
    importance: float = 0.0

    def primary_concept_ids(self) -> list[str]:
        return [c.id for c in self.primary_concepts]

    def candidate_concept_ids(self) -> list[str]:
        return [c.id for c in self.candidate_concepts]

    def all_concept_ids(self) -> list[str]:
        return self.primary_concept_ids() + self.candidate_concept_ids()


def _parse_concept_list(items: list[dict]) -> list[ConceptRef]:
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(ConceptRef(id=item.get("id", ""), label=item.get("label", "")))
        elif isinstance(item, str):
            slug = item.lower().replace(" ", "-")
            result.append(ConceptRef(id=slug, label=item))
    return result


def _parse_entity_list(items: list[dict]) -> list[EntityRef]:
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(EntityRef(id=item.get("id", ""), label=item.get("label", "")))
        elif isinstance(item, str):
            result.append(EntityRef(id=item, label=item))
    return result


def _extract_json(text: str) -> str:
    """Extract a JSON object from text that may be wrapped in markdown fences
    or have preamble/trailing text. Returns the raw JSON substring."""
    s = text.strip()

    # Strip markdown code fences: ```json\n...\n``` or ```\n...\n```
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()

    # If it still has surrounding text, grab the outermost {...} block.
    if not s.startswith("{"):
        start = s.find("{")
        if start == -1:
            return s  # let json.loads raise a clear error
        # find matching closing brace by depth counting
        depth = 0
        end = -1
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            s = s[start:end + 1]
    return s


def parse_analysis_response(json_str: str) -> AnalysisResult:
    data = json.loads(_extract_json(json_str))
    return AnalysisResult(
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        why_important=data.get("why_important", ""),
        key_points=data.get("key_points", []),
        primary_concepts=_parse_concept_list(data.get("primary_concepts", [])),
        candidate_concepts=_parse_concept_list(data.get("candidate_concepts", [])),
        display_tags=data.get("display_tags", []),
        entities=_parse_entity_list(data.get("entities", [])),
        confidence=float(data.get("confidence", 0.0)),
        importance=float(data.get("importance", 0.0)),
    )


def build_front_matter_update(analysis: AnalysisResult, ulid: str,
                              source_type: str) -> dict:
    concepts_dict: dict = {
        "primary": [
            {"id": f"concept:{c.id}", "label": c.label, "weight": 1.0}
            for c in analysis.primary_concepts
        ],
        "candidates": [
            {"id": f"concept:{c.id}", "label": c.label, "weight": 0.5}
            for c in analysis.candidate_concepts
        ],
    }
    if analysis.entities:
        concepts_dict["entities"] = [
            {"id": e.id, "label": e.label} for e in analysis.entities
        ]
    return {
        "analysis": {
            "analyzer_version": "analyzer_v1",
            "confidence": analysis.confidence,
            "importance": analysis.importance,
        },
        "concepts": concepts_dict,
        "tags_display": analysis.display_tags,
        "summary": analysis.summary,
    }


logger = logging.getLogger(__name__)

_AGENT_PROXY_ENV = "KB_AGENT_PROXY"
_PROXY_CANDIDATES = [
    os.path.expanduser("~/ai-hub_agent_proxy/dist/cli.js"),
    "/opt/ai-hub_agent_proxy/dist/cli.js",
]
_MAX_RETRIES = 3
_RETRY_EXIT_CODES = {1, 2}  # transient backend failures eligible for retry


def agent_proxy_bin() -> str | None:
    """Return path to ai-hub-agent-proxy dist/cli.js, or None if unavailable."""
    path = os.environ.get(_AGENT_PROXY_ENV)
    if path and os.path.isfile(path):
        return path
    for cand in _PROXY_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def get_api_key() -> str | None:
    """Backward-compat: returns a truthy marker when the agent proxy is available.

    The analyzer no longer uses API keys — it delegates to ai-hub-agent-proxy,
    which owns its own backend credentials. We keep this name so callers and the
    CLI can still gate on "is analysis available" without a sweeping rename.
    """
    return agent_proxy_bin()


def call_agent(prompt: str, *, timeout_sec: int = 3600) -> str:
    """Run the analysis prompt through ai-hub-agent-proxy and return stdout.

    Spawns `node <proxy>/dist/cli.js --quiet --timeout N -p <prompt>`. The proxy
    runs Claude Code (primary) with OpenCode fallback; secrets stay in the
    proxy's own .env. Default 1h timeout — LLM analysis of large documents can
    take many minutes. Retries on transient exit codes.
    """
    proxy = agent_proxy_bin()
    if not proxy:
        raise RuntimeError(
            "ai-hub-agent-proxy not found. Set KB_AGENT_PROXY to dist/cli.js."
        )
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node executable not found on PATH")

    # Give the proxy itself the same budget so its own SIGTERM grace window
    # aligns with our subprocess timeout. subprocess timeout is +60s headroom.
    cmd = [node, proxy, "--quiet", "--timeout", str(timeout_sec), "-p", prompt]
    subproc_timeout = timeout_sec + 60
    last_err = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=subproc_timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            # A genuine timeout means the agent is stuck; do not burn another
            # full budget retrying — surface it immediately.
            raise RuntimeError(
                f"agent proxy timed out (proxy budget {timeout_sec}s, "
                f"subprocess limit {subproc_timeout}s)"
            )
        if result.returncode == 0:
            return result.stdout
        if result.returncode in _RETRY_EXIT_CODES and attempt < _MAX_RETRIES:
            last_err = f"rc={result.returncode}: {result.stderr[:300]}"
            logger.warning("agent proxy transient failure (attempt %d): %s",
                           attempt + 1, last_err)
            continue
        raise RuntimeError(
            f"agent proxy failed (rc={result.returncode}): {result.stderr[:500]}"
        )
    raise RuntimeError(f"agent proxy failed after {_MAX_RETRIES + 1} attempts: {last_err}")


# Deprecated alias retained for any external callers; delegates to the agent proxy.
def call_llm_api(prompt: str, api_key: str | None = None) -> str:  # noqa: ARG001
    """Deprecated: use call_agent(). Kept for backward compatibility."""
    return call_agent(prompt)


def apply_analysis_to_doc(doc_path: str, analysis: AnalysisResult) -> None:
    """Write analysis results into the document's front matter."""
    with open(doc_path) as f:
        text = f.read()

    update = build_front_matter_update(
        analysis, _extract_ulid(text), _extract_source_type(text),
    )

    lines = text.split("\n")
    fm_end = _find_fm_end(lines)
    if fm_end < 0:
        return

    # Drop any prior analysis-authored keys (and their nested indented children)
    # so re-analysis replaces instead of duplicating them. The id/title/
    # source_*/created/updated/repo_*/raw_ref lines are top-level keys kept as-is.
    analysis_keys = {"summary", "analysis", "concepts", "tags_display"}
    kept: list[str] = []
    skipping = False
    for line in lines[:fm_end]:
        if line and not line[0].isspace() and not line.startswith("#"):
            # a top-level YAML key: "key: ..." or "key:"
            key = line.split(":", 1)[0].strip()
            skipping = key in analysis_keys
        if skipping and (line.startswith("  ") or line.strip() == ""):
            continue
        if skipping:
            continue
        kept.append(line)
    # Trim trailing blank lines we may have exposed
    while kept and kept[-1].strip() == "":
        kept.pop()

    new_lines = kept
    new_lines.append(f"summary: {yaml_scalar(update['summary'])}")
    new_lines.append("analysis:")
    for k, v in update["analysis"].items():
        new_lines.append(f"  {k}: {v}")
    new_lines.append("concepts:")
    for group in ("primary", "candidates"):
        items = update["concepts"].get(group, [])
        if not items:
            continue
        new_lines.append(f"  {group}:")
        for c in items:
            new_lines.append(f"    - id: {c['id']}")
            new_lines.append(f"      label: {yaml_scalar(c['label'])}")
            new_lines.append(f"      weight: {c['weight']}")
    if update["concepts"].get("entities"):
        new_lines.append("  entities:")
        for e in update["concepts"]["entities"]:
            new_lines.append(f"    - id: {e['id']}")
            new_lines.append(f"      label: {yaml_scalar(e['label'])}")
    if update.get("tags_display"):
        new_lines.append("tags_display:")
        for tag in update["tags_display"]:
            new_lines.append(f"  - {yaml_scalar(tag)}")
    new_lines.append("---")
    new_lines.extend(lines[fm_end:])

    with open(doc_path, "w") as f:
        f.write("\n".join(new_lines))


def analyze_document(doc_path: str, api_key: str | None = None) -> AnalysisResult | None:
    """Analyze a single document via ai-hub-agent-proxy and update its front matter.

    api_key is ignored (kept for backward compatibility); the proxy owns credentials.
    """
    with open(doc_path) as f:
        text = f.read()

    ulid = _extract_ulid(text)
    lines = text.split("\n")
    fm_end = _find_fm_end(lines)
    if fm_end < 0:
        return None

    body = "\n".join(lines[fm_end + 1:])
    title = _extract_fm_field(lines, "title") or ulid
    source_type = _extract_fm_field(lines, "source_type") or "web"
    source_url = _extract_fm_field(lines, "source")

    # Cap body length: huge READMEs (LLaMA-Factory, graphrag) can exceed the
    # proxy's command-line arg budget and fail to launch. 8000 chars keeps the
    # analysis tractable while preserving the salient content.
    if len(body) > 8000:
        body = body[:8000] + "\n\n[... truncated for analysis ...]"

    prompt = build_analysis_prompt(source_type, title, body.strip(), source_url)
    raw_response = call_agent(prompt)

    analysis = parse_analysis_response(raw_response)
    apply_analysis_to_doc(doc_path, analysis)
    return analysis


def _analyze_one(doc_path: str) -> tuple[str, str | None]:
    """Analyze a single doc, returning (doc_path, error_message_or_None).

    Each doc is independent (own file, own subprocess), so failures don't abort
    the batch — the parallel driver records the error and moves on.
    """
    try:
        analyze_document(doc_path)
        return (doc_path, None)
    except Exception as e:  # noqa: BLE001 — surface all failures to the driver
        return (doc_path, str(e))


def analyze_documents_parallel(
    doc_paths: list[str], *, workers: int = 1,
) -> tuple[int, list[tuple[str, str]]]:
    """Analyze many documents concurrently via ai-hub-agent-proxy.

    Each analysis is an independent subprocess call (I/O-bound on the agent
    backend), so a thread pool gives real wall-clock speedup. Returns
    (success_count, [(doc_path, error), ...]).

    Scaling: workers=N cuts wall-clock ~N× for the analysis phase — the dominant
    cost when ingesting thousands of sites (single-doc analysis is 1-2 min).
    """
    if workers <= 1:
        ok = 0
        failures: list[tuple[str, str]] = []
        for p in doc_paths:
            _p, err = _analyze_one(p)
            if err:
                failures.append((_p, err))
            else:
                ok += 1
        return ok, failures

    from concurrent.futures import ThreadPoolExecutor, as_completed

    ok = 0
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_analyze_one, p): p for p in doc_paths}
        for fut in as_completed(futures):
            _p, err = fut.result()
            if err:
                failures.append((_p, err))
            else:
                ok += 1
    return ok, failures


def find_docs_without_analysis(root: str) -> list[tuple[str, str]]:
    """Find documents missing analysis.confidence. Returns [(ulid, abs_path), ...]."""
    from .core import DOC_DIR, RECORDS_DIR

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    results = []
    for dirpath, _dirnames, filenames in os.walk(doc_dir):
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            with open(abs_path) as f:
                text = f.read()
            if "analysis:" not in text or "confidence:" not in text:
                ulid = _extract_ulid(text)
                if ulid:
                    results.append((ulid, abs_path))
    return results


def _find_fm_end(lines: list[str]) -> int:
    """Return the index of the closing --- of front matter, or -1."""
    if not lines or lines[0].strip() != "---":
        return -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return -1


def _extract_fm_field(lines: list[str], field: str) -> str | None:
    pat = re.compile(rf"^{field}:\s*(.+)$")
    for line in lines:
        m = pat.match(line)
        if m:
            return m.group(1).strip()
    return None


def _extract_ulid(text: str) -> str:
    m = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def _extract_source_type(text: str) -> str:
    m = re.search(r"^source_type:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else "web"
