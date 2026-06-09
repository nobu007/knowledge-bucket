"""Analyzer framework: prompt loading, analysis request building, response parsing."""

import json
import os
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
    }
