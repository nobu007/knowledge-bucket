"""Paper parser: fetch metadata from arXiv and DOI (CrossRef) sources."""

import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

_ARXIV_API = "http://export.arxiv.org/api/query?id_list={}&max_results=1"
_CROSSREF_API = "https://api.crossref.org/works/{}"

_ARXIV_RE = re.compile(
    r"(?:https?://arxiv\.org/(?:abs|pdf|html)/)?"  # optional URL prefix
    r"(\d{4}\.\d{4,5}(?:v\d+)?)"  # new-style ID: 2301.12345[v2]
    r"|"
    r"(?:https?://arxiv\.org/(?:abs|pdf|html)/)?"  # optional URL prefix
    r"([a-z-]+/\d{7}(?:v\d+)?)"  # old-style ID: hep-th/9901001[v2]
)

_DOI_RE = re.compile(
    r"(?:https?://doi\.org/)?"
    r"(10\.\d{4,9}/[^\s]+)"
)


def _parse_arxiv_id(text: str) -> str | None:
    """Extract arXiv ID from URL or bare ID. Returns None if not arXiv."""
    text = text.strip()
    m = _ARXIV_RE.fullmatch(text)
    if m:
        return m.group(1) or m.group(2)
    return None


def _parse_doi(text: str) -> str | None:
    """Extract DOI from URL or bare DOI. Returns None if not a DOI."""
    text = text.strip()
    m = _DOI_RE.fullmatch(text)
    if m:
        return m.group(1)
    return None


def _classify_input(text: str) -> tuple[str, str]:
    """Return (source_type, identifier) where source_type is 'arxiv'|'doi'|'raw'."""
    arxiv_id = _parse_arxiv_id(text)
    if arxiv_id:
        return "arxiv", arxiv_id
    doi = _parse_doi(text)
    if doi:
        return "doi", doi
    return "raw", text


def _fetch_url(url: str, timeout: int = 30) -> bytes:
    """Fetch URL content with a simple User-Agent header."""
    req = urllib.request.Request(url, headers={"User-Agent": "knowledge-bucket/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"Network error fetching {url}: {e}") from e


def fetch_arxiv_metadata(arxiv_id: str) -> dict:
    """Fetch paper metadata from arXiv API.

    Returns dict with: title, authors, abstract, arxiv_id, published, pdf_url.
    """
    url = _ARXIV_API.format(arxiv_id)
    data = _fetch_url(url)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise RuntimeError(f"Invalid XML from arXiv for {arxiv_id}") from e

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    entries = root.findall("atom:entry", ns)
    if not entries:
        raise RuntimeError(f"No arXiv entry found for: {arxiv_id}")

    entry = entries[0]

    title_el = entry.find("atom:title", ns)
    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""

    authors = []
    for author_el in entry.findall("atom:author", ns):
        name_el = author_el.find("atom:name", ns)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    summary_el = entry.find("atom:summary", ns)
    abstract = (summary_el.text or "").strip() if summary_el is not None else ""

    published_el = entry.find("atom:published", ns)
    if published_el is not None and published_el.text:
        published = published_el.text.strip()[:10]
    else:
        published = ""

    pdf_url = ""
    for link in entry.findall("atom:link", ns):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
            break

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "arxiv_id": arxiv_id,
        "published": published,
        "pdf_url": pdf_url,
    }


def fetch_doi_metadata(doi: str) -> dict:
    """Fetch paper metadata from CrossRef API.

    Returns dict with: title, authors, abstract, doi, published, container, url.
    """
    url = _CROSSREF_API.format(urllib.request.quote(doi, safe=""))
    data = _fetch_url(url)
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from CrossRef for {doi}") from e
    msg = obj.get("message", {})

    titles = msg.get("title", [])
    title = titles[0].strip() if titles else ""

    authors = []
    for a in msg.get("author", []):
        parts = [a.get("given", ""), a.get("family", "")]
        name = " ".join(p for p in parts if p).strip()
        if name:
            authors.append(name)

    abstract = (msg.get("abstract") or "").strip()

    date_parts = msg.get("published-print", msg.get("published-online", {}))
    date_list = date_parts.get("date-parts", [[]])
    parts = date_list[0] if date_list else []
    published = "-".join(str(p) for p in parts) if parts else ""

    container = ""
    containers = msg.get("container-title", [])
    if containers:
        container = containers[0].strip()

    page_url = msg.get("URL", "")

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "doi": doi,
        "published": published,
        "container": container,
        "url": page_url,
    }


def parse_paper(input_str: str, content: str | None = None) -> dict:
    """Parse a paper reference into a structured document dict.

    input_str: arXiv URL/ID, DOI URL/DOI, or a title for raw text.
    content: optional raw paper text to include as body.

    Returns dict with: title, source_url, source_type, body, metadata.
    """
    source_type, identifier = _classify_input(input_str)

    if source_type == "arxiv":
        meta = fetch_arxiv_metadata(identifier)
        return _build_arxiv_doc(meta, content)
    elif source_type == "doi":
        meta = fetch_doi_metadata(identifier)
        return _build_doi_doc(meta, content)
    else:
        return _build_raw_doc(identifier, content)


def _build_arxiv_doc(meta: dict, content: str | None) -> dict:
    """Build document dict from arXiv metadata."""
    title = meta["title"]
    source_url = f"https://arxiv.org/abs/{meta['arxiv_id']}"

    parts: list[str] = []
    if meta["authors"]:
        parts.append(f"**Authors:** {', '.join(meta['authors'])}")
    if meta["published"]:
        parts.append(f"**Published:** {meta['published']}")
    if meta["pdf_url"]:
        parts.append(f"**PDF:** {meta['pdf_url']}")

    header = "\n".join(parts)
    abstract = f"## Abstract\n\n{meta['abstract']}" if meta["abstract"] else ""

    body_sections = [s for s in [header, abstract] if s]
    if content:
        body_sections.append(f"## Notes\n\n{content}")
    body = "\n\n---\n\n".join(body_sections)

    return {
        "title": title,
        "source_url": source_url,
        "source_type": "paper",
        "body": body,
        "metadata": {
            "authors": meta["authors"],
            "arxiv_id": meta["arxiv_id"],
            "published": meta["published"],
        },
    }


def _build_doi_doc(meta: dict, content: str | None) -> dict:
    """Build document dict from DOI/CrossRef metadata."""
    title = meta["title"]
    source_url = meta.get("url") or f"https://doi.org/{meta['doi']}"

    parts: list[str] = []
    if meta["authors"]:
        parts.append(f"**Authors:** {', '.join(meta['authors'])}")
    if meta["published"]:
        parts.append(f"**Published:** {meta['published']}")
    if meta["container"]:
        parts.append(f"**Journal:** {meta['container']}")

    header = "\n".join(parts)
    abstract = f"## Abstract\n\n{meta['abstract']}" if meta["abstract"] else ""

    body_sections = [s for s in [header, abstract] if s]
    if content:
        body_sections.append(f"## Notes\n\n{content}")
    body = "\n\n---\n\n".join(body_sections)

    return {
        "title": title,
        "source_url": source_url,
        "source_type": "paper",
        "body": body,
        "metadata": {
            "authors": meta["authors"],
            "doi": meta["doi"],
            "published": meta["published"],
            "container": meta["container"],
        },
    }


def _build_raw_doc(title: str, content: str | None) -> dict:
    """Build document dict for a raw paper with just a title."""
    body = content or ""
    return {
        "title": title,
        "source_url": "",
        "source_type": "paper",
        "body": body,
        "metadata": {},
    }
