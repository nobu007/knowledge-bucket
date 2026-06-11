"""Tests for analyzer module: prompt loading, request building, response parsing."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from kb.analyzer import (
    AnalysisResult,
    ConceptRef,
    apply_analysis_to_doc,
    build_analysis_prompt,
    build_front_matter_update,
    call_llm_api,
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


class TestCallLlmApi:
    def test_successful_call(self):
        mock_resp = json.dumps({
            "choices": [{"message": {"content": '{"title": "Test"}'}}],
        }).encode()

        with patch("kb.analyzer.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            mock_urlopen.return_value.read.return_value = mock_resp
            result = call_llm_api("test prompt", "fake-key")
        assert result == '{"title": "Test"}'

    def test_retry_on_429(self):
        import urllib.error

        error_429 = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {}, None,
        )
        mock_data = json.dumps({
            "choices": [{"message": {"content": '{"title": "OK"}'}}],
        }).encode()

        call_count = 0

        class MockResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def read(self):
                return mock_data

        def side_effect(req, timeout=60):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise error_429
            return MockResp()

        with patch("kb.analyzer.urllib.request.urlopen", side_effect=side_effect):
            with patch("kb.analyzer.time.sleep"):
                result = call_llm_api("test prompt", "fake-key")
        assert result == '{"title": "OK"}'

    def test_raises_on_non_429_error(self):
        import urllib.error

        error_500 = urllib.error.HTTPError(
            "url", 500, "Internal Server Error", {}, None,
        )

        with patch("kb.analyzer.urllib.request.urlopen", side_effect=error_500):
            with pytest.raises(urllib.error.HTTPError):
                call_llm_api("test prompt", "fake-key")


class TestGetApiKey:
    def test_returns_key(self):
        with patch.dict(os.environ, {"KB_LLM_API_KEY": "test-key"}):
            assert get_api_key() == "test-key"

    def test_returns_none_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            result = get_api_key()
            assert result is None


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
                f.write(f"---\nid: {ulid}\ntitle: Test\n---\n\n")
                f.write("analysis:\n  confidence: 0.9\n  importance: 0.8\n")

            results = find_docs_without_analysis(tmp)
            assert len(results) == 0
