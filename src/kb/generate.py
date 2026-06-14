"""Training-data generation: turn analyzed docs into SFT instruction pairs.

Uses the agent proxy (Claude Code) to read each document's summary + concepts +
body and emit N diverse, fact-grounded instruction/response pairs. Output is a
JSONL file in OpenAI-messages or Alpaca format, deduplicated and validated.
"""

import hashlib
import json
import logging
import os
import re
from importlib import resources

from .analyzer import _extract_json, call_agent
from .core import DOC_DIR, RECORDS_DIR

logger = logging.getLogger(__name__)

_OUTPUT_FORMATS = ("openai", "alpaca")


def _load_prompt() -> str:
    """Load the training-data generation prompt template from the package."""
    ref = resources.files("kb.prompts").joinpath("training_data.md")
    return ref.read_text(encoding="utf-8")


def _doc_body_excerpt(doc_path: str, max_chars: int = 4000) -> str:
    """Return the doc body (after front matter), truncated for the prompt."""
    with open(doc_path, encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    c = 0
    fm_end = -1
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            c += 1
            if c == 2:
                fm_end = i
                break
    body = "\n".join(lines[fm_end + 1:]) if fm_end >= 0 else text
    return body[:max_chars]


def _extract_fm_field(doc_path: str, field: str) -> str:
    with open(doc_path, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith(f"{field}:"):
                return ln.split(":", 1)[1].strip().strip('"')
            if ln.strip() == "---" and any(
                l.startswith("id:") for l in open(doc_path)
            ):
                # past front matter start; allow one --- close
                pass
    return ""


def _doc_meta(doc_path: str) -> dict:
    """Cheap front-matter field reader for title/source_type/summary/concepts."""
    with open(doc_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    meta = {}
    if m:
        for ln in m.group(1).split("\n"):
            if ":" in ln and not ln.startswith(" "):
                k, _, v = ln.partition(":")
                meta[k.strip()] = v.strip().strip('"')
    return meta


def select_docs(root: str, *, concept: str | None = None,
                source_type: str | None = None, limit: int | None = None,
                ) -> list[str]:
    """Select doc paths matching the domain filter (concept substring or type)."""
    import yaml

    doc_dir = os.path.join(root, RECORDS_DIR, DOC_DIR)
    out = []
    for dp, _dn, fns in os.walk(doc_dir):
        for fn in sorted(fns):
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dp, fn)
            text = open(p, encoding="utf-8").read()
            m = re.search(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
            if not m:
                continue
            try:
                meta = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if source_type and meta.get("source_type") != source_type:
                continue
            if concept:
                blob = json.dumps(meta, ensure_ascii=False, default=str).lower()
                if concept.lower() not in blob:
                    continue
            out.append(p)
            if limit and len(out) >= limit:
                return out
    return out


def _build_prompt(doc_path: str, n_pairs: int) -> str:
    meta = _doc_meta(doc_path)
    tmpl = _load_prompt()
    concepts = meta.get("concepts", "")
    return (
        tmpl
        .replace("{title}", meta.get("title", ""))
        .replace("{source_type}", meta.get("source_type", "web"))
        .replace("{summary}", meta.get("summary", ""))
        .replace("{concepts}", str(concepts))
        .replace("{n_pairs}", str(n_pairs))
        .replace("{body}", _doc_body_excerpt(doc_path))
    )


def _to_output(pair: dict, fmt: str, system: str) -> dict:
    """Convert an internal pair to the requested output format."""
    instr = pair.get("instruction", "").strip()
    ctx = pair.get("context", "").strip()
    resp = pair.get("response", "").strip()
    user = f"{instr}\n\n{ctx}".strip() if ctx else instr
    if fmt == "alpaca":
        return {"instruction": instr, "input": ctx, "output": resp}
    return {"messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": resp},
    ]}


def generate_for_doc(doc_path: str, *, n_pairs: int = 5,
                     fmt: str = "openai", system: str = "",
                     seen: set[str] | None = None) -> list[dict]:
    """Generate training pairs for one document via the agent proxy."""
    if fmt not in _OUTPUT_FORMATS:
        raise ValueError(f"format must be one of {_OUTPUT_FORMATS}")
    prompt = _build_prompt(doc_path, n_pairs)
    raw = call_agent(prompt)
    try:
        pairs = json.loads(_extract_json(raw))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"agent did not return valid JSON pairs: {e}") from e
    if not isinstance(pairs, list):
        raise RuntimeError("agent did not return a JSON array")

    if not system:
        system = "あなたはドメイン特化のAIアシスタントです。提供された知識に基づき正確に答えてください。"
    seen = seen if seen is not None else set()
    out = []
    for p in pairs:
        if not isinstance(p, dict):
            continue
        instr = (p.get("instruction") or "").strip()
        resp = (p.get("response") or "").strip()
        if not instr or not resp:
            continue
        key = hashlib.sha1(instr.lower().encode()).hexdigest()[:12]
        if key in seen:
            continue
        seen.add(key)
        rec = _to_output(p, fmt, system)
        rec["_source_doc"] = os.path.basename(doc_path).removesuffix(".md")
        rec["_difficulty"] = p.get("difficulty", "")
        rec["_tags"] = p.get("tags", [])
        out.append(rec)
    return out


def generate(root: str, *, concept: str | None = None, source_type: str | None = None,
             n_pairs: int = 5, fmt: str = "openai", limit: int | None = None,
             output: str | None = None, system: str = "") -> dict:
    """Generate a training dataset from selected docs and write JSONL.

    Returns a summary dict: {docs, pairs, output, duplicates_skipped}.
    """
    docs = select_docs(root, concept=concept, source_type=source_type, limit=limit)
    if not docs:
        return {"docs": 0, "pairs": 0, "output": None, "duplicates_skipped": 0}

    out_path = output or os.path.join(root, ".kb", "training", _default_name(concept, source_type))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    seen: set[str] = set()
    total = 0
    dup_skipped = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for dp in docs:
            try:
                before = len(seen)
                recs = generate_for_doc(dp, n_pairs=n_pairs, fmt=fmt, system=system, seen=seen)
                dup_skipped += (n_pairs - (len(seen) - before)) - len(recs)
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    total += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("generate failed for %s: %s", dp, e)
    return {"docs": len(docs), "pairs": total, "output": out_path,
            "duplicates_skipped": max(dup_skipped, 0)}


def _default_name(concept: str | None, source_type: str | None) -> str:
    tag = concept or source_type or "all"
    tag = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-") or "all"
    return f"sft-{tag}.jsonl"
