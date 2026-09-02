"""按需读取莲心自我说明与运行时状态。"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DOCS_DIR = SKILL_DIR / "docs"

_INDEX = None


def _load_index() -> dict:
    global _INDEX
    if _INDEX is None:
        _INDEX = json.loads((DOCS_DIR / "index.json").read_text(encoding="utf-8"))
    return _INDEX


def _resolve_topic(topic: str) -> tuple[str, dict] | None:
    value = str(topic or "").strip().lower()
    if not value:
        return None
    for item in _load_index()["topics"]:
        aliases = [item["id"], item["name"], *item.get("aliases", [])]
        if any(alias.lower() in value or value in alias.lower() for alias in aliases):
            return item["id"], item
    return None


def _sections(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE))
    result = {"overview": text.split("\n## ", 1)[0].strip()}
    aliases = {
        "一句话介绍": "overview", "解决的问题": "purpose", "主要能力": "purpose",
        "技术架构": "architecture", "数据与其他系统的关系": "relations",
        "使用方式和界面入口": "usage", "当前状态含义": "status",
        "能力边界与限制": "limitations", "可以怎样向莲心提问": "usage",
    }
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group(1).strip()
        result[aliases.get(title, title.lower())] = f"## {title}\n{text[start:end].strip()}"
    return result


def query_self_knowledge(topic: str = "", aspect: str = "overview") -> str:
    """读取一个自我认知专题的指定章节。"""
    resolved = _resolve_topic(topic)
    if resolved is None:
        if not str(topic or "").strip():
            overview = _sections(DOCS_DIR / "总览.md")
            content = overview.get(aspect, overview.get("overview", ""))
            return f"专题：莲心能力总览\n文档状态：implemented\n\n{content[:5000]}"
        names = "、".join(item["name"] for item in _load_index()["topics"])
        return f"未找到专题「{topic}」。可查询：{names}"
    topic_id, item = resolved
    aspect = str(aspect or "overview").strip().lower()
    if aspect not in {"overview", "purpose", "architecture", "usage", "relations", "limitations", "status"}:
        aspect = "overview"
    sections = _sections(DOCS_DIR / item["file"])
    content = sections.get(aspect) or sections.get("overview", "")
    return f"专题：{item['name']}\n文档状态：{item.get('status', 'implemented')}\n\n{content[:5000]}"


_STATUS_DETAILS = {"summary", "health", "recent_activity", "metrics", "full"}
_CAPABILITY_MAP = {
    "prism_memory": {"search_memory", "list_memories", "search_graph_memory", "save_memory"},
    "time_capsule": {"read_diary", "write_diary"},
    "notebook": set(),
    "vision": {"describe_image", "capture_from_camera", "ocr_image"},
    "voice": set(),
    "network": {"web_search", "fetch_webpage", "get_weather"},
    "qq": {"send_file_to_qq"},
    "shoulder": {"shoulder_observe", "shoulder_status", "shoulder_servo"},
}


def _data_root() -> Path:
    # Do not call get_user_data_dir(): its mkdir side effect is undesirable in
    # a status query. The application uses this exact path convention.
    return Path.home() / ".lianxin"


def _short(value, limit: int = 120) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _time_text(value) -> str:
    if value in (None, "", 0, 0.0):
        return ""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).isoformat(timespec="seconds")
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def _read_rows(path: Path, sql: str, params=()) -> list[dict]:
    """Read an existing SQLite database without schema creation or migration."""
    if not path.exists():
        return []
    try:
        # SQLite URI syntax also works on Windows when the drive letter is
        # kept in the absolute path (for example file:E:/data/app.db).
        uri = f"file:{path.absolute().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except (OSError, sqlite3.Error):
        return []


def _tables(path: Path) -> set[str]:
    rows = _read_rows(path, "SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row.get("name", "")) for row in rows}


def _empty_provider(*, available: bool = False, limitation: str = "实时状态暂无数据") -> dict:
    return {"available": available, "health": "正常" if available else "暂无数据",
            "limitations": [limitation] if limitation else []}


def _status_prism_memory() -> dict:
    path = Path(__file__).resolve().parents[2] / "memory" / "conversations.db"
    tables = _tables(path)
    if not tables:
        return _empty_provider(limitation="记忆数据库尚未建立")
    facts = _read_rows(path, "SELECT content, created_at, updated_at FROM memory_facts "
                            "WHERE status='active' ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 1") if "memory_facts" in tables else []
    fact_count = _read_rows(path, "SELECT COUNT(*) AS count FROM memory_facts WHERE status='active'") if "memory_facts" in tables else []
    states = _read_rows(path, "SELECT COUNT(*) AS count FROM memory_current_states WHERE status='active'") if "memory_current_states" in tables else []
    graph = _read_rows(path, "SELECT COUNT(*) AS count FROM graph_entities") if "graph_entities" in tables else []
    edges = _read_rows(path, "SELECT COUNT(*) AS count FROM graph_edges") if "graph_edges" in tables else []
    latest = facts[0] if facts else {}
    return {"available": True, "health": "正常", "last_activity": _time_text(latest.get("updated_at") or latest.get("created_at")),
            "last_activity_summary": _short(latest.get("content")),
            "metrics": {"active_facts": int((fact_count or [{"count": 0}])[0]["count"]),
                        "active_current_states": int((states or [{"count": 0}])[0]["count"]),
                        "graph_entities": int((graph or [{"count": 0}])[0]["count"]),
                        "graph_edges": int((edges or [{"count": 0}])[0]["count"])},
            "limitations": ["最近记忆仅返回安全摘要，不返回完整敏感内容"]}


def _status_ripple_emotion() -> dict:
    path = Path(__file__).resolve().parents[2] / "memory" / "conversations.db"
    rows = _read_rows(path, "SELECT state_json, updated_at FROM emotion_v3_states "
                         "WHERE persona_id='default-lianxin' AND subject_id='owner' LIMIT 1")
    if not rows:
        return _empty_provider(limitation="尚未找到涟漪情感运行快照")
    try:
        state = json.loads(rows[0].get("state_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _empty_provider(limitation="涟漪情感运行快照格式异常") | {"health": "错误"}
    return {"available": True, "enabled": bool(state.get("enabled", True)), "health": "正常",
            "last_activity": _time_text(rows[0].get("updated_at")),
            "last_activity_summary": f"当前情绪：{state.get('mood_cluster', '平稳')}",
            "metrics": {key: state[key] for key in ("mood_cluster", "relationship_stage", "last_activity_type") if key in state},
            "limitations": ["情感数值是运行快照，不等同于用户的客观心理诊断"]}


def _status_persona() -> dict:
    root = _data_root() / "personas"
    state_path = root / "active.json"
    if not state_path.exists():
        return _empty_provider(limitation="人格状态文件尚未建立")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        profile_id = str(state.get("active_id", ""))
        profile = json.loads((root / f"{profile_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_provider(limitation="人格状态文件不可读") | {"health": "错误"}
    return {"available": True, "enabled": bool(state.get("enabled", False)), "health": "正常",
            "last_activity": _time_text(profile.get("updated_at")),
            "last_activity_summary": f"当前人格：{profile.get('profile_name') or profile_id}",
            "metrics": {"profile_id": profile_id, "revision": None},
            "limitations": ["草稿保存不代表人格已激活"]}


def _status_time_capsule() -> dict:
    path = _data_root() / "time_capsule.db"
    tables = _tables(path)
    if not tables:
        return _empty_provider(limitation="时间胶囊数据库尚未建立")
    days = _read_rows(path, "SELECT date, user_content, lianxin_content, updated_at FROM capsule_days "
                       "WHERE user_content<>'' OR lianxin_content<>'' ORDER BY updated_at DESC LIMIT 1") if "capsule_days" in tables else []
    notes = _read_rows(path, "SELECT content, created_at FROM tree_hole_notes WHERE archived=0 "
                        "ORDER BY created_at DESC, id DESC LIMIT 1") if "tree_hole_notes" in tables else []
    latest = days[0] if days else {}
    note = notes[0] if notes else {}
    last_time = latest.get("updated_at") or note.get("created_at")
    summary = _short(latest.get("user_content") or latest.get("lianxin_content") or note.get("content"))
    counts = {}
    for table, key in (("capsule_days", "diary_days"), ("tree_hole_notes", "tree_hole_notes"), ("capsule_collections", "collections")):
        if table in tables:
            rows = _read_rows(path, f"SELECT COUNT(*) AS count FROM {table}")
            counts[key] = int((rows or [{"count": 0}])[0]["count"])
    return {"available": True, "health": "正常", "last_activity": _time_text(last_time),
            "last_activity_summary": summary, "metrics": counts,
            "limitations": ["时间胶囊包含日记与树洞纸条；备忘本是独立功能"]}


def _status_notebook() -> dict:
    path = _data_root() / "note.json"
    if not path.exists():
        return _empty_provider(limitation="备忘本尚未保存内容")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = path.stat().st_mtime
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_provider(limitation="备忘本文件不可读") | {"health": "错误"}
    return {"available": True, "health": "正常", "last_activity": _time_text(stamp),
            "last_activity_summary": _short(data.get("content")), "metrics": {"has_content": bool(data.get("content"))},
            "limitations": ["备忘本当前是单份内容，不包含时间胶囊的日记或树洞记录"]}


def _status_study_room() -> dict:
    path = _data_root() / "study_room.db"
    tables = _tables(path)
    if "focus_sessions" not in tables:
        return _empty_provider(limitation="自习室尚未产生专注记录")
    recent = _read_rows(path, "SELECT task_name, ended_at, duration_seconds, completed FROM focus_sessions "
                          "ORDER BY ended_at DESC, id DESC LIMIT 1")
    totals = _read_rows(path, "SELECT COUNT(*) AS count, COALESCE(SUM(duration_seconds),0) AS seconds, "
                           "COALESCE(SUM(completed),0) AS completed FROM focus_sessions")
    row, total = (recent[0] if recent else {}), (totals[0] if totals else {})
    return {"available": True, "health": "正常", "last_activity": _time_text(row.get("ended_at")),
            "last_activity_summary": (f"最近专注：{row.get('task_name') or '未命名任务'}，"
                                       f"{int(row.get('duration_seconds') or 0) // 60} 分钟" if row else "暂无专注记录"),
            "metrics": {"focus_sessions": int(total.get("count", 0)), "total_seconds": int(total.get("seconds", 0)),
                        "completed_sessions": int(total.get("completed", 0))},
            "limitations": ["历史记录不代表当前计时器正在运行"]}


def _status_music_space() -> dict:
    result = {"available": False, "health": "暂无数据", "metrics": {},
              "limitations": ["播放器未注册 GUI 状态回调"]}
    try:
        import brain.tools as brain_tools
        callback = getattr(brain_tools, "_music_info_callback", None)
        if callback:
            raw = str(callback("status") or "")
            result.update(available=True, health="正常", last_activity_summary=_short(raw), limitations=[])
    except Exception as exc:
        result.update(health="错误", limitations=[f"读取播放器状态失败：{type(exc).__name__}"])
    stats_path = _data_root() / "music_stats.json"
    try:
        data = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
        songs = data.get("songs", {})
        best = max(songs.values(), key=lambda item: item.get("seconds", 0), default={})
        result["metrics"].update({"total_seconds": int(data.get("total_seconds", 0) or 0),
                                   "song_count": len(songs), "most_played_song": best.get("name", "")})
    except (OSError, ValueError, json.JSONDecodeError):
        result["limitations"].append("音乐统计文件不可读")
    return result


def _status_proactive_chat() -> dict:
    path = _data_root() / "proactive_settings.json"
    if not path.exists():
        return _empty_provider(limitation="主动聊天配置尚未建立")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_provider(limitation="主动聊天配置不可读") | {"health": "错误"}
    enabled = bool(data.get("desktop_enabled") or data.get("qq_enabled"))
    obs = data.get("_last_observation", "")
    action_keys = (
        "slack_supplement_diary", "slack_review_old_diary", "slack_search_old_topic",
        "slack_remind_todo", "slack_random_question", "slack_weather_chitchat",
        "slack_read_local_files", "slack_browser_history", "slack_check_cpu_disk",
        "slack_check_recycle_bin", "slack_remind_rest", "slack_remind_water",
        "slack_anniversary_remind", "slack_next_song",
    )
    return {"available": True, "enabled": enabled, "health": "正常",
            "last_activity": _time_text(data.get("_last_global_success")),
            "last_activity_summary": _short(obs) if obs else "暂无最近观察记录",
            "metrics": {"desktop_enabled": bool(data.get("desktop_enabled")), "qq_enabled": bool(data.get("qq_enabled")),
                        "bilibili_enabled": bool(data.get("bilibili_enabled")),
                        "enabled_slack_actions": sum(1 for key in action_keys if data.get(key))},
            "limitations": ["配置启用不代表下一次主动消息已经触发"]}


def _runtime(component: str) -> dict | None:
    try:
        from brain.runtime_status import get_status
        return get_status(component)
    except Exception:
        return None


def _status_vision() -> dict:
    state = _runtime("vision")
    tracking = _runtime("face_tracking")
    if not state and not tracking:
        return _empty_provider(limitation="视觉模块当前没有运行时快照")
    state = state or {}
    tracking = tracking or {}
    running = bool(state.get("running") or tracking.get("running"))
    metrics = {key: state[key] for key in ("camera", "fps", "face", "gesture", "provider") if key in state}
    if tracking:
        metrics["face_tracking"] = tracking.get("status", "运行中" if tracking.get("running") else "已停止")
    return {"available": True, "enabled": running, "health": state.get("health", "正常"),
            "last_activity": state.get("updated_at") or tracking.get("updated_at"),
            "last_activity_summary": state.get("last_activity_summary") or tracking.get("last_activity_summary", ""),
            "metrics": metrics,
            "limitations": ["视觉状态只反映本地进程；人脸识别结果不等同于身份认证"]}


def _status_voice() -> dict:
    state = _runtime("voice")
    if not state:
        return _empty_provider(limitation="语音模块当前没有运行时快照")
    running = bool(state.get("running"))
    return {"available": True, "enabled": running, "health": state.get("health", "正常"),
            "last_activity": state.get("updated_at"),
            "last_activity_summary": state.get("last_activity_summary", ""),
            "metrics": {key: state[key] for key in ("mode", "state", "stt_engine", "device") if key in state},
            "limitations": ["模型已加载不代表当前正在录音；语音转录结果以实际识别文本为准"]}


def _status_qq() -> dict:
    state = _runtime("qq")
    if not state:
        return _empty_provider(limitation="QQ 桥接当前没有运行时快照")
    connected = bool(state.get("connected"))
    return {"available": True, "enabled": bool(state.get("running", connected)),
            "health": "正常" if connected else ("连接中" if state.get("running") else "未连接"),
            "last_activity": state.get("updated_at"),
            "last_activity_summary": state.get("last_activity_summary", ""),
            "metrics": {key: state[key] for key in ("running", "connected", "url") if key in state},
            "limitations": ["QQ 桥接在线不代表 QQ 消息发送一定成功，仍受 NapCat 与网络状态影响"]}


def _status_shoulder() -> dict:
    state = _runtime("shoulder")
    if not state:
        try:
            import brain.tools as brain_tools
            bridge = getattr(brain_tools, "_shoulder_bridge", None)
            if bridge is not None and bool(getattr(bridge, "connected", False)):
                state = {"running": True, "connected": True, "last_activity_summary": "肩载桥接已连接"}
        except Exception:
            state = None
    if not state:
        return _empty_provider(limitation="肩载设备当前没有运行时连接快照；未主动连接设备")
    connected = bool(state.get("connected"))
    return {"available": True, "enabled": bool(state.get("running", connected)),
            "health": "正常" if connected else "未连接",
            "last_activity": state.get("updated_at"),
            "last_activity_summary": state.get("last_activity_summary", ""),
            "metrics": {key: state[key] for key in ("connected", "mode", "pan", "tilt", "wifi_rssi") if key in state},
            "limitations": ["未连接时不会主动探测 ESP32-CAM；状态暂无数据不代表硬件损坏"]}


def _status_star_map() -> dict:
    """The constellation currently shares the graph-memory database."""
    path = Path(__file__).resolve().parents[2] / "memory" / "conversations.db"
    tables = _tables(path)
    if "graph_entities" not in tables or "graph_edges" not in tables:
        return _empty_provider(limitation="星图数据库尚未建立")
    entities = _read_rows(path, "SELECT COUNT(*) AS count FROM graph_entities")
    edges = _read_rows(path, "SELECT COUNT(*) AS count FROM graph_edges")
    return {"available": True, "health": "正常",
            "metrics": {"entities": int((entities or [{"count": 0}])[0]["count"]),
                        "relations": int((edges or [{"count": 0}])[0]["count"])},
            "limitations": ["星图是实体关系视图，不是日记或长期记忆原文列表"]}


def _status_capability_hub(items: list) -> dict:
    enabled = sum(1 for item in items if item.enabled)
    available = sum(1 for item in items if item.enabled and item.available)
    return {"available": True, "health": "正常",
            "metrics": {"registered": len(items), "enabled": enabled, "enabled_and_available": available},
            "limitations": ["启用只表示能力已登记并允许调用；外部服务是否在线由对应专题状态决定"]}


_STATUS_PROVIDERS = {"prism_memory": _status_prism_memory, "ripple_emotion": _status_ripple_emotion,
    "persona_hub": _status_persona, "time_capsule": _status_time_capsule,
    "notebook": _status_notebook, "study_room": _status_study_room,
                     "music_space": _status_music_space, "proactive_chat": _status_proactive_chat,
                     "star_map": _status_star_map, "vision": _status_vision,
                     "voice": _status_voice, "qq": _status_qq, "shoulder": _status_shoulder}


def _capability_items(topic_id: str, items: list) -> list[dict]:
    wanted = _CAPABILITY_MAP.get(topic_id)
    matched = [item for item in items if wanted and item.name in wanted]
    return [{"name": item.display_name,
             "status": "已启用且可用" if item.enabled and item.available else ("已停用" if not item.enabled else "当前不可用"),
             "provider": item.provider_name} for item in matched]


def _topic_status(topic_id: str, topic_item: dict, items: list) -> dict:
    provider = _STATUS_PROVIDERS.get(topic_id)
    data = provider() if provider else _empty_provider(limitation="该专题暂无独立运行时状态提供器")
    if topic_id == "capability_hub":
        data = _status_capability_hub(items)
    capabilities = _capability_items(topic_id, items)
    data = {"id": topic_id, "name": topic_item["name"], "implemented": topic_item.get("status") == "implemented",
            # A topic is registered by the product even when its optional
            # runtime backend has no live snapshot. `None` means the current
            # enablement is unknown, not disabled.
            "enabled": data.pop("enabled", bool(capabilities) if capabilities else None), **data}
    if capabilities:
        data["capabilities"] = capabilities
    return data


def _status_for(topic: str, detail_level: str) -> dict:
    from brain.capability_catalog import list_capabilities
    from brain.skill_manager import _active_skills
    items = list_capabilities()
    detail_level = str(detail_level or "summary").strip().lower()
    if detail_level not in _STATUS_DETAILS:
        detail_level = "summary"
    resolved = _resolve_topic(topic) if topic else None
    skill_active = "自我认知功能" in _active_skills
    result = {"topic": resolved[1]["name"] if resolved else "莲心整体能力",
              "skill_enabled": skill_active, "checked_at": datetime.now().isoformat(timespec="seconds")}
    if resolved:
        topic_id, topic_item = resolved
        result["status"] = _topic_status(topic_id, topic_item, items)
    else:
        result["topics"] = [_topic_status(item["id"], item, items) for item in _load_index()["topics"]]
        result["capability_count"] = len(items)
    if detail_level in {"summary", "health", "recent_activity", "metrics", "full"}:
        def trim(status: dict) -> dict:
            allowed = {"id", "name", "implemented", "enabled", "available", "health", "limitations"}
            if detail_level in {"summary", "recent_activity", "full"}:
                allowed |= {"last_activity", "last_activity_summary"}
            if detail_level in {"metrics", "full"}:
                allowed |= {"metrics", "capabilities"}
            if detail_level in {"health", "full"}:
                allowed |= {"limitations", "capabilities"}
            return {key: value for key, value in status.items() if key in allowed}
        if "status" in result:
            result["status"] = trim(result["status"])
        else:
            result["topics"] = [trim(status) for status in result["topics"]]
    return result


def query_self_status(topic: str = "", detail_level: str = "summary") -> str:
    """查询能力目录中的实时启用和可用状态。"""
    return json.dumps(_status_for(topic, detail_level), ensure_ascii=False, indent=2)


def inspect_self_capability(topic: str = "", aspect: str = "overview", include_status: bool = True) -> str:
    """组合读取专题说明与实时状态，减少模型往返调用。"""
    knowledge = query_self_knowledge(topic, aspect)
    status = query_self_status(topic, "summary") if include_status else "未查询实时状态。"
    return f"{knowledge}\n\n【实时状态】\n{status}"


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "query_self_knowledge", "description": "按专题和章节读取莲心自身功能的权威说明。", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "aspect": {"type": "string", "enum": ["overview", "purpose", "architecture", "usage", "relations", "limitations", "status"]}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "query_self_status", "description": "查询莲心某项功能当前是否启用、可用及基础运行状态。", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "detail_level": {"type": "string", "enum": ["summary", "health", "recent_activity", "metrics", "full"]}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "inspect_self_capability", "description": "一次读取莲心自身专题说明和实时状态，适合完整自我认知问题。", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}, "aspect": {"type": "string"}, "include_status": {"type": "boolean"}}, "required": ["topic"]}}},
]

TOOL_EXECUTORS = {
    "query_self_knowledge": lambda inp: query_self_knowledge(inp.get("topic", ""), inp.get("aspect", "overview")),
    "query_self_status": lambda inp: query_self_status(inp.get("topic", ""), inp.get("detail_level", "summary")),
    "inspect_self_capability": lambda inp: inspect_self_capability(inp.get("topic", ""), inp.get("aspect", "overview"), inp.get("include_status", True)),
}
