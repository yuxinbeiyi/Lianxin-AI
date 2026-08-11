"""GitHub MCP skill for Lianxin

Provides a small, dependency-light wrapper over GitHub REST API for common
operations used by Lianxin: search repos, fetch README, fetch file contents,
list commits and create issues.

Behavior:
- Reads Personal Access Token (PAT) from environment variable
  LIANXIN_GITHUB_TOKEN, or from user config file at %USERPROFILE%/.lianxin/user_config.json
  under key: { "github": { "token": "..." } }.
- If no token is configured, read-only operations on public repositories still work
  (subject to GitHub rate limits).

Usage (programmatic):
    from skills.github_mcp.github_mcp import GitHubMCP
    mcp = GitHubMCP()
    results = mcp.search_repos("Lianxin", per_page=5)

This file intentionally keeps external dependencies minimal (requests only).
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


@dataclass
class GHError(Exception):
    message: str


class GitHubMCP:
    """Minimal GitHub micro-controller/profile used by Lianxin skills.

    It exposes basic GitHub operations used by the assistant:
      - search_repos
      - get_readme
      - get_file
      - list_commits
      - create_issue

    Token resolution order:
      1. env LIANXIN_GITHUB_TOKEN
      2. ~/.lianxin/user_config.json -> ["github"]["token"]
      3. None (unauthenticated — read-only, subject to rate limits)
    """

    API_BASE = "https://api.github.com"

    def __init__(self, token: Optional[str] = None, timeout: int = 10):
        self.timeout = timeout
        self.token = token or self._discover_token()
        self.headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"

    def _discover_token(self) -> Optional[str]:
        # 1) environment
        env = os.environ.get("LIANXIN_GITHUB_TOKEN")
        if env:
            return env.strip()

        # 2) user config file
        try:
            home = Path.home()
            cfg_path = home / ".lianxin" / "user_config.json"
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                token = data.get("github", {}).get("token")
                if token:
                    return str(token).strip()
        except Exception:
            pass
        return None

    def _url(self, path: str) -> str:
        return f"{self.API_BASE}{path}"

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        r = requests.get(self._url(path), headers=self.headers, params=params, timeout=self.timeout)
        if r.status_code >= 400:
            raise GHError(f"GET {path} -> {r.status_code}: {r.text}")
        return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        r = requests.post(self._url(path), headers=self.headers, json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise GHError(f"POST {path} -> {r.status_code}: {r.text}")
        return r.json()

    def search_repos(self, q: str, per_page: int = 8) -> Dict[str, Any]:
        """Search repositories using GitHub Search API.

        Returns the raw JSON response from GitHub search endpoint.
        """
        params = {"q": q, "per_page": max(1, min(per_page, 100))}
        return self._get("/search/repositories", params=params)

    def get_readme(self, owner: str, repo: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """Fetch repository README.md (decoded text).

        Returns dict: {"path": str, "content": str, "encoding": str}
        """
        params = {"ref": ref} if ref else None
        data = self._get(f"/repos/{owner}/{repo}/readme", params=params)
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if content and encoding == "base64":
            try:
                decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
            except Exception:
                decoded = ""
        else:
            decoded = content
        return {"path": data.get("path"), "content": decoded, "encoding": encoding, "html_url": data.get("html_url")}

    def get_file(self, owner: str, repo: str, filepath: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """Fetch a file's contents from a repository. Returns decoded text if file is a blob.
        If it's a directory or binary file, returns the raw JSON response for caller to inspect.
        """
        params = {"ref": ref} if ref else None
        data = self._get(f"/repos/{owner}/{repo}/contents/{filepath}", params=params)
        if isinstance(data, dict) and data.get("type") == "file":
            content = data.get("content", "")
            encoding = data.get("encoding", "base64")
            if content and encoding == "base64":
                try:
                    decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                except Exception:
                    decoded = ""
            else:
                decoded = content
            return {"path": data.get("path"), "content": decoded, "encoding": encoding, "html_url": data.get("html_url")}
        return data

    def list_commits(self, owner: str, repo: str, per_page: int = 30, page: int = 1) -> List[Dict[str, Any]]:
        params = {"per_page": max(1, min(100, per_page)), "page": max(1, page)}
        return self._get(f"/repos/{owner}/{repo}/commits", params=params)

    def create_issue(self, owner: str, repo: str, title: str, body: Optional[str] = None, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.token:
            raise GHError("Creating issues requires a configured GitHub token")
        payload: Dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        return self._post(f"/repos/{owner}/{repo}/issues", payload)


# Convenience module-level instance for simple scripts within Lianxin.
_default_instance: Optional[GitHubMCP] = None


def get_mcp(token: Optional[str] = None) -> GitHubMCP:
    global _default_instance
    if _default_instance is None:
        _default_instance = GitHubMCP(token=token)
    return _default_instance


# Small CLI for local testing
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--search", help="search query")
    p.add_argument("--readme", help="owner/repo")
    p.add_argument("--file", help="owner/repo:path")
    p.add_argument("--commits", help="owner/repo")
    p.add_argument("--issue", help="owner/repo:title")
    args = p.parse_args()

    m = GitHubMCP()
    if args.search:
        print(json.dumps(m.search_repos(args.search, per_page=10), ensure_ascii=False, indent=2))
    if args.readme:
        owner, repo = args.readme.split("/", 1)
        print(m.get_readme(owner, repo)["content"][:1000])
    if args.file:
        repopart, path = args.file.split(":", 1)
        owner, repo = repopart.split("/", 1)
        print(m.get_file(owner, repo, path)["content"][:1000])
    if args.commits:
        owner, repo = args.commits.split("/", 1)
        print(json.dumps(m.list_commits(owner, repo, per_page=5), ensure_ascii=False, indent=2))
    if args.issue:
        repopart, title = args.issue.split(":", 1)
        owner, repo = repopart.split("/", 1)
        print(json.dumps(m.create_issue(owner, repo, title, body="Created by Lianxin MCP"), ensure_ascii=False, indent=2))
