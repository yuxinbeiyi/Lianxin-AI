"""Conservative final-answer checks for facts attributed to web pages.

The web evidence cache keeps source text available, but a model can still
invent a number while summarising it.  This module intentionally performs a
small, lexical check at the final-answer boundary.  It is not a fact checker
and does not attempt open-ended arithmetic or semantic inference.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from brain.web_evidence_cache import is_successful_web_result


logger = logging.getLogger("WebEvidenceGuard")

_WEB_FETCH_TOOLS = {
    "fetch_webpage",
    "fetch_webpage_via_api",
    "fetch_webpage_stealth",
    "fetch_webpage_browser",
}

_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    r"\d+(?:\.\d+)?\s*[%％]"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:万|千|百|亿)?"
    r"|[零〇一二两三四五六七八九十百千万亿]+"
    r")"
    r"(?![A-Za-z0-9_])"
)
_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*(?:日|号)?")
_MONTH_DAY_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)")
_TIME_PATTERN = re.compile(
    r"(?:(上午|下午|凌晨|晚上|早上|中午)\s*)?"
    r"(\d{1,2})(?::|：|点|时)(\d{1,2})?\s*(?:分)?"
)
_UNIT_PATTERN = re.compile(
    r"(?:条|个|次|人|件|家|篇|页|美元|元|人民币|小时|分钟|秒|天|周|月|年|倍|万|千|百|亿|%|％|度|℃|版本|位|名|名次|第)"
)
_CONFLICT_TOPIC_WORDS = (
    "峰值", "报告量", "数量", "比例", "百分比", "金额", "价格", "人数",
    "时长", "持续", "发生", "时间", "日期", "排名", "版本", "报告",
)
_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]（）【】]+", re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\([^)]*\)")
_CJK_DIGIT_VALUES = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CJK_SMALL = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


@dataclass(frozen=True)
class NumberMention:
    raw: str
    normalized: str
    kind: str
    start: int
    end: int
    has_unit: bool


@dataclass(frozen=True)
class WebEvidenceValidation:
    content: str
    checked: bool
    grounded: bool
    unsupported: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_count: int = 0


def _parse_chinese_integer(value: str) -> int | None:
    """Parse common integer Chinese numerals, without guessing decimals."""
    text = str(value or "").strip()
    if not text or not all(char in _CJK_DIGIT_VALUES or char in _CJK_SMALL for char in text):
        return None
    # Handle the common shorthand ``一万二``/``三万七``.  Without an explicit
    # 千/百/十 unit, the trailing digit denotes the next decimal place.
    if "万" in text:
        high, low = text.split("万", 1)
        if low and all(char in _CJK_DIGIT_VALUES for char in low):
            high_value = _parse_chinese_integer(high)
            if high_value is not None:
                low_value = int("".join(str(_CJK_DIGIT_VALUES[char]) for char in low))
                scale = {1: 1000, 2: 100, 3: 10}.get(len(low), 1)
                return high_value * 10_000 + low_value * scale
    if not any(char in _CJK_SMALL for char in text):
        return sum(_CJK_DIGIT_VALUES[char] for char in text)

    total = 0
    section = 0
    number = 0
    for char in text:
        if char in _CJK_DIGIT_VALUES:
            number = _CJK_DIGIT_VALUES[char]
            continue
        unit = _CJK_SMALL[char]
        if unit < 10_000:
            section += (number or 1) * unit
        else:
            section += number
            total += (section or 1) * unit
            section = 0
        number = 0
    return total + section + number


def _normalize_number(raw: str) -> tuple[str, str] | None:
    value = re.sub(r"\s+", "", str(raw or "")).replace(",", "")
    if not value:
        return None
    if value.endswith(("%", "％")):
        numeric = value[:-1]
        try:
            return f"percent:{float(numeric):g}", "percent"
        except ValueError:
            return None
    multiplier = 1
    # Chinese compound numerals (e.g. 三万七千) are parsed as a whole.
    # A trailing multiplier is peeled only from an Arabic expression such as
    # 3.7万, where the decimal value must be scaled.
    if value[-1:] in ("万", "千", "百", "亿") and re.match(r"^\d", value):
        multiplier = {"万": 10_000, "千": 1_000, "百": 100, "亿": 100_000_000}[value[-1]]
        value = value[:-1]
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        try:
            numeric = float(value) * multiplier
        except ValueError:
            return None
        rendered = str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"
        return f"number:{rendered}", "number"
    parsed = _parse_chinese_integer(value)
    if parsed is not None:
        return f"number:{parsed * multiplier}", "number"
    return None


def _without_urls(text: str) -> str:
    value = _MARKDOWN_LINK_PATTERN.sub(" ", str(text or ""))
    return _URL_PATTERN.sub(" ", value)


def extract_number_mentions(text: str) -> tuple[NumberMention, ...]:
    """Extract comparable numeric mentions, excluding URLs and link targets."""
    value = _without_urls(str(text or ""))
    mentions: list[NumberMention] = []
    covered: list[tuple[int, int]] = []

    def add(raw: str, start: int, end: int, *, kind: str | None = None, normalized: str | None = None) -> None:
        parsed = (normalized, kind) if normalized and kind else _normalize_number(raw)
        if not parsed:
            return
        normalized_value, inferred_kind = parsed
        has_unit = bool(_UNIT_PATTERN.search(value[max(0, start - 1): min(len(value), end + 8)]))
        mentions.append(NumberMention(raw, normalized_value, kind or inferred_kind, start, end, has_unit))
        covered.append((start, end))

    for match in _DATE_PATTERN.finditer(value):
        add(match.group(0), match.start(), match.end(), kind="date", normalized=(f"date:{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"))
    for match in _MONTH_DAY_PATTERN.finditer(value):
        add(match.group(0), match.start(), match.end(), kind="date", normalized=(f"date:month-day:{int(match.group(1)):02d}-{int(match.group(2)):02d}"))
    for match in _TIME_PATTERN.finditer(value):
        hour = int(match.group(2))
        if match.group(1) in ("下午", "晚上") and hour < 12:
            hour += 12
        if match.group(1) == "中午" and hour < 11:
            hour += 12
        minute = int(match.group(3) or 0)
        add(match.group(0), match.start(), match.end(), kind="time", normalized=f"time:{hour:02d}:{minute:02d}")

    for match in _NUMBER_PATTERN.finditer(value):
        if any(start <= match.start() < end or start < match.end() <= end for start, end in covered):
            continue
        add(match.group(0), match.start(), match.end())
    return tuple(mentions)


def _is_web_fact_request(request_text: str, audit: Iterable[dict], capabilities: Iterable[str]) -> bool:
    value = str(request_text or "").lower()
    if any(token in value for token in ("网页", "网页原文", "文章", "报道", "新闻", "原文", "页面", "according to", "article")):
        return True
    if any(capability in {"web_research", "web_fetch", "web_search"} for capability in capabilities):
        return True
    return any(
        event.get("name") in _WEB_FETCH_TOOLS
        and not event.get("is_error")
        and is_successful_web_result(event.get("result", ""))
        for event in audit
    )


def _evidence_texts(audit: Iterable[dict], evidence_text: str = "") -> list[str]:
    texts = [str(evidence_text or "").strip()] if str(evidence_text or "").strip() else []
    for event in audit:
        if event.get("name") not in _WEB_FETCH_TOOLS or event.get("is_error"):
            continue
        result = str(event.get("result", "") or "").strip()
        if result and is_successful_web_result(result):
            texts.append(result)
    return list(dict.fromkeys(texts))


def _question_demands_numeric_fact(request_text: str, answer: str) -> bool:
    value = f"{request_text}\n{answer}"
    return bool(re.search(
        r"(?:多少|几条|几次|数字|数量|峰值|比例|百分比|金额|价格|时间|几点|日期|哪天|持续|排名|第几|具体|原文.*(?:提到|声称|指出)|according to|how many|when|exact)",
        value,
        re.IGNORECASE,
    ))


def _context_window(text: str, mention: NumberMention, radius: int = 90) -> str:
    return str(text or "")[max(0, mention.start - radius): min(len(str(text or "")), mention.end + radius)].lower()


def _find_unsupported(answer_mentions: tuple[NumberMention, ...], evidence_mentions: tuple[NumberMention, ...], request_text: str) -> tuple[str, ...]:
    if not evidence_mentions:
        return ()
    high_risk = _question_demands_numeric_fact(request_text, "")
    unsupported: list[str] = []
    evidence_values = {mention.normalized for mention in evidence_mentions}
    for mention in answer_mentions:
        # Standalone list numbering and years in ordinary prose are too noisy
        # to police. Units, dates/times, percentages and numeric questions are
        # the useful high-risk subset.
        if not (mention.has_unit or mention.kind in {"date", "time", "percent"} or high_risk):
            continue
        if mention.normalized not in evidence_values:
            unsupported.append(mention.raw)
    return tuple(dict.fromkeys(unsupported))


def _find_conflicts(evidence_texts: list[str], request_text: str) -> tuple[str, ...]:
    """Flag competing values in similarly relevant evidence windows."""
    if len(evidence_texts) < 2 or not _question_demands_numeric_fact(request_text, ""):
        return ()
    topics = [word for word in _CONFLICT_TOPIC_WORDS if word in str(request_text or "")]
    if not topics:
        return ()
    candidates_by_topic: dict[str, set[str]] = {topic: set() for topic in topics}
    for text in evidence_texts:
        for mention in extract_number_mentions(text):
            window = _context_window(text, mention)
            for topic in topics:
                if topic in window:
                    candidates_by_topic[topic].add(mention.normalized)
    conflicted_topics = [topic for topic, values in candidates_by_topic.items() if len(values) > 1]
    if not conflicted_topics:
        return ()
    # Only return a compact diagnostic marker; the caller must not expose the
    # complete source text through logs or an automatic error message.
    return ("multiple_numeric_values",)


def _rewrite_sentences(content: str, unsupported: tuple[str, ...]) -> str:
    if not unsupported:
        return content
    pattern = re.compile(r"[^。！？!?\n]*(?:" + "|".join(re.escape(item) for item in unsupported) + r")[^。！？!?\n]*[。！？!?]?", re.IGNORECASE)
    replacement = "原文未提供该句中的具体数字，我无法可靠确认。"
    rewritten, count = pattern.subn(replacement, str(content or ""))
    if count:
        return rewritten.strip()
    return "我在当前网页原文中没有找到回答所需的具体数字，因此不能可靠确认。"


def validate_web_evidence_claims(
    content: str,
    request_text: str,
    audit: Iterable[dict] = (),
    *,
    capabilities: Iterable[str] = (),
    evidence_text: str = "",
) -> WebEvidenceValidation:
    """Validate high-risk numeric claims against successful web evidence."""
    text = str(content or "")
    events = list(audit or ())
    evidence_texts = _evidence_texts(events, evidence_text)
    if not text or not evidence_texts or not _is_web_fact_request(request_text, events, capabilities):
        return WebEvidenceValidation(text, False, True, evidence_count=len(evidence_texts))

    answer_mentions = extract_number_mentions(text)
    evidence_mentions = extract_number_mentions("\n\n".join(evidence_texts))
    unsupported = _find_unsupported(answer_mentions, evidence_mentions, request_text)
    conflicts = _find_conflicts(evidence_texts, request_text)
    if conflicts and not unsupported:
        logger.warning("[WebEvidence] final_guard conflict=%s evidence_count=%d", ",".join(conflicts), len(evidence_texts))
        return WebEvidenceValidation(
            "当前已读取的网页证据存在相互冲突的具体数字，我不能在没有区分来源的情况下给出确定结论。请让我重新核对原始网页。",
            True,
            False,
            conflicts=conflicts,
            evidence_count=len(evidence_texts),
        )
    if unsupported:
        logger.warning(
            "[WebEvidence] final_guard unsupported=%d evidence_count=%d",
            len(unsupported), len(evidence_texts),
        )
        return WebEvidenceValidation(
            _rewrite_sentences(text, unsupported),
            True,
            False,
            unsupported=unsupported,
            conflicts=conflicts,
            evidence_count=len(evidence_texts),
        )
    logger.debug(
        "[WebEvidence] final_guard grounded=True numbers=%d evidence_count=%d",
        len(answer_mentions), len(evidence_texts),
    )
    return WebEvidenceValidation(text, True, True, evidence_count=len(evidence_texts))


__all__ = [
    "NumberMention",
    "WebEvidenceValidation",
    "extract_number_mentions",
    "validate_web_evidence_claims",
]
