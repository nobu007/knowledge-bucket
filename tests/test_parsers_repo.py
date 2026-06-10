"""Tests for src/kb/parsers/repo.py."""

import json
import subprocess
from unittest.mock import patch

import pytest

from kb.parsers.repo import (
    _parse_owner_repo,
    fetch_readme,
    fetch_repo_metadata,
    parse_repo,
)


class TestParseOwnerRepo:
    def test_https_url(self):
        assert _parse_owner_repo("https://github.com/owner/repo") == ("owner", "repo")

    def test_https_url_trailing_slash(self):
        assert _parse_owner_repo("https://github.com/owner/repo/") == ("owner", "repo")

    def test_https_url_dot_git(self):
        assert _parse_owner_repo("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_ssh_url(self):
        assert _parse_owner_repo("git@github.com:owner/repo.git") == ("owner", "repo")

    def test_shorthand(self):
        assert _parse_owner_repo("owner/repo") == ("owner", "repo")

    def test_subpath_ignored(self):
        result = _parse_owner_repo("https://github.com/owner/repo/tree/main/src")
        assert result == ("owner", "repo")

    def test_invalid_no_slash(self):
        with pytest.raises(ValueError):
            _parse_owner_repo("noslash")

    def test_invalid_url(self):
        with pytest.raises(ValueError):
            _parse_owner_repo("https://example.com/something")


class TestFetchRepoMetadata:
    @patch("kb.parsers.repo.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _mock_completed_process(
            stdout=json.dumps({
                "name": "my-repo",
                "full_name": "user/my-repo",
                "description": "A test repo",
                "language": "Python",
                "stargazers_count": 42,
                "topics": ["cli", "tools"],
                "html_url": "https://github.com/user/my-repo",
                "default_branch": "main",
            }),
        )
        result = fetch_repo_metadata("https://github.com/user/my-repo")
        assert result["full_name"] == "user/my-repo"
        assert result["language"] == "Python"
        assert result["stargazers_count"] == 42
        assert result["topics"] == ["cli", "tools"]

    @patch("kb.parsers.repo.subprocess.run")
    def test_gh_failure(self, mock_run):
        mock_run.return_value = _mock_completed_process(
            returncode=1, stderr="not found",
        )
        with pytest.raises(RuntimeError, match="gh api failed"):
            fetch_repo_metadata("https://github.com/user/nonexistent")

    @patch("kb.parsers.repo.subprocess.run")
    def test_null_fields(self, mock_run):
        mock_run.return_value = _mock_completed_process(
            stdout=json.dumps({
                "name": "repo",
                "full_name": "u/repo",
                "description": None,
                "language": None,
                "stargazers_count": 0,
                "topics": [],
                "html_url": "https://github.com/u/repo",
                "default_branch": "main",
            }),
        )
        result = fetch_repo_metadata("u/repo")
        assert result["description"] == ""
        assert result["language"] == ""


class TestFetchReadme:
    @patch("kb.parsers.repo.subprocess.run")
    def test_success(self, mock_run):
        import base64
        content = base64.b64encode(b"# Hello\n\nWorld").decode()
        mock_run.return_value = _mock_completed_process(stdout=content)
        result = fetch_readme("https://github.com/owner/repo")
        assert result == "# Hello\n\nWorld"

    @patch("kb.parsers.repo.subprocess.run")
    def test_no_readme(self, mock_run):
        mock_run.return_value = _mock_completed_process(returncode=1, stderr="404")
        result = fetch_readme("owner/repo")
        assert result == ""

    @patch("kb.parsers.repo.subprocess.run")
    def test_empty_content(self, mock_run):
        mock_run.return_value = _mock_completed_process(stdout="")
        result = fetch_readme("owner/repo")
        assert result == ""


class TestParseRepo:
    @patch("kb.parsers.repo.fetch_readme")
    @patch("kb.parsers.repo.fetch_repo_metadata")
    def test_full(self, mock_meta, mock_readme):
        mock_meta.return_value = {
            "name": "my-repo",
            "full_name": "user/my-repo",
            "description": "A test repo",
            "language": "Python",
            "stargazers_count": 100,
            "topics": ["cli"],
            "html_url": "https://github.com/user/my-repo",
            "default_branch": "main",
        }
        mock_readme.return_value = "# My Repo\n\nSome description."
        result = parse_repo("https://github.com/user/my-repo")

        assert result["source_type"] == "git_repo"
        assert result["source_url"] == "https://github.com/user/my-repo"
        assert "user/my-repo" in result["title"]
        assert "A test repo" in result["title"]
        assert "**Language:** Python" in result["body"]
        assert "**Stars:** 100" in result["body"]
        assert "# My Repo" in result["body"]
        assert result["metadata"]["language"] == "Python"
        assert result["metadata"]["stars"] == 100

    @patch("kb.parsers.repo.fetch_readme")
    @patch("kb.parsers.repo.fetch_repo_metadata")
    def test_no_readme(self, mock_meta, mock_readme):
        mock_meta.return_value = {
            "name": "bare",
            "full_name": "u/bare",
            "description": "",
            "language": "",
            "stargazers_count": 0,
            "topics": [],
            "html_url": "https://github.com/u/bare",
            "default_branch": "main",
        }
        mock_readme.return_value = ""
        result = parse_repo("u/bare")

        assert result["title"] == "u/bare"
        assert result["body"] == ""
        assert result["metadata"]["description"] == ""


class TestParseRepoIntegration:
    """Integration-style test for CLI wiring (no actual gh calls)."""

    @patch("kb.parsers.repo.fetch_readme")
    @patch("kb.parsers.repo.fetch_repo_metadata")
    def test_metadata_dict_shape(self, mock_meta, mock_readme):
        mock_meta.return_value = {
            "name": "test",
            "full_name": "org/test",
            "description": "Test project",
            "language": "Rust",
            "stargazers_count": 500,
            "topics": ["database", "embedded"],
            "html_url": "https://github.com/org/test",
            "default_branch": "master",
        }
        mock_readme.return_value = "# Test\nHello"
        result = parse_repo("org/test")

        assert set(result.keys()) == {
            "title", "source_url", "source_type", "body", "metadata",
        }
        assert set(result["metadata"].keys()) == {
            "description", "language", "stars", "topics", "full_name",
        }


def _mock_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    """Create a mock subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["gh", "api"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


# --- Error message tests (Phase 6.1) ---


class TestFetchRepoMetadataErrors:
    @patch("kb.parsers.repo.subprocess.run")
    def test_gh_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        with pytest.raises(RuntimeError, match="gh CLI not installed"):
            fetch_repo_metadata("owner/repo")

    @patch("kb.parsers.repo.subprocess.run")
    def test_gh_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["gh"], 30)
        with pytest.raises(RuntimeError, match="timed out"):
            fetch_repo_metadata("owner/repo")

    @patch("kb.parsers.repo.subprocess.run")
    def test_rate_limit(self, mock_run):
        mock_run.return_value = _mock_completed_process(
            returncode=1, stderr="rate limit exceeded",
        )
        with pytest.raises(RuntimeError, match="rate limit"):
            fetch_repo_metadata("owner/repo")

    @patch("kb.parsers.repo.subprocess.run")
    def test_auth_error(self, mock_run):
        mock_run.return_value = _mock_completed_process(
            returncode=1, stderr="authentication required. login required.",
        )
        with pytest.raises(RuntimeError, match="gh auth required"):
            fetch_repo_metadata("owner/repo")
