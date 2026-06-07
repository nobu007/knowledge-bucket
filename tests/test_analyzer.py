"""Tests for analyzer module: prompt loading, request building, response parsing."""

import json

import pytest

from kb.analyzer import (
    AnalysisResult,
    ConceptRef,
    build_analysis_prompt,
    build_front_matter_update,
    format_body_for_analysis,
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
