from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import config


class ApiConfigConflict(RuntimeError):
    pass


class ApiConfigError(ValueError):
    pass


FIELDS = {
    "deepseek": ("provider", "api_key", "base_url", "model", "api_format", "max_tokens", "local_base_url", "local_model_name", "router_model"),
    "agnes": ("api_key", "model"),
    "qq_bridge": ("enabled", "ws_url", "qq_account", "owner_qq", "owner_name"),
    "siliconflow": ("api_key", "base_url", "vision_model"),
    "image_gen": ("enabled", "provider", "agnes_api_key", "model", "siliconflow_model", "default_size", "default_quality", "num_inference_steps", "guidance_scale"),
    "video_gen": ("enabled", "model", "default_duration", "default_frame_rate"),
    "qweather": ("api_key", "api_host", "dev_id", "default_city", "auto_remind", "remind_time"),
}
DEFAULTS = {
    "deepseek": config._DEEPSEEK_DEFAULTS,
    "agnes": config._AGNES_DEFAULTS,
    "qq_bridge": config._QQ_BRIDGE_DEFAULTS,
    "siliconflow": config._SILICONFLOW_DEFAULTS,
    "image_gen": config._IMAGE_GEN_DEFAULTS,
    "video_gen": config._VIDEO_GEN_DEFAULTS,
    "qweather": config._QWEATHER_DEFAULTS,
}
SECRETS = {"api_key", "agnes_api_key"}
PROFILE_FIELDS = ("api_key", "base_url", "model", "api_format", "max_tokens")
_lock = threading.RLock()
_generation_lock = threading.Lock()


def _load() -> dict:
    path = config._USER_CONFIG_PATH
    full = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(full, dict):
        raise ApiConfigError("本地配置文件格式错误，未作修改")
    return full


def _sections(full: dict) -> dict:
    return {name: {**defaults, **full.get(name, {})} for name, defaults in DEFAULTS.items()}


def _revision(full: dict) -> str:
    visible = {name: full.get(name) for name in (*FIELDS, "chat_profiles", "active_chat_profile")}
    return hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()


def _public(full: dict) -> dict:
    sections = {}
    for name, values in _sections(full).items():
        sections[name] = {field: values[field] for field in FIELDS[name] if field not in SECRETS}
        for field in FIELDS[name]:
            if field in SECRETS:
                sections[name][field] = ""
                sections[name][f"{field}_set"] = bool(values.get(field))
    profiles = [{"name": item["name"], **{field: item.get(field, DEFAULTS["deepseek"].get(field)) for field in PROFILE_FIELDS if field not in SECRETS},
                 "api_key": "", "api_key_set": bool(item.get("api_key"))} for item in full.get("chat_profiles", [])]
    return {"sections": sections, "profiles": profiles, "activeProfile": full.get("active_chat_profile", ""), "revision": _revision(full)}


def state() -> dict:
    with _lock:
        return _public(_load())


def _url(value: str, schemes=("http", "https")) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ApiConfigError("地址格式错误，不允许在 URL 中嵌入账号密码")


def _validate(name: str, values: dict) -> None:
    for field in FIELDS[name]:
        value = values[field]
        default = DEFAULTS[name][field]
        if isinstance(default, bool):
            valid = isinstance(value, bool)
        elif isinstance(default, (int, float)):
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        else:
            valid = isinstance(value, str) and len(value) <= (8192 if field in SECRETS else 2048) and "\n" not in value and "\r" not in value
        if not valid:
            raise ApiConfigError(f"配置字段类型错误：{name}.{field}")
    enums = {
        "deepseek": {"provider": {"deepseek", "agnes", "local"}, "api_format": {"openai", "anthropic"}},
        "image_gen": {"provider": {"agnes", "siliconflow"}, "default_size": {"1024x1024", "1792x1024", "1024x1792", "4k"}, "default_quality": {"standard", "hd"}},
        "video_gen": {"default_duration": {3, 5, 10, 18}, "default_frame_rate": {16, 24, 30, 60}},
    }
    for field, choices in enums.get(name, {}).items():
        if values[field] not in choices:
            raise ApiConfigError(f"配置选项无效：{name}.{field}")
    limits = {"deepseek": {"max_tokens": (512, 32768, True)}, "image_gen": {"num_inference_steps": (1, 100, True), "guidance_scale": (0, 30, False)}}
    for field, (minimum, maximum, integer) in limits.get(name, {}).items():
        value = values[field]
        if not minimum <= value <= maximum or integer and int(value) != value:
            raise ApiConfigError(f"配置数值超出范围：{name}.{field}")
    for field in ("base_url", "local_base_url", "ws_url"):
        if field in FIELDS[name]:
            _url(values[field], ("ws", "wss") if field == "ws_url" else ("http", "https"))
    if name == "qweather":
        try:
            hour, minute = map(int, values["remind_time"].split(":"))
            if not 0 <= hour < 24 or not 0 <= minute < 60:
                raise ApiConfigError()
        except (ValueError, TypeError):
            raise ApiConfigError("天气提醒时间应为 HH:MM") from None
    for field in ("model", "local_model_name", "vision_model", "siliconflow_model"):
        if field in FIELDS[name] and not values[field].strip():
            raise ApiConfigError(f"模型名称不能为空：{name}.{field}")


def _merge(full: dict, payload: dict) -> dict:
    incoming = payload.get("sections", {})
    clears = payload.get("clearSecrets", [])
    if not isinstance(incoming, dict) or not isinstance(clears, list) or any(not isinstance(item, str) for item in clears):
        raise ApiConfigError("配置提交格式错误")
    allowed_secrets = {f"{name}.{field}" for name, fields in FIELDS.items() for field in fields if field in SECRETS}
    if set(clears) - allowed_secrets:
        raise ApiConfigError("未知密钥字段")
    result = copy.deepcopy(full)
    source = payload.get("sourceProfile", "")
    if not isinstance(source, str):
        raise ApiConfigError("来源档案无效")
    if source:
        profile = next((item for item in full.get("chat_profiles", []) if item.get("name") == source), None)
        if profile is None:
            raise LookupError("来源档案已被删除，请重新加载")
        result["deepseek"] = {**DEFAULTS["deepseek"], **result.get("deepseek", {}), "api_key": profile.get("api_key", "")}
    incoming = copy.deepcopy(incoming)
    for identifier in clears:
        incoming.setdefault(identifier.split(".")[0], {})
    for name, fields in incoming.items():
        if name not in FIELDS or not isinstance(fields, dict) or set(fields) - set(FIELDS[name]):
            raise ApiConfigError("未知配置字段")
        values = {**DEFAULTS[name], **result.get(name, {})}
        for field, value in fields.items():
            if field in SECRETS:
                if not isinstance(value, str):
                    raise ApiConfigError("密钥必须为文本")
                if not value.strip():
                    continue
            values[field] = value.strip() if isinstance(value, str) else value
        for field in SECRETS.intersection(FIELDS[name]):
            if f"{name}.{field}" in clears:
                if fields.get(field):
                    raise ApiConfigError("不能同时设置和清除同一密钥")
                values[field] = ""
        _validate(name, values)
        if name == "deepseek":
            values["use_local"] = values["provider"] == "local"
        result[name] = values
    return result


def _write(full: dict) -> None:
    path = config._USER_CONFIG_PATH
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
        if temporary is not None and temporary.exists():
            temporary.unlink()


def mutate(action: str, payload: dict) -> dict:
    with _lock:
        full = _load()
        if payload.get("revision") != _revision(full):
            raise ApiConfigConflict("API 配置已被其它窗口更改，请重新加载后再保存")
        name = payload.get("name", "")
        if not isinstance(name, str) or len(name) > 100:
            raise ApiConfigError("档案名称无效")
        name = name.strip()
        profiles = copy.deepcopy(full.get("chat_profiles", []))
        profile = next((item for item in profiles if item.get("name") == name), None)
        if action == "save":
            updated = _merge(full, payload)
            sections = _sections(updated)
            provider = sections["deepseek"]["provider"]
            if provider != "local" and not sections["agnes" if provider == "agnes" else "deepseek"]["api_key"]:
                raise ApiConfigError("当前聊天提供商的 API Key 不能为空")
            if name and provider == "deepseek":
                if profile is None:
                    raise LookupError("选中的配置档案不存在")
                profile.update({field: sections["deepseek"][field] for field in PROFILE_FIELDS})
                updated["chat_profiles"] = profiles
                updated["active_chat_profile"] = name
            elif provider != "deepseek" or payload.get("name") == "":
                updated["active_chat_profile"] = ""
        elif action == "profile-save":
            if not name:
                raise ApiConfigError("请填写档案名称")
            draft = _sections(_merge(full, payload))["deepseek"]
            if not draft["api_key"]:
                raise ApiConfigError("档案 API Key 不能为空")
            if profile is None:
                profile = {"name": name}
                profiles.append(profile)
            profile.update({field: draft[field] for field in PROFILE_FIELDS})
            updated = {**full, "chat_profiles": profiles}
        elif action == "profile-apply":
            if profile is None:
                raise LookupError("配置档案不存在")
            if not profile.get("api_key"):
                raise ApiConfigError("配置档案没有 API Key")
            updated = _merge(full, {"sections": {"deepseek": {**{field: profile.get(field, DEFAULTS['deepseek'][field]) for field in PROFILE_FIELDS}, "provider": "deepseek"}}})
            updated["active_chat_profile"] = name
        elif action == "profile-delete":
            if profile is None:
                raise LookupError("配置档案不存在")
            updated = {**full, "chat_profiles": [item for item in profiles if item.get("name") != name]}
            if full.get("active_chat_profile") == name:
                updated["active_chat_profile"] = ""
        else:
            raise ApiConfigError("未知配置操作")
        _write(updated)
        return _public(updated)


def reveal(payload: dict) -> dict:
    name, field = payload.get("section"), payload.get("field")
    if name not in FIELDS or field not in SECRETS or field not in FIELDS[name]:
        raise ApiConfigError("密钥字段无效")
    with _lock:
        full = _load()
        if payload.get("revision") != _revision(full):
            raise ApiConfigConflict("配置已变化，请重新加载后再查看密钥")
        if name == "deepseek" and payload.get("sourceProfile"):
            profile = next((item for item in full.get("chat_profiles", []) if item.get("name") == payload["sourceProfile"]), None)
            if profile is None:
                raise LookupError("档案不存在")
            return {"value": profile.get(field, "")}
        return {"value": _sections(full)[name].get(field, "")}


def _test_sections(payload: dict) -> dict:
    with _lock:
        full = _load()
        if payload.get("revision") != _revision(full):
            raise ApiConfigConflict("配置已变化，请重新加载后再测试")
        return _sections(_merge(full, payload))


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _response(response):
    if response.status_code != 200:
        raise ApiConfigError(f"供应商返回 HTTP {response.status_code}，请检查地址、密钥、模型或配额")
    return response.json()


def fetch_models(payload: dict) -> dict:
    import requests

    sections = _test_sections(payload)
    chat = sections["deepseek"]
    if chat["provider"] == "local":
        base, key = config.normalize_local_base_url(chat["local_base_url"]), "ollama"
    elif chat["provider"] == "agnes":
        base, key = sections["agnes"]["base_url"], sections["agnes"]["api_key"]
    else:
        base, key = chat["base_url"], chat["api_key"]
    base = base.rstrip("/").removesuffix("/chat/completions").removesuffix("/messages").rstrip("/")
    urls = [base] if base.endswith("/models") else [base + "/models"] if base.rsplit("/", 1)[-1] in {"v1", "v2", "v3", "v4", "api", "openai"} else [base + "/v1/models", base + "/models"]
    with requests.Session() as session:
        session.trust_env = False
        for endpoint in urls:
            headers = _headers(key) if key else {}
            if chat["provider"] == "deepseek" and chat["api_format"] == "anthropic":
                headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            try:
                response = session.get(endpoint, headers=headers, timeout=(10, 20))
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except ValueError:
                continue
            items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
            if not isinstance(items, list):
                continue
            models = {str(item.get("id") or item.get("name") or "") if isinstance(item, dict) else str(item) for item in items}
            models.discard("")
            if models:
                return {"models": sorted(models)}
    raise ApiConfigError("未获取到模型列表，仍可手动填写模型名称")


def _connection(sections: dict) -> dict:
    import litellm

    chat = sections["deepseek"]
    provider = chat["provider"]
    if provider == "agnes":
        selected = sections["agnes"]
        model, base, key = f"openai/{selected['model']}", selected["base_url"], selected["api_key"]
    elif provider == "local":
        model = config.normalize_local_model_for_litellm(chat["local_model_name"])
        base, key = config.normalize_local_base_url(chat["local_base_url"]), "ollama"
    else:
        model = f"anthropic/{chat['model'].removeprefix('anthropic/')}" if chat["api_format"] == "anthropic" else config.normalize_model_for_litellm(chat["model"], chat["base_url"])
        base, key = chat["base_url"], chat["api_key"]
        if chat["api_format"] == "anthropic":
            base = config.normalize_anthropic_base_url(base)
    if not key:
        raise ApiConfigError("请先填写当前提供商的 API Key")
    response = litellm.completion(model=model, api_base=base, api_key=key, max_tokens=32,
                                  messages=[{"role": "user", "content": "请回复：连接成功"}], timeout=30, num_retries=0)
    return {"message": f"连接成功 · {provider} · {model}", "reply": (response.choices[0].message.content or "")[:100]}


def _save_media(value: str, kind: str) -> str:
    import requests

    folder = Path.home() / ".lianxin" / ("generated_images" if kind == "image" else "videos")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"test_{uuid.uuid4().hex}.{('png' if kind == 'image' else 'mp4')}"
    limit = 32 * 1024 * 1024 if kind == "image" else 512 * 1024 * 1024
    try:
        if value.startswith(("http://", "https://")):
            _url(value)
            with requests.get(value, timeout=(10, 120), stream=True) as response:
                response.raise_for_status()
                size = 0
                with path.open("wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise ApiConfigError("测试文件过大，已停止下载，请到供应商控制台查看")
                        output.write(chunk)
            if not size:
                raise ApiConfigError("供应商返回空文件")
        else:
            if len(value) > limit * 4 // 3 + 4:
                raise ApiConfigError("测试文件过大，已停止保存")
            content = base64.b64decode(value, validate=True)
            if not content:
                raise ApiConfigError("供应商返回空文件")
            path.write_bytes(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return str(path)


def _image(sections: dict) -> dict:
    import requests

    image = sections["image_gen"]
    is_sf = image["provider"] == "siliconflow"
    selected = sections["siliconflow"] if is_sf else sections["agnes"]
    key = selected["api_key"] if is_sf else image["agnes_api_key"] or selected["api_key"]
    if not key:
        raise ApiConfigError("请填写视觉理解的 SiliconFlow Key，或 Agnes 生图专用/聊天 Key")
    body = {"model": image["siliconflow_model"] if is_sf else image["model"], "prompt": "一只可爱的卡通小猫，坐在窗台上看月亮"}
    if is_sf:
        sizes = {"1792x1024": "1024x768", "1024x1792": "768x1024", "4k": "1024x1024"}
        body.update(image_size=sizes.get(image["default_size"], image["default_size"]), batch_size=1,
                    num_inference_steps=image["num_inference_steps"], guidance_scale=image["guidance_scale"])
    else:
        body["size"] = image["default_size"]
    data = _response(requests.post(selected["base_url"].rstrip("/") + "/images/generations", headers=_headers(key), json=body, timeout=(10, 120)))
    if is_sf and data.get("code") not in (None, 0, 20000):
        raise ApiConfigError("SiliconFlow 返回业务错误，请检查模型和参数")
    images = data.get("data") or data.get("images")
    if isinstance(images, list):
        images = images[0] if images else None
    value = (images.get("url") or images.get("b64_json")) if isinstance(images, dict) else images
    if not isinstance(value, str) or not value:
        raise ApiConfigError("返回数据中没有图片")
    return {"message": "测试图片已保存到本地", "path": _save_media(value, "image")}


def video_frames(duration: int, fps: int) -> int:
    return ((min(duration * fps, 441) - 1) // 8) * 8 + 1


def _video(sections: dict) -> dict:
    import requests

    key = sections["agnes"]["api_key"]
    if not key:
        raise ApiConfigError("请填写聊天 API 中的 Agnes Key，视频与它共用密钥")
    video = sections["video_gen"]
    headers = _headers(key)
    body = {"model": video["model"], "prompt": "A cute cat playing with a ball of yarn in a sunny room, soft lighting",
            "num_frames": video_frames(video["default_duration"], video["default_frame_rate"]), "frame_rate": video["default_frame_rate"]}
    data = _response(requests.post("https://apihub.agnes-ai.com/v1/videos", headers=headers, json=body, timeout=(10, 120)))
    video_id = data.get("video_id")
    if not video_id:
        raise ApiConfigError("供应商没有返回视频任务 ID")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        time.sleep(5)
        data = _response(requests.get("https://apihub.agnes-ai.com/agnesapi", params={"video_id": video_id}, headers=headers, timeout=(10, 30)))
        if data.get("status") == "failed":
            raise ApiConfigError("供应商视频生成失败，请检查模型或额度")
        if data.get("status") == "completed":
            value = data.get("remixed_from_video_id") or data.get("url")
            if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                raise ApiConfigError("供应商没有返回有效视频地址")
            return {"message": "测试视频已保存到本地", "path": _save_media(value, "video")}
    raise ApiConfigError("视频轮询超时，供应商任务可能仍在运行，请勿重复付费创建")


def test_action(payload: dict) -> dict:
    kind = payload.get("kind")
    if kind not in {"connection", "image", "video"}:
        raise ApiConfigError("未知测试类型")
    sections = _test_sections(payload)
    if kind == "connection":
        return _connection(sections)
    if payload.get("confirmed") is not True:
        raise ApiConfigError("生图或视频测试可能收费，需要明确确认")
    if not _generation_lock.acquire(blocking=False):
        raise ApiConfigConflict("已有媒体生成测试进行中，请勿重复提交")
    try:
        return _image(sections) if kind == "image" else _video(sections)
    finally:
        _generation_lock.release()
