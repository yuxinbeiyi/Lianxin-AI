from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from utils import proactive_chat


class ProactivePanelConflict(RuntimeError):
    pass


BEHAVIORS = ("normal", "observe", "bilibili", "slack")
SLACK_ACTIONS = ("supplement_diary", "review_old_diary", "search_old_topic", "remind_todo", "random_question", "weather_chitchat", "read_local_files", "browser_history", "check_cpu_disk", "check_recycle_bin", "remind_rest", "remind_water", "anniversary_remind", "next_song")
BOOL_FIELDS = ("desktop_enabled", "qq_enabled", "music_feedback_enabled", "memory_link_enabled", "normal_enabled", "avoid_behavior_repeat", "fallback_on_failure", "observe_enabled", "observe_send_to_qq", "bilibili_enabled", "slack_enabled", *("slack_" + action for action in SLACK_ACTIONS))
RANGES = {"frequency": (1, 50), "min_interval_minutes": (10, 120), "user_defer_minutes": (5, 60), "memory_evaluation_interval_minutes": (10, 1440), "screenshot_prob": (0, 100), "camera_prob": (0, 100), "camera_wait": (3, 30), "camera_index": (0, 255), "slack_idle_minutes": (0, 240)}
FIELDS = (*BOOL_FIELDS, *RANGES, "weights", "behavior_weights", "behavior_cooldowns")


def _read():
    path = proactive_chat._SETTINGS_PATH
    full = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(full, dict):
        raise ValueError("主动聊天配置格式错误，未修改文件")
    if "enabled" in full and "desktop_enabled" not in full:
        full["desktop_enabled"] = full["enabled"]
    defaults = proactive_chat.ProactiveChatScheduler._default_settings(None)
    result = {**defaults, **full}
    for field in ("behavior_weights", "behavior_cooldowns"):
        if not isinstance(full.get(field, {}), dict):
            raise ValueError("主动行为配置格式错误，未修改文件")
        result[field] = {**defaults[field], **full.get(field, {})}
    return result


def _public(full):
    result = {field: copy.deepcopy(full[field]) for field in FIELDS}
    for field in ("behavior_weights", "behavior_cooldowns"):
        result[field] = {name: result[field][name] for name in BEHAVIORS}
    return result


def _revision(full):
    return hashlib.sha256(json.dumps(_public(full), sort_keys=True).encode()).hexdigest()


def _number(value, minimum, maximum, name):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 应为 {minimum}–{maximum} 之间的整数")


def validate(payload, full):
    if not isinstance(payload, dict) or set(payload) - {"revision", "settings"}:
        raise ValueError("主动聊天配置请求无效")
    if payload.get("revision") != _revision(full):
        raise ProactivePanelConflict("主动聊天设置已在其它窗口变化，请重新加载后再保存")
    settings = payload.get("settings")
    if not isinstance(settings, dict) or set(settings) - set(FIELDS):
        raise ValueError("包含未知或不可修改的主动聊天字段")
    updated = {**_public(full), **copy.deepcopy(settings)}
    for field in BOOL_FIELDS:
        if not isinstance(updated[field], bool):
            raise ValueError(f"{field} 必须为开关值")
    for field, (minimum, maximum) in RANGES.items():
        _number(updated[field], minimum, maximum, field)
    if not isinstance(updated["weights"], list) or len(updated["weights"]) != 24:
        raise ValueError("时段权重必须完整包含 24 小时")
    for weight in updated["weights"]:
        _number(weight, 0, 10, "时段权重")
    for field, maximum in (("behavior_weights", 100), ("behavior_cooldowns", 1440)):
        values = updated[field]
        if not isinstance(values, dict) or set(values) != set(BEHAVIORS):
            raise ValueError("请完整填写四类行为的权重和冷却")
        for value in values.values():
            _number(value, 0, maximum, field)
    if sum(updated["behavior_weights"].values()) == 0:
        raise ValueError("四类主动行为权重不能同时为 0")
    return updated


def _write(full):
    path = proactive_chat._SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(full, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def state(scheduler):
    with scheduler._settings_lock:
        full = _read()
        return {"settings": _public(full), "revision": _revision(full), "defaultWeights": list(proactive_chat._DEFAULT_WEIGHTS), "baseRate": proactive_chat._BASE_RATE,
                "hour": datetime.now().hour, "recentBehaviors": list(scheduler._settings.get("_behavior_history", []))[-8:], "lastSuccess": scheduler._settings.get("_last_global_success", "")}


def save(scheduler, payload, validate_only=False):
    with scheduler._settings_lock:
        full = _read()
        updated = validate(payload, full)
        if validate_only:
            return {"valid": True}
        runtime = {key: copy.deepcopy(value) for key, value in scheduler._settings.items() if key.startswith("_")}
        merged = {**full, **runtime, **updated}
        for field in ("behavior_weights", "behavior_cooldowns"):
            merged[field] = {**full[field], **updated[field]}
        _write(merged)
        scheduler._settings.update(copy.deepcopy(merged))
        return state(scheduler)


def cameras(confirmed):
    if confirmed is not True:
        raise ValueError("扫描会激活摄像头，需要明确确认")
    import cv2

    available = []
    backend = getattr(cv2, "CAP_DSHOW", 0)
    for index in range(4):
        capture = None
        try:
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                available.append({"index": index, "label": f"摄像头 {index}"})
        except Exception:
            continue
        finally:
            if capture is not None:
                capture.release()
    return {"items": available}


def history_request(action, payload):
    from utils.bilibili_history import get_bilibili_history

    history = get_bilibili_history()
    keyword = payload.get("keyword", "")
    if not isinstance(keyword, str) or len(keyword) > 120:
        raise ValueError("关键词无效")
    with history._lock:
        if action == "history":
            return {"items": history.get_history(limit=50, keyword=keyword)}
        previous = copy.deepcopy(history._data)
        try:
            if action == "history-clear":
                if payload.get("confirmed") is not True:
                    raise ValueError("清空历史需要明确确认")
                history.clear_history()
            elif action == "tag-delete":
                if not any(tag["keyword"] == keyword for tag in history.get_search_tags()):
                    raise ValueError("标签已不存在")
                history.remove_tag(keyword)
            elif action == "history-react":
                record_id, bvid, reaction = (payload.get(key) for key in ("recordId", "bvid", "reaction"))
                if reaction not in ("liked", "disliked"):
                    raise ValueError("反馈类型无效")
                record = next((item for item in history.get_history(limit=50) if item.get("id") == record_id), None)
                if not record or not any(item.get("bvid") == bvid for item in record.get("results", [])):
                    raise ValueError("浏览记录已不存在，请刷新")
                history.react_to_video(record_id, bvid, reaction)
            else:
                raise LookupError("未知历史操作")
            history.save()
        except Exception:
            history._data = previous
            raise
        return {"items": history.get_history(limit=50, keyword=""), "ok": True}
