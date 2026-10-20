"""Unified read model for built-in, Skill and MCP callable capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from utils.paths import get_user_data_dir


CATEGORY_ORDER = (
    "文件与文档", "联网与研究", "记忆与知识", "任务与效率", "视觉与媒体",
    "系统与设备", "开发工具", "通信与集成", "莲心功能", "扩展能力",
)

_CATEGORY_TOOLS = {
    "文件与文档": {
        "read_file", "read_file_chunk", "read_file_lines", "write_file", "edit_file",
        "list_directory", "search_files_everything", "get_file_info_everything", "glob_files",
        "grep_file", "clear_document_cache", "read_excel", "write_excel",
        "copy_excel_content", "write_docx", "format_document", "diff_files",
    },
    "联网与研究": {
        "web_search", "fetch_webpage", "fetch_webpage_browser", "fetch_webpage_via_api",
        "fetch_webpage_stealth", "configure_network_tools", "get_weather", "set_user_city",
        "bilibili_search", "bilibili_add_tag", "bilibili_list_tags",
    },
    "记忆与知识": {
        "save_memory", "update_current_state", "search_cross_session",
        "search_conversation_history", "search_memory", "trace_memory_source",
        "explain_memory_quality", "review_memory_conflict", "update_memory", "delete_memory",
        "list_memories", "search_graph_memory", "discover_connections",
        "query_connected_entities", "delete_graph_entity", "add_graph_edge", "remove_graph_edge",
    },
    "任务与效率": {
        "add_todo", "list_todos", "complete_todo", "plan_tasks", "delegate_task", "track_tasks",
        "run_execution_task", "resume_execution_task",
    },
    "视觉与媒体": {
        "ocr_image", "ocr_batch", "describe_image", "capture_from_camera", "capture_desktop",
        "generate_image", "generate_video",
    },
    "系统与设备": {
        "open_app", "get_clipboard", "run_command", "get_current_time", "get_balance",
    },
    "开发工具": {
        "run_python_code", "search_code", "run_shell", "git_status", "code_structure",
        "code_goto_def", "code_find_refs", "code_diagnostics",
    },
    "通信与集成": {"send_file_to_qq"},
    "莲心功能": {
        "read_diary", "write_diary", "toggle_proactive_chat", "set_expression",
        "list_skills", "activate_skill", "deactivate_skill",
    },
}
_TOOL_CATEGORY = {
    name: category for category, names in _CATEGORY_TOOLS.items() for name in names
}


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    display_name: str
    description: str
    category: str
    source_kind: str
    provider_id: str
    provider_name: str
    enabled: bool
    available: bool
    parameters: dict
    version: str = ""
    favorite: bool = False

    @property
    def status(self) -> str:
        if not self.enabled:
            return "已停用"
        return "可用" if self.available else "不可用"

    @property
    def searchable_text(self) -> str:
        return " ".join((self.name, self.display_name, self.description, self.category,
                         self.provider_name)).lower()


def _function(tool_definition: dict) -> dict:
    return tool_definition.get("function", {}) if isinstance(tool_definition, dict) else {}


def _summary(name: str, description: str) -> str:
    try:
        from brain.tool_router import TOOL_DESCRIPTIONS
        if TOOL_DESCRIPTIONS.get(name):
            return str(TOOL_DESCRIPTIONS[name])
    except Exception:
        pass
    clean = " ".join(str(description or "").split())
    if clean:
        return clean.split("。", 1)[0][:32]
    return name


def _favorites_path() -> Path:
    return get_user_data_dir() / "favorite_tools.json"


def load_favorites() -> set[str]:
    try:
        data = json.loads(_favorites_path().read_text(encoding="utf-8"))
        return {str(name) for name in data.get("favorites", []) if name}
    except Exception:
        return set()


def save_favorites(favorites: Iterable[str]) -> None:
    path = _favorites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"favorites": sorted(set(favorites))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def toggle_favorite(name: str) -> bool:
    favorites = load_favorites()
    if name in favorites:
        favorites.remove(name)
        state = False
    else:
        favorites.add(name)
        state = True
    save_favorites(favorites)
    return state


def _core_capabilities(favorites: set[str]) -> list[CapabilityDescriptor]:
    from brain.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
    from config import get_builtin_tool_config

    enabled_config = get_builtin_tool_config()
    result = []
    for definition in TOOL_DEFINITIONS:
        fn = _function(definition)
        name = str(fn.get("name", ""))
        if not name:
            continue
        description = str(fn.get("description", ""))
        result.append(CapabilityDescriptor(
            name=name, display_name=_summary(name, description), description=description,
            category=_TOOL_CATEGORY.get(name, "莲心功能"), source_kind="builtin",
            provider_id="lianxin", provider_name="莲心内置", enabled=enabled_config.get(name, True),
            available=name in TOOL_EXECUTORS, parameters=fn.get("parameters", {}) or {},
            favorite=name in favorites,
        ))
    return result


def _skill_capabilities(favorites: set[str]) -> list[CapabilityDescriptor]:
    try:
        from brain.skill_manager import _active_skills, _skill_registry
        from brain.tools import TOOL_EXECUTORS
    except Exception:
        return []
    result = []
    for skill_name, info in _skill_registry.items():
        active = skill_name in _active_skills
        for definition in info.get("tool_definitions", []):
            fn = _function(definition)
            name = str(fn.get("name", ""))
            if not name:
                continue
            description = str(fn.get("description", ""))
            result.append(CapabilityDescriptor(
                name=name, display_name=_summary(name, description), description=description,
                category="扩展能力", source_kind="skill", provider_id=skill_name,
                provider_name=skill_name, enabled=active,
                available=active and name in TOOL_EXECUTORS,
                parameters=fn.get("parameters", {}) or {}, version=str(info.get("version", "")),
                favorite=name in favorites,
            ))
    return result


def _mcp_capabilities(favorites: set[str]) -> list[CapabilityDescriptor]:
    try:
        from brain.mcp.mcp_registry import MANIFEST_CACHE, MCP_REGISTRY, is_mcp_enabled
    except Exception:
        return []
    result = []
    for service_name, agent in MCP_REGISTRY.items():
        manifest = MANIFEST_CACHE.get(service_name, {})
        provider_name = str(
            manifest.get("displayName") or getattr(agent, "display_name", "") or service_name
        )
        enabled = is_mcp_enabled(service_name)
        try:
            definitions = agent.get_tool_definitions_openai()
        except Exception:
            definitions = []
        for definition in definitions:
            fn = _function(definition)
            name = str(fn.get("name", ""))
            if not name:
                continue
            description = str(fn.get("description", ""))
            result.append(CapabilityDescriptor(
                name=name, display_name=_summary(name, description), description=description,
                category="扩展能力", source_kind="mcp", provider_id=service_name,
                provider_name=provider_name, enabled=enabled, available=enabled,
                parameters=fn.get("parameters", {}) or {},
                version=str(manifest.get("version") or getattr(agent, "version", "")),
                favorite=name in favorites,
            ))
    return result


def list_capabilities(*, include_disabled: bool = True) -> list[CapabilityDescriptor]:
    favorites = load_favorites()
    by_name: dict[str, CapabilityDescriptor] = {}
    for item in _core_capabilities(favorites) + _skill_capabilities(favorites) + _mcp_capabilities(favorites):
        by_name[item.name] = item
    items = list(by_name.values())
    if not include_disabled:
        items = [item for item in items if item.enabled and item.available]
    category_index = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    return sorted(
        items,
        key=lambda item: (category_index.get(item.category, len(CATEGORY_ORDER)),
                          not item.favorite, item.display_name.lower(), item.name),
    )


def get_capability(name: str) -> CapabilityDescriptor | None:
    return next((item for item in list_capabilities() if item.name == name), None)


def search_capabilities(query: str, items: Iterable[CapabilityDescriptor] | None = None
                        ) -> list[CapabilityDescriptor]:
    candidates = list(items) if items is not None else list_capabilities()
    terms = [term for term in str(query or "").lower().split() if term]
    if not terms:
        return candidates
    return [item for item in candidates if all(term in item.searchable_text for term in terms)]


def with_usage(items: Iterable[CapabilityDescriptor], summaries: dict) -> list[tuple[CapabilityDescriptor, object]]:
    """Pair descriptors with usage summaries without making the catalog own persistence."""
    return [(replace(item), summaries.get(item.name)) for item in items]
