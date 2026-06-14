"""Tests for analyzer module: prompt loading, request building, response parsing."""

import json
import os
import tempfile
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from kb.analyzer import (
    AnalysisResult,
    ConceptRef,
    agent_proxy_bin,
    apply_analysis_to_doc,
    build_analysis_prompt,
    build_front_matter_update,
    call_agent,
    find_docs_without_analysis,
    format_body_for_analysis,
    get_api_key,
    load_base_prompt,
    load_prompt,
    parse_analysis_response,
    prompts_dir,
)


class TestLoadPrompt:
    def test_loads_base_prompt(self):
        text = load_base_prompt()
        assert "情報圧縮器" in text
        assert "primary_concepts" in text

    def test_loads_web_prompt(self):
        text = load_prompt("web")
        assert "Web記事" in text or "web" in text.lower()

    def test_loads_paper_prompt(self):
        text = load_prompt("paper")
        assert "論文" in text or "Paper" in text

    def test_loads_repo_prompt(self):
        text = load_prompt("repo")
        assert "リポジトリ" in text or "Repository" in text

    def test_git_repo_alias(self):
        text = load_prompt("git_repo")
        repo_text = load_prompt("repo")
        assert text == repo_text

    def test_loads_pdf_prompt(self):
        text = load_prompt("pdf")
        assert "PDF" in text

    def test_loads_memo_prompt(self):
        text = load_prompt("memo")
        assert "メモ" in text

    def test_unknown_type_defaults_to_web(self):
        text = load_prompt("unknown_type")
        web_text = load_prompt("web")
        assert text == web_text

    def test_prompts_dir_exists(self):
        import os
        assert os.path.isdir(prompts_dir())


class TestBuildAnalysisPrompt:
    def test_includes_base_and_specific(self):
        prompt = build_analysis_prompt("web", "Test Article", "Some body text")
        assert "情報圧縮器" in prompt
        assert "Web記事" in prompt or "web" in prompt.lower()
        assert "Test Article" in prompt
        assert "Some body text" in prompt

    def test_includes_source_url(self):
        prompt = build_analysis_prompt(
            "web", "Title", "Body", source_url="https://example.com",
        )
        assert "https://example.com" in prompt

    def test_no_source_url(self):
        prompt = build_analysis_prompt("memo", "My Note", "Some thoughts")
        assert "Source:" not in prompt

    def test_paper_prompt(self):
        prompt = build_analysis_prompt("paper", "RAG Paper", "Abstract here")
        assert "論文" in prompt or "Paper" in prompt
        assert "RAG Paper" in prompt


class TestFormatBodyForAnalysis:
    def test_paper_with_metadata(self):
        result = format_body_for_analysis(
            "paper", "Abstract text here",
            metadata={"authors": "Smith et al.", "doi": "10.1234/5678"},
        )
        assert "Authors: Smith et al." in result
        assert "DOI: 10.1234/5678" in result
        assert "Abstract text here" in result

    def test_paper_with_arxiv(self):
        result = format_body_for_analysis(
            "paper", "Text",
            metadata={"arxiv_id": "2401.12345"},
        )
        assert "arXiv: 2401.12345" in result

    def test_repo_with_metadata(self):
        result = format_body_for_analysis(
            "repo", "README content",
            metadata={"description": "A RAG framework", "language": "Python"},
        )
        assert "Description: A RAG framework" in result
        assert "Language: Python" in result

    def test_memo_no_metadata(self):
        result = format_body_for_analysis("memo", "Just a note")
        assert result == "Just a note"

    def test_web_no_metadata(self):
        result = format_body_for_analysis("web", "Article body")
        assert result == "Article body"


class TestAnalysisResult:
    def test_concept_ids(self):
        r = AnalysisResult(
            primary_concepts=[
                ConceptRef(id="retrieval-augmented-generation", label="RAG"),
                ConceptRef(id="knowledge-graph", label="Knowledge Graph"),
            ],
            candidate_concepts=[
                ConceptRef(id="graph-rag", label="GraphRAG"),
            ],
        )
        assert r.primary_concept_ids() == ["retrieval-augmented-generation", "knowledge-graph"]
        assert r.candidate_concept_ids() == ["graph-rag"]
        assert r.all_concept_ids() == [
            "retrieval-augmented-generation",
            "knowledge-graph",
            "graph-rag",
        ]


class TestParseAnalysisResponse:
    def test_full_response(self):
        data = {
            "title": "RAG Systems Survey",
            "summary": "A comprehensive survey of RAG systems.",
            "why_important": "Foundation for our knowledge management project.",
            "key_points": ["Point 1", "Point 2"],
            "primary_concepts": [
                {"id": "retrieval-augmented-generation", "label": "RAG"},
            ],
            "candidate_concepts": [
                {"id": "graph-rag", "label": "GraphRAG"},
            ],
            "display_tags": ["AI", "NLP"],
            "entities": [
                {"id": "tool:langchain", "label": "LangChain"},
            ],
            "confidence": 0.85,
            "importance": 0.7,
        }
        result = parse_analysis_response(json.dumps(data))
        assert result.title == "RAG Systems Survey"
        assert result.summary == "A comprehensive survey of RAG systems."
        assert len(result.primary_concepts) == 1
        assert result.primary_concepts[0].id == "retrieval-augmented-generation"
        assert len(result.candidate_concepts) == 1
        assert result.entities[0].id == "tool:langchain"
        assert result.confidence == 0.85
        assert result.importance == 0.7

    def test_minimal_response(self):
        data = {"title": "Note"}
        result = parse_analysis_response(json.dumps(data))
        assert result.title == "Note"
        assert result.summary == ""
        assert result.primary_concepts == []
        assert result.confidence == 0.0

    def test_string_concepts(self):
        data = {
            "primary_concepts": ["Retrieval Augmented Generation"],
        }
        result = parse_analysis_response(json.dumps(data))
        assert len(result.primary_concepts) == 1
        assert result.primary_concepts[0].id == "retrieval-augmented-generation"
        assert result.primary_concepts[0].label == "Retrieval Augmented Generation"

    def test_string_entities(self):
        data = {"entities": ["tool:sqlite"]}
        result = parse_analysis_response(json.dumps(data))
        assert len(result.entities) == 1
        assert result.entities[0].id == "tool:sqlite"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_analysis_response("not json")


class TestBuildFrontMatterUpdate:
    def test_generates_correct_structure(self):
        analysis = AnalysisResult(
            primary_concepts=[ConceptRef(id="rag", label="RAG")],
            candidate_concepts=[ConceptRef(id="graph-rag", label="GraphRAG")],
            display_tags=["AI", "NLP"],
            confidence=0.9,
            importance=0.8,
        )
        fm = build_front_matter_update(analysis, "01K2Z9P7Y8QWERTY1234567890", "web")
        assert fm["analysis"]["analyzer_version"] == "analyzer_v1"
        assert fm["analysis"]["confidence"] == 0.9
        assert fm["analysis"]["importance"] == 0.8
        assert len(fm["concepts"]["primary"]) == 1
        assert fm["concepts"]["primary"][0]["id"] == "concept:rag"
        assert fm["concepts"]["primary"][0]["weight"] == 1.0
        assert len(fm["concepts"]["candidates"]) == 1
        assert fm["concepts"]["candidates"][0]["weight"] == 0.5
        assert fm["tags_display"] == ["AI", "NLP"]
        assert fm["summary"] == ""


class TestCallAgent:
    def test_successful_call(self):
        completed = CompletedProcess(
            args=[], returncode=0, stdout='{"title": "Test"}', stderr="",
        )
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"), \
             patch("kb.analyzer.shutil.which", return_value="/usr/bin/node"), \
             patch("kb.analyzer.subprocess.run", return_value=completed):
            result = call_agent("test prompt")
        assert result == '{"title": "Test"}'

    def test_retries_on_transient_exit_code(self):
        transient = CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        ok = CompletedProcess(args=[], returncode=0, stdout='{"title": "OK"}', stderr="")
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"), \
             patch("kb.analyzer.shutil.which", return_value="/usr/bin/node"), \
             patch("kb.analyzer.subprocess.run", side_effect=[transient, ok]):
            result = call_agent("test prompt")
        assert result == '{"title": "OK"}'

    def test_raises_on_fatal_exit_code(self):
        fatal = CompletedProcess(args=[], returncode=127, stdout="", stderr="not found")
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"), \
             patch("kb.analyzer.shutil.which", return_value="/usr/bin/node"), \
             patch("kb.analyzer.subprocess.run", return_value=fatal):
            with pytest.raises(RuntimeError, match="rc=127"):
                call_agent("test prompt")

    def test_raises_when_proxy_missing(self):
        with patch("kb.analyzer.agent_proxy_bin", return_value=None):
            with pytest.raises(RuntimeError, match="not found"):
                call_agent("test prompt")

    def test_timeout_does_not_retry(self):
        import subprocess
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"), \
             patch("kb.analyzer.shutil.which", return_value="/usr/bin/node"), \
             patch("kb.analyzer.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)):
            with pytest.raises(RuntimeError, match="timed out"):
                call_agent("test prompt", timeout_sec=1)

    def test_passes_timeout_flag_to_proxy(self):
        completed = CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"), \
             patch("kb.analyzer.shutil.which", return_value="/usr/bin/node") as _w, \
             patch("kb.analyzer.subprocess.run", return_value=completed) as mock_run:
            call_agent("p", timeout_sec=120)
        cmd = mock_run.call_args.args[0]
        assert "--timeout" in cmd
        assert "120" in cmd


class TestGetApiKey:
    def test_returns_proxy_path_when_available(self):
        with patch("kb.analyzer.agent_proxy_bin", return_value="/fake/proxy.js"):
            assert get_api_key() == "/fake/proxy.js"

    def test_returns_none_when_proxy_missing(self):
        with patch("kb.analyzer.agent_proxy_bin", return_value=None):
            assert get_api_key() is None


class TestAgentProxyBin:
    def test_env_var_takes_precedence(self, tmp_path):
        proxy = tmp_path / "cli.js"
        proxy.write_text("#!/usr/bin/env node")
        with patch.dict(os.environ, {"KB_AGENT_PROXY": str(proxy)}):
            assert agent_proxy_bin() == str(proxy)

    def test_returns_none_when_unset_and_not_found(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("kb.analyzer._PROXY_CANDIDATES", []):
            assert agent_proxy_bin() is None


class TestAnalyzeParallel:
    def test_workers_1_runs_sequentially(self):
        from kb.analyzer import analyze_documents_parallel
        with patch("kb.analyzer.analyze_document") as mock_an:
            paths = ["/a.md", "/b.md", "/c.md"]
            ok, failures = analyze_documents_parallel(paths, workers=1)
        assert ok == 3
        assert failures == []
        assert mock_an.call_count == 3

    def test_workers_n_runs_concurrently(self):
        from kb.analyzer import analyze_documents_parallel
        with patch("kb.analyzer.analyze_document") as mock_an:
            paths = [f"/d{i}.md" for i in range(10)]
            ok, failures = analyze_documents_parallel(paths, workers=5)
        assert ok == 10
        assert failures == []
        assert mock_an.call_count == 10

    def test_failures_are_collected_not_raised(self):
        from kb.analyzer import analyze_documents_parallel

        def fake(path):
            if path == "/bad.md":
                raise RuntimeError("boom")

        with patch("kb.analyzer.analyze_document", side_effect=fake):
            ok, failures = analyze_documents_parallel(
                ["/good.md", "/bad.md"], workers=2,
            )
        assert ok == 1
        assert len(failures) == 1
        assert failures[0][0] == "/bad.md"
        assert "boom" in failures[0][1]


class TestApplyAnalysisToDoc:
    def test_writes_analysis_to_front_matter(self):
        analysis = AnalysisResult(
            summary="A test summary",
            primary_concepts=[ConceptRef(id="rag", label="RAG")],
            confidence=0.85,
            importance=0.7,
            display_tags=["AI"],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: 01K2Z9P7Y8QWERTY1234567890\n")
            f.write("title: Test Doc\nsource_type: web\n---\n\nBody text here.\n")
            path = f.name
        try:
            apply_analysis_to_doc(path, analysis)
            with open(path) as f:
                text = f.read()
            assert "summary: A test summary" in text
            assert "analysis:" in text
            assert "confidence: 0.85" in text
            assert "importance: 0.7" in text
            assert "concept:rag" in text
            assert "tags_display:" in text
            assert "Body text here." in text
        finally:
            os.unlink(path)

    def test_re_analysis_replaces_not_duplicates(self):
        """Re-analyzing a doc must not leave duplicate summary/analysis/concepts keys."""
        analysis = AnalysisResult(
            summary="Second summary",
            primary_concepts=[ConceptRef(id="rag", label="RAG")],
            confidence=0.9,
            importance=0.8,
            display_tags=["AI"],
        )
        pre_analyzed = (
            "---\nid: 01K2Z9P7Y8QWERTY1234567890\n"
            "title: Test\nsource_type: web\n"
            "summary: First summary\n"
            "analysis:\n  confidence: 0.5\n"
            "concepts:\n  primary:\n    - id: concept:old\n"
            "---\n\nBody.\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(pre_analyzed)
            path = f.name
        try:
            apply_analysis_to_doc(path, analysis)
            with open(path) as f:
                text = f.read()
            # Each analysis-authored key appears exactly once
            assert text.count("\nsummary:") == 1
            assert text.count("\nanalysis:") == 1
            assert text.count("\nconcepts:") == 1
            assert text.count("\ntags_display:") == 1
            # Old values replaced
            assert "First summary" not in text
            assert "Second summary" in text
            assert "concept:old" not in text
        finally:
            os.unlink(path)


class TestFindDocsWithoutAnalysis:
    def test_finds_unanalyzed_docs(self):
        from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path

        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            # Create unanalyzed doc
            ulid = generate_ulid()
            rel = shard_path(ulid)
            abs_dir = os.path.join(tmp, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
            os.makedirs(abs_dir, exist_ok=True)
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, rel)
            with open(doc_path, "w") as f:
                f.write(f"---\nid: {ulid}\ntitle: Test\n---\n\nBody\n")

            results = find_docs_without_analysis(tmp)
            assert len(results) == 1
            assert results[0][0] == ulid

    def test_skips_analyzed_docs(self):
        from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path

        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            ulid = generate_ulid()
            rel = shard_path(ulid)
            abs_dir = os.path.join(tmp, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
            os.makedirs(abs_dir, exist_ok=True)
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, rel)
            with open(doc_path, "w") as f:
                f.write(f"---\nid: {ulid}\ntitle: Test\n")
                f.write("analysis:\n  confidence: 0.9\n  importance: 0.8\n")
                f.write("---\n\nBody.\n")

            results = find_docs_without_analysis(tmp)
            assert len(results) == 0

    def test_body_mentioning_confidence_is_not_false_skip(self):
        """A doc whose BODY mentions "analysis:"/"confidence:" but whose front
        matter lacks analysis.confidence must be flagged as needing analysis."""
        from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path

        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            ulid = generate_ulid()
            rel = shard_path(ulid)
            abs_dir = os.path.join(tmp, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
            os.makedirs(abs_dir, exist_ok=True)
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, rel)
            with open(doc_path, "w") as f:
                f.write(f"---\nid: {ulid}\ntitle: Test\n---\n\n")
                f.write("This doc discusses analysis: and confidence: in its body\n")
                f.write("but has no analysis front matter at all.\n")

            results = find_docs_without_analysis(tmp)
            assert len(results) == 1
            assert results[0][0] == ulid

    def test_front_matter_confidence_zero_still_analyzed(self):
        """analysis.confidence of 0 is a real value (not None), so the doc is
        treated as analyzed and must NOT be flagged."""
        from kb.core import DOC_DIR, RECORDS_DIR, ensure_dirs, generate_ulid, shard_path

        with tempfile.TemporaryDirectory() as tmp:
            ensure_dirs(tmp)
            ulid = generate_ulid()
            rel = shard_path(ulid)
            abs_dir = os.path.join(tmp, RECORDS_DIR, DOC_DIR, os.path.dirname(rel))
            os.makedirs(abs_dir, exist_ok=True)
            doc_path = os.path.join(tmp, RECORDS_DIR, DOC_DIR, rel)
            with open(doc_path, "w") as f:
                f.write(
                    f"---\nid: {ulid}\ntitle: Test\n"
                    "analysis:\n  confidence: 0.0\n"
                    "---\n\nBody.\n"
                )

            results = find_docs_without_analysis(tmp)
            assert len(results) == 0
