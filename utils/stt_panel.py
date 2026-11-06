import asyncio
import copy
import io
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import wave

import config
from utils.config_panel_store import _load, _write, _lock, merge, public, sections
from utils.panel_task import AUDIO_PANEL_LOCK, PanelTask, PanelActionError


TASK = PanelTask()


def state():
    full = _load()
    result = public("stt", full)
    cfg = full.get("stt_engine", {})
    result["migration"] = not cfg.get("_migrated", False) and any(full.get(name) for name in ("volcano_stt", "aliyun_stt"))
    result["modelPath"] = sections("stt", full)["funasr"].get("model_path", "")
    result["task"] = TASK.state()
    return result


def _apply(full):
    engines = full["stt_engine"]["engines"]
    full["stt_engine"]["_migrated"] = True
    full.setdefault("device_preferences", {})["funasr"] = engines["funasr"]["device"]
    full["device_preferences"]["whisper"] = engines["whisper"]["device"]
    old = full.setdefault("volcano_stt", {})
    old.update(appid=engines["volcano"]["appid"], access_key=engines["volcano"]["access_token"])
    _write(full)
    config.STT_VOLCANO_APPID = old["appid"]
    config.STT_VOLCANO_ACCESS_KEY = old["access_key"]


def _audio(source):
    if not isinstance(source, str) or len(source) > 4096:
        raise ValueError("测试音频路径无效")
    if source:
        path = Path(source)
        if path.suffix.lower() != ".wav" or not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("请选择不超过20MB的WAV文件")
        data = path.read_bytes()
    else:
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0" * 32000)
        data = output.getvalue()
    with wave.open(io.BytesIO(data), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000) or audio.getnframes() > 16000 * 120:
            raise ValueError("测试音频需16kHz、16bit单声道WAV，最长120秒")
    return data


def _install(package):
    commands = {"aliyun": [sys.executable, "setup.py", "install"], "whisper": [sys.executable, "-m", "pip", "install", "openai-whisper"]}
    cwd = Path(__file__).resolve().parents[1] / "alibabacloud-nls-python-sdk" if package == "aliyun" else None
    if cwd is not None and not (cwd / "setup.py").is_file():
        raise PanelActionError("本地阿里云SDK目录不存在，请检查仓库内alibabacloud-nls-python-sdk")
    process = subprocess.run(commands[package], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.returncode:
        raise PanelActionError("依赖安装失败，请检查当前Python环境的网络与写入权限")
    return "依赖安装命令成功；请重新测试连接或识别，不等于模型已经可用"


def _execute(action, values, payload, progress):
    if action in ("diagnose", "check", "download") and importlib.util.find_spec("funasr") is not None:
        from utils.torch_runtime import ensure_ready
        ensure_ready()
    if action in ("diagnose", "check"):
        from brain.stt_funasr import check_model_status
        status = check_model_status()
        message = f"FunASR：{'模型已加载 · ' + status.get('device', '') if status.get('loaded') else status.get('reason', '尚未就绪')}"
        if action == "diagnose":
            from utils.gpu_resources import get_gpu_memory
            gpu = get_gpu_memory()
            message += f"\nGPU：剩余 {gpu['free_mb']} / {gpu['total_mb']} MiB（整卡）" if gpu else "\nGPU遥测不可用，可使用CPU；未尝试加载GPU模型"
            for name, keys in (("volcano", ("appid", "access_token")), ("aliyun", ("access_key_id", "access_key_secret", "app_key"))):
                message += f"\n{name}：{'凭据已填写（未验证连接）' if all(values[name].get(key) for key in keys) else '凭据未完整配置'}"
        return message
    if action == "download":
        from brain.stt_funasr import download_model
        for percent in download_model():
            progress("FunASR 下载/加载中；使用已保存的设备偏好", percent)
        return "FunASR下载/加载完成；请用真实语音文件验证识别"
    if action == "install":
        return _install(payload["engine"])
    engine = payload["engine"]
    cfg = values[engine]
    if engine == "aliyun":
        if not all(cfg.get(key) for key in ("access_key_id", "access_key_secret", "app_key")):
            raise PanelActionError("阿里云凭据不完整，请填写Key ID、Secret和AppKey")
        from nls.token import getToken
        if not getToken(cfg["access_key_id"], cfg["access_key_secret"]):
            raise PanelActionError("阿里云Token获取失败，请核对凭据与平台权限")
        return "阿里云鉴权Token获取成功；未验证AppKey项目权限或完整语音识别"
    audio = _audio(payload.get("audioPath", ""))
    started = time.monotonic()
    if engine == "funasr":
        from brain.stt_funasr import transcribe
        text = transcribe(audio)
    elif engine == "volcano":
        if not cfg.get("appid") or not cfg.get("access_token"):
            raise PanelActionError("火山AppID/Token未配置")
        from brain.stt_volcano import _try_v2
        legacy = config.get_volcano_stt_config()
        text = asyncio.run(asyncio.wait_for(_try_v2(audio, {**legacy, "appid": cfg["appid"], "access_key": cfg["access_token"]}), timeout=45))
    else:
        from utils.torch_runtime import ensure_ready
        ensure_ready()
        from brain.stt_whisper import transcribe
        cfg = copy.deepcopy(cfg)
        if cfg["device"] == "auto":
            import torch
            cfg["device"] = "cuda" if torch.cuda.is_available() else "cpu"
        text = transcribe(audio, cfg)
    elapsed = time.monotonic() - started
    return f"识别返回：{str(text)[:500]}（{elapsed:.2f}秒）" if text and str(text).strip() else f"未返回识别文本（{elapsed:.2f}秒）；静音/凭据/模型失败均可能导致空结果，不能据此宣称连接或识别成功"


def request(action, payload, bridge):
    with _lock:
        if action == "state":
            return state()
        if action == "job":
            return {"task": TASK.state()}
        if action == "save":
            if not AUDIO_PANEL_LOCK.acquire(blocking=False):
                raise ValueError("语音测试正在进行，请稍后保存")
            try:
                updated = merge("stt", _load(), payload)
                _apply(updated)
                return state()
            finally:
                AUDIO_PANEL_LOCK.release()
        if action == "migrate":
            if payload.get("confirmed") is not True:
                raise ValueError("迁移需要确认")
            full = merge("stt", _load(), payload)
            if full.get("stt_engine", {}).get("_migrated"):
                raise ValueError("已处理过迁移，请重新加载")
            migrated = []
            for name, old_name, mapping in (("volcano", "volcano_stt", {"appid": "appid", "access_token": "access_key"}), ("aliyun", "aliyun_stt", {key: key for key in ("access_key_id", "access_key_secret", "app_key")})):
                target = full["stt_engine"]["engines"][name]
                old = full.get(old_name, {})
                if not any(target.get(key) for key in mapping):
                    target.update({key: old.get(source, "") for key, source in mapping.items()})
                    target["enabled"] = all(target.get(key) for key in mapping)
                    if target["enabled"]:
                        migrated.append(name)
            if migrated and "engine_priority" not in full["stt_engine"]:
                full["stt_engine"]["engine_priority"] = migrated + [name for name in config._STT_ENGINE_DEFAULTS["engine_priority"] if name not in migrated]
            if not AUDIO_PANEL_LOCK.acquire(blocking=False):
                raise ValueError("语音测试正在执行，请稍后迁移")
            try:
                _apply(full)
                return state()
            finally:
                AUDIO_PANEL_LOCK.release()
        if action not in ("diagnose", "check", "download", "test", "install"):
            raise LookupError("未知语音转录动作")
        if payload.get("confirmed") is not True:
            raise ValueError("测试、检查、下载及安装需要明确确认")
        engine = payload.get("engine", "funasr")
        if engine not in ("funasr", "volcano", "aliyun", "whisper") or action == "install" and engine not in ("aliyun", "whisper"):
            raise ValueError("引擎无效")
        values = sections("stt", merge("stt", _load(), payload))
        def execute(progress):
            if not bridge._chat_lock.acquire(blocking=False):
                raise PanelActionError("聊天正在执行，请稍后测试")
            try:
                if bridge.voice_state().get("active") or bridge.proactive_status().get("running"):
                    raise PanelActionError("语音聊天或主动行为执行中，请先结束")
                return _execute(action, values, {**payload, "engine": engine}, progress)
            finally:
                bridge._chat_lock.release()
        TASK.start("语音转录调试正在执行…", execute)
        return state()
