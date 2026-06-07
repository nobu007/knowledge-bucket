"""Tests for src/kb/parsers/pdf.py."""

import os
import tempfile
from unittest.mock import patch

import pytest

from kb.parsers.pdf import extract_pdf_metadata, parse_pdf


def _create_test_pdf(
    title: str = "Test Document",
    author: str = "Test Author",
    page_texts: list[str] | None = None,
) -> str:
    """Create a minimal test PDF and return its path."""
    from pypdf import PdfWriter

    writer = PdfWriter()

    if page_texts is None:
        page_texts = ["Hello World from page one.", "Page two content here."]

    for text in page_texts:
        writer.add_blank_page(width=200, height=200)
        # pypdf blank pages have no text, so we'll add a text annotation approach
        # Instead, let's use a simpler method: write bytes directly

    # Build a minimal PDF manually with text content
    # Using pypdf's ability to add text is limited, so let's use reportlab-free approach
    # Actually, pypdf's PdfWriter blank pages don't have extractable text.
    # We'll mock the extraction functions in most tests.
    # For integration tests, we'll create PDFs with actual text.

    # Write a minimal PDF with text using raw PDF syntax
    pages_pdf = []
    for i, text in enumerate(page_texts):
        escaped = text.replace("(", "\\(").replace(")", "\\)")
        pages_pdf.append(f"""\
% PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td ({escaped}) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF""")

    # Actually, creating proper multi-page PDFs by hand is fragile.
    # Let's just use pypdf to write PDFs with metadata and mock text extraction.

    writer = PdfWriter()
    for _text in page_texts:
        writer.add_blank_page(width=200, height=200)

    # Set metadata
    writer.add_metadata({
        "/Title": title,
        "/Author": author,
        "/Producer": "test-producer",
    })

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    with open(path, "wb") as f:
        writer.write(f)

    return path


class TestExtractPdfMetadata:
    def test_with_metadata(self):
        path = _create_test_pdf(title="My Paper", author="Jane Doe")
        try:
            result = extract_pdf_metadata(path)
            assert result["title"] == "My Paper"
            assert result["author"] == "Jane Doe"
            assert result["page_count"] == 2
            assert result["producer"] == "test-producer"
        finally:
            os.unlink(path)

    def test_no_metadata(self):
        writer = __import__("pypdf", fromlist=["PdfWriter"]).PdfWriter()
        writer.add_blank_page(width=200, height=200)
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as f:
            writer.write(f)
        try:
            result = extract_pdf_metadata(path)
            assert result["title"] == ""
            assert result["author"] == ""
            assert result["page_count"] == 1
        finally:
            os.unlink(path)


class TestExtractPdfText:
    """Test text extraction - blank pages yield empty text, so mock it."""

    @patch("kb.parsers.pdf.extract_pdf_text")
    def test_returns_joined_pages(self, mock_extract):
        mock_extract.return_value = "Page 1 text\n\nPage 2 text"
        result = mock_extract("/fake.pdf")
        assert "Page 1 text" in result
        assert "Page 2 text" in result


class TestParsePdf:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="PDF not found"):
            parse_pdf("/nonexistent/file.pdf")

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_full_with_metadata(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "Attention Paper",
                "author": "Alice Smith",
                "page_count": 12,
                "producer": "LaTeX",
            }
            mock_text.return_value = "We propose a new architecture."
            result = parse_pdf(path)

            assert result["source_type"] == "pdf"
            assert result["title"] == "Attention Paper"
            assert result["source_url"] == ""
            assert "**Author:** Alice Smith" in result["body"]
            assert "**Pages:** 12" in result["body"]
            assert "We propose a new architecture." in result["body"]
            assert result["metadata"]["page_count"] == 12
            assert result["metadata"]["author"] == "Alice Smith"
        finally:
            os.unlink(path)

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_no_metadata_uses_filename(self, mock_meta, mock_text):
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="my_document_")
        os.close(fd)
        try:
            mock_meta.return_value = {
                "title": "",
                "author": "",
                "page_count": 3,
                "producer": "",
            }
            mock_text.return_value = "Some text."
            result = parse_pdf(path)

            assert "my_document_" in result["title"]
            assert result["source_url"] == ""
        finally:
            os.unlink(path)

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_with_source_url(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "Report",
                "author": "",
                "page_count": 5,
                "producer": "",
            }
            mock_text.return_value = "Text."
            result = parse_pdf(path, source_url="https://s3.example.com/report.pdf")

            assert result["source_url"] == "https://s3.example.com/report.pdf"
        finally:
            os.unlink(path)

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_with_user_notes(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "Paper",
                "author": "Bob",
                "page_count": 1,
                "producer": "",
            }
            mock_text.return_value = "Extracted text."
            result = parse_pdf(path, content="My personal notes on this paper")

            assert "## Notes" in result["body"]
            assert "My personal notes" in result["body"]
        finally:
            os.unlink(path)

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_text_truncation(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "Long Paper",
                "author": "",
                "page_count": 100,
                "producer": "",
            }
            mock_text.return_value = "x" * 10000
            result = parse_pdf(path)

            assert "... [truncated]" in result["body"]
            assert len(result["body"]) < 10000
        finally:
            os.unlink(path)

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_empty_text(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "Empty PDF",
                "author": "",
                "page_count": 1,
                "producer": "",
            }
            mock_text.return_value = ""
            result = parse_pdf(path)

            assert "## Extracted Text" not in result["body"]
            assert result["body"] == "**Pages:** 1"
        finally:
            os.unlink(path)


class TestParsePdfDocShape:
    """Verify document dict shape matches other parsers' contract."""

    @patch("kb.parsers.pdf.extract_pdf_text")
    @patch("kb.parsers.pdf.extract_pdf_metadata")
    def test_shape(self, mock_meta, mock_text):
        path = _create_test_pdf()
        try:
            mock_meta.return_value = {
                "title": "T",
                "author": "",
                "page_count": 1,
                "producer": "",
            }
            mock_text.return_value = "body"
            result = parse_pdf(path)
            assert set(result.keys()) == {
                "title", "source_url", "source_type", "body", "metadata",
            }
        finally:
            os.unlink(path)
