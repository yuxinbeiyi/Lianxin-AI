"""Manage only Lianxin's own loopback d3 server, without shell commands."""

from __future__ import annotations

import json
import atexit
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


LOCAL_MODEL = "vllm-sr/d3-edge"
_lock = threading.RLock()
_process = None
_port = None
_device = None
_generation = 0
_auto_start_paused = False
_log_path = Path(__file__).resolve().parent.parent / "logs" / "decision_local.log"


def endpoint(config: dict) -> str:
    return f"http://127.0.0.1:{int(config['local_port'])}/v1/systemone"


def status(config: dict) -> dict:
    with _lock:
        port = int(config["local_port"])
        running = bool(_process and _process.poll() is None)
        base = {"ready": False, "managed": running, "auto_start_paused": _auto_start_paused,
                "pid": int(_process.pid) if running else None, "port": _port if running else port,
                "device": _device if running else None, "modalities": []}
        if running and _port != port:
            return {**base, "status": "different_port"}
        try:
            with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/health", timeout=0.6) as response:
                payload = json.loads(response.read(8192))
            if payload.get("status") == "ready" and payload.get("model") == LOCAL_MODEL:
                if payload.get("authentication"):
                    return {**base, "status": "authentication_required"}
                modalities = payload.get("modalities", [])
                if not isinstance(modalities, list):
                    modalities = []
                return {**base, "status": "ready", "ready": True,
                        "modalities": [item for item in ("text", "image", "video") if item in modalities]}
            return {**base, "status": "unexpected_service"}
        except Exception:
            if _process and _port == port:
                return {**base, "status": "starting" if running else "failed"}
            return {**base, "status": "stopped"}


def generation() -> int:
    with _lock:
        return _generation


def request_allowed(expected_generation: int) -> bool:
    with _lock:
        return not _auto_start_paused and expected_generation == _generation


def start(config: dict) -> dict:
    global _process, _port, _device, _auto_start_paused, _generation
    with _lock:
        current = status(config)
        if current["status"] in {"ready", "starting"}:
            _auto_start_paused = False
            return {**current, "auto_start_paused": False}
        if current["status"] == "different_port":
            raise ValueError("stop_managed_local_before_changing_port")
        if current["status"] not in {"stopped", "failed"}:
            raise ValueError("local_port_in_use_or_authentication_required")
        if _process and _process.poll() is None:
            raise ValueError("stop_managed_local_before_changing_port")
        directory = Path(config["local_model_dir"]).expanduser().resolve()
        interpreter = Path(config["local_python"]).expanduser().resolve()
        script = directory / "d3_server.py"
        if not interpreter.is_file() or not script.is_file():
            raise ValueError("local_python_or_server_not_found")
        device = str(config["local_device"])
        if not re.fullmatch(r"cpu|cuda(?::\d+)?", device):
            raise ValueError("invalid_local_device")
        env = os.environ.copy()
        env.pop("DECISION_API_KEY", None)
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONIOENCODING="utf-8")
        arguments = [str(interpreter), "-u", str(script), "--model", str(directory),
                     "--host", "127.0.0.1", "--port", str(int(config["local_port"])),
                     "--device", device, "--name", LOCAL_MODEL, "--batch-size", "1"]
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_path.open("ab") as log:
            _generation += 1
            _process = subprocess.Popen(arguments, cwd=str(directory), env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _port = int(config["local_port"])
        _device = device
        _auto_start_paused = False
        return {"status": "starting", "ready": False, "managed": True, "auto_start_paused": False,
                "pid": int(_process.pid), "port": _port, "device": device, "modalities": []}


def ensure_ready(config: dict):
    with _lock:
        if _auto_start_paused:
            raise RuntimeError("local_paused")
        current = status(config)
        if current["ready"]:
            return
        if current["status"] == "stopped" and config.get("local_auto_start", True):
            current = start(config)
        raise RuntimeError("local_" + current["status"])


def stop() -> dict:
    global _process, _port, _device, _generation, _auto_start_paused
    with _lock:
        _auto_start_paused = True
        _generation += 1
        if _process and _process.poll() is None:
            _process.terminate()
            try:
                _process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _process.kill()
                _process.wait(timeout=2)
        _process = None
        _port = None
        _device = None
        return {"status": "stopped", "ready": False, "managed": False, "auto_start_paused": True,
                "pid": None, "port": None, "device": None, "modalities": []}


atexit.register(stop)
