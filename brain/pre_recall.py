"""默认预回忆：每轮用廉价关键词通道检索相关长期记忆。

设计要点（见 docs/回忆优先设计.md L2）：
- 走 FTS5/BM25 关键词通道，毫秒级，**不加载向量模型**；
- 自指 / 历史问题绝不跳过；只有“纯外部世界查询”才跳过；
- 输出带记忆编号与记录日期，便于模型引用与后续溯源；
- 任何异常都必须静默降级，不能影响正常对话。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("PreRecall")

_MAX_HITS = 3
_MAX_CHARS = 2400

# ── 事实对象：出现这些词说明问题指向一个具体事实 ──────────
_FACT_OBJECT_RE = re.compile(
    r"(?:仓库|链接|地址|网址|项目|文件|账号|密码|电话|邮箱|名字|生日|公司|学校|住址|"
    r"记录|记忆|资料|信息|这件事|这篇文章|编号|订单|计划|安排|日程)"
)

# ── 自指 / 历史指向 ────────────────────────────────────
_SELF_RE = re.compile(r"(?:你自己|你的|我的|我们的|咱们|莲心(?:自己|的)|我是谁|你是谁)")
_HISTORY_RE = re.compile(r"(?:之前|上次|以前|还记得|记得过|那次|曾经|聊过|说过|提过)")

# ── 否定式断言：与事实对象相邻才算“否认既有事实” ─────────
_DENIAL_RE = re.compile(
    r"(?:我(?:并)?没有|没有|并不存在|不存在|没记录|没有记录|不知道|不清楚|"
    r"不是我的|从未(?:有过|说过|提过)|没听说过|查不到|找不到)"
)


def _text(value) -> str:
    return str(value or "")


def _has_fact_object_near(text: str, start: int, end: int, radius: int = 14) -> bool:
    window = text[max(0, start - radius): min(len(text), end + radius)]
    return bool(_FACT_OBJECT_RE.search(window))


def request_needs_recall(text: str) -> bool:
    """问题是否指向“莲心自己 / 用户 / 过往”的具体事实。"""
    value = _text(text).strip()
    if not value:
        return False
    if not _FACT_OBJECT_RE.search(value):
        return False
    return bool(_SELF_RE.search(value) or _HISTORY_RE.search(value))


def looks_like_denial(text: str) -> bool:
    """模型是否在断言“不存在/没有”。只认与事实对象相邻的否认。"""
    value = _text(text)
    if not value:
        return False
    for match in _DENIAL_RE.finditer(value):
        if _has_fact_object_near(value, match.start(), match.end()):
            return True
    return False


def is_pure_external_lookup(text: str) -> bool:
    """只有“问外部世界的最新信息”才跳过预回忆。

    自指 / 历史问题即使提到网页、联网、最新，也不能跳过——这正是
    “帮我打开你的 github 仓库网页”失败的根因。
    """
    if request_needs_recall(text):
        return False
    try:
        from brain.request_tool_policy import is_external_lookup_request
        return bool(is_external_lookup_request(text))
    except Exception:
        return False


def _relevance_terms_split(text: str) -> "tuple[list[str], list[str]]":
    """把查询拆成（拉丁词, 中文二元组）。

    拉丁词（github、repo 等）是强信号；中文二元组很容易被通用词
    （“今天”“时间”“我的”）蒙中，因此单独区分、要求更强证据。
    """
    value = _text(text).lower()
    latin = re.findall(r"[a-z0-9_]{3,}", value)
    bigrams: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        bigrams.extend(run[index:index + 2] for index in range(len(run) - 1))
    return latin, bigrams


def _relevance_terms(text: str) -> "list[str]":
    latin, bigrams = _relevance_terms_split(text)
    return latin + bigrams


def _is_relevant(content: str, latin: "list[str]", bigrams: "list[str]") -> bool:
    """命中一个拉丁词，或命中至少两个不同的中文二元组，才算相关。

    只命中一个中文二元组时属于分词噪声（例如“今天广州天气”撞上
    一段含“今天”的时间胶囊记忆），宁可漏召回也不要往 prompt 里
    塞两千多字的无关记忆。
    """
    if not latin and not bigrams:
        return True
    lowered = content.lower()
    if any(term in lowered for term in latin):
        return True
    matched = {term for term in bigrams if term in content}
    return len(matched) >= 2


def cheap_recall(query: str, *, limit: int = _MAX_HITS, scan: int = 40) -> "list[dict]":
    """FTS5/BM25 关键词召回（不加载向量模型）。"""
    value = _text(query).strip()
    if len(value) < 2:
        return []
    try:
        from brain.memory_rag import _lexical_memory_results
        rows = _lexical_memory_results(value, limit=max(1, int(scan)))
    except Exception as exc:
        logger.debug("廉价预回忆通道不可用: %s", exc)
        return []

    latin, bigrams = _relevance_terms_split(value)
    hits: list[dict] = []
    for _score, item in rows:
        content = _text(item.get("content"))
        if not content:
            continue
        if not _is_relevant(content, latin, bigrams):
            # FTS 的 CJK 分词与查询分词不一致时会产生噪声命中，这里补一道相关性过滤。
            continue
        hits.append(item)
        if len(hits) >= max(1, int(limit)):
            break
    return hits


def format_pre_recall_context(hits: "list[dict]", self_hits: "list[dict]") -> str:
    if not hits and not self_hits:
        return ""
    lines = ["【可能相关的既有记忆】"]
    for hit in self_hits:
        source = hit.get("source") or ""
        suffix = f"（来源：{source}）" if source else ""
        lines.append(f"· [自我事实] {hit.get('display', '')}：{hit.get('value', '')}{suffix}")
    for item in hits:
        memory_id = item.get("memory_id") or item.get("id") or "?"
        content = _text(item.get("content")).strip()
        recorded = _text(item.get("updated_at") or item.get("created_at")).strip()[:10]
        suffix = f"（记录于 {recorded}）" if recorded else ""
        lines.append(f"· [记忆#{memory_id}] {content}{suffix}")
    lines.append(
        "以上是系统检索到的既有事实。与你的判断冲突时以它们为准；"
        "直接相关时不得声称“没有记录”或“不知道”。"
    )
    return "\n".join(lines)[:_MAX_CHARS]


def build_pre_recall_context(query: str) -> "tuple[str, int, int]":
    """返回 (注入文本, 记忆条数, 自我事实条数)。"""
    value = _text(query).strip()
    if not value:
        return "", 0, 0
    self_hits: "list[dict]" = []
    try:
        from brain.self_facts import lookup_self_fact
        self_hits = lookup_self_fact(value)
    except Exception as exc:
        logger.debug("自我事实检索不可用: %s", exc)
    hits: "list[dict]" = []
    if not is_pure_external_lookup(value):
        hits = cheap_recall(value)
    text = format_pre_recall_context(hits, self_hits)
    return text, len(hits), len(self_hits)
