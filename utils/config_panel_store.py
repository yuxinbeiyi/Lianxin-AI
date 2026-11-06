import copy
import hashlib
import json
import math
from urllib.parse import urlparse

import config
from utils.api_config_panel import _load, _write, _lock


class PanelConflict(ValueError):
    pass


STT_FIELDS = {
    "stt": ("default_engine", "auto_fallback"),
    "funasr": ("enabled", "device"),
    "volcano": ("enabled", "appid", "access_token"),
    "aliyun": ("enabled", "access_key_id", "access_key_secret", "app_key"),
    "whisper": ("enabled", "model_size", "language", "device"),
}
TTS_FIELDS = ("engine", "gpt_sovits_path", "gpt_sovits_version", "gpt_sovits_gpt_path", "gpt_sovits_sovits_path", "ref_wav_override", "default_mood", "speed", "tts_warmup", "gpt_sovits_idle_timeout_seconds", "temperature", "top_k", "top_p", "sample_steps", "how_to_cut", "pause_second")
NETWORK_FIELDS = {"tavily": ("api_key",), "firecrawl": ("api_key",), "zhihu": ("access_secret",), "proxy": ("enabled", "http_proxy", "https_proxy", "no_proxy"), "bilibili": ("sessdata", "bili_jct"), "network_tool_order": ("search_order", "fetch_order", "retry_count", "fallback_on_quota")}
FIELDS = {"stt": STT_FIELDS, "sound": {"tts": TTS_FIELDS}, "network": NETWORK_FIELDS}
SECRETS = {"volcano.access_token", "aliyun.access_key_id", "aliyun.access_key_secret", "aliyun.app_key", "tavily.api_key", "firecrawl.api_key", "zhihu.access_secret", "bilibili.sessdata", "bilibili.bili_jct", "proxy.http_proxy", "proxy.https_proxy"}
ADVANCED = {"temperature": .3, "top_k": 5, "top_p": .9, "sample_steps": 32, "how_to_cut": "不切", "pause_second": .3}
CUTS = ("不切", "凑四句一切", "凑50字一切", "按中文句号。切", "按英文句号.切", "按标点符号切")


def sections(domain, full):
    if domain == "stt":
        defaults = config._STT_ENGINE_DEFAULTS
        stored = full.get("stt_engine", {})
        engines = stored.get("engines", {})
        result = {"stt": {**defaults, **stored}, **{name: {**values, **engines.get(name, {})} for name, values in defaults["engines"].items()}}
        preferences = full.get("device_preferences", {})
        for name in ("funasr", "whisper"):
            result[name]["device"] = preferences.get(name, result[name]["device"])
        return result
    if domain == "sound":
        values = {**config._TTS_DEFAULTS, **full.get("tts", {})}
        if values["engine"] not in ("edge_tts", "gpt_sovits"):
            values["engine"] = "edge_tts"
        values.setdefault("how_to_cut", "不切")
        values.setdefault("pause_second", .3)
        return {"tts": values}
    names = ("tavily", "firecrawl", "zhihu", "proxy", "bilibili")
    result = {name: {**getattr(config, f"_{name.upper()}_DEFAULTS"), **full.get(name, {})} for name in names}
    result["network_tool_order"] = {**config.get_network_tool_order_config(), **full.get("network_tool_order", {})}
    return result


def revision(domain, full):
    names = {"stt": ("stt_engine", "device_preferences", "volcano_stt", "aliyun_stt"), "sound": ("tts",), "network": (*NETWORK_FIELDS, "builtin_tools")}[domain]
    values = {name: full.get(name) for name in names}
    if domain == "sound":
        from utils.settings import SettingsManager
        values["silent_mode"] = SettingsManager().silent_mode
    if domain == "network":
        from brain.network_router import tool_catalog
        values["switches"] = [[(item["id"], item["checked"]) for item in tool_catalog(kind)] for kind in ("search", "fetch")]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def public(domain, full):
    result = {}
    for name, fields in FIELDS[domain].items():
        values = sections(domain, full)[name]
        result[name] = {field: "" if f"{name}.{field}" in SECRETS else values[field] for field in fields}
        result[name].update({f"{field}_set": bool(values.get(field)) for field in fields if f"{name}.{field}" in SECRETS})
    return {"sections": result, "revision": revision(domain, full)}


def _validate(name, values):
    enums = {"stt": {"default_engine": ("auto", "funasr", "volcano", "aliyun", "whisper")}, "funasr": {"device": ("auto", "cpu", "cuda")}, "whisper": {"device": ("auto", "cpu", "cuda"), "model_size": ("tiny", "base", "small", "medium", "large"), "language": ("zh", "en", "ja", "ko", "")}, "tts": {"engine": ("edge_tts", "gpt_sovits"), "gpt_sovits_version": ("v2Pro", "v3", "v4", "v2ProPlus"), "default_mood": ("auto", "casual", "tsundere", "romantic", "long"), "how_to_cut": CUTS, "gpt_sovits_idle_timeout_seconds": (0, 60, 300, 900)}}
    for field, choices in enums.get(name, {}).items():
        if values[field] not in choices:
            raise ValueError(f"配置选项无效：{name}.{field}")
    ranges = {"tts": {"speed": (.5, 2), "temperature": (.1, 1), "top_k": (1, 20), "top_p": (.1, 1), "sample_steps": (4, 64), "pause_second": (0, 1)}, "network_tool_order": {"retry_count": (0, 5)}}
    for field, (lower, upper) in ranges.get(name, {}).items():
        value = values[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper or field in ("top_k", "sample_steps", "retry_count") and int(value) != value:
            raise ValueError(f"配置数值超出范围：{name}.{field}")
    if name == "network_tool_order":
        from brain.network_router import SEARCH_IDS, FETCH_IDS
        for field, expected in (("search_order", SEARCH_IDS), ("fetch_order", FETCH_IDS)):
            order = values[field]
            if not isinstance(order, list) or len(order) != len(expected) or any(not isinstance(item, str) for item in order) or set(order) != set(expected):
                raise ValueError("联网顺序必须完整且不能重复")
    if name == "proxy":
        for field in ("http_proxy", "https_proxy"):
            if values[field]:
                parsed = urlparse(values[field])
                if parsed.scheme not in ("http", "https", "socks5", "socks5h") or not parsed.hostname:
                    raise ValueError("代理地址格式错误")


def merge(domain, full, payload):
    if payload.get("revision") != revision(domain, full):
        raise PanelConflict("配置已被其它窗口修改，请重新加载后再操作；本次未保存")
    incoming, clears = payload.get("sections", {}), payload.get("clearSecrets", [])
    if not isinstance(incoming, dict) or not isinstance(clears, list) or any(not isinstance(item, str) or item not in SECRETS or item.split(".")[0] not in FIELDS[domain] for item in clears):
        raise ValueError("配置请求格式错误")
    values = copy.deepcopy(sections(domain, full))
    for name, fields in incoming.items():
        if name not in FIELDS[domain] or not isinstance(fields, dict) or set(fields) - set(FIELDS[domain][name]):
            raise ValueError("未知配置字段")
        for field, value in fields.items():
            previous = values[name].get(field)
            if isinstance(previous, bool):
                valid = type(value) is bool
            elif isinstance(previous, (float, int)):
                valid = type(value) in (float, int) and math.isfinite(value)
            elif isinstance(previous, list):
                valid = isinstance(value, list)
            else:
                valid = isinstance(value, str) and len(value) <= 16384 and "\n" not in value and "\r" not in value
            if not valid:
                raise ValueError(f"配置类型错误：{name}.{field}")
            if f"{name}.{field}" in SECRETS and not value.strip():
                continue
            values[name][field] = value.strip() if isinstance(value, str) else value
    for identifier in clears:
        name, field = identifier.split(".")
        if incoming.get(name, {}).get(field):
            raise ValueError("不能同时设置和清除同一凭据")
        values[name][field] = ""
    for name, section in values.items():
        _validate(name, section)
    result = copy.deepcopy(full)
    if domain == "stt":
        original = result.setdefault("stt_engine", {})
        original.update({key: values["stt"][key] for key in STT_FIELDS["stt"]})
        for name in ("funasr", "volcano", "aliyun", "whisper"):
            original.setdefault("engines", {})[name] = values[name]
        for name in ("funasr", "whisper"):
            result.setdefault("device_preferences", {})[name] = values[name]["device"]
    else:
        for name in FIELDS[domain]:
            result[name] = values[name]
    return result
