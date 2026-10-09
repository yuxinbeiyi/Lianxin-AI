# brain/music_lyrics.py
"""歌词整形：把播放器的结构化歌词转成适合喂给 LLM 的纯文本。

state["lyrics"] 的元素形如 {"time": 12.3, "text": "..."}。早期实现在
music_watcher 里做 " / ".join(str(x) for x in lines[:6])，模型收到的是
Python 字典字面量（"{'time': 0.0, 'text': '作词 : ななせ'}"），而且前几行
还是作词/作曲版权行，于是出现"念制作名单""胡编曲风"这类跑题评论。

本模块负责三件事：
1. 剔除版权行与占位行；
2. 按播放位置取"刚唱过 + 即将唱到"的几句；
3. 输出 "[mm:ss] 文本"（当前行用 "→" 标注）并给出纯音乐判定。
"""

from __future__ import annotations

import re
from typing import Any, List, Tuple

# 播放器在没有真实歌词时写入的占位文本（前后端共用同一份规则）
PLACEHOLDER_LYRICS = {
    "纯音乐，请欣赏", "纯音乐请欣赏", "纯音乐",
    "暂无歌词", "（暂无歌词）", "无歌词",
}

# 版权/制作信息行：对齐参考项目 server.js:915 的 isCreditLine，并补充常见字段。
_CREDIT_RE = re.compile(
    r"^\s*(作词|作曲|编曲|制作人|配唱制作|监制|出品|键盘|和声|录音|混音|母带|"
    r"吉他|贝斯|鼓|弦乐|OP|SP|By)\s*[:：]",
    re.IGNORECASE,
)

_MAX_BLOCK_CHARS = 400


def is_credit_line(text: str) -> bool:
    """判断是否为作词/作曲这类版权信息行。"""
    return bool(text) and bool(_CREDIT_RE.match(text))


def _coerce_time(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_lines(raw: Any) -> List[dict]:
    """把任意形态的歌词规整为 [{"time": float, "text": str}]。"""
    if isinstance(raw, str):
        raw = [{"time": 0.0, "text": raw}]
    if not isinstance(raw, (list, tuple)):
        return []
    lines = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("line") or "").strip()
            stamp = _coerce_time(item.get("time", item.get("start", 0.0)))
        else:
            text = str(item or "").strip()
            stamp = 0.0
        if not text:
            continue
        lines.append({"time": stamp, "text": text})
    lines.sort(key=lambda entry: entry["time"])
    return lines


def clean_lines(raw: Any) -> List[dict]:
    """剔除版权行与占位行，只留真正的歌词。"""
    return [
        entry for entry in parse_lines(raw)
        if entry["text"] not in PLACEHOLDER_LYRICS and not is_credit_line(entry["text"])
    ]


def is_instrumental(raw: Any) -> bool:
    """没有可用歌词（纯音乐/纯器乐/歌词只有版权行）时为 True。"""
    return not clean_lines(raw)


def format_time(seconds: Any) -> str:
    total = int(max(0.0, _coerce_time(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def upcoming_lines(lines: List[dict], position: Any, before: int = 1, after: int = 5) -> List[Tuple[dict, bool]]:
    """取播放位置附近的行，返回 [(歌词行, 是否正在唱)]。"""
    if not lines:
        return []
    pos = max(0.0, _coerce_time(position))
    index = next((i for i, entry in enumerate(lines) if entry["time"] >= pos), None)
    if index is None:
        index = len(lines) - 1
    start = max(0, index - max(0, before))
    end = min(len(lines), index + max(1, after))
    return [(lines[offset], offset == index) for offset in range(start, end)]


def format_for_prompt(raw: Any, position: Any, before: int = 1, after: int = 5) -> str:
    """输出可直接放进 prompt 的歌词片段；没有有效歌词时返回空串。"""
    window = upcoming_lines(clean_lines(raw), position, before=before, after=after)
    if not window:
        return ""
    rendered = [
        f"{'→' if is_current else ' '}[{format_time(entry['time'])}] {entry['text']}"
        for entry, is_current in window
    ]
    block = "\n".join(rendered)
    if len(block) > _MAX_BLOCK_CHARS:
        block = block[:_MAX_BLOCK_CHARS].rstrip() + " …"
    return block