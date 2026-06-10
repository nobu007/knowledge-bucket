"""Git repository parser: fetch metadata and README from GitHub repos."""

import json
import subprocess


def _parse_owner_repo(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL.

    Accepts:
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - git@github.com:owner/repo.git
      - owner/repo
    """
    url = url.strip().removesuffix("/")

    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
    elif "github.com/" in url:
        path = url.split("github.com/", 1)[1]
    elif "/" in url and url.count("/") == 1:
        path = url
    else:
        raise ValueError(f"Cannot parse GitHub repo from: {url}")

    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse GitHub repo from: {url}")

    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def fetch_repo_metadata(url: str) -> dict:
    """Fetch repo metadata via gh api.

    Returns dict with keys: name, full_name, description, language,
    stargazers_count, topics, html_url, default_branch.
    """
    owner, repo = _parse_owner_repo(url)
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not installed. Install from https://cli.github.com/")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh api timed out for {owner}/{repo}")

    if result.returncode != 0:
        stderr = result.stderr.strip().lower()
        if "rate limit" in stderr:
            raise RuntimeError("GitHub API rate limit exceeded. Try again later.")
        if "authentication" in stderr or "login" in stderr:
            raise RuntimeError("gh auth required. Run 'gh auth login'.")
        raise RuntimeError(f"gh api failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    return {
        "name": data.get("name", ""),
        "full_name": data.get("full_name", ""),
        "description": data.get("description", "") or "",
        "language": data.get("language", "") or "",
        "stargazers_count": data.get("stargazers_count", 0),
        "topics": data.get("topics", []),
        "html_url": data.get("html_url", ""),
        "default_branch": data.get("default_branch", "main"),
    }


def fetch_readme(url: str) -> str:
    """Fetch README content via gh api.

    Returns decoded README text, or empty string if not found.
    """
    owner, repo = _parse_owner_repo(url)
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/readme", "--jq", ".content"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return ""

    import base64
    content_b64 = result.stdout.strip()
    if not content_b64:
        return ""

    return base64.b64decode(content_b64).decode("utf-8", errors="replace")


def parse_repo(url: str) -> dict:
    """Parse a GitHub repo into a structured document dict.

    Returns dict with keys: title, source_url, source_type, body, metadata.
    """
    meta = fetch_repo_metadata(url)
    readme = fetch_readme(url)

    title = meta["full_name"] or meta["name"]
    if meta["description"]:
        title = f"{meta['full_name']}: {meta['description']}"

    parts: list[str] = []
    if meta["description"]:
        parts.append(f"**Description:** {meta['description']}")
    if meta["language"]:
        parts.append(f"**Language:** {meta['language']}")
    if meta["stargazers_count"]:
        parts.append(f"**Stars:** {meta['stargazers_count']}")
    if meta["topics"]:
        parts.append(f"**Topics:** {', '.join(meta['topics'])}")

    header = "\n".join(parts)
    body = f"{header}\n\n---\n\n{readme}" if readme else header

    return {
        "title": title,
        "source_url": meta["html_url"],
        "source_type": "git_repo",
        "body": body,
        "metadata": {
            "description": meta["description"],
            "language": meta["language"],
            "stars": meta["stargazers_count"],
            "topics": meta["topics"],
            "full_name": meta["full_name"],
        },
    }
