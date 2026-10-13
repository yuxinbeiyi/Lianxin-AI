"""把视觉模型输出收敛为可复用、可展示的观察证据。"""

from __future__ import annotations

import re


OBSERVATION_PROMPT = """请只记录画面中能够直接确认的事实，供另一个对话模型使用。
要求：
1. 控制在350字以内，优先写人物、物体、屏幕主要内容和可辨认文字；
2. 不要寒暄，不要写“好的”“以下是详细描述”，不要使用Markdown标题；
3. 看不清的文字、应用名称、人物身份必须写“不确定”，严禁根据图标或相似字猜测；
4. 不推断用户情绪、意图、工作进度或屏幕外发生的事情；
5. 使用3到6条简短事实，每条一行。"""

_INTRO_RE = re.compile(
    r"^(?:好的[，,。！!\s]*|这(?:张)?图片(?:展示|显示|中)[^：:\n]{0,80}[：:]?\s*|"
    r"这是一(?:张|份)[^：:\n]{0,80}(?:描述|画面)[^：:\n]{0,40}[：:]?\s*|"
    r"以下是[^：:\n]{0,80}[：:]?\s*)+",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*|^\s*[-—]{3,}\s*$")
_UNCERTAIN_APP_RE = re.compile(r"(?<!莲心)(?:瑶心|涟心|怜心)\s*AI", re.IGNORECASE)

# 视觉链路失败时返回的原因前缀（见 brain/vision.py 的 _fail 与 observation.analyze_observation）
VISION_FAILURE_PREFIXES = (
    "错误：",
    "图片理解失败",
    "[截图分析失败",
    "[摄像头分析失败",
)


def is_vision_failure(text: str) -> bool:
    """判断文本是"视觉调用失败的原因"而不是真实的观察内容。

    失败原因必须能被识别出来：历史上它被静默归一化成空串，
    于是莲心拿到空结果照常评论，日志里也查不到任何错误。
    """
    return str(text or "").lstrip().startswith(VISION_FAILURE_PREFIXES)


def normalize_observation(text: str, max_chars: int = 600) -> str:
    """去掉报告腔、危险推断和常见 OCR 误认，并限制上下文体积。

    失败原因统一返回空串——调用方应先 is_vision_failure() 判断，
    绝不能把失败文本当成观察内容展示给用户。
    """
    value = str(text or "").strip()
    if not value:
        return ""
    if is_vision_failure(value):
        return ""

    value = _INTRO_RE.sub("", value)
    value = _UNCERTAIN_APP_RE.sub("未确认名称的 AI 应用", value)
    lines: list[str] = []
    for raw in value.splitlines():
        line = _HEADING_RE.sub("", raw).strip()
        line = re.sub(r"^\s*(?:[-*•]|\d+[.、])\s*", "", line)
        line = line.replace("**", "").strip()
        if not line or line in {"主要界面内容与布局", "主要内容", "画面描述"}:
            continue
        # 视觉模型常把推测包装成事实；保留画面事实，丢弃纯推断句。
        if any(marker in line for marker in (
            "似乎在进行", "可能正在", "应该是在", "看起来他想", "表明用户",
        )):
            continue
        lines.append(line)

    compact = "\n".join(dict.fromkeys(lines)).strip()
    if len(compact) <= max_chars:
        return compact
    clipped = compact[:max_chars].rsplit("\n", 1)[0].strip()
    return (clipped or compact[:max_chars]).rstrip("，,。；;") + "……"


def observation_preview(text: str, max_chars: int = 320) -> str:
    normalized = normalize_observation(text, max_chars=max_chars)
    return normalized
