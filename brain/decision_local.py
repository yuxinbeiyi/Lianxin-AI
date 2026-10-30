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
_log_path = Path(__file__).resolve().parent.parent / "logs" / "decision_local.log"


def endpoint(config: dict) -> str:
    return f"http://127.0.0.1:{int(config['local_port'])}/v1/systemone"


def status(config: dict) -> dict:
    port = int(config["local_port"])
    try:
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/health", timeout=0.6) as response:
            payload = json.loads(response.read(8192))
        if payload.get("status") == "ready" and payload.get("model") == LOCAL_MODEL:
            if payload.get("authentication"):
                return {"status": "authentication_required", "ready": False, "managed": False}
            return {"status": "ready", "ready": True,
                    "managed": bool(_process and _process.poll() is None and _port == port)}
        return {"status": "unexpected_service", "ready": False, "managed": False}
    except Exception:
        if _process and _port == port:
            running = _process.poll() is None
            return {"status": "starting" if running else "failed", "ready": False, "managed": running}
        return {"status": "stopped", "ready": False, "managed": False}


def start(config: dict) -> dict:
    global _process, _port
    with _lock:
        current = status(config)
        if current["status"] in {"ready", "starting"}:
            return current
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
            _process = subprocess.Popen(arguments, cwd=str(directory), env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _port = int(config["local_port"])
        return {"status": "starting", "ready": False, "managed": True}


def ensure_ready(config: dict):
    current = status(config)
    if current["ready"]:
        return
    if current["status"] == "stopped" and config.get("local_auto_start", True):
        current = start(config)
    raise RuntimeError("local_" + current["status"])


def stop() -> dict:
    global _process, _port
    with _lock:
        if _process and _process.poll() is None:
            _process.terminate()
            _process.wait(timeout=5)
        _process = None
        _port = None
        return {"status": "stopped", "ready": False, "managed": False}


atexit.register(stop)
