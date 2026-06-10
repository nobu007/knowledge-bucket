"""PDF parser: extract text and metadata from local PDF files."""

import os


def extract_pdf_metadata(pdf_path: str) -> dict:
    """Extract metadata from a PDF file.

    Returns dict with: title, author, page_count, producer.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Cannot read PDF {pdf_path}: {e}") from e

    info = reader.metadata or {}

    title = (info.title or "").strip()
    author = (info.author or "").strip()
    producer = (info.producer or "").strip()
    page_count = len(reader.pages)

    return {
        "title": title,
        "author": author,
        "page_count": page_count,
        "producer": producer,
    }


def extract_pdf_text(pdf_path: str, max_pages: int | None = None) -> str:
    """Extract text content from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        max_pages: Maximum number of pages to extract. None = all pages.

    Returns:
        Extracted text content.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Cannot read PDF {pdf_path}: {e}") from e

    pages = reader.pages
    if max_pages is not None:
        pages = pages[:max_pages]

    parts: list[str] = []
    for page in pages:
        try:
            text = page.extract_text()
        except Exception:
            continue
        if text and text.strip():
            parts.append(text.strip())

    return "\n\n".join(parts)


def parse_pdf(pdf_path: str, source_url: str | None = None,
              content: str | None = None) -> dict:
    """Parse a local PDF file into a structured document dict.

    Args:
        pdf_path: Path to the PDF file.
        source_url: Optional URL where the raw PDF is stored externally.
        content: Optional user notes to include in the body.

    Returns dict with: title, source_url, source_type, body, metadata.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    meta = extract_pdf_metadata(pdf_path)
    text = extract_pdf_text(pdf_path)

    # Use filename as fallback title if PDF metadata has none
    title = meta["title"] or os.path.splitext(os.path.basename(pdf_path))[0]

    # Build body sections
    header_parts: list[str] = []
    if meta["author"]:
        header_parts.append(f"**Author:** {meta['author']}")
    header_parts.append(f"**Pages:** {meta['page_count']}")
    if meta["producer"]:
        header_parts.append(f"**Producer:** {meta['producer']}")

    header = "\n".join(header_parts)

    # Truncate extracted text to reasonable size (per GOAL.md: no full text in Git)
    max_text_chars = 5000
    if len(text) > max_text_chars:
        text = text[:max_text_chars] + "\n\n... [truncated]"

    extracted = f"## Extracted Text\n\n{text}" if text else ""

    body_sections = [s for s in [header, extracted] if s]
    if content:
        body_sections.append(f"## Notes\n\n{content}")
    body = "\n\n---\n\n".join(body_sections)

    metadata: dict = {
        "page_count": meta["page_count"],
    }
    if meta["author"]:
        metadata["author"] = meta["author"]

    return {
        "title": title,
        "source_url": source_url or "",
        "source_type": "pdf",
        "body": body,
        "metadata": metadata,
    }
