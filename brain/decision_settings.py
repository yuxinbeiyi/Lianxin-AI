"""Decision hub settings and explicit synthetic connection checks."""

from __future__ import annotations

import math
import threading
from urllib.parse import urlparse

from brain import decision_local
from brain.decision_client import DecisionClient, DecisionError, MODEL
from config import get_decision_config, save_decision_config
from utils.decision_credentials import clear_api_key, credential_state, save_api_key


_lock = threading.RLock()


def trusted_origin(origin: str) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https", "tauri"} and parsed.hostname in {
        "127.0.0.1", "localhost", "tauri.localhost", "[::1]", "::1",
    }


def settings_state() -> dict:
    settings = get_decision_config()
    return {**settings, **credential_state(), "api_model": MODEL, "local_model": decision_local.LOCAL_MODEL,
            "local": decision_local.status(settings)}


def save_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("invalid_decision_settings")
    with _lock:
        settings = get_decision_config()
        for key in settings:
            if key not in payload:
                continue
            value = payload[key]
            if key in {"enabled", "allow_cloud_context", "local_auto_start"}:
                if not isinstance(value, bool):
                    raise ValueError("invalid_decision_boolean")
            elif key == "provider":
                if value not in {"api", "local"}:
                    raise ValueError("invalid_decision_provider")
            elif key in {"timeout_seconds", "budget_seconds", "local_port"}:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("invalid_decision_number")
                maximum = {"timeout_seconds": 15, "budget_seconds": 30, "local_port": 65535}[key]
                minimum = 1024 if key == "local_port" else 1
                if not minimum <= value <= maximum or key == "local_port" and int(value) != value:
                    raise ValueError("decision_number_out_of_range")
            elif not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError("invalid_local_setting")
            settings[key] = value
        if "api_key" in payload and not isinstance(payload["api_key"], str):
            raise ValueError("invalid_api_key")
        if "clear_api_key" in payload and not isinstance(payload["clear_api_key"], bool):
            raise ValueError("invalid_clear_key")
        if payload.get("clear_api_key") and payload.get("api_key"):
            raise ValueError("conflicting_key_operation")
        if payload.get("api_key", "").strip():
            save_api_key(payload["api_key"])
        elif payload.get("clear_api_key"):
            clear_api_key()
        save_decision_config(settings)
        return settings_state()


def test_connection() -> dict:
    settings = get_decision_config()
    if settings["provider"] == "api" and not settings["allow_cloud_context"]:
        return {"ok": False, "error": "cloud_context_not_allowed"}
    try:
        client = DecisionClient(settings)
        client.decide({"message": "Synthetic connectivity test. This is not a real conversation or memory."}, {
            "connection": {"type": "noul", "instructions": "Is this a synthetic connectivity test?"},
        })
        return {"ok": True, "model": client.model, "provider": client.provider,
                "summary": "合成请求已返回且格式校验通过；不代表真实决策准确率。"}
    except DecisionError as exc:
        return {"ok": False, "error": str(exc)}


def local_action(action: str) -> dict:
    if action == "start":
        return decision_local.start(get_decision_config())
    if action == "stop":
        return decision_local.stop()
    raise ValueError("invalid_local_action")
