"""Dependency-light GitHub REST client used by the Lianxin GitHub Skill."""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from config import get_github_config


_REPO_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_MAX_FILE_BYTES = 1_000_000


@dataclass
class GHError(Exception):
    message: str
    status_code: int | None = None
    rate_remaining: str | None = None
    rate_reset: str | None = None

    def __str__(self) -> str:
        return self.message


def _safe_part(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not _REPO_PART.fullmatch(value):
        raise ValueError(f"{label} 格式无效")
    return value


class GitHubMCP:
    API_BASE = "https://api.github.com"

    def __init__(self, token: str | None = None, timeout: int = 10):
        config = get_github_config()
        self.timeout = max(1, min(int(timeout), 30))
        self.token = token or os.environ.get("LIANXIN_GITHUB_TOKEN", "").strip() or str(config.get("token", "")).strip()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Lianxin-AI-GitHub-Skill/0.1",
        }
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    @staticmethod
    def _rate_reset(headers: Any) -> str | None:
        value = headers.get("X-RateLimit-Reset") if headers else None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat() if value else None
        except (TypeError, ValueError, OSError):
            return None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = requests.request(method, f"{self.API_BASE}{path}", headers=self.headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise GHError("无法连接 GitHub API，请检查网络后重试") from exc
        if response.status_code >= 400:
            message = "GitHub API 请求失败"
            if response.status_code in (403, 429):
                message = "GitHub API 速率限制或权限不足"
            raise GHError(message, response.status_code, response.headers.get("X-RateLimit-Remaining"), self._rate_reset(response.headers))
        try:
            return response.json()
        except ValueError as exc:
            raise GHError("GitHub API 返回了无法解析的数据", response.status_code) from exc

    def search_repos(self, query: str, per_page: int = 8) -> dict:
        return self._request("GET", "/search/repositories", params={"q": query, "per_page": max(1, min(int(per_page), 30))})

    def _contents(self, owner: str, repo: str, path: str = "", ref: str | None = None) -> Any:
        owner, repo = _safe_part(owner, "owner"), _safe_part(repo, "repo")
        path = str(path or "").strip()
        if len(path) > 500 or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("path 格式无效")
        params = {"ref": ref} if ref else None
        suffix = f"/{quote(path, safe='/')}" if path else ""
        return self._request("GET", f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/contents{suffix}", params=params)

    @staticmethod
    def _text_file(data: dict) -> dict:
        size = int(data.get("size") or 0)
        base = {"path": data.get("path"), "html_url": data.get("html_url"), "size": size}
        if data.get("type") != "file":
            return {**base, "kind": "directory"}
        if size > _MAX_FILE_BYTES or data.get("encoding") != "base64":
            return {**base, "kind": "non_text_or_large"}
        try:
            raw = base64.b64decode((data.get("content") or "").replace("\n", ""), validate=True)
            content = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return {**base, "kind": "non_text_or_large"}
        return {**base, "kind": "text", "content": content}

    def get_readme(self, owner: str, repo: str, ref: str | None = None) -> dict:
        owner, repo = _safe_part(owner, "owner"), _safe_part(repo, "repo")
        data = self._request("GET", f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/readme", params={"ref": ref} if ref else None)
        return self._text_file(data)

    def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> dict:
        data = self._contents(owner, repo, path, ref)
        return self._text_file(data) if isinstance(data, dict) else {"kind": "directory", "items": data}

    def list_directory(self, owner: str, repo: str, path: str = "", ref: str | None = None) -> dict:
        data = self._contents(owner, repo, path, ref)
        if not isinstance(data, list):
            return {"kind": "not_directory", "path": data.get("path"), "html_url": data.get("html_url")}
        items = [
            {
                "name": item.get("name"), "path": item.get("path"),
                "type": item.get("type"), "size": item.get("size"),
                "html_url": item.get("html_url"),
            }
            for item in data[:100]
        ]
        return {
            "kind": "directory", "path": path or "/", "items": items,
            "truncated": len(data) > len(items),
        }

    def list_commits(self, owner: str, repo: str, per_page: int = 20, page: int = 1) -> list[dict]:
        owner, repo = _safe_part(owner, "owner"), _safe_part(repo, "repo")
        return self._request("GET", f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits", params={"per_page": max(1, min(int(per_page), 50)), "page": max(1, min(int(page), 10))})


_default_instance: GitHubMCP | None = None


def get_mcp() -> GitHubMCP:
    global _default_instance
    if _default_instance is None:
        _default_instance = GitHubMCP()
    return _default_instance
