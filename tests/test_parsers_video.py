"""Tests for src/kb/parsers/video.py."""

import json
import urllib.error
from unittest.mock import patch

import pytest

from kb.parsers.video import (
    _parse_video_id,
    fetch_youtube_metadata,
    parse_video,
)

# --- Video ID parsing ---


class TestParseVideoId:
    def test_watch_url(self):
        assert _parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_watch_url_with_params(self):
        assert _parse_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"
        ) == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert _parse_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self):
        assert _parse_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        assert _parse_video_id("https://www.youtube.com/shorts/abc123DEF") == "abc123DEF"

    def test_no_scheme(self):
        assert _parse_video_id("www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_not_youtube(self):
        assert _parse_video_id("https://vimeo.com/12345") is None

    def test_random_text(self):
        assert _parse_video_id("not a url") is None


# --- oEmbed metadata fetch ---


class TestFetchYoutubeMetadata:
    @patch("kb.parsers.video._fetch_url")
    def test_success(self, mock_fetch):
        oembed = {
            "title": "Attention Is All You Need - Explained",
            "author_name": "AI Explained",
            "author_url": "https://www.youtube.com/@AIExplained",
            "thumbnail_url": "https://i.ytimg.com/vi/test/hqdefault.jpg",
        }
        mock_fetch.return_value = json.dumps(oembed).encode()
        result = fetch_youtube_metadata("https://www.youtube.com/watch?v=test")
        assert result["title"] == "Attention Is All You Need - Explained"
        assert result["author_name"] == "AI Explained"

    @patch("kb.parsers.video._fetch_url")
    def test_invalid_json(self, mock_fetch):
        mock_fetch.return_value = b"not json"
        with pytest.raises(RuntimeError, match="Invalid JSON"):
            fetch_youtube_metadata("https://www.youtube.com/watch?v=test")

    @patch("kb.parsers.video._fetch_url")
    def test_fetch_error_propagates(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("HTTP 404 fetching oembed")
        with pytest.raises(RuntimeError, match="HTTP 404"):
            fetch_youtube_metadata("https://www.youtube.com/watch?v=test")


# --- _fetch_url network error handling ---


class TestFetchUrlNetworkErrors:
    @patch("kb.parsers.video.urllib.request.urlopen")
    def test_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://example.com", 500, "Server Error", {}, None,
        )
        with pytest.raises(RuntimeError, match="HTTP 500"):
            from kb.parsers.video import _fetch_url
            _fetch_url("http://example.com")

    @patch("kb.parsers.video.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("timed out")
        with pytest.raises(RuntimeError, match="Network error"):
            from kb.parsers.video import _fetch_url
            _fetch_url("http://example.com")


# --- parse_video integration ---


class TestParseVideo:
    @patch("kb.parsers.video.fetch_youtube_metadata")
    def test_basic(self, mock_meta):
        mock_meta.return_value = {
            "title": "Transformers Explained",
            "author_name": "3Blue1Brown",
            "author_url": "https://www.youtube.com/@3b1b",
            "thumbnail_url": "https://i.ytimg.com/vi/abc/hqdefault.jpg",
        }
        result = parse_video("https://www.youtube.com/watch?v=abc123")

        assert result["source_type"] == "video"
        assert result["title"] == "Transformers Explained"
        assert result["source_url"] == "https://www.youtube.com/watch?v=abc123"
        assert "**Channel:** 3Blue1Brown" in result["body"]
        assert result["metadata"]["video_id"] == "abc123"
        assert result["metadata"]["platform"] == "youtube"

    @patch("kb.parsers.video.fetch_youtube_metadata")
    def test_with_notes(self, mock_meta):
        mock_meta.return_value = {
            "title": "Test Video",
            "author_name": "TestChannel",
            "author_url": "",
            "thumbnail_url": "",
        }
        result = parse_video("https://youtu.be/xyz789", content="My notes here")
        assert "## Notes" in result["body"]
        assert "My notes here" in result["body"]

    def test_not_youtube_url(self):
        with pytest.raises(ValueError, match="Not a recognized YouTube URL"):
            parse_video("https://vimeo.com/12345")

    @patch("kb.parsers.video.fetch_youtube_metadata")
    def test_oembed_fails_gracefully(self, mock_meta):
        mock_meta.side_effect = RuntimeError("HTTP 500")
        result = parse_video("https://www.youtube.com/watch?v=fallback")
        assert result["source_type"] == "video"
        assert "fallback" in result["title"]
        assert result["metadata"]["video_id"] == "fallback"

    @patch("kb.parsers.video.fetch_youtube_metadata")
    def test_doc_shape(self, mock_meta):
        mock_meta.return_value = {
            "title": "T",
            "author_name": "C",
            "author_url": "",
            "thumbnail_url": "",
        }
        result = parse_video("https://www.youtube.com/watch?v=shape")
        assert set(result.keys()) == {
            "title", "source_url", "source_type", "body", "metadata",
        }
