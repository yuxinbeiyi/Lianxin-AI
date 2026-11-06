from pathlib import Path

from utils.config_panel_store import _load, _write, _lock, merge, public, revision, ADVANCED
from utils.panel_task import AUDIO_PANEL_LOCK, PanelTask, PanelActionError


TASK = PanelTask()


def _output():
    from utils.settings import SettingsManager
    settings = SettingsManager()
    return {"silent_mode": settings.silent_mode, "tts_volume": settings.tts_volume, "sfx_volume": settings.sfx_volume}


def state():
    result = public("sound", _load())
    result.update(output=_output(), task=TASK.state(), advancedDefaults=ADVANCED)
    return result


def references():
    from brain.tts_engine import _get_ref_wavs_dir
    roots = [Path(__file__).resolve().parents[1] / "skills" / "语音合成" / "ref_wavs", Path.home() / ".lianxin" / "tts" / "ref_wavs"]
    configured = _get_ref_wavs_dir()
    if configured:
        roots.insert(0, Path(configured))
    items = {}
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob("*.wav")):
                items[str(path)] = {"path": str(path), "label": f"[{path.parent.name}] {path.name}"}
    return {"items": list(items.values())}


def runtime():
    from brain import tts_engine
    from utils.gpu_resources import gpu_snapshot
    worker = getattr(tts_engine, "_worker_process", None)
    running = bool(worker and worker.poll() is None)
    return {"running": running, "pid": worker.pid if running else None, "gpu": gpu_snapshot(worker.pid if running else None)}


def _save_output(values):
    from utils.settings import SettingsManager, get_settings
    settings = SettingsManager()
    for field, value in values.items():
        setattr(settings, field, value)
    get_settings()._load()


def _job(action, payload, bridge, progress):
    if not bridge._chat_lock.acquire(blocking=False):
        raise PanelActionError("当前聊天或工具正在执行，请稍后操作声音引擎")
    try:
        if bridge.proactive_status().get("running") or bridge.tts_state().get("speaking") or bridge.voice_state().get("active"):
            raise PanelActionError("主动行为、朗读或语音聊天正在执行，请结束后再操作")
        from brain import tts_engine
        if action == "release":
            tts_engine.release_gpt_sovits_worker()
            return "已释放GPT-SoVITS独立worker；下次使用会按需加载，未释放其它模型"
        if action == "transcribe":
            from brain.ref_transcriber import get_ref_transcripts
            from utils.torch_runtime import ensure_ready
            directory = tts_engine._get_ref_wavs_dir()
            if not directory:
                raise PanelActionError("未找到参考音频目录，请先放置参考WAV文件")
            ensure_ready()
            cache = get_ref_transcripts(directory, force=True)
            tts_engine.reset_gpt_sovits_cache()
            done = sum(bool((item.get("text") or "").strip()) for item in cache.values())
            return f"参考音频转录完成：{done}/{len(cache)}条有文本；未识别条目不视为成功"
        with _lock:
            full = _load()
            updated = merge("sound", full, payload)
            for field in ("tts_warmup",):
                if field in full.get("tts", {}):
                    updated["tts"][field] = full["tts"][field]
                else:
                    updated["tts"].pop(field, None)
            _write(updated)
            TASK.committed(revision("sound", updated))
            tts_engine.reset_gpt_sovits_cache()
        progress("试听配置已保存，正在合成；取消关窗不会撤销这次落盘")
        prepared = bridge.prepare_speech("你好，我是莲心。很高兴见到你。")
        if not prepared.get("prepared"):
            raise PanelActionError("试听合成未完成或已静默；请核对已生效静默设置及TTS模型状态")
        played = bridge.play_prepared_speech(prepared["id"])
        if not played.get("played"):
            raise PanelActionError("试听播放未完成或被取消；试听参数已经保存")
        return "试听播放完成；引擎及高级参数已保存，静默与预热草稿未提交"
    finally:
        bridge._chat_lock.release()


def request(action, payload, bridge):
    with _lock:
        if action == "state":
            return state()
        if action == "job":
            return {"task": TASK.state()}
        if action == "references":
            return references()
        if action == "runtime":
            return runtime()
        if action == "path-check":
            source = payload.get("path")
            if not isinstance(source, str) or len(source) > 4096:
                raise ValueError("安装路径无效")
            if not source.strip():
                return {"message": "未配置GPT-SoVITS路径；请按所选引擎核对配置"}
            path = Path(source.strip())
            message = "安装路径不存在" if not path.is_dir() else "GPT_SoVITS目录已识别；不代表GPU/权重/推理已通过" if (path / "GPT_SoVITS").is_dir() else "目录存在，但未检测到GPT_SoVITS模块"
            return {"message": message}
        if action == "volume":
            field, value = payload.get("field"), payload.get("value")
            if field not in ("tts_volume", "sfx_volume") or type(value) not in (float, int) or not 0 <= value <= 1:
                raise ValueError("音量必须在0–1之间")
            _save_output({field: value})
            return state()
        if action == "save":
            updated = merge("sound", _load(), payload)
            output = payload.get("output", {})
            if not isinstance(output, dict) or set(output) - {"silent_mode"} or type(output.get("silent_mode")) is not bool:
                raise ValueError("静默模式必须为布尔值")
            if not AUDIO_PANEL_LOCK.acquire(blocking=False):
                raise ValueError("语音调试进行中，请稍后保存")
            try:
                from brain.tts_engine import reset_gpt_sovits_cache
                _write(updated)
                _save_output(output)
                reset_gpt_sovits_cache()
                return state()
            finally:
                AUDIO_PANEL_LOCK.release()
        if action not in ("preview", "release", "transcribe"):
            raise LookupError("未知声音动作")
        if payload.get("confirmed") is not True:
            raise ValueError("声音调试需要明确确认")
        if action == "preview":
            merge("sound", _load(), payload)
        TASK.start("声音动作正在执行…", lambda progress: _job(action, payload, bridge, progress))
        return state()
