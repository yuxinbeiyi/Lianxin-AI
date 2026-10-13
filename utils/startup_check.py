"""
startup_check.py — 莲心 AI 启动健康检查
逐项检测关键依赖：API、本地模型、语音、浏览器、桥接、数据库、硬件。
每项独立超时，不阻塞启动。仅在发现问题时弹窗提示。
"""

import sys
import os
import json
import time
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CheckResult:
    name: str
    icon: str       # "✅" | "⚠️" | "❌" | "⏭️"
    message: str
    detail: str = ""


# ══════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════

def _run_with_timeout(func, timeout: float = 5.0, default=None):
    """在后台线程执行 func，超时返回 default。"""
    q: queue.Queue = queue.Queue()

    def _worker():
        try:
            q.put(("ok", func()))
        except Exception as e:
            q.put(("err", str(e)))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return ("timeout", default)
    try:
        return q.get_nowait()
    except queue.Empty:
        return ("timeout", default)


def _ok(msg: str, detail: str = "") -> CheckResult:
    return CheckResult(name="", icon="✅", message=msg, detail=detail)

def _warn(msg: str, detail: str = "") -> CheckResult:
    return CheckResult(name="", icon="⚠️", message=msg, detail=detail)

def _err(msg: str, detail: str = "") -> CheckResult:
    return CheckResult(name="", icon="❌", message=msg, detail=detail)

def _skip(msg: str = "未启用") -> CheckResult:
    return CheckResult(name="", icon="⏭️", message=msg)


# ══════════════════════════════════════════════════════════
# Individual Checks
# ══════════════════════════════════════════════════════════

def _check_ai_api() -> CheckResult:
    """检测 AI API Key 和连通性。"""
    try:
        from config import get_api_config, get_agnes_config
    except Exception as e:
        return _err("无法加载配置", str(e))

    # 检查当前使用的 provider
    api_cfg = get_api_config()
    provider = api_cfg.get("provider", "deepseek")
    use_local = api_cfg.get("use_local", False)

    if use_local:
        return _skip("使用本地模型")

    if provider == "agnes":
        agnes_cfg = get_agnes_config()
        key = agnes_cfg.get("api_key", "").strip()
        if not key:
            return _warn("Agnes API Key 未配置")
        base_url = agnes_cfg.get("base_url", "https://apihub.agnes-ai.com/v1")
    else:
        key = api_cfg.get("api_key", "").strip()
        if not key:
            return _warn("DeepSeek API Key 未配置")
        base_url = api_cfg.get("base_url", "https://api.deepseek.com")

    # 连通性检测
    status, result = _run_with_timeout(
        lambda: _test_api_reachable(base_url, key),
        timeout=5.0,
    )
    if status == "timeout":
        return _warn(f"{base_url} 连接超时", "请检查网络或代理设置")
    if status == "err":
        return _warn(f"API 连通检测失败", str(result))
    return _ok(f"API 连通正常 ({provider})")


def _test_api_reachable(base_url: str, api_key: str) -> str:
    import requests
    headers = {"Authorization": f"Bearer {api_key}"}
    r = requests.get(f"{base_url}/models", headers=headers, timeout=5)
    r.raise_for_status()
    return "ok"


def _check_ollama() -> CheckResult:
    """检测 Ollama 本地模型。"""
    try:
        from config import get_api_config
        cfg = get_api_config()
        if not cfg.get("use_local", False) and not cfg.get("router_model", ""):
            return _skip("未使用本地模型")
    except Exception:
        return _skip()

    local_url = cfg.get("local_base_url", "http://localhost:11434/v1")
    # 从 v1 URL 提取 base URL
    base = local_url.replace("/v1", "") if "/v1" in local_url else local_url

    status, result = _run_with_timeout(
        lambda: _test_ollama(base),
        timeout=3.0,
    )
    if status == "timeout":
        return _warn("Ollama 服务未响应", base)
    if status == "err":
        return _err("Ollama 连接失败", str(result))

    # 检查模型名
    model_name = cfg.get("local_model_name", "")
    router_model = cfg.get("router_model", "")
    if model_name and model_name not in str(result):
        return _warn(f"模型 '{model_name}' 未安装", f"可用: {result}")
    if router_model and router_model not in str(result):
        return _warn(f"路由模型 '{router_model}' 未安装")
    return _ok(f"Ollama 在线 ({model_name or 'N/A'})")


def _test_ollama(base_url: str) -> str:
    import requests
    r = requests.get(f"{base_url}/api/tags", timeout=3)
    r.raise_for_status()
    data = r.json()
    names = [m.get("name", "") for m in data.get("models", [])]
    return ", ".join(names[:10])


def _check_vision_api() -> CheckResult:
    """检测 SiliconFlow 视觉 API Key。"""
    try:
        from config import get_siliconflow_config
        cfg = get_siliconflow_config()
        key = cfg.get("api_key", "").strip()
    except Exception:
        return _skip()
    if not key:
        return _skip("未配置 SiliconFlow Key")
    return _ok("SiliconFlow Key 已配置")


def _check_stt() -> CheckResult:
    """检测语音识别（阿里云/火山 STT + Whisper 模型）。"""
    try:
        from config import get_aliyun_stt_config, get_volcano_stt_config
    except Exception:
        return _skip()

    aliyun = get_aliyun_stt_config()
    volcano = get_volcano_stt_config()

    has_aliyun = aliyun.get("access_key_id", "").strip()
    has_volcano = volcano.get("access_key", "").strip()

    if has_aliyun:
        stt_provider = "阿里云 NLS"
    elif has_volcano:
        stt_provider = "火山引擎"
    else:
        return _skip("未配置 STT")

    # 检查 Whisper 模型
    try:
        import faster_whisper
        # 仅检查模块可用，不加载模型（耗时）
        _ = faster_whisper.WhisperModel
        whisper_ok = True
    except ImportError:
        whisper_ok = False

    detail = f"STT: {stt_provider}"
    if whisper_ok:
        detail += ", Whisper: 可用"
    else:
        detail += ", Whisper: 未安装"
    return _ok(detail)


def _probe_edge_tts(voice: str) -> str:
    """真正合成一小段音频验证可用性；成功返回空串，失败返回可读原因。"""
    import asyncio
    import tempfile

    try:
        import edge_tts
    except ImportError:
        return "未安装 edge-tts"

    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="lianxin_tts_probe_")
    os.close(fd)
    try:
        async def _run():
            from voice import edge_tts_compat
            await edge_tts_compat.save("莲心语音合成自检。", voice, path)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size < 256:
            return f"合成结果为空（{size} 字节）"
        return ""
    except Exception as exc:
        detail = str(exc)
        if "403" in detail:
            return "服务端拒绝（HTTP 403，Edge Read Aloud 新旧端点均被拒）"
        if "401" in detail or "404" in detail:
            return "服务端拒绝（HTTP 4xx）"
        if "timeout" in detail.lower() or "timed out" in detail.lower():
            return "连接超时"
        return detail[:120] or exc.__class__.__name__
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def _check_tts() -> CheckResult:
    """检测语音合成：GPT-SoVITS 路径 + Edge-TTS 试合成（import 成功不代表可用）。"""
    try:
        from config import get_tts_config
        cfg = get_tts_config()
    except Exception:
        return _skip()

    engine = cfg.get("engine", "auto")
    voice = str(cfg.get("edge_tts_voice") or "zh-CN-XiaoxiaoNeural")
    parts = []
    problems = []

    # GPT-SoVITS（本地主引擎）
    sovits_path = str(cfg.get("gpt_sovits_path", "") or "").strip()
    if sovits_path:
        if Path(sovits_path).exists():
            parts.append("GPT-SoVITS: 路径有效")
        else:
            parts.append("GPT-SoVITS: 路径不存在")
            problems.append("GPT-SoVITS 路径无效")
    elif engine in ("gpt_sovits", "auto"):
        parts.append("GPT-SoVITS: 未配置路径")

    # Edge-TTS 回退引擎：必须真的合成成功一次才算可用
    status, probe = _run_with_timeout(
        lambda: _probe_edge_tts(voice), timeout=15.0, default=""
    )
    if status == "ok" and not probe:
        parts.append("Edge-TTS: 试合成正常")
    elif status == "ok":
        parts.append(f"Edge-TTS: 合成失败（{probe}）")
        problems.append(f"Edge-TTS 不可用：{probe}")
    elif status == "timeout":
        parts.append("Edge-TTS: 试合成超时")
        problems.append("Edge-TTS 试合成超时")
    else:
        parts.append(f"Edge-TTS: 检测异常（{probe}）")
        problems.append(f"Edge-TTS 检测异常：{probe}")

    if not parts:
        return _skip("无 TTS 引擎")
    detail = "; ".join(parts)
    if problems:
        return _warn("语音合成存在问题（详见下方）", detail)
    return _ok(detail)


def _check_ffmpeg() -> CheckResult:
    """检测 FFmpeg：QQ 语音 SILK 转换与 pydub 依赖它。"""
    try:
        from brain.audio_utils import _resolve_ffmpeg
    except Exception as exc:
        return _err("FFmpeg 检测模块不可用", str(exc)[:160])

    try:
        path = _resolve_ffmpeg()
    except Exception as exc:
        return _warn("未找到 FFmpeg（QQ 语音回复会失败）", str(exc)[:200])

    try:
        import pydub  # noqa: F401
        pydub_note = "pydub: 可用"
    except ImportError:
        pydub_note = "pydub: 未安装"
    return _ok(f"FFmpeg: {path}", pydub_note)


def _check_playwright() -> CheckResult:
    """检测 Playwright 浏览器。"""
    try:
        from config import get_browser_config
        cfg = get_browser_config()
    except Exception:
        return _skip()

    channel = cfg.get("channel", "chromium")

    status, result = _run_with_timeout(
        lambda: _test_playwright(channel),
        timeout=3.0,
    )
    if status == "timeout":
        return _warn("Playwright 检测超时")
    if status == "err":
        return _warn(f"Playwright 浏览器未安装", str(result))
    return _ok(f"Playwright ({channel}) 可用")


def _test_playwright(channel: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if channel == "msedge":
            browser = p.chromium.launch(channel="msedge", headless=True)
        else:
            browser = p.chromium.launch(headless=True)
        browser.close()
    return "ok"


def _check_qq_bridge() -> CheckResult:
    """检测 QQ 桥接 NapCatQQ。"""
    try:
        from config import get_qq_bridge_config
        cfg = get_qq_bridge_config()
    except Exception:
        return _skip()

    if not cfg.get("enabled", False):
        return _skip("QQ 桥接未启用")

    ws_url = cfg.get("ws_url", "ws://127.0.0.1:3001")
    status, result = _run_with_timeout(
        lambda: _test_websocket(ws_url),
        timeout=3.0,
    )
    if status == "timeout":
        return _warn(f"NapCatQQ 未响应", ws_url)
    if status == "err":
        return _err(f"NapCatQQ 连接失败", str(result))
    return _ok("NapCatQQ 在线")


def _test_websocket(ws_url: str) -> str:
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3001
    sock = socket.create_connection((host, port), timeout=3)
    sock.close()
    return "ok"


def _check_wechat_bridge() -> CheckResult:
    """检测微信桥接 AstrBot。"""
    try:
        from config import get_wechat_bridge_config
        cfg = get_wechat_bridge_config()
    except Exception:
        return _skip()

    if not cfg.get("auto_start", False):
        return _skip("微信桥接未启用")

    port = cfg.get("listen_port", 8088)
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return _ok(f"端口 {port} 可用")
    except OSError:
        return _warn(f"端口 {port} 已被占用")


def _check_database() -> CheckResult:
    """检测 SQLite 数据库可读写。"""
    try:
        import sqlite3
        from utils.paths import get_user_data_dir
        db_dir = get_user_data_dir()
        db_dir.mkdir(parents=True, exist_ok=True)
        test_path = db_dir / "_health_check_test.db"
        conn = sqlite3.connect(str(test_path))
        conn.execute("CREATE TABLE IF NOT EXISTS _test (id INTEGER)")
        conn.execute("INSERT INTO _test VALUES (1)")
        conn.execute("DELETE FROM _test")
        conn.commit()
        conn.close()
        test_path.unlink(missing_ok=True)
        return _ok("数据库可读写")
    except Exception as e:
        return _err("数据库不可写", str(e))


def _check_hardware() -> CheckResult:
    """检测麦克风和扬声器。"""
    parts = []

    # 麦克风
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if inputs:
            parts.append(f"麦克风: {inputs[0]['name'][:30]}")
        else:
            parts.append("麦克风: 未检测到")
    except Exception:
        parts.append("麦克风: sounddevice 不可用")

    # 扬声器
    try:
        import pygame
        ok = False
        try:
            pygame.mixer.init()
            ok = True
        finally:
            if ok:
                pygame.mixer.quit()
        parts.append("扬声器: 可用")
    except Exception:
        parts.append("扬声器: 初始化失败")

    return _ok("; ".join(parts)) if parts else _skip()


def _check_runtime_foundation() -> CheckResult:
    """Initialize durable stores and report the platform-specific desktop baseline."""
    try:
        from brain.task_store import TaskStore
        from brain.workflow import WorkflowStore
        from utils.platform_capabilities import get_platform_capabilities

        caps = get_platform_capabilities()
        task_store = TaskStore()
        workflow_store = WorkflowStore()
        detail = (
            f"{caps.system} {caps.release}; "
            f"tasks={task_store.db_path.name}; workflows={workflow_store.db_path.name}; "
            f"global_hotkey={'native' if caps.native_global_hotkey else 'window-local'}"
        )
        return _ok("运行底座与持久化迁移正常", detail)
    except Exception as exc:
        return _err("运行底座初始化失败", str(exc))


# ══════════════════════════════════════════════════════════
# Main API
# ══════════════════════════════════════════════════════════

_CHECKS = [
    ("运行底座",        _check_runtime_foundation),
    ("AI API",        _check_ai_api),
    ("Ollama 本地模型", _check_ollama),
    ("视觉 API",       _check_vision_api),
    ("语音识别",       _check_stt),
    ("语音合成",       _check_tts),
    ("FFmpeg",        _check_ffmpeg),
    ("Playwright",    _check_playwright),
    ("QQ 桥接",        _check_qq_bridge),
    ("微信桥接",       _check_wechat_bridge),
    ("数据库",         _check_database),
    ("硬件",           _check_hardware),
]


def run_checks() -> list[CheckResult]:
    """运行所有启动健康检查。返回结果列表。"""
    results = []
    for name, fn in _CHECKS:
        try:
            r = fn()
        except Exception as e:
            r = _err(f"检测异常: {e}")
        r.name = name
        results.append(r)

        # 实时输出到终端
        print(f"  {r.icon} {r.name}: {r.message}", flush=True)

    return results


def has_errors(results: list[CheckResult]) -> bool:
    return any(r.icon == "❌" for r in results)


def has_warnings(results: list[CheckResult]) -> bool:
    return any(r.icon in ("⚠️", "❌") for r in results)


def format_report(results: list[CheckResult]) -> str:
    """格式化为可读文本报告。跳过未启用的项不显示。"""
    visible = [r for r in results if r.icon != "⏭️"]
    if not visible:
        return "✅ 所有检测通过！"

    lines = ["莲心AI 启动体检报告：", ""]
    for r in visible:
        line = f"  {r.icon} {r.name}: {r.message}"
        if r.detail:
            line += f"\n     └─ {r.detail}"
        lines.append(line)

    ok_count = sum(1 for r in visible if r.icon == "✅")
    lines.append("")
    lines.append(f"通过 {ok_count}/{len(visible)} 项")
    return "\n".join(lines)
