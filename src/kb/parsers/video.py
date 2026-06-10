"""Video parser: fetch metadata from YouTube URLs via oEmbed API."""

import json
import re
import urllib.error
import urllib.request

_YOUTUBE_OEMBED = "https://www.youtube.com/oembed?url={}&format=json"

_YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:"
    r"www\.youtube\.com/watch\?v=([^\s&]+)"
    r"|youtu\.be/([^\s?]+)"
    r"|www\.youtube\.com/embed/([^\s?]+)"
    r"|www\.youtube\.com/shorts/([^\s?]+)"
    r")"
)


def _parse_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL. Returns None if not a YouTube URL."""
    m = _YOUTUBE_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3) or m.group(4)


def _fetch_url(url: str, timeout: float = 15.0) -> bytes:
    """Fetch URL contents. Raises RuntimeError on network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "knowledge-bucket/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}") from e
    except (TimeoutError, urllib.error.URLError) as e:
        raise RuntimeError(f"Network error fetching {url}: {e}") from e


def fetch_youtube_metadata(url: str) -> dict:
    """Fetch video metadata from YouTube oEmbed API.

    Returns dict with keys: title, author_name, author_url, thumbnail_url,
    duration (empty string if unavailable).
    """
    encoded = urllib.parse.quote(url, safe="")
    oembed_url = _YOUTUBE_OEMBED.format(encoded)
    data = _fetch_url(oembed_url)

    try:
        result = json.loads(data)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"Invalid JSON from YouTube oEmbed: {e}") from e

    return {
        "title": result.get("title", ""),
        "author_name": result.get("author_name", ""),
        "author_url": result.get("author_url", ""),
        "thumbnail_url": result.get("thumbnail_url", ""),
    }


def parse_video(url: str, content: str | None = None) -> dict:
    """Parse a YouTube URL and return a document dict.

    Args:
        url: YouTube video URL.
        content: Optional user notes to include.

    Returns:
        Dict with keys: title, source_url, source_type, body, metadata.
    """
    video_id = _parse_video_id(url)
    if not video_id:
        raise ValueError(f"Not a recognized YouTube URL: {url}")

    try:
        meta = fetch_youtube_metadata(url)
    except RuntimeError:
        meta = {
            "title": "",
            "author_name": "",
            "author_url": "",
            "thumbnail_url": "",
        }

    title = meta["title"] or f"YouTube Video {video_id}"
    channel = meta["author_name"] or ""
    source_url = f"https://www.youtube.com/watch?v={video_id}"

    body_parts = [f"# {title}\n"]
    body_parts.append(f"**Channel:** {channel}" if channel else f"**Video ID:** {video_id}")
    body_parts.append(f"**URL:** {source_url}")
    if meta.get("thumbnail_url"):
        body_parts.append(f"**Thumbnail:** {meta['thumbnail_url']}")
    body_parts.append("")

    if content:
        body_parts.append("## Notes\n")
        body_parts.append(content)
        body_parts.append("")

    return {
        "title": title,
        "source_url": source_url,
        "source_type": "video",
        "body": "\n".join(body_parts),
        "metadata": {
            "video_id": video_id,
            "channel": channel,
            "platform": "youtube",
        },
    }
