"""Conservative final-answer checks for facts attributed to web pages.

The web evidence cache keeps source text available, but a model can still
invent a number while summarising it.  This module intentionally performs a
small, lexical check at the final-answer boundary.  It is not a fact checker
and does not attempt open-ended arithmetic or semantic inference.

The check must never edit or delete the answer: it may only append one short
boundary sentence in the assistant's own voice.  Earlier versions replaced
whole sentences with a system-toned disclaimer, which destroyed correct
content and repeated itself once per matched number.
"""

from __future__ import annotations

import logging
import re
import threading
import time
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
# 英文页面（GitHub、README、外媒）里大量使用 Oct 13, 2026 这类写法，不识别
# 就会把答案里正确的中文日期判成“无依据”。
_EN_MONTH_DATE_PATTERN = re.compile(
    r"(?<![A-Za-z])(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?![0-9])",
    re.IGNORECASE,
)
_EN_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TIME_PATTERN = re.compile(
    r"(?:(上午|下午|凌晨|晚上|早上|中午)\s*)?"
    r"(\d{1,2})(?::|：|点|时)(\d{1,2})?\s*(?:分)?"
)
# 只有“真的会成为争议数据”的单位才纳入高风险判据：货币、百分比、时间、
# 度量、倍数。中文量词（个/条/第/页…）是叙述骨架而不是事实数据，把它们
# 当单位会把“第一个仓库”“两个都读了”误判成编造数字，进而在最终答案里
# 生成大段免责话术。用户明确在问数字时仍由 high_risk 分支兜底。
_UNIT_PATTERN = re.compile(
    r"(?:美元|人民币|元|小时|分钟|秒钟|秒|天|周|月|年|倍|万|千|百|亿|%|％|度|℃)"
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
        # 各模式之间会互相重叠（“2026 年 10 月 13 日”里还嵌着一个
        # “10 月 13 日”）。先到先得，否则同一天会被抽出两个不同粒度的值，
        # 与证据比对时互相判成“没有依据”，制造新的误伤。
        if any(start < covered_end and covered_start < end for covered_start, covered_end in covered):
            return
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
    for match in _EN_MONTH_DATE_PATTERN.finditer(value):
        _month = _EN_MONTHS.get(match.group(1)[:3].lower())
        if _month is None:
            continue
        add(
            match.group(0), match.start(), match.end(),
            kind="date",
            normalized=f"date:{int(match.group(3))}-{_month:02d}-{int(match.group(2)):02d}",
        )
    for match in _TIME_PATTERN.finditer(value):
        hour = int(match.group(2))
        if match.group(1) in ("下午", "晚上") and hour < 12:
            hour += 12
        if match.group(1) == "中午" and hour < 11:
            hour += 12
        minute = int(match.group(3) or 0)
        add(match.group(0), match.start(), match.end(), kind="time", normalized=f"time:{hour:02d}:{minute:02d}")

    for match in _NUMBER_PATTERN.finditer(value):
        add(match.group(0), match.start(), match.end())
    return tuple(mentions)


def _comparison_keys(mention: NumberMention) -> tuple[str, ...]:
    """返回比对用的规范化键。

    页面写 “Oct 13, 2026”、答案写 “10 月 13 日” 是完全正常的转述，粒度不同
    不代表编造。完整日期额外派生一个月日键，让两种写法能够互通。
    """
    keys = [mention.normalized]
    if mention.kind == "date" and mention.normalized.startswith("date:"):
        body = mention.normalized[len("date:"):]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", body):
            keys.append(f"date:month-day:{body[5:]}")
    return tuple(keys)


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
    # 证据里完全没有数字时不能直接放行：那恰恰意味着答案里的金额/日期/比例
    # 找不到任何出处。旧实现在这里提前 return，等于让编造的数字正好绕开
    # 这一层（实测：证据无数字 + 答案“9999 个 star、500 亿元”被判 grounded）。
    high_risk = _question_demands_numeric_fact(request_text, "")
    unsupported: list[str] = []
    evidence_values: set[str] = set()
    for mention in evidence_mentions:
        evidence_values.update(_comparison_keys(mention))
    for mention in answer_mentions:
        # Ordinary list numbering and years in prose are too noisy to police.
        # Real units, dates/times, percentages and explicit numeric questions
        # are the useful high-risk subset.
        if not (mention.has_unit or mention.kind in {"date", "time", "percent"} or high_risk):
            continue
        if not any(key in evidence_values for key in _comparison_keys(mention)):
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


# 证据边界说明的固定开头，同时用于“同一轮不重复追加”的幂等判断。
# 同一类说明准备多套措辞轮换：实测连续四轮追加逐字相同的句子，读起来
# 像系统水印而不像莲心在说话（缺陷 P1-1）。
_BOUNDARY_NOTE_MARKER = "另说一句"
_BOUNDARY_NOTE_UNSUPPORTED = (
    "另说一句：上面那几个数字，我只在原文里核准了一部分，先别当定论用。"
    "要我再回去把原文读一遍也行。",
    "另说一句：这几个数字在原网页里我没能逐一对上，先按待核实看待。"
    "你要的话我可以再翻一遍原文。",
    "另说一句：上面的数字我只对上了一部分，剩下的还没找到出处，别急着当定论。",
)
_BOUNDARY_NOTE_CONFLICT = (
    "另说一句：几份来源对同一个数字的说法不太一样，我先不给你定论。"
    "要我把原文都摊开核对一遍也行。",
    "另说一句：这几个数字在几份来源里对不上，我先不下结论。"
    "想让我摊开原文逐个核对也可以。",
    "另说一句：同一组数字在不同来源口径不一致，我先不拍板。",
)
_BOUNDARY_NOTE_MIXED = (
    "另说一句：上面那些数字我在原文里只核准了一部分，而且几份来源的口径"
    "还不太一样，先别当定论用。要我再核一遍也行。",
    "另说一句：这些数字我一部分能在原文里对上，另一部分几份来源说法还不一致，"
    "先别当定论。要我再核一遍也行。",
    "另说一句：上面的数字有的只在原文里对上一部分，有的几份来源口径不同，先当待核实。",
)
# 说明过于频繁时改用最短的一套，避免每轮回复都挂一段免责说明。
_BOUNDARY_NOTE_SHORT = (
    "另说一句：这些数字我只核准了一部分，先当待核实。",
    "另说一句：这几个数字还没核准完，先当待核实。",
)


class BoundaryNoteLimiter:
    """控制证据边界说明的重复度（轮换措辞 + 时间窗内降频）。

    纯追加式的边界说明本身是对的，但同一句话连说四遍会破坏沉浸感。这里
    只做两件事：同因说明按序轮换措辞；时间窗内超过上限后改用最短模板。
    """

    def __init__(self, *, window_seconds: float = 900.0, max_long_notes: int = 3) -> None:
        self._lock = threading.Lock()
        self._recent: list[float] = []
        self._index = 0
        self._window_seconds = float(window_seconds)
        self._max_long_notes = int(max_long_notes)

    def note(self, *, unsupported: tuple[str, ...] = (), conflicts: tuple[str, ...] = ()) -> str:
        if unsupported and conflicts:
            pool = _BOUNDARY_NOTE_MIXED
        elif conflicts:
            pool = _BOUNDARY_NOTE_CONFLICT
        elif unsupported:
            pool = _BOUNDARY_NOTE_UNSUPPORTED
        else:
            return ""
        with self._lock:
            now = time.monotonic()
            self._recent = [
                stamp for stamp in self._recent if now - stamp < self._window_seconds
            ]
            crowded = len(self._recent) >= self._max_long_notes
            self._recent.append(now)
            if crowded:
                pool = _BOUNDARY_NOTE_SHORT
            note = pool[self._index % len(pool)]
            self._index += 1
            return note


_DEFAULT_LIMITER = BoundaryNoteLimiter()


def _append_boundary_note(
    content: str,
    *,
    unsupported: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    limiter: "BoundaryNoteLimiter | None" = None,
) -> str:
    """保留原答案，只在末尾追加一行莲心口吻的证据边界说明。

    旧实现把含可疑数字的整句删除并替换成系统口吻免责模板，结果是正确信息
    丢失、同一段话按命中数重复多遍、还会用“网页可能截断”这类内部口径向
    用户解释。这里改为纯追加：不改动、不删除任何原句，最多追加一次。
    注意不能使用全角括号，显示前 text_hygiene 会把括号旁白整行剥掉。
    """
    text = str(content or "").strip()
    if not text:
        return text
    if _BOUNDARY_NOTE_MARKER in text:
        return text
    note = (limiter or _DEFAULT_LIMITER).note(
        unsupported=unsupported, conflicts=conflicts
    )
    if not note:
        return text
    return f"{text}\n{note}"


def validate_web_evidence_claims(
    content: str,
    request_text: str,
    audit: Iterable[dict] = (),
    *,
    capabilities: Iterable[str] = (),
    evidence_text: str = "",
    limiter: "BoundaryNoteLimiter | None" = None,
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
            _append_boundary_note(text, conflicts=conflicts, limiter=limiter),
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
            _append_boundary_note(
                text, unsupported=unsupported, conflicts=conflicts, limiter=limiter
            ),
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
    "BoundaryNoteLimiter",
    "NumberMention",
    "WebEvidenceValidation",
    "extract_number_mentions",
    "validate_web_evidence_claims",
]
