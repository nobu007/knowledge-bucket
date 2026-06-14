"""Tests for training-data generation (generate.py)."""

import json
import os
from unittest.mock import patch

from kb.generate import (
    _to_output,
    generate_for_doc,
    select_docs,
)


def _write_doc(tmp, name, source_type, concepts_yaml, body):
    import hashlib
    from kb.core import generate_ulid
    ulid = generate_ulid()
    doc_dir = os.path.join(tmp, "records", "doc", name[:2], name[2:4])
    os.makedirs(doc_dir, exist_ok=True)
    path = os.path.join(doc_dir, f"{ulid}.md")
    with open(path, "w") as f:
        f.write(
            f"---\nid: {ulid}\ntitle: Test {name}\nsource_type: {source_type}\n"
            f"source_key: url:x-{name}\nsummary: A {name} summary.\n"
            f"concepts:\n{concepts_yaml}---\n\n{body}\n"
        )
    return path


class TestSelectDocs:
    def test_filter_by_source_type(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        _write_doc(str(d), "aaa", "web", "", "body a")
        _write_doc(str(d), "bbb", "paper", "", "body b")
        web = select_docs(str(d), source_type="web")
        assert len(web) == 1
        assert "aaa" not in web[0]  # path uses ULID, not the name seed

    def test_filter_by_concept(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        _write_doc(str(d), "ccc", "web", "  primary:\n    - id: concept:rag\n", "body")
        _write_doc(str(d), "ddd", "web", "  primary:\n    - id: concept:llm\n", "body")
        rag = select_docs(str(d), concept="rag")
        assert len(rag) == 1

    def test_limit(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        _write_doc(str(d), "e1", "web", "", "b")
        _write_doc(str(d), "e2", "web", "", "b")
        _write_doc(str(d), "e3", "web", "", "b")
        assert len(select_docs(str(d), source_type="web", limit=2)) == 2


class TestToOutput:
    def test_openai_format(self):
        rec = _to_output(
            {"instruction": "What is X?", "context": "", "response": "X is Y."},
            "openai", "sys",
        )
        assert rec["messages"][0]["role"] == "system"
        assert rec["messages"][1]["content"] == "What is X?"
        assert rec["messages"][2]["content"] == "X is Y."

    def test_alpaca_format(self):
        rec = _to_output(
            {"instruction": "Q", "context": "ctx", "response": "A"},
            "alpaca", "sys",
        )
        assert rec == {"instruction": "Q", "input": "ctx", "output": "A"}


class TestGenerateForDoc:
    def test_parses_agent_pairs_and_dedups(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        path = _write_doc(str(d), "gen", "web", "", "the body content")
        agent_out = json.dumps([
            {"difficulty": "basic", "instruction": "What is it?", "context": "", "response": "It is X.", "tags": ["x"]},
            {"difficulty": "advanced", "instruction": "Compare A and B.", "context": "", "response": "A is faster.", "tags": []},
            {"difficulty": "basic", "instruction": "What is it?", "context": "", "response": "dup", "tags": []},  # dup
            {"difficulty": "basic", "instruction": "", "context": "", "response": "no instr", "tags": []},  # skip
        ])
        with patch("kb.generate.call_agent", return_value=agent_out):
            recs = generate_for_doc(str(path), n_pairs=4, fmt="openai")
        assert len(recs) == 2  # dup + empty filtered
        assert recs[0]["messages"][1]["content"] == "What is it?"
        assert recs[0]["_source_doc"]
        assert recs[0]["_difficulty"] == "basic"

    def test_fenced_json_handled(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        path = _write_doc(str(d), "fence", "web", "", "body")
        fenced = "```json\n" + json.dumps([
            {"instruction": "Q", "context": "", "response": "A", "difficulty": "basic", "tags": []},
        ]) + "\n```"
        with patch("kb.generate.call_agent", return_value=fenced):
            recs = generate_for_doc(str(path), n_pairs=1, fmt="openai")
        assert len(recs) == 1

    def test_invalid_json_raises(self, tmp_path):
        d = tmp_path / "kb"
        d.mkdir()
        path = _write_doc(str(d), "bad", "web", "", "body")
        with patch("kb.generate.call_agent", return_value="not json at all"):
            try:
                generate_for_doc(str(path), n_pairs=1)
                assert False, "should raise"
            except RuntimeError:
                pass
