from __future__ import annotations

import copy
import hashlib
import json
import math

import config
from utils.api_config_panel import ApiConfigConflict, ApiConfigError, _load, _lock, _write

QQ_TIMING = tuple(key for key in config._QQ_TIMING_DEFAULTS if key not in ("profile_version", "segment_interval_min", "segment_interval_max"))
QQ_BRIDGE = ("voice_reply_enabled", "segmented_reply_enabled", "poke_enabled", "poke_llm", "poke_cooldown_seconds")
WECHAT_TIMING = tuple(config._WECHAT_TIMING_DEFAULTS)
WECHAT_BRIDGE = ("auto_start", "listen_port", "owner_id", "voice_reply_enabled")


def _settings(channel, full):
    if channel == "qq":
        raw = full.get("qq_bridge", {})
        return {
            "timing": {**config._QQ_TIMING_DEFAULTS, **raw.get("timing", {})},
            "bridge": {"auto_start": raw.get("auto_start", False), **{key: raw.get(key, config._QQ_BRIDGE_DEFAULTS.get(key, True)) for key in QQ_BRIDGE}},
        }
    raw = full.get("wechat", {})
    return {
        "timing": {**config._WECHAT_TIMING_DEFAULTS, **raw.get("timing", {})},
        "bridge": {key: value for key, value in {**config._WECHAT_BRIDGE_DEFAULTS, **raw.get("bridge", {})}.items() if key in WECHAT_BRIDGE},
    }


def _revision(channel, settings):
    if channel == "qq":
        settings = {"timing": {key: settings["timing"][key] for key in QQ_TIMING},
                    "bridge": {key: settings["bridge"][key] for key in QQ_BRIDGE}}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ApiConfigError(f"{name}必须是有效数字")
    if not low <= value <= high or integer and int(value) != value:
        raise ApiConfigError(f"{name}超出允许范围")
    return int(value) if integer else float(value)


def _bool(value, name):
    if not isinstance(value, bool):
        raise ApiConfigError(f"{name}必须是开关值")
    return value


def _defaults(channel, current):
    if channel == "qq":
        return {"timing": copy.deepcopy(config._QQ_TIMING_DEFAULTS), "bridge": {
            "auto_start": current["bridge"]["auto_start"], "voice_reply_enabled": True,
            "segmented_reply_enabled": True, "poke_enabled": True, "poke_llm": True,
            "poke_cooldown_seconds": 30}}
    return {"timing": copy.deepcopy(config._WECHAT_TIMING_DEFAULTS), "bridge": {
        "auto_start": False, "listen_port": current["bridge"]["listen_port"],
        "owner_id": current["bridge"]["owner_id"], "voice_reply_enabled": True}}


def state(channel, bridge):
    if channel not in ("qq", "wechat"):
        raise ApiConfigError("未知聊天渠道")
    with _lock:
        settings = _settings(channel, _load())
    status = bridge.qq_status() if channel == "qq" else bridge.wechat_status()
    return {"channel": channel, "settings": settings, "defaults": _defaults(channel, settings),
            "revision": _revision(channel, settings), "status": status}


def _validate(channel, incoming, current):
    if not isinstance(incoming, dict) or set(incoming) != {"timing", "bridge"}:
        raise ApiConfigError("配置提交格式无效")
    timing, bridge = incoming["timing"], incoming["bridge"]
    if not isinstance(timing, dict) or not isinstance(bridge, dict):
        raise ApiConfigError("配置提交格式无效")
    timing_keys, bridge_keys = (QQ_TIMING, QQ_BRIDGE) if channel == "qq" else (WECHAT_TIMING, WECHAT_BRIDGE)
    if set(timing) != set(timing_keys) or set(bridge) != set(bridge_keys):
        raise ApiConfigError("存在未知或缺失的配置字段")
    result = copy.deepcopy(current)
    if channel == "qq":
        rules = {"think_delay_min": (.5, 30, False), "think_delay_max": (.5, 30, False),
                 "type_speed_min": (10, 2000, True), "type_speed_max": (10, 2000, True),
                 "min_reply_interval": (0, 30, False), "global_send_interval_min": (0, 120, False),
                 "global_send_interval_max": (0, 120, False), "daily_limit_other": (1, 999, True),
                 "cross_session_context_limit": (0, 50, True)}
        for key, (low, high, integer) in rules.items():
            result["timing"][key] = _number(timing[key], key, low, high, integer)
        result["timing"]["limit_enabled"] = _bool(timing["limit_enabled"], "limit_enabled")
        for key in QQ_BRIDGE:
            result["bridge"][key] = _number(bridge[key], key, 0, 600, True) if key == "poke_cooldown_seconds" else _bool(bridge[key], key)
        pairs = (("think_delay_min", "think_delay_max"), ("type_speed_min", "type_speed_max"), ("global_send_interval_min", "global_send_interval_max"))
    else:
        rules = {"think_delay_min": (.5, 30, False), "think_delay_max": (.5, 30, False),
                 "type_speed_min": (10, 999, True), "type_speed_max": (10, 999, True),
                 "min_reply_interval": (.5, 60, False), "segment_threshold_min": (20, 500, True),
                 "segment_threshold_max": (20, 500, True), "segment_interval_min": (1, 60, False),
                 "segment_interval_max": (1, 60, False), "global_send_interval_min": (1, 120, False),
                 "global_send_interval_max": (1, 120, False), "daily_limit_owner": (5, 500, True),
                 "daily_limit_other": (1, 999, True), "per_group_daily_limit": (5, 200, True),
                 "cross_session_context_limit": (0, 50, True)}
        for key, (low, high, integer) in rules.items():
            result["timing"][key] = _number(timing[key], key, low, high, integer)
        for key in ("limit_enabled", "block_links"):
            result["timing"][key] = _bool(timing[key], key)
        result["bridge"]["auto_start"] = _bool(bridge["auto_start"], "auto_start")
        result["bridge"]["listen_port"] = _number(bridge["listen_port"], "listen_port", 1024, 65535, True)
        result["bridge"]["voice_reply_enabled"] = _bool(bridge["voice_reply_enabled"], "voice_reply_enabled")
        owner = bridge["owner_id"]
        if not isinstance(owner, str) or len(owner) > 256 or "\n" in owner or "\r" in owner:
            raise ApiConfigError("主人微信 ID 格式无效")
        result["bridge"]["owner_id"] = owner.strip()
        pairs = (("think_delay_min", "think_delay_max"), ("type_speed_min", "type_speed_max"),
                 ("segment_threshold_min", "segment_threshold_max"), ("segment_interval_min", "segment_interval_max"),
                 ("global_send_interval_min", "global_send_interval_max"))
    for lower, upper in pairs:
        result["timing"][lower], result["timing"][upper] = sorted((result["timing"][lower], result["timing"][upper]))
    return result


def save(channel, payload, bridge):
    if channel not in ("qq", "wechat") or not isinstance(payload, dict):
        raise ApiConfigError("配置提交格式无效")
    with _lock:
        full = _load()
        current = _settings(channel, full)
        if payload.get("revision") != _revision(channel, current):
            raise ApiConfigConflict("配置已在其它窗口修改，请重新加载后再保存")
        values = _validate(channel, payload.get("settings"), current)
        updated = copy.deepcopy(full)
        if channel == "qq":
            section = copy.deepcopy(updated.get("qq_bridge", {}))
            timing = copy.deepcopy(section.get("timing", {}))
            timing.update({key: values["timing"][key] for key in QQ_TIMING})
            timing.update({"segment_interval_min": 0.1, "segment_interval_max": 0.4})
            timing["profile_version"] = config._QQ_TIMING_PROFILE_VERSION
            section["timing"] = timing
            section.update({key: values["bridge"][key] for key in QQ_BRIDGE})
            updated["qq_bridge"] = section
        else:
            section = copy.deepcopy(updated.get("wechat", {}))
            timing = copy.deepcopy(section.get("timing", {}))
            timing.update(values["timing"])
            section["timing"] = timing
            bridge_config = copy.deepcopy(section.get("bridge", {}))
            bridge_config.update(values["bridge"])
            section["bridge"] = bridge_config
            updated["wechat"] = section
        _write(updated)
    if channel == "qq":
        bridge.reload_qq()
    else:
        bridge.reload_wechat()
    return state(channel, bridge)


def set_qq_auto_start(payload):
    if not isinstance(payload, dict):
        raise ApiConfigError("配置提交格式无效")
    enabled = _bool(payload.get("enabled"), "auto_start")
    with _lock:
        full = _load()
        section = copy.deepcopy(full.get("qq_bridge", {}))
        section["auto_start"] = enabled
        full["qq_bridge"] = section
        _write(full)
        current = _settings("qq", full)
    return {"enabled": enabled, "revision": _revision("qq", current)}


def request(channel, action, payload, bridge):
    if action == "state":
        return state(channel, bridge)
    if action == "save":
        return save(channel, payload, bridge)
    if action == "auto-start" and channel == "qq":
        return set_qq_auto_start(payload)
    if action == "fast-reply" and channel == "qq":
        return bridge.set_qq_fast_reply(_bool(payload.get("enabled"), "fast_reply"))
    if action == "retry" and channel == "qq":
        bridge.stop_qq()
        return bridge.start_qq()
    if action == "connect":
        return bridge.start_qq() if channel == "qq" else bridge.start_wechat()
    if action == "disconnect":
        return bridge.stop_qq() if channel == "qq" else bridge.stop_wechat()
    raise LookupError("未知聊天设置操作")
