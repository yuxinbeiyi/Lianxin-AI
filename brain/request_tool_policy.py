"""单次请求的工具边界。

这一层只回答两个问题：当前请求允许模型看到哪些工具，以及某次实际调用
是否得到了用户授权。它不依赖模型自行理解权限，因此可作为最终代码防线。
"""

from __future__ import annotations

import re
from typing import Iterable

from brain.request_context import parse_request_context


URL_RE = re.compile(r"https?://[^\s<>\]\[\)（）。]+", re.IGNORECASE)

NETWORK_READ_TOOLS = {
    "web_search", "fetch_webpage", "fetch_webpage_via_api",
    "fetch_webpage_stealth", "fetch_webpage_browser",
}
URL_FETCH_TOOLS = {"fetch_webpage"}
GITHUB_READ_TOOLS = {
    "github_search_repositories", "github_get_readme", "github_get_file",
    "github_list_directory", "github_list_commits",
}
BROWSER_TOOLS = {
    "browser_navigate", "browser_snapshot", "browser_click",
    "browser_fill", "browser_press", "browser_scroll",
    "browser_wait", "browser_tabs", "browser_screenshot",
    "browser_connect", "browser_disconnect",
}

_BROWSER_INTERACTION_RE = re.compile(
    r"(?:"
    r"用浏览器|浏览器(?:打开|访问|进入|操作|点击|输入|填写|登录|截图|滚动)|"
    r"打开网页|网页上(?:点击|输入|填写|登录|搜索|滚动)|"
    r"页面上(?:点击|输入|填写|登录|选择|滚动)|"
    r"网页(?:点击|填表|自动登录|自动化)|"
    r"浏览器自动化|接管(?:我)?(?:已经|已)?打开的浏览器|接管浏览器|CDP|"
    r"(?:搜|搜索|查找|检索).{0,48}(?:打开|访问|进入|浏览器|接管)"
    r")",
    re.IGNORECASE,
)

_NETWORK_CODE_RE = re.compile(
    r"\b(?:requests?|urllib|httpx|aiohttp|socket)\b|https?://",
    re.IGNORECASE,
)

_EXPLICIT_CHANGE_WORDS = (
    "添加", "新增", "删除", "移除", "修改", "更新", "设置", "配置",
    "启用", "开启", "禁用", "停用", "关闭", "取消使用", "调整", "排序", "恢复默认",
    "合并", "清理", "整理", "保存",
    "add", "delete", "remove", "change", "enable", "disable", "reset",
)

_MEMORY_SAVE_WORDS = (
    "记住", "记下来", "保存到长期记忆", "存入长期记忆", "写入长期记忆",
    "save this memory", "remember this",
)

_MEMORY_BACKGROUND_MARKERS = (
    "我叫", "我的名字", "我喜欢", "我偏好", "我习惯", "我是", "我在",
    "我从事", "我的职业", "我的工作", "我的项目", "我目前使用",
)


def _auto_save_enabled() -> bool:
    """「对话过程中自动保存记忆」开关：开启时允许莲心自主判断保存与去重。"""
    try:
        from config import get_memory_config
        return bool(get_memory_config().get("conversation_auto_save", False))
    except Exception:
        return False


def classify_memory_write_intent(text: str) -> str:
    """Classify memory writes without inferring consent for immediate writes."""
    value = _normalized_request_text(text).lower()
    if any(token in value for token in _MEMORY_SAVE_WORDS):
        return "explicit"
    if any(token in value for token in _MEMORY_BACKGROUND_MARKERS):
        return "background"
    return "none"


def _normalized_request_text(text: str) -> str:
    """只将当前用户意图交给权限层，隔离引用中的旧 URL/任务。"""
    return parse_request_context(text).routing_text


def network_change_requested(text: str) -> bool:
    value = _normalized_request_text(text).lower()
    has_context = any(token in value for token in (
        "联网", "网络工具", "搜索工具", "抓取工具", "知乎搜索", "tavily", "firecrawl",
    ))
    return has_context and any(token in value for token in _EXPLICIT_CHANGE_WORDS)


def requires_verified_web_content(text: str) -> bool:
    value = _normalized_request_text(text).lower()
    return bool(extract_urls(value)) or any(token in value for token in (
        "核实正文", "核验正文", "核实原文", "核验原文", "打开原文", "读取正文",
        "抓取正文", "看完正文", "verify the article", "fetch the page",
    ))


def _event_succeeded(event: dict, name: str | None = None) -> bool:
    if name and event.get("name") != name:
        return False
    if event.get("is_error") or event.get("authorized") is False:
        return False
    # 工具结果可能是网页、README 或源码正文；其中出现 “error”/“failed”
    # 是正常内容，不能据此否定已成功完成的抓取。统一复用工具层的前缀式
    # 状态分类，避免 GitHub 页面正文触发误判和无意义的强制重试。
    from brain.tool_usage import classify_tool_result
    return classify_tool_result(event.get("result", "")) in {"success", "cached"}


def has_successful_tool_call(audit: Iterable[dict] | None, names: set[str]) -> bool:
    return any(
        event.get("name") in names and _event_succeeded(event)
        for event in (audit or [])
    )


def has_successful_network_change(audit: Iterable[dict] | None) -> bool:
    return any(
        event.get("name") == "configure_network_tools"
        and str(event.get("args", {}).get("action", "status")).lower() != "status"
        and _event_succeeded(event)
        for event in (audit or [])
    )


def extract_urls(text: str) -> list[str]:
    """提取请求中的 HTTP(S) URL，并保持原始顺序去重。"""
    urls = [
        value.rstrip(".,!?;:'\"，！？；：")
        for value in URL_RE.findall(str(text or ""))
    ]
    return list(dict.fromkeys(value for value in urls if value))


def is_external_lookup_request(text: str) -> bool:
    """是否属于不应被本地文件/RAG 抢占的外部信息请求。"""
    value = _normalized_request_text(text).lower()
    return bool(extract_urls(value)) or any(token in value for token in (
        "联网", "上网", "网页", "搜索一下", "查一下最新", "最新消息",
        "最新新闻", "实时", "官网", "web search", "search online",
    ))


def request_tool_allowlist(text: str) -> set[str] | None:
    """URL 请求使用严格读取白名单；普通请求返回 ``None``。"""
    normalized = _normalized_request_text(text)
    if extract_urls(normalized):
        # A GitHub repository URL can represent either a generic webpage to
        # summarize or an explicit repository API task. Keep the former on the
        # fast fetch path while allowing the latter through the GitHub Skill.
        try:
            from brain.request_router import classify_request
            if "github" in classify_request(normalized).capabilities:
                return set(GITHUB_READ_TOOLS) | {"get_current_time"}
        except Exception:
            pass
        allowed = set(URL_FETCH_TOOLS) | {"get_current_time"}
        # 明确的浏览器交互不能被普通 URL 阅读策略降级掉。
        if _BROWSER_INTERACTION_RE.search(normalized):
            allowed.update(BROWSER_TOOLS)
        return allowed
    return None


def filter_definitions_for_request(definitions: Iterable[dict], text: str) -> list[dict]:
    """在工具定义注入前应用请求级白名单。"""
    definitions = list(definitions)
    browser_enabled = True
    try:
        from config import get_browser_config
        browser_enabled = bool(get_browser_config().get("enabled", True))
    except Exception:
        pass
    allowed = request_tool_allowlist(text)
    if allowed is None:
        return [
            item for item in definitions
            if browser_enabled or item.get("function", {}).get("name", "") not in BROWSER_TOOLS
        ]
    return [
        item for item in definitions
        if item.get("function", {}).get("name", "") in allowed
        and (browser_enabled or item.get("function", {}).get("name", "") not in BROWSER_TOOLS)
    ]


def authorize_tool_call(name: str, args: dict, request_text: str,
                        audit: Iterable[dict] | None = None) -> tuple[bool, str]:
    """执行前进行确定性授权，返回 ``(允许, 给模型的原因)``。"""
    request = _normalized_request_text(request_text)
    lowered = request.lower()

    if name in BROWSER_TOOLS:
        try:
            from config import get_browser_config
            if not bool(get_browser_config().get("enabled", True)):
                return False, "浏览器自动化能力当前已关闭，请先在浏览器能力设置中启用。"
        except Exception:
            pass

    allowed = request_tool_allowlist(request)
    if allowed is not None and name not in allowed:
        return False, (
            f"本轮是 URL 阅读请求，已阻止无关工具 {name}。"
            + (
                "请使用 browser_navigate/browser_snapshot/browser_click/browser_fill/browser_press/browser_scroll/browser_wait/browser_tabs 完成浏览器交互；"
                "如用户明确要求接管已打开浏览器，可使用 browser_connect。"
                if _BROWSER_INTERACTION_RE.search(request)
                else "请仅使用 fetch_webpage 获取该 URL 的正文。"
            )
        )

    if (name in NETWORK_READ_TOOLS and network_change_requested(request)
            and not has_successful_network_change(audit)):
        return False, "请先成功完成本轮联网工具配置变更，再执行搜索或网页读取。"

    if name in {"read_file", "read_file_chunk", "read_file_lines"}:
        path = str(args.get("path", ""))
        if extract_urls(path):
            return False, "URL 不是本地文件，请改用 fetch_webpage。"

    if name == "run_python_code" and _NETWORK_CODE_RE.search(str(args.get("code", ""))):
        return False, "禁止用 Python 绕过联网工具路由；请改用 web_search 或 fetch_webpage。"

    if name == "bilibili_add_tag":
        has_bilibili_context = any(token in lowered for token in ("b站", "哔哩哔哩", "bilibili"))
        has_change_intent = any(token in lowered for token in _EXPLICIT_CHANGE_WORDS) or "关注" in lowered
        if not (has_bilibili_context and has_change_intent):
            return False, "添加 B 站兴趣标签会修改用户数据，但用户本轮没有明确授权。"

    if name == "configure_network_tools":
        has_network_context = any(token in lowered for token in (
            "联网", "网络工具", "搜索工具", "抓取工具", "知乎搜索", "tavily", "firecrawl",
        ))
        has_change_intent = any(token in lowered for token in _EXPLICIT_CHANGE_WORDS)
        action = str(args.get("action", "status")).lower()
        if action != "status" and not (has_network_context and has_change_intent):
            return False, "修改联网工具配置需要用户在本轮明确提出启停、排序或恢复默认。"

    if name == "save_memory":
        if _auto_save_enabled():
            return True, ""
        intent = classify_memory_write_intent(request)
        if intent != "explicit":
            if intent == "background":
                return False, "这条信息将交给后台自动记忆提取；当前回合未获得立即写入授权。"
            return False, "立即写入长期记忆需要用户明确说“请记住”或“保存到长期记忆”。"

    if name in {"update_memory", "delete_memory", "review_memory_conflict"}:
        # 自动保存开启时，允许保存后自主去重/合并（只放行裁决工具，不改写工具）。
        if name == "review_memory_conflict" and _auto_save_enabled():
            return True, ""
        has_memory_context = "记忆" in lowered
        has_change_intent = any(token in lowered for token in _EXPLICIT_CHANGE_WORDS)
        if not (has_memory_context and has_change_intent):
            return False, "修改长期记忆需要用户在本轮明确提出相应操作。"

    return True, ""
