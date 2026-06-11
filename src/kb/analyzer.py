"""Analyzer framework: prompt loading, analysis request building, response parsing."""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")

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
    return _PROMPTS_DIR


def load_base_prompt() -> str:
    path = os.path.join(_PROMPTS_DIR, _BASE_FILE)
    with open(path) as f:
        return f.read()


def load_prompt(source_type: str) -> str:
    filename = _SOURCE_TYPE_FILES.get(source_type, "analyzer_web.md")
    path = os.path.join(_PROMPTS_DIR, filename)
    with open(path) as f:
        return f.read()


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


def parse_analysis_response(json_str: str) -> AnalysisResult:
    data = json.loads(json_str)
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

_LLM_BASE_URL = os.environ.get("KB_LLM_BASE_URL", "https://api.openai.com/v1")
_LLM_MODEL = os.environ.get("KB_LLM_MODEL", "gpt-4o-mini")
_API_CALL_INTERVAL = 0.5
_MAX_RETRIES = 3


def get_api_key() -> str | None:
    return os.environ.get("KB_LLM_API_KEY")


def call_llm_api(prompt: str, api_key: str) -> str:
    """Call LLM chat completions API and return the assistant message content."""
    url = f"{_LLM_BASE_URL}/chat/completions"
    payload = json.dumps({
        "model": _LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _MAX_RETRIES:
                wait = (2 ** attempt) * 1.0
                logger.warning("429 rate-limited, retrying in %.1fs", wait)
                time.sleep(wait)
                continue
            raise
        time.sleep(_API_CALL_INTERVAL)
    raise RuntimeError("LLM API call failed after retries")


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

    new_lines = lines[:fm_end]
    new_lines.append(f"summary: {update['summary']}")
    new_lines.append("analysis:")
    for k, v in update["analysis"].items():
        new_lines.append(f"  {k}: {v}")
    new_lines.append("concepts:")
    for group in ("primary", "candidates"):
        for c in update["concepts"].get(group, []):
            new_lines.append(f"  {group}:")
            new_lines.append(f"    - id: {c['id']}")
            new_lines.append(f"      label: {c['label']}")
            new_lines.append(f"      weight: {c['weight']}")
    if update["concepts"].get("entities"):
        new_lines.append("  entities:")
        for e in update["concepts"]["entities"]:
            new_lines.append(f"    - id: {e['id']}")
            new_lines.append(f"      label: {e['label']}")
    if update.get("tags_display"):
        new_lines.append("tags_display:")
        for tag in update["tags_display"]:
            new_lines.append(f"  - {tag}")
    new_lines.append("---")
    new_lines.extend(lines[fm_end:])

    with open(doc_path, "w") as f:
        f.write("\n".join(new_lines))


def analyze_document(doc_path: str, api_key: str) -> AnalysisResult | None:
    """Analyze a single document via LLM API and update its front matter."""
    with open(doc_path) as f:
        text = f.read()

    ulid = _extract_ulid(text)
    lines = text.split("\n")
    fm_end = _find_fm_end(lines)
    if fm_end < 0:
        return None

    body = "\n".join(lines[fm_end:])
    title = _extract_fm_field(lines, "title") or ulid
    source_type = _extract_fm_field(lines, "source_type") or "web"
    source_url = _extract_fm_field(lines, "source")

    prompt = build_analysis_prompt(source_type, title, body.strip(), source_url)
    raw_response = call_llm_api(prompt, api_key)

    analysis = parse_analysis_response(raw_response)
    apply_analysis_to_doc(doc_path, analysis)
    return analysis


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
