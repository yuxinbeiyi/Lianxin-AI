"""跨轮数字事实一致性：记录最终答复中的关键数字，同主题再次出现时提醒模型保持一致。"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from utils.paths import get_user_data_dir


_CACHE_PATH = get_user_data_dir() / "fact_consistency.json"
_LOCK = threading.Lock()
_MAX_FACTS_PER_SESSION = 20
_MAX_SESSIONS = 30

_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:\d+(?:\.\d+)?\s*[%％]|\d[\d,]*(?:\.\d+)?\s*(?:万|千|百|亿)?"
    r"|[零〇一二两三四五六七八九十百千万亿]+)"
    r"(?![A-Za-z0-9_])"
)
_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]（）【】]+", re.IGNORECASE)
_EN_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*")
_EN_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "this", "that", "with", "as", "by", "at", "from", "it", "its", "v", "vs",
    "you", "your", "not", "be", "do", "does", "did", "was", "were", "has", "have",
}


def _facts_path() -> Path:
    return _CACHE_PATH


def _load() -> dict:
    try:
        if _facts_path().exists():
            data = json.loads(_facts_path().read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    try:
        _facts_path().parent.mkdir(parents=True, exist_ok=True)
        _facts_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _extract_topic_tokens(text: str) -> set[str]:
    tokens = {t.lower() for t in _EN_TOKEN_PATTERN.findall(str(text or ""))}
    return {t for t in tokens if t not in _EN_STOPWORDS and len(t) >= 2}


def _extract_number_facts(text: str) -> list[dict]:
    facts = []
    for match in _NUMBER_PATTERN.finditer(str(text or "")):
        raw = match.group(0)
        start = max(0, match.start() - 16)
        end = min(len(text), match.end() + 16)
        context = text[start:end].replace("\n", " ")
        facts.append({"number": raw.strip(), "context": context.strip()})
        if len(facts) >= 8:
            break
    return facts


def record_facts(session_id, text: str) -> None:
    """最终答复生成后记录其中数字事实（含上下文与来源 URL）。"""
    if not text:
        return
    facts = _extract_number_facts(text)
    if not facts:
        return
    urls = list(dict.fromkeys(_URL_PATTERN.findall(text)))[:3]
    today = date.today().isoformat()
    with _LOCK:
        data = _load()
        entry = data.setdefault(str(session_id), {"date": today, "facts": []})
        if entry.get("date") != today:
            entry["date"] = today
            entry["facts"] = []
        for fact in facts:
            fact["urls"] = urls
            fact["recorded_at"] = datetime.now().strftime("%H:%M")
        entry["facts"] = (entry["facts"] + facts)[-_MAX_FACTS_PER_SESSION:]
        if len(data) > _MAX_SESSIONS:
            for old_key in sorted(data, key=lambda k: data[k].get("date", ""))[:- _MAX_SESSIONS]:
                data.pop(old_key, None)
        _save(data)


def build_consistency_context(session_id, user_text: str) -> str:
    """返回跨轮一致性提示；若用户问题与旧事实主题无重叠则返回空串。"""
    if not user_text:
        return ""
    user_tokens = _extract_topic_tokens(user_text)
    if not user_tokens:
        return ""
    today = date.today().isoformat()
    with _LOCK:
        data = _load()
        entry = data.get(str(session_id), {})
        if entry.get("date") != today:
            return ""
        facts = entry.get("facts", [])[-5:]
    if not facts:
        return ""
    matched = []
    for fact in facts:
        context_tokens = _extract_topic_tokens(fact.get("context", ""))
        if user_tokens & context_tokens:
            matched.append(fact)
    if not matched:
        return ""
    lines = [
        "【跨轮事实一致性提醒】",
        "你在这轮对话中早些时候提到过以下数字，如果本次回答涉及同一对象，请保持口径一致；"
        "若信息确实更新，请明确说明是更正/更新并给出依据：",
    ]
    for fact in matched:
        src = "、".join(fact.get("urls", [])) if fact.get("urls") else "（无来源）"
        lines.append(f"- {fact['context']}（来源：{src}）")
    return "\n".join(lines)
