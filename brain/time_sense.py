"""时间感知公共工具：把时间间隔语义化，供对话与主动聊天复用。

提供：
- describe_elapsed: 把「距上次对话多久」描述成自然中文（刚刚/几分钟前/跨天等）
- build_time_sense_block: 生成【时间感知】注入块（间隔 + 跨天/长间隔认知引导）
- build_recent_timeline: 生成【最近对话时间线】块（最近消息带相对时间）
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def describe_elapsed(now: datetime, last: datetime) -> str:
    """把距上次时间 last 到 now 的间隔描述成自然中文。"""
    seconds = int((now - last).total_seconds())
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    if last.date() == now.date():
        return f"{minutes // 60} 小时前（{_fmt_time(last)}）"
    days = (now.date() - last.date()).days
    if days == 1:
        return f"昨天 {_fmt_time(last)}"
    if days == 2:
        return f"前天 {_fmt_time(last)}"
    return f"{days} 天前（{last.strftime('%m月%d日')} {_fmt_time(last)}）"


def build_time_sense_block(
    now: datetime,
    last_reply: Optional[datetime],
    *,
    warn_hours: float = 2.0,
) -> str:
    """生成【时间感知】注入块：间隔描述 + 跨天/长间隔认知引导。

    当间隔超过 warn_hours 小时或跨天时，追加认知引导，提醒莲心
    用户状态可能已变化（如已下班回家），不要沿用上次对话的过时假设。
    """
    if last_reply is None:
        return ""
    hours = (now - last_reply).total_seconds() / 3600
    crossed_day = last_reply.date() != now.date()
    lines = [
        "【时间感知】",
        f"距上次对话：{describe_elapsed(now, last_reply)}"
        f"（上次对话时间：{last_reply.strftime('%m月%d日 %H:%M')}）",
    ]
    if crossed_day or hours >= warn_hours:
        lines.append(
            "注意：距上次对话已跨天或超过一段时间，用户的状态可能已经变化"
            "（例如已下班回家）。不要沿用上次对话结尾的场景假设；"
            "若话题明显过时，应先自然确认或换话题。"
        )
    return "\n".join(lines)


def relative_day_label(dt: datetime, now: datetime) -> str:
    """把某时间相对今天标注成 今天/昨天/前天/具体日期。"""
    days = (now.date() - dt.date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days == 2:
        return "前天"
    return dt.strftime("%m月%d日")


def build_recent_timeline(
    history_mgr,
    session_id: int,
    now: datetime,
    *,
    max_items: int = 8,
) -> str:
    """生成【最近对话时间线】块：最近几条消息带相对时间标注。"""
    try:
        msgs = history_mgr.get_messages(session_id, limit=max_items)
    except Exception:
        return ""
    if not msgs:
        return ""
    lines = ["【最近对话时间线】"]
    for m in msgs:
        ts = m.get("timestamp") or ""
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            when = f"{relative_day_label(dt, now)} {_fmt_time(dt)}"
        except Exception:
            when = ts
        speaker = "用户" if m.get("role") == "user" else "莲心"
        content = (m.get("content") or "").strip().replace("\n", " ")[:120]
        if content:
            lines.append(f"{when} {speaker}：{content}")
    lines.append("以上话题发生的时间已标注，请据此判断哪些话题已经过时、哪些仍适用。")
    return "\n".join(lines)