"""Short-lived, source-traceable cache for web research evidence.

This module deliberately does not call the network or the LLM.  It stores the
complete successful output of a web-reading tool and provides a small lexical
retrieval layer for later turns.  The agent can therefore keep prompts small
without losing the original evidence needed to answer a follow-up question.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from utils.paths import get_user_data_dir


logger = logging.getLogger("WebEvidenceCache")

SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_CHUNK_CHARS = 2400
DEFAULT_MAX_CONTENT_CHARS = 200_000
DEFAULT_RETRIEVAL_MAX_CHARS = 12_000
DEFAULT_RETRIEVAL_MAX_CHUNKS = 6

STATUS_CACHE_HIT_GROUNDED = "CACHE_HIT_GROUNDED"
STATUS_CACHE_MISS_NEEDS_FETCH = "CACHE_MISS_NEEDS_FETCH"
STATUS_CACHE_CONFLICT = "CACHE_CONFLICT"
STATUS_CACHE_EMPTY = "CACHE_EMPTY"

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/%+-]{1,}")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:[,.]\d+)*%?|\d+(?:点|时|分|条|个|万|千|百))(?![A-Za-z0-9])")
_EVIDENCE_ID_RE = re.compile(r"^ev_[0-9a-f]{64}$")

_WEB_REFERENCE_RE = re.compile(
    r"(?:原文|文章|网页|页面|报道|新闻|这篇|该篇|上文|刚才|之前|上次|"
    r"你读过|你看过|你查过|刚读|刚看|读过的|看过的)",
    re.IGNORECASE,
)
_WEB_FACT_QUERY_RE = re.compile(
    r"(?:多少|几条|几次|数字|数量|峰值|比例|百分比|何时|什么时候|哪天|日期|时间|"
    r"具体|原话|提到|声称|指出|根据|依据|细节|数据|数字|为什么|怎么说|讲了什么|"
    r"总结|概括|内容|还有什么|what|how many|when|according to|exact)",
    re.IGNORECASE,
)
_LIVE_WEB_REQUEST_RE = re.compile(
    r"(?:https?://|联网|上网|搜索|搜一下|查最新|最新新闻|实时|重新读取|"
    r"重新阅读|重新核对|再读取|再阅读|再次读取|再次核对|fetch_webpage|web_search)",
    re.IGNORECASE,
)


def is_web_followup_request(text: str) -> bool:
    """Return whether a message likely asks about a recently read webpage.

    This is intentionally conservative. Live-search and explicit re-read
    requests must stay on the real network/tool path, and weather remains a
    real-time capability rather than a cached webpage fact.
    """
    value = str(text or "").strip()
    if not value or _LIVE_WEB_REQUEST_RE.search(value):
        return False
    if re.search(r"天气|气温|温度|空气质量|下雨|下雪", value):
        return False
    return bool(_WEB_REFERENCE_RE.search(value) and _WEB_FACT_QUERY_RE.search(value))


def canonicalize_url(url: str) -> str:
    """Normalize a web URL while preserving query parameters and removing fragments."""
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def content_digest(content: str) -> str:
    """Return a stable SHA-256 digest for normalized text."""
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def is_successful_web_result(result: str, *, is_error: bool = False) -> bool:
    """Return whether a tool result is safe to persist as webpage evidence."""
    if is_error:
        return False
    value = str(result or "").strip()
    if not value:
        return False
    failure_prefixes = (
        "错误", "访问失败", "网络连接失败", "获取网页失败", "浏览器获取网页失败",
        "未能提取", "处理网页时出错", "网络请求未成功", "MCP工具调用失败",
        "工具执行错误", "Tool execution failed", "[工具失败]", "[拒绝]",
    )
    return not value.startswith(failure_prefixes)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return _now()
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _query_terms(text: str) -> list[str]:
    """Extract useful terms without requiring a heavyweight tokenizer."""
    value = str(text or "").lower()
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = term.strip().lower()
        if len(term) < 2 or term in seen:
            return
        seen.add(term)
        terms.append(term)

    for match in _WORD_RE.finditer(value):
        add(match.group(0))
    for match in _NUMBER_RE.finditer(value):
        add(match.group(0))
    for match in _CJK_RE.finditer(value):
        phrase = match.group(0)
        add(phrase)
        for index in range(len(phrase) - 1):
            add(phrase[index : index + 2])
    return terms


def _split_chunks(text: str, max_chars: int) -> list[str]:
    """Split on paragraph boundaries first, then hard-wrap oversized paragraphs."""
    limit = max(200, int(max_chars))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for paragraph in paragraphs or [text.strip()]:
        if len(paragraph) <= limit:
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            current = paragraph
            continue

        flush()
        for start in range(0, len(paragraph), limit):
            chunks.append(paragraph[start : start + limit])
    flush()
    return chunks


@dataclass(frozen=True)
class WebEvidenceChunk:
    chunk_id: str
    ordinal: int
    text: str
    keywords: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict) -> "WebEvidenceChunk":
        return cls(
            chunk_id=str(value["chunk_id"]),
            ordinal=int(value["ordinal"]),
            text=str(value["text"]),
            keywords=tuple(str(item) for item in value.get("keywords", [])),
        )

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "keywords": list(self.keywords),
        }


@dataclass(frozen=True)
class WebEvidence:
    evidence_id: str
    research_task_id: str
    url: str
    canonical_url: str
    title: str
    fetched_at: str
    content_digest: str
    content_chars: int
    content: str
    chunks: tuple[WebEvidenceChunk, ...]
    source_tool: str
    provider: str
    status: str


@dataclass(frozen=True)
class EvidenceHit:
    evidence: WebEvidence
    chunk: WebEvidenceChunk
    score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceRetrieval:
    status: str
    task_id: str
    query: str
    hits: tuple[EvidenceHit, ...]
    evidence_ids: tuple[str, ...]
    text: str


class WebEvidenceCache:
    """Persist complete web evidence with task-scoped lexical retrieval."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        self.root = Path(root) if root is not None else get_user_data_dir() / "web_evidence_cache"
        self.evidence_root = self.root / "evidence"
        self.tasks_root = self.root / "tasks"
        self.url_index_path = self.root / "url_index.json"
        self.max_bytes = max(0, int(max_bytes))
        self.max_age = timedelta(hours=max(0.0, float(max_age_hours)))
        self.chunk_chars = max(200, int(chunk_chars))
        self.max_content_chars = max(1, int(max_content_chars))
        self._lock = threading.RLock()
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self.cleanup()

    def store(
        self,
        research_task_id: str,
        url: str,
        content: str,
        *,
        title: str = "",
        source_tool: str = "fetch_webpage",
        provider: str = "",
        fetched_at: datetime | str | None = None,
    ) -> WebEvidence:
        """Store a successful, non-empty webpage result and attach it to a task."""
        task_id = str(research_task_id or "").strip()
        original_url = str(url or "").strip()
        normalized = str(content or "").strip()
        if not task_id:
            raise ValueError("research_task_id 不能为空")
        if not original_url:
            raise ValueError("url 不能为空")
        if not normalized:
            raise ValueError("网页证据正文不能为空")
        if len(normalized) > self.max_content_chars:
            raise ValueError(
                f"网页证据正文超过上限（{len(normalized)} > {self.max_content_chars}），未写入缓存"
            )

        canonical_url = canonicalize_url(original_url)
        digest = content_digest(normalized)
        evidence_id = f"ev_{digest}"
        fetched = _as_datetime(fetched_at).isoformat()
        chunks = tuple(
            WebEvidenceChunk(
                chunk_id=f"{evidence_id}:{ordinal:03d}",
                ordinal=ordinal,
                text=chunk,
                keywords=tuple(_query_terms(chunk)),
            )
            for ordinal, chunk in enumerate(_split_chunks(normalized, self.chunk_chars), start=1)
        )
        evidence = WebEvidence(
            evidence_id=evidence_id,
            research_task_id=task_id,
            url=original_url,
            canonical_url=canonical_url,
            title=str(title or "").strip(),
            fetched_at=fetched,
            content_digest=f"sha256:{digest}",
            content_chars=len(normalized),
            content=normalized,
            chunks=chunks,
            source_tool=str(source_tool or "fetch_webpage"),
            provider=str(provider or ""),
            status="success",
        )
        with self._lock:
            existing = self._read_evidence(evidence_id)
            if existing is None:
                self._write_evidence(evidence)
                logger.debug(
                    "[WebEvidence] store task=%s url=%s digest=%s chars=%d",
                    task_id,
                    self._log_url(canonical_url),
                    digest[:12],
                    len(normalized),
                )
            else:
                evidence = existing
                self._touch_evidence(evidence_id)
            self._attach_task(task_id, evidence, fetched_at=fetched)
            self._attach_url_index(evidence)
            self.cleanup(exclude={evidence_id})
        return evidence

    def get(self, evidence_id: str) -> WebEvidence | None:
        with self._lock:
            evidence = self._read_evidence(str(evidence_id or ""))
            if evidence is not None:
                self._touch_evidence(evidence.evidence_id)
            return evidence

    def list_task(self, research_task_id: str) -> tuple[WebEvidence, ...]:
        """Return non-expired evidence attached to a task, newest first."""
        task = self._read_task(str(research_task_id or ""))
        if not task:
            return ()
        entries: list[WebEvidence] = []
        for evidence_id in task.get("evidence_ids", []):
            evidence = self._read_evidence(str(evidence_id))
            if evidence is not None:
                entries.append(evidence)
        return tuple(sorted(entries, key=lambda item: item.fetched_at, reverse=True))

    def recent_task_ids(self, *, limit: int = 8) -> tuple[str, ...]:
        """Return recent research task IDs whose cache metadata still exists."""
        # 同一 Agent 进程可能持续运行数天；初始化时的清理不能覆盖这段
        # 生命周期，因此在关联任务前再次执行 TTL/损坏条目清理。
        self.cleanup()
        candidates: list[tuple[datetime, str]] = []
        for task_path in self.tasks_root.glob("*.json"):
            task = self._read_json(task_path)
            if not task:
                continue
            task_id = str(task.get("research_task_id", "")).strip()
            if not task_id or not task.get("evidence_ids"):
                continue
            try:
                activity = _as_datetime(task.get("last_activity_at"))
            except (TypeError, ValueError, OverflowError):
                activity = datetime.min.replace(tzinfo=timezone.utc)
            candidates.append((activity, task_id))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return tuple(task_id for _, task_id in candidates[: max(0, int(limit))])

    def retrieve_recent(
        self,
        query: str,
        *,
        preferred_task_ids: Iterable[str] = (),
        max_tasks: int = 8,
        max_chunks: int = DEFAULT_RETRIEVAL_MAX_CHUNKS,
        max_chars: int = DEFAULT_RETRIEVAL_MAX_CHARS,
    ) -> EvidenceRetrieval:
        """Search preferred and recent tasks, selecting the strongest hit.

        The preferred IDs come from the current Agent audit trail. Recent task
        metadata is the restart-safe fallback. A task with evidence but no
        matching chunk is returned as ``CACHE_MISS_NEEDS_FETCH`` so callers can
        distinguish an uncovered detail from an empty cache.
        """
        task_ids = list(dict.fromkeys(
            str(task_id).strip()
            for task_id in (*preferred_task_ids, *self.recent_task_ids(limit=max_tasks))
            if str(task_id or "").strip()
        ))[: max(0, int(max_tasks))]
        if not task_ids:
            return EvidenceRetrieval(STATUS_CACHE_EMPTY, "", str(query or ""), (), (), "")

        best: EvidenceRetrieval | None = None
        best_score = float("-inf")
        fallback: EvidenceRetrieval | None = None
        for task_id in task_ids:
            retrieval = self.search(
                task_id,
                query,
                max_chunks=max_chunks,
                max_chars=max_chars,
            )
            if retrieval.status == STATUS_CACHE_HIT_GROUNDED:
                score = max((hit.score for hit in retrieval.hits), default=0.0)
                if score > best_score:
                    best = retrieval
                    best_score = score
            elif retrieval.status == STATUS_CACHE_MISS_NEEDS_FETCH and fallback is None:
                fallback = retrieval
        if best is not None:
            return best
        if fallback is not None:
            return fallback
        return EvidenceRetrieval(STATUS_CACHE_EMPTY, "", str(query or ""), (), (), "")

    @staticmethod
    def format_followup_context(retrieval: EvidenceRetrieval) -> str:
        """Format a bounded, explicit evidence boundary for model input."""
        if retrieval.status == STATUS_CACHE_HIT_GROUNDED:
            return (
                "【网页证据缓存：CACHE_HIT_GROUNDED】\n"
                "下面是此前真实网页读取结果中的原文片段。它不是模型猜测，也不是实时状态；"
                "回答具体数字、时间、比例、原话和出处时，只能依据这些片段。"
                "片段没有提供的事实必须明确说原文未提供，不能用常识补全。\n\n"
                + retrieval.text
            )
        if retrieval.status == STATUS_CACHE_MISS_NEEDS_FETCH:
            return (
                "【网页证据缓存：CACHE_MISS_NEEDS_FETCH】\n"
                f"已关联研究任务 {retrieval.task_id}，但缓存原文没有覆盖当前问题的细节。"
                "不要根据上一轮摘要或常识猜测具体数字、时间或原话；"
                "只有真实网页工具读取成功后，才能声称已经核对。"
            )
        return (
            "【网页证据缓存：CACHE_EMPTY】\n"
            "当前没有找到可关联的网页原文缓存。不要声称已经读过或核对过原文；"
            "如果问题需要网页事实，应先执行真实网页读取工具。"
        )

    def search(
        self,
        research_task_id: str,
        query: str,
        *,
        max_chunks: int = DEFAULT_RETRIEVAL_MAX_CHUNKS,
        max_chars: int = DEFAULT_RETRIEVAL_MAX_CHARS,
    ) -> EvidenceRetrieval:
        """Find relevant contiguous source chunks within one research task."""
        task_id = str(research_task_id or "")
        query_text = str(query or "").strip()
        terms = _query_terms(query_text)
        evidence_items = self.list_task(task_id)
        if not evidence_items or not terms:
            result = EvidenceRetrieval(STATUS_CACHE_EMPTY, task_id, query_text, (), (), "")
            logger.debug(
                "[WebEvidence] retrieve task=%s query_terms=%d hits=0 state=%s",
                task_id,
                len(terms),
                result.status,
            )
            return result

        hits: list[EvidenceHit] = []
        for evidence in evidence_items:
            title_text = evidence.title.lower()
            for chunk in evidence.chunks:
                chunk_text = chunk.text.lower()
                matched = tuple(term for term in terms if term in chunk_text)
                if not matched:
                    continue
                score = float(len(matched))
                score += sum(1.5 for term in matched if term in title_text)
                score += sum(1.0 for term in matched if term in _NUMBER_RE.findall(query_text.lower()))
                hits.append(EvidenceHit(evidence, chunk, score, matched))

        hits.sort(key=lambda hit: (-hit.score, hit.evidence.fetched_at, hit.chunk.ordinal))
        selected: list[EvidenceHit] = []
        used_chars = 0
        for hit in hits:
            if len(selected) >= max(0, int(max_chunks)):
                break
            if selected and used_chars + len(hit.chunk.text) > max(0, int(max_chars)):
                continue
            if not selected and len(hit.chunk.text) > max_chars:
                text = hit.chunk.text[: max(0, int(max_chars))]
                hit = EvidenceHit(
                    hit.evidence,
                    WebEvidenceChunk(hit.chunk.chunk_id, hit.chunk.ordinal, text, hit.chunk.keywords),
                    hit.score,
                    hit.matched_terms,
                )
            selected.append(hit)
            used_chars += len(hit.chunk.text)

        unique_evidence_ids = tuple(dict.fromkeys(hit.evidence.evidence_id for hit in selected))
        if not selected:
            status = STATUS_CACHE_MISS_NEEDS_FETCH if evidence_items else STATUS_CACHE_EMPTY
            result = EvidenceRetrieval(status, task_id, query_text, (), (), "")
        else:
            status = STATUS_CACHE_HIT_GROUNDED
            text = self._format_hits(selected)
            result = EvidenceRetrieval(status, task_id, query_text, tuple(selected), unique_evidence_ids, text)
        logger.debug(
            "[WebEvidence] retrieve task=%s query_terms=%d hits=%d state=%s",
            task_id,
            len(terms),
            len(result.hits),
            result.status,
        )
        return result

    def cleanup(self, *, exclude: set[str] | None = None) -> None:
        """Remove expired or least-recently-used evidence without touching sources."""
        excluded = set(exclude or ())
        with self._lock:
            entries: list[tuple[str, Path, Path, int, float, datetime]] = []
            now = _now()
            for metadata_path in self.evidence_root.rglob("*.json"):
                evidence_id = metadata_path.stem
                content_path = metadata_path.with_suffix(".md")
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    fetched = _as_datetime(metadata.get("fetched_at"))
                    if evidence_id not in excluded and now - fetched > self.max_age:
                        self._remove_evidence_files(evidence_id)
                        continue
                    if not content_path.is_file():
                        self._remove_evidence_files(evidence_id)
                        continue
                    stat = content_path.stat()
                    entries.append((evidence_id, metadata_path, content_path, stat.st_size, stat.st_mtime, fetched))
                except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
                    self._remove_evidence_files(evidence_id)

            total_size = sum(item[3] for item in entries)
            for evidence_id, _, content_path, size, _, _ in sorted(entries, key=lambda item: item[4]):
                if total_size <= self.max_bytes:
                    break
                if evidence_id in excluded:
                    continue
                self._remove_evidence_files(evidence_id)
                total_size -= size

            self._rebuild_indexes()
            for directory in (self.evidence_root, self.tasks_root):
                for child in list(directory.iterdir()):
                    if child.is_dir() and not any(child.iterdir()):
                        child.rmdir()

    def clear(self) -> None:
        with self._lock:
            if self.root.exists():
                shutil.rmtree(self.root)
            self.evidence_root.mkdir(parents=True, exist_ok=True)
            self.tasks_root.mkdir(parents=True, exist_ok=True)

    def stats(self) -> tuple[int, int]:
        with self._lock:
            files = list(self.evidence_root.rglob("*.md"))
            return len(files), sum(path.stat().st_size for path in files if path.is_file())

    def _write_evidence(self, evidence: WebEvidence) -> None:
        metadata_path, content_path = self._evidence_paths(evidence.evidence_id)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "evidence_id": evidence.evidence_id,
            "research_task_id": evidence.research_task_id,
            "url": evidence.url,
            "canonical_url": evidence.canonical_url,
            "title": evidence.title,
            "fetched_at": evidence.fetched_at,
            "content_digest": evidence.content_digest,
            "content_chars": evidence.content_chars,
            "content_file": content_path.name,
            "chunks": [chunk.to_dict() for chunk in evidence.chunks],
            "source_tool": evidence.source_tool,
            "provider": evidence.provider,
            "status": evidence.status,
        }
        self._atomic_write(content_path, evidence.content)
        self._atomic_write(metadata_path, _safe_json(metadata))

    def _read_evidence(self, evidence_id: str) -> WebEvidence | None:
        if not _EVIDENCE_ID_RE.fullmatch(str(evidence_id or "")):
            return None
        metadata_path, content_path = self._evidence_paths(evidence_id)
        if not metadata_path.is_file() or not content_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            content = content_path.read_text(encoding="utf-8")
            if metadata.get("schema_version") != SCHEMA_VERSION:
                return None
            if content_digest(content) != str(metadata.get("content_digest", "")).removeprefix("sha256:"):
                logger.warning("[WebEvidence] digest mismatch evidence=%s", evidence_id)
                return None
            chunks = tuple(WebEvidenceChunk.from_dict(item) for item in metadata.get("chunks", []))
            return WebEvidence(
                evidence_id=str(metadata["evidence_id"]),
                research_task_id=str(metadata["research_task_id"]),
                url=str(metadata["url"]),
                canonical_url=str(metadata.get("canonical_url", "")),
                title=str(metadata.get("title", "")),
                fetched_at=str(metadata["fetched_at"]),
                content_digest=str(metadata["content_digest"]),
                content_chars=int(metadata.get("content_chars", len(content))),
                content=content,
                chunks=chunks,
                source_tool=str(metadata.get("source_tool", "fetch_webpage")),
                provider=str(metadata.get("provider", "")),
                status=str(metadata.get("status", "success")),
            )
        except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("[WebEvidence] invalid cache entry ignored evidence=%s", evidence_id)
            return None

    def _attach_task(self, task_id: str, evidence: WebEvidence, *, fetched_at: str) -> None:
        path = self._task_path(task_id)
        current = self._read_json(path) or {
            "schema_version": SCHEMA_VERSION,
            "research_task_id": task_id,
            "created_at": fetched_at,
            "last_activity_at": fetched_at,
            "status": "active",
            "evidence_ids": [],
            "urls": [],
        }
        evidence_ids = list(dict.fromkeys([*current.get("evidence_ids", []), evidence.evidence_id]))
        urls = list(dict.fromkeys([*current.get("urls", []), evidence.canonical_url]))
        current.update({"last_activity_at": fetched_at, "evidence_ids": evidence_ids, "urls": urls})
        self._atomic_write(path, _safe_json(current))

    def _attach_url_index(self, evidence: WebEvidence) -> None:
        index = self._read_json(self.url_index_path) or {}
        ids = list(dict.fromkeys([*index.get(evidence.canonical_url, []), evidence.evidence_id]))
        index[evidence.canonical_url] = ids
        self._atomic_write(self.url_index_path, _safe_json(index))

    def _rebuild_indexes(self) -> None:
        valid_ids: set[str] = set()
        index: dict[str, list[str]] = {}
        for content_path in self.evidence_root.rglob("*.md"):
            evidence_id = content_path.stem
            evidence = self._read_evidence(evidence_id)
            if evidence is None:
                self._remove_evidence_files(evidence_id)
                continue
            valid_ids.add(evidence_id)
            index.setdefault(evidence.canonical_url, []).append(evidence_id)
        self._atomic_write(self.url_index_path, _safe_json(index))
        for task_path in self.tasks_root.glob("*.json"):
            task = self._read_json(task_path)
            if not task:
                task_path.unlink(missing_ok=True)
                continue
            retained = [item for item in task.get("evidence_ids", []) if item in valid_ids]
            if not retained:
                task_path.unlink(missing_ok=True)
                continue
            task["evidence_ids"] = retained
            self._atomic_write(task_path, _safe_json(task))

    def _evidence_paths(self, evidence_id: str) -> tuple[Path, Path]:
        value = str(evidence_id or "")
        digest = value.removeprefix("ev_") if _EVIDENCE_ID_RE.fullmatch(value) else "invalid"
        shard = self.evidence_root / digest[:2]
        return shard / f"{value}.json", shard / f"{value}.md"

    def _task_path(self, task_id: str) -> Path:
        digest = hashlib.sha256(str(task_id).encode("utf-8")).hexdigest()
        return self.tasks_root / f"{digest}.json"

    def _read_task(self, task_id: str) -> dict | None:
        return self._read_json(self._task_path(task_id))

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeDecodeError, TypeError, json.JSONDecodeError):
            return None

    def _touch_evidence(self, evidence_id: str) -> None:
        _, content_path = self._evidence_paths(evidence_id)
        try:
            os.utime(content_path, None)
        except OSError:
            pass

    def _remove_evidence_files(self, evidence_id: str) -> None:
        metadata_path, content_path = self._evidence_paths(evidence_id)
        metadata_path.unlink(missing_ok=True)
        content_path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".part",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _format_hits(hits: Iterable[EvidenceHit]) -> str:
        sections: list[str] = []
        for hit in hits:
            evidence = hit.evidence
            sections.append(
                "[网页证据] "
                f"evidence_id={evidence.evidence_id} | title={evidence.title or '(untitled)'} | "
                f"url={evidence.url} | fetched_at={evidence.fetched_at}\n"
                f"[原文片段 {hit.chunk.ordinal}]\n{hit.chunk.text}"
            )
        return "\n\n".join(sections)

    @staticmethod
    def _log_url(url: str) -> str:
        try:
            return urlsplit(url).netloc or "(unknown)"
        except ValueError:
            return "(invalid)"


__all__ = [
    "EvidenceHit",
    "EvidenceRetrieval",
    "STATUS_CACHE_CONFLICT",
    "STATUS_CACHE_EMPTY",
    "STATUS_CACHE_HIT_GROUNDED",
    "STATUS_CACHE_MISS_NEEDS_FETCH",
    "WebEvidence",
    "WebEvidenceCache",
    "WebEvidenceChunk",
    "canonicalize_url",
    "content_digest",
    "is_successful_web_result",
    "is_web_followup_request",
]
