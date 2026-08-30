"""对话上下文窗口与工具输出压缩的纯函数基础设施。

本模块不调用模型、不读写数据库，也不持有全局会话状态。AgentCore 负责
生成摘要，HistoryManager 负责持久化快照；这里仅负责安全边界、降级摘要
和工具结果压缩，确保所有调用端使用同一套规则。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
import re


DEFAULT_TOKEN_THRESHOLD = 80_000
DEFAULT_TOOL_RESULT_MAX_CHARS = 12_000
DEFAULT_STALE_TOOL_RESULT_MAX_CHARS = 2_400
DEFAULT_KEEP_RECENT_TOOL_RESULTS = 4
DEFAULT_SUMMARY_MAX_CHARS = 4_000


@dataclass(frozen=True)
class HistoryWindow:
    """一次窗口选择的不可变结果。"""

    turns: list[list[dict]]
    overflow_messages: list[dict]
    recent_messages: list[dict]
    should_compress: bool
    trigger: str

    @property
    def covered_message_count(self) -> int:
        return len(self.overflow_messages)


def split_into_turns(history: list[dict]) -> list[list[dict]]:
    """按 user 边界切分消息，保持 assistant/tool 调用链完整。

    system 消息由请求编排层单独管理，不进入会话窗口。主动消息等出现在首个
    user 之前的 assistant/tool 消息会成为独立 turn，不会被静默丢弃。
    """
    turns: list[list[dict]] = []
    current: list[dict] = []

    for message in history:
        role = message.get("role")
        if role == "system":
            continue
        if role == "user":
            if current:
                turns.append(current)
            current = [message]
        elif role in ("assistant", "tool"):
            current.append(message)

    if current:
        turns.append(current)
    return turns


def select_history_window(
    history: list[dict],
    *,
    keep_turns: int,
    trigger_turns: int,
    last_input_tokens: int = 0,
    token_threshold: int = DEFAULT_TOKEN_THRESHOLD,
    force: bool = False,
) -> HistoryWindow:
    """选择应摘要的完整旧 turn 和应原样保留的最近 turn。"""
    keep_turns = max(1, int(keep_turns))
    trigger_turns = max(keep_turns, int(trigger_turns))
    token_threshold = max(1, int(token_threshold))
    turns = split_into_turns(history)

    if len(turns) <= keep_turns and not force:
        return HistoryWindow(turns, [], list(history), False, "none")

    turn_triggered = len(turns) > trigger_turns
    token_triggered = int(last_input_tokens or 0) >= token_threshold
    should_compress = force or turn_triggered or token_triggered
    if not should_compress:
        return HistoryWindow(turns, [], list(history), False, "none")

    overflow_turns = turns[:-keep_turns]
    recent_turns = turns[-keep_turns:]
    trigger = "snapshot" if force else "tokens" if token_triggered else "turns"
    return HistoryWindow(
        turns=turns,
        overflow_messages=[m for turn in overflow_turns for m in turn],
        recent_messages=[m for turn in recent_turns for m in turn],
        should_compress=True,
        trigger=trigger,
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return str(content or "").strip()


def format_messages_for_summary(messages: list[dict], max_chars: int = 24_000) -> str:
    """把完整消息链格式化为有限长度的摘要输入。"""
    labels = {"user": "用户", "assistant": "莲心", "tool": "工具结果"}
    lines: list[str] = []
    remaining = max(1_000, int(max_chars))

    for message in messages:
        text = _content_text(message.get("content"))
        if not text:
            continue
        label = labels.get(message.get("role"), str(message.get("role", "消息")))
        per_message = min(1_200, max(180, remaining // max(1, len(messages))))
        if len(text) > per_message:
            text = text[:per_message] + "…"
        line = f"{label}：{text}"
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line)

    return "\n".join(lines)


def build_fallback_summary(messages: list[dict], max_chars: int = 4_000) -> str:
    """模型摘要失败时保留真实信息的确定性降级摘要。

    与“直接截断”不同，这个摘要让每条被覆盖消息至少保留一个短片段，避免
    网络或模型故障导致早期上下文永久消失。
    """
    if not messages:
        return ""
    budget = max(800, int(max_chars))
    per_message = min(320, max(80, (budget - 80) // len(messages)))
    labels = {"user": "用户", "assistant": "莲心", "tool": "工具"}
    lines = [f"【降级历史摘要｜{len(messages)}条消息】"]
    for message in messages:
        text = _content_text(message.get("content"))
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > per_message:
            text = text[:per_message] + "…"
        lines.append(f"- {labels.get(message.get('role'), '消息')}：{text}")
    result = "\n".join(lines)
    return result[:budget]


def compact_summary_text(content: Any, max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> str:
    """将滚动摘要限制在固定预算内，同时保留最早事实和最近进展。"""
    text = _content_text(content)
    max_chars = max(800, int(max_chars))
    if len(text) <= max_chars:
        return text
    marker = "\n…【摘要已按固定预算压缩】…\n"
    available = max_chars - len(marker)
    head = max(400, int(available * 0.58))
    tail = max(300, available - head)
    return text[:head] + marker + text[-tail:]


def merge_summaries_bounded(
    old_summary: str, new_summary: str,
    max_chars: int = DEFAULT_SUMMARY_MAX_CHARS,
) -> str:
    """模型合并失败时使用的确定性、有界合并。"""
    old_text = str(old_summary or "").strip()
    new_text = str(new_summary or "").strip()
    max_chars = max(800, int(max_chars))
    divider = "\n【新增摘要】\n"
    prefix = "【早期摘要】\n"
    available = max_chars - len(prefix) - len(divider)
    old_budget = max(240, int(available * 0.48))
    new_budget = max(240, available - old_budget)

    def clip_segment(text: str, budget: int) -> str:
        if len(text) <= budget:
            return text
        marker = "\n…【片段压缩】…\n"
        usable = max(1, budget - len(marker))
        head = max(1, int(usable * 0.62))
        tail = max(0, usable - head)
        return text[:head] + marker + (text[-tail:] if tail else "")

    result = (
        prefix + clip_segment(old_text, old_budget)
        + divider + clip_segment(new_text, new_budget)
    )
    return result[:max_chars]


_TEXT_TOOL_PROTOCOL_RE = re.compile(
    r"<\s*/?\s*(?:tool(?:_call)?|function|parameter)\b|"
    r"<\s*(?:function|parameter)\s*=|"
    r"<\s*[｜|]\s*DSML\s*[｜|]\s*(?:tool_calls?|invoke|parameter)?",
    re.IGNORECASE,
)

# 部分兼容网关会把函数调用降级成 ``get_weather(city=...)`` 纯文本。
# 这不是可执行的 tool_call，不能直接展示或写入对话历史。
_TEXT_FUNCTION_CALL_RE = re.compile(
    r"^\s*[A-Za-z_]\w*\s*\([^\n]{1,2000}\)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def contains_textual_tool_protocol(content: Any, *, has_real_tool_result: bool = False) -> bool:
    """检测被模型写进普通正文的内部工具协议，包括未闭合的流式前缀。"""
    # 当 has_real_tool_result=True 时，本轮已有真实工具结果，模型提及工具名属正常总结，仅检测 XML/DSML 标签形式伪调用。
    text = _content_text(content)
    if not text:
        return False
    lowered = text.lower()
    if _TEXT_TOOL_PROTOCOL_RE.search(text):
        return True
    if "<tool" in lowered or "<function" in lowered or "<parameter" in lowered:
        return True
    if "dsml" in lowered and ("tool_call" in lowered or "<｜" in text):
        return True
    # DSML 序列化形态（含只有闭合标签的残片）：无论本轮是否已有工具结果都算泄漏
    if _DSML_REGION_RE.search(text) or _DSML_TAG_RE.search(text) or _DSML_ORPHAN_RE.search(text):
        return True
    if _TOOL_TOKEN_RE.search(text):
        return True
    if has_real_tool_result:
        return False
    return _TEXT_FUNCTION_CALL_RE.match(text) is not None


# 伪工具调用的可剥离形态：成对 XML 块、流式截断的未闭合块、
# DeepSeek 系网关的 DSML 序列化文本、整行「【调用 xxx(...)】」。
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\s*(?:tool_call|function_call)\s*>.*?<\s*/\s*(?:tool_call|function_call)\s*>|"
    r"<\s*(?:function|tool)\s*>.*?<\s*/\s*(?:function|tool)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_TOOL_BLOCK_RE = re.compile(
    r"<\s*(?:tool_call|function_call|function)\s*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
# DSML 形态：<｜DSML｜function_calls><｜DSML｜invoke name="...">…</｜DSML｜invoke></｜DSML｜function_calls>
# 必须整区域删除（区域内只有调用结构，没有用户正文），只删标签前缀会留下孤儿残肢。
_DSML_REGION_RE = re.compile(
    r"<\s*/?\s*｜DSML｜\s*function_calls\s*>.*?(?:<\s*/?\s*｜DSML｜\s*function_calls\s*>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TAG_RE = re.compile(r"<\s*/?\s*｜DSML｜[^>\n]*>")
_DSML_ORPHAN_RE = re.compile(
    r"^\s*(?:function_calls?|invoke|parameter)\b[^>\n]*>",
    re.MULTILINE | re.IGNORECASE,
)
_TOOL_TOKEN_RE = re.compile(r"<\s*[｜|]\s*tool[^>\n]{0,30}[｜|]\s*>?")
_NAMED_CALL_LINE_RE = re.compile(
    r"^\s*【?\s*(?:调用|执行|工具调用)[^】()\n]{0,60}\([^)\n]{0,400}\)\s*】?\s*[：:]?\s*$",
    re.MULTILINE,
)


def strip_textual_tool_protocol(content: Any, *, tool_names=None) -> tuple[str, bool]:
    """剥离正文中的伪工具调用标记，尽量保留正常回答文本。

    返回 ``(清理后的文本, 是否发生了剥离)``。提供 ``tool_names``（已知工具
    名集合）时，成对标签块只有在其内容引用了这些工具名时才剥离，避免误伤
    正常讨论工具调用语法的回答；传 ``None`` 则无条件剥离（用于历史洗白）。
    """
    text = _content_text(content)
    if not text:
        return text, False

    def _drop_block(block: str) -> bool:
        if not tool_names:
            return True
        return not any(name and name in block for name in tool_names)

    cleaned = _DSML_REGION_RE.sub("", text)
    cleaned = _DSML_TAG_RE.sub("", cleaned)
    cleaned = _DSML_ORPHAN_RE.sub("", cleaned)
    cleaned = _TOOL_TOKEN_RE.sub("", cleaned)
    cleaned = _TOOL_CALL_BLOCK_RE.sub(
        lambda m: "" if _drop_block(m.group(0)) else m.group(0), cleaned
    )
    cleaned = _OPEN_TOOL_BLOCK_RE.sub(
        lambda m: "" if _drop_block(m.group(0)) else m.group(0), cleaned
    )
    cleaned = _NAMED_CALL_LINE_RE.sub("", cleaned)
    # 整体就是一条函数调用文本 → 没有可保留的正文
    if _TEXT_FUNCTION_CALL_RE.match(cleaned.strip()):
        return "", True
    if cleaned != text:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, True
    return cleaned, False


_MEMORY_BLOCK_RE = re.compile(
    r"(?:不要|不需要|无需|别|禁止).{0,16}"
    r"(?:保存|记录|存入|写入|加入).{0,10}(?:长期记忆|永久记忆|记忆库)|"
    r"(?:长期记忆|永久记忆|记忆库).{0,10}"
    r"(?:不要|不需要|无需|别|禁止).{0,12}(?:保存|记录|存入|写入|加入)",
)
_MEMORY_ALLOW_RE = re.compile(
    r"(?:可以|允许|恢复|重新|继续).{0,12}"
    r"(?:保存|记录|存入|写入|加入).{0,10}(?:长期记忆|永久记忆|记忆库)|"
    r"(?:允许|恢复|开启).{0,8}(?:长期记忆|永久记忆|记忆保存)",
)
_SESSION_SCOPE_MARKERS = ("本次会话", "这次会话", "这段对话", "测试期间", "接下来")


def memory_persistence_directive(content: Any) -> str:
    """识别用户对长期记忆写入的明确授权边界。

    返回 ``block_request``、``block_session``、``allow`` 或 ``none``。
    仅处理明确的同意/拒绝表达，不推断含糊语义。
    """
    text = _content_text(content)
    if not text:
        return "none"
    if _MEMORY_ALLOW_RE.search(text):
        return "allow"
    if not _MEMORY_BLOCK_RE.search(text):
        return "none"
    if any(marker in text for marker in _SESSION_SCOPE_MARKERS):
        return "block_session"
    return "block_request"


def compact_tool_result(content: Any, max_chars: int) -> str:
    """压缩单个工具结果，保留开头、结尾和原始长度。"""
    text = _content_text(content)
    max_chars = max(400, int(max_chars))
    if len(text) <= max_chars:
        return text
    marker = f"\n…【工具输出已压缩，原始 {len(text)} 字】…\n"
    available = max_chars - len(marker)
    head = max(200, int(available * 0.72))
    tail = max(100, available - head)
    return text[:head] + marker + text[-tail:]


def prune_stale_tool_outputs(
    messages: list[dict],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT_TOOL_RESULTS,
    latest_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
    stale_max_chars: int = DEFAULT_STALE_TOOL_RESULT_MAX_CHARS,
) -> list[dict]:
    """在不破坏 tool_call_id 配对的前提下压缩工具输出内容。"""
    result = deepcopy(messages)
    tool_indices = [i for i, m in enumerate(result) if m.get("role") == "tool"]
    recent_count = max(0, int(keep_recent))
    recent = set(tool_indices[-recent_count:]) if recent_count else set()

    for index in tool_indices:
        limit = latest_max_chars if index in recent else stale_max_chars
        result[index]["content"] = compact_tool_result(
            result[index].get("content", ""), limit
        )
    return result


def extract_input_tokens(usage: Any) -> int:
    """兼容 OpenAI/Gemini/LiteLLM usage 对象提取本次输入 token。"""
    if usage is None:
        return 0
    keys = ("prompt_tokens", "input_tokens", "promptTokenCount")
    if isinstance(usage, dict):
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return max(0, int(value))
        return 0
    for key in keys:
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 0
