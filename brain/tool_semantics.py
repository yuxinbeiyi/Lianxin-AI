"""工具执行语义分类：决定“同名同参数”的跨轮去重该怎么判。

背景（见 docs/工具调用执行链优化方案_2026-10-16.md G2.2 / 缺陷 D9）：
现有跨轮去重只看 `(工具名, 参数)`，但 `browser_snapshot({})` 的结果取决于
**当前页面**——中间一旦发生导航，第二次截图的含义就完全不同，却被判成重复。

因此按执行语义分三类：
- DETERMINISTIC_READ 确定性读取：结果有效且未过期时可复用缓存，判重合适；
- DYNAMIC_OBSERVE   动态观察：结果随环境变化，判重键必须带上“代次”（如导航代次）；
- SIDE_EFFECT       有副作用的操作：写文件/发消息等，仍按判重 + 确认执行。
"""

from __future__ import annotations

import json

DETERMINISTIC_READ = "deterministic_read"
DYNAMIC_OBSERVE = "dynamic_observe"
SIDE_EFFECT = "side_effect"

_DETERMINISTIC_READ = frozenset({
    "read_file", "read_file_chunk", "read_file_lines", "list_directory",
    "glob_files", "grep_file", "get_file_info_everything",
    "search_files_everything", "search_code", "search_graph_memory",
    "search_conversation_history", "search_cross_session",
    "github_get_readme", "github_get_file", "github_list_directory",
    "github_list_commits", "github_search_repositories",
    "web_search", "fetch_webpage", "fetch_webpage_via_api",
    "fetch_webpage_browser", "fetch_webpage_stealth",
    "get_current_time", "get_weather", "ocr_image", "describe_image",
})

# 动态观察：结果随“当前页面/当前环境”变化。
_DYNAMIC_OBSERVE = frozenset({
    "browser_snapshot", "browser_screenshot", "browser_tabs",
    "capture_desktop", "capture_from_camera", "look_at_camera",
    "get_clipboard", "query_self_status",
})

# 会改变“当前页面”的浏览器动作：成功一次，观察类结果的含义就变了。
NAVIGATION_TOOLS = frozenset({
    "browser_navigate", "browser_click", "browser_press", "browser_connect",
})

# 去重键需要附加导航代次的观察类工具。
_NAV_SENSITIVE = frozenset({"browser_snapshot", "browser_screenshot", "browser_tabs"})

_SIDE_EFFECT = frozenset({
    "write_file", "edit_file", "run_shell", "run_command", "run_python_code",
    "send_file_to_qq", "save_memory", "update_memory", "delete_memory",
    "update_current_state", "send_message", "open_app", "plan_tasks",
    "delegate_task", "generate_image", "generate_video", "write_docx",
    "write_excel", "format_document", "copy_excel_content",
})


def tool_semantics(name: str) -> str:
    """返回工具的执行语义类别；未知工具按“确定性读取”处理（保持旧行为）。"""
    value = str(name or "")
    if value in _DYNAMIC_OBSERVE:
        return DYNAMIC_OBSERVE
    if value in _SIDE_EFFECT:
        return SIDE_EFFECT
    if value in _DETERMINISTIC_READ:
        return DETERMINISTIC_READ
    return DETERMINISTIC_READ


def is_dynamic_observe(name: str) -> bool:
    return tool_semantics(name) == DYNAMIC_OBSERVE


def is_navigation_tool(name: str) -> bool:
    return str(name or "") in NAVIGATION_TOOLS


def build_dedup_key(name: str, args, *, nav_generation: int = 0):
    """构造跨轮去重键。

    动态观察类（且受导航影响）的工具会把当前导航代次并入键：
    导航之后重复 `browser_snapshot({})` 会被放行，未导航时的重复仍被拦下。
    """
    value = str(name or "")
    try:
        serialized = json.dumps(args if args is not None else {}, ensure_ascii=False,
                                sort_keys=True, default=str)
    except Exception:
        serialized = str(args)
    if value in _NAV_SENSITIVE:
        return (value, serialized, int(nav_generation or 0))
    return (value, serialized)
