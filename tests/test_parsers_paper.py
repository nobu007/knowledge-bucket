"""Tests for src/kb/parsers/paper.py."""

import json
from unittest.mock import patch

import pytest

from kb.parsers.paper import (
    _classify_input,
    _parse_arxiv_id,
    _parse_doi,
    fetch_arxiv_metadata,
    fetch_doi_metadata,
    parse_paper,
)

# --- arXiv ID parsing ---


class TestParseArxivId:
    def test_new_style_bare(self):
        assert _parse_arxiv_id("2301.12345") == "2301.12345"

    def test_new_style_with_version(self):
        assert _parse_arxiv_id("2301.12345v2") == "2301.12345v2"

    def test_new_style_abs_url(self):
        assert _parse_arxiv_id("https://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_new_style_pdf_url(self):
        assert _parse_arxiv_id("https://arxiv.org/pdf/2301.12345") == "2301.12345"

    def test_new_style_html_url(self):
        assert _parse_arxiv_id("https://arxiv.org/html/2301.12345v3") == "2301.12345v3"

    def test_old_style_bare(self):
        assert _parse_arxiv_id("hep-th/9901001") == "hep-th/9901001"

    def test_old_style_url(self):
        assert _parse_arxiv_id("https://arxiv.org/abs/hep-th/9901001") == "hep-th/9901001"

    def test_not_arxiv(self):
        assert _parse_arxiv_id("10.1234/something") is None

    def test_random_text(self):
        assert _parse_arxiv_id("not an id") is None


# --- DOI parsing ---


class TestParseDoi:
    def test_bare_doi(self):
        assert _parse_doi("10.1234/test.5678") == "10.1234/test.5678"

    def test_doi_url(self):
        assert _parse_doi("https://doi.org/10.1234/test.5678") == "10.1234/test.5678"

    def test_complex_doi(self):
        doi = "10.1103/PhysRevLett.132.123401"
        assert _parse_doi(doi) == doi

    def test_not_doi(self):
        assert _parse_doi("2301.12345") is None

    def test_random_text(self):
        assert _parse_doi("not a doi") is None


# --- Input classification ---


class TestClassifyInput:
    def test_arxiv_bare(self):
        kind, ident = _classify_input("2301.12345")
        assert kind == "arxiv"
        assert ident == "2301.12345"

    def test_arxiv_url(self):
        kind, ident = _classify_input("https://arxiv.org/abs/2301.12345")
        assert kind == "arxiv"

    def test_doi_bare(self):
        kind, ident = _classify_input("10.1234/test")
        assert kind == "doi"

    def test_doi_url(self):
        kind, ident = _classify_input("https://doi.org/10.1234/test")
        assert kind == "doi"

    def test_raw_fallback(self):
        kind, ident = _classify_input("Some Paper Title")
        assert kind == "raw"
        assert ident == "Some Paper Title"


# --- arXiv metadata fetch ---


_ARXIV_XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Attention Is All You Need</title>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <summary>The dominant sequence transduction models are based on
    complex recurrent or convolutional neural networks.</summary>
    <published>2017-06-12T00:00:00Z</published>
    <link title="pdf" href="https://arxiv.org/pdf/1706.03762v1"/>
  </entry>
</feed>
"""

_ARXIV_EMPTY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>
"""


class TestFetchArxivMetadata:
    @patch("kb.parsers.paper._fetch_url")
    def test_success(self, mock_fetch):
        mock_fetch.return_value = _ARXIV_XML_TEMPLATE.encode()
        result = fetch_arxiv_metadata("1706.03762")
        assert result["title"] == "Attention Is All You Need"
        assert result["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
        assert "dominant sequence" in result["abstract"]
        assert result["published"] == "2017-06-12"
        assert "1706.03762" in result["pdf_url"]

    @patch("kb.parsers.paper._fetch_url")
    def test_not_found(self, mock_fetch):
        mock_fetch.return_value = _ARXIV_EMPTY_XML.encode()
        with pytest.raises(RuntimeError, match="No arXiv entry"):
            fetch_arxiv_metadata("0000.00000")


# --- DOI metadata fetch ---


class TestFetchDoiMetadata:
    @patch("kb.parsers.paper._fetch_url")
    def test_success(self, mock_fetch):
        crossref = {
            "message": {
                "title": ["Deep Residual Learning"],
                "author": [
                    {"given": "Kaiming", "family": "He"},
                    {"given": "Xiangyu", "family": "Zhang"},
                ],
                "abstract": "Deeper neural networks are harder to train.",
                "published-print": {"date-parts": [[2016, 6, 1]]},
                "container-title": ["CVPR"],
                "URL": "https://doi.org/10.1109/CVPR.2016.1",
            },
        }
        mock_fetch.return_value = json.dumps(crossref).encode()
        result = fetch_doi_metadata("10.1109/CVPR.2016.1")
        assert result["title"] == "Deep Residual Learning"
        assert result["authors"] == ["Kaiming He", "Xiangyu Zhang"]
        assert "harder to train" in result["abstract"]
        assert result["published"] == "2016-6-1"
        assert result["container"] == "CVPR"

    @patch("kb.parsers.paper._fetch_url")
    def test_minimal_fields(self, mock_fetch):
        crossref = {"message": {"title": [], "author": []}}
        mock_fetch.return_value = json.dumps(crossref).encode()
        result = fetch_doi_metadata("10.0000/nothing")
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["published"] == ""


# --- parse_paper integration ---


class TestParsePaper:
    @patch("kb.parsers.paper.fetch_arxiv_metadata")
    def test_arxiv(self, mock_arxiv):
        mock_arxiv.return_value = {
            "title": "Test Paper",
            "authors": ["Alice", "Bob"],
            "abstract": "An abstract.",
            "arxiv_id": "2301.12345",
            "published": "2023-01-15",
            "pdf_url": "https://arxiv.org/pdf/2301.12345",
        }
        result = parse_paper("2301.12345")

        assert result["source_type"] == "paper"
        assert result["source_url"] == "https://arxiv.org/abs/2301.12345"
        assert "Test Paper" == result["title"]
        assert "**Authors:** Alice, Bob" in result["body"]
        assert "## Abstract" in result["body"]
        assert result["metadata"]["arxiv_id"] == "2301.12345"

    @patch("kb.parsers.paper.fetch_doi_metadata")
    def test_doi(self, mock_doi):
        mock_doi.return_value = {
            "title": "ResNet Paper",
            "authors": ["Kaiming He"],
            "abstract": "Deep networks.",
            "doi": "10.1109/CVPR.2016.1",
            "published": "2016-6",
            "container": "CVPR",
            "url": "https://doi.org/10.1109/CVPR.2016.1",
        }
        result = parse_paper("10.1109/CVPR.2016.1")

        assert result["source_type"] == "paper"
        assert "ResNet Paper" == result["title"]
        assert "**Journal:** CVPR" in result["body"]
        assert result["metadata"]["doi"] == "10.1109/CVPR.2016.1"

    def test_raw_with_content(self):
        result = parse_paper("My Custom Paper", content="Some notes here")

        assert result["source_type"] == "paper"
        assert result["title"] == "My Custom Paper"
        assert "Some notes here" in result["body"]
        assert result["source_url"] == ""

    @patch("kb.parsers.paper.fetch_arxiv_metadata")
    def test_arxiv_with_user_notes(self, mock_arxiv):
        mock_arxiv.return_value = {
            "title": "Paper Title",
            "authors": ["Author"],
            "abstract": "Abs.",
            "arxiv_id": "2301.00001",
            "published": "2023-01-01",
            "pdf_url": "",
        }
        result = parse_paper("2301.00001", content="My personal notes")
        assert "## Notes" in result["body"]
        assert "My personal notes" in result["body"]


class TestParsePaperDocShape:
    """Verify document dict shape matches repo parser contract."""

    @patch("kb.parsers.paper.fetch_arxiv_metadata")
    def test_shape(self, mock_arxiv):
        mock_arxiv.return_value = {
            "title": "T",
            "authors": [],
            "abstract": "A",
            "arxiv_id": "2301.00001",
            "published": "",
            "pdf_url": "",
        }
        result = parse_paper("2301.00001")
        assert set(result.keys()) == {
            "title", "source_url", "source_type", "body", "metadata",
        }
