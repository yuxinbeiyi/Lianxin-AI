"""
tool_registry.py — 工具注册中心
- 工具分类 + 调用统计（次数、成功率、耗时）
- 在 execute_tool() 中自动追踪，无需逐个修改 TOOL_EXECUTORS
"""

from dataclasses import dataclass
from typing import Optional

from brain.tool_usage import get_tool_usage_store

# ══════════════════════════════════════════════════════════
# Category definitions
# ══════════════════════════════════════════════════════════

CATEGORIES = {
    "📁 文件操作": [
        "read_file", "read_file_chunk", "read_file_lines", "clear_document_cache",
        "write_file", "edit_file", "list_directory",
        "glob_files", "grep_file", "search_code",
        "diff_files", "code_structure", "goto_definition",
        "find_references", "code_diagnostics",
        "read_excel", "write_excel", "copy_excel_content",
        "write_docx", "format_document",
    ],
    "💻 系统命令": [
        "run_command", "run_shell", "run_python_code",
        "open_app", "get_clipboard", "get_current_time",
        "get_system_info", "get_balance",
    ],
    "🌐 联网搜索": [
        "web_search", "fetch_webpage", "fetch_webpage_via_api",
        "fetch_webpage_browser", "fetch_webpage_stealth",
    ],
    "👁️ 视觉理解": [
        "describe_image", "ocr_image", "ocr_batch",
        "capture_from_camera", "capture_desktop",
    ],
    "🧠 记忆知识": [
        "save_memory", "update_current_state", "review_memory_conflict",
        "search_memory", "trace_memory_source", "explain_memory_quality", "update_memory",
        "delete_memory", "list_memories",
        "search_graph_memory", "discover_connections", "query_connected_entities",
        "delete_graph_entity", "add_graph_edge", "remove_graph_edge",
        "search_cross_session",
    ],
    "📋 待办提醒": [
        "add_todo", "list_todos", "complete_todo",
        "add_alarm", "list_alarms", "delete_alarm",
    ],
    "🎵 音乐日记": [
        "control_music", "get_music_playlist", "get_music_status",
        "read_diary", "write_diary",
        "read_note", "organize_note",
    ],
    "🤖 代理调度": [
        "plan_tasks", "delegate_task", "track_tasks",
    ],
    "🎙️ 语音外设": [
        "speak_voice", "set_voice_mood", "list_voice_styles",
        "shoulder_photo", "shoulder_pan", "shoulder_tilt",
        "shoulder_center", "shoulder_status", "shoulder_temp",
        "start_observation_mode", "stop_observation_mode",
        "shoulder_observe", "shoulder_human_track", "stop_human_track",
        "shoulder_face_track", "stop_face_tracking",
        "bilibili_search", "bilibili_add_tag", "bilibili_list_tags",
    ],
    "🌐 浏览器自动化": [
        "browser_navigate", "browser_snapshot", "browser_click",
        "browser_fill", "browser_press", "browser_scroll",
        "browser_wait", "browser_tabs", "browser_screenshot",
        "browser_connect", "browser_disconnect",
    ],
    "🔧 其他": [
        "get_weather", "set_user_city", "set_expression",
        "generate_image", "generate_video",
        "list_skills", "activate_skill", "deactivate_skill",
        "toggle_proactive_chat", "send_file_to_qq",
    ],
}

# Build name → category mapping
_NAME_TO_CATEGORY: dict[str, str] = {}
for _cat, _names in CATEGORIES.items():
    for _n in _names:
        _NAME_TO_CATEGORY[_n] = _cat


def get_category(name: str) -> str:
    return _NAME_TO_CATEGORY.get(name, "🔧 其他")


# ══════════════════════════════════════════════════════════
# ToolStats
# ══════════════════════════════════════════════════════════

@dataclass
class ToolStats:
    name: str
    category: str = ""
    call_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    last_called: str = ""          # ISO format
    last_duration_ms: float = 0.0
    total_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.success_count / self.call_count

    @property
    def avg_duration_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_duration_ms / self.call_count


# ══════════════════════════════════════════════════════════
# ToolRegistry
# ══════════════════════════════════════════════════════════

class ToolRegistry:
    """工具注册中心：管理工具分类和调用统计。"""

    def __init__(self):
        self._stats: dict[str, ToolStats] = {}

    def register(self, name: str, category: str = "") -> ToolStats:
        cat = category or get_category(name)
        if name not in self._stats:
            self._stats[name] = ToolStats(name=name, category=cat)
        else:
            self._stats[name].category = cat
        return self._stats[name]

    def record_call(self, name: str, success: bool, duration_ms: float):
        """Compatibility entry point backed by the persistent usage store."""
        if name not in self._stats:
            self.register(name)
        get_tool_usage_store().record(
            name, status="success" if success else "failure", duration_ms=duration_ms,
        )

    def _materialize(self, name: str) -> Optional[ToolStats]:
        metadata = self._stats.get(name)
        if metadata is None:
            return None
        summary = get_tool_usage_store().summaries([name])[name]
        return ToolStats(
            name=name, category=metadata.category, call_count=summary.call_count,
            success_count=summary.success_count, fail_count=(
                summary.failure_count + summary.blocked_count + summary.cancelled_count
            ), last_called=summary.last_called,
            last_duration_ms=summary.avg_duration_ms,
            total_duration_ms=summary.total_duration_ms,
        )

    def get_stats(self, name: str) -> Optional[ToolStats]:
        return self._materialize(name)

    def get_all_stats(self) -> list[ToolStats]:
        items = [self._materialize(name) for name in self._stats]
        return sorted((item for item in items if item), key=lambda s: (-s.call_count, s.name))

    def get_by_category(self) -> dict[str, list[ToolStats]]:
        groups: dict[str, list[ToolStats]] = {}
        for s in self.get_all_stats():
            groups.setdefault(s.category, []).append(s)
        # 按分类顺序排序
        ordered = {}
        for cat in CATEGORIES:
            if cat in groups:
                ordered[cat] = sorted(groups[cat], key=lambda s: s.call_count, reverse=True)
        for cat, items in groups.items():
            if cat not in ordered:
                ordered[cat] = items
        return ordered

    def reset_stats(self):
        get_tool_usage_store().reset()


# ══════════════════════════════════════════════════════════
# Global singleton
# ══════════════════════════════════════════════════════════

_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def record_tool_call(name: str, success: bool, duration_ms: float):
    get_tool_registry().record_call(name, success, duration_ms)


def init_tool_registry(tool_names: list[str]):
    """从 TOOL_EXECUTORS 的 key 初始化注册中心。"""
    reg = get_tool_registry()
    for name in tool_names:
        cat = get_category(name)
        reg.register(name, cat)
