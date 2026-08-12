"""
stt_funasr.py — FunASR SenseVoice-Small 语音识别封装
作为全双工语音的主力 STT 引擎（本地 GPU，免费无限次）
"""

import os
import sys
import logging
import re
import tempfile
from typing import Optional, Tuple

# ── 必须在 import funasr 之前设置 ──
os.environ.setdefault("TQDM_DISABLE", "1")

logger = logging.getLogger("lianxin.stt_funasr")

# 抑制 ModelScope Hub 每次启动的下载校验日志
for _name in ("modelscope", "modelscope_hub", "modelscope_hub.download",
              "funasr"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# 全局单例，首次调用时懒加载
_model = None
_load_attempted = False


def _load_model():
    """懒加载 SenseVoice-Small 模型（GPU 推理）。"""
    global _model, _load_attempted
    if _load_attempted:
        return _model

    # Do not construct FunASR concurrently with the embedding model.  Native
    # Torch initialisation is not safe to race on the affected Windows builds.
    from utils.torch_model_loading import torch_model_load_lock
    with torch_model_load_lock:
        if _load_attempted:
            return _model
        _load_attempted = True
        return _load_model_locked()


def _load_model_locked():
    """Load the singleton while ``torch_model_load_lock`` is held."""
    global _model

    # Torch must be initialized by the Qt main thread before FunASR creates
    # native model objects on this worker thread.
    from utils.torch_runtime import ensure_ready
    ensure_ready()

    # 抑制 funasr import 时的 print() 和 modelscope 的 warnings
    import warnings as _w
    _w.simplefilter("ignore")
    _saved_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")

    try:
        from funasr import AutoModel
    finally:
        sys.stdout.close()
        sys.stdout = _saved_stdout

    dev = "cpu"
    gpu_lease = False
    try:
        from config import resolve_device, get_device_preference
        dev = resolve_device("funasr")
        if dev == "cuda:0":
            from utils.model_resource_manager import get_model_resource_manager
            manager = get_model_resource_manager()
            admission = manager.acquire(
                "funasr", minimum_free_mb=1024, fallback="cpu"
            )
            if admission.preempt:
                # FunASR is interactive input and takes priority over the
                # optional GPT-SoVITS voice worker.  The latter falls back to
                # Edge-TTS until its next safe admission.
                if "gpt_sovits" in admission.preempt:
                    from brain.tts_engine import release_gpt_sovits_worker
                    release_gpt_sovits_worker()
                admission = manager.acquire(
                    "funasr", minimum_free_mb=1024, fallback="cpu"
                )
            if admission.granted:
                gpu_lease = True
            else:
                if get_device_preference("funasr") == "auto":
                    logger.warning("FunASR GPU admission denied (%s); falling back to CPU", admission.reason)
                    dev = "cpu"
                else:
                    raise RuntimeError(f"FunASR GPU admission denied: {admission.reason}")

        # 抑制 transformers 加载远程代码时的 No module named 'model' 警告（无害）
        import warnings
        warnings.filterwarnings("ignore", message=".*Loading remote code failed.*")

        logger.info(f"🔊 正在加载 FunASR SenseVoice-Small 模型 ({dev})…")
        _model = AutoModel(
            model="iic/SenseVoiceSmall",
            device=dev,
            disable_pbar=True,
            disable_update=True,
            trust_remote_code=True,
        )
        logger.info(f"✅ FunASR 模型加载完成 ({dev})")
        # 模型已驻留显存，推理不再需要 lease。立即释放，否则 FunASR 会永久
        # 独占 GPU admission，导致 GPT-SoVITS 每次申请都被拒、永远回退 Edge-TTS。
        if gpu_lease:
            from utils.model_resource_manager import get_model_resource_manager
            get_model_resource_manager().release("funasr")
    except ImportError:
        logger.warning("⚠️ FunASR 未安装，跳过 (pip install funasr)")
    except Exception as e:
        if dev == "cuda:0" and gpu_lease:
            from utils.model_resource_manager import get_model_resource_manager
            get_model_resource_manager().release("funasr")
        # auto 模式下加载失败，尝试 CPU 回退
        if get_device_preference("funasr") == "auto":
            try:
                logger.warning(f"⚠️ {dev} 加载失败，回退 CPU…")
                _model = AutoModel(
                    model="iic/SenseVoiceSmall",
                    device="cpu",
                    disable_pbar=True,
                    disable_update=True,
                    trust_remote_code=True,
                )
                logger.info("✅ FunASR 模型加载完成 (CPU 回退)")
            except Exception as e2:
                logger.warning(f"⚠️ FunASR CPU 回退也失败: {e2}")
        else:
            logger.warning(f"⚠️ FunASR 模型加载失败 ({dev}): {e}")

    return _model


def transcribe(wav_bytes: bytes, language: str = "zh") -> str:
    """使用 FunASR SenseVoice-Small 将 WAV 字节转录为文字。

    Args:
        wav_bytes: 16kHz 16bit mono WAV 音频字节
        language: 语言代码，默认 "zh"

    Returns:
        识别文本，失败返回空字符串
    """
    import time as _time
    _t0 = _time.time()
    model = _load_model()
    _t1 = _time.time()
    if model is None:
        return ""

    # 写入临时文件（FunASR 需要文件路径）
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(wav_bytes)
        tmp.close()
        _t2 = _time.time()

        result = model.generate(
            input=tmp.name,
            language=language,
            use_itn=True,            # 逆文本正则化（数字/日期等）
            ban_emo_unk=True,        # 过滤未知情绪标签
        )
        _t3 = _time.time()
        logger.info(f"⏱ FunASR 耗时: 加载={_t1-_t0:.1f}s, 写入={_t2-_t1:.2f}s, 推理={_t3-_t2:.1f}s, 总计={_t3-_t0:.1f}s")
        if result and len(result) > 0:
            text = result[0].get("text", "").strip()
            # 去掉所有 SenseVoice 标签: <|HAPPY|>, <|NEUTRAL|>, <|Speech|>, <|withitn|> 等
            import re as _re
            text = _re.sub(r'<\|[^|>]+\|>', '', text).strip()
            # 过滤：仅剩标点/单字=静音/噪音
            if not text or len(text) <= 1:
                return ""
            if _re.fullmatch(r'[\s。，、；：？！…\.\,\;\:\?\!]+', text):
                return ""
            return text

    except Exception as e:
        logger.warning(f"FunASR 转录失败: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return ""


def transcribe_with_lang(wav_bytes: bytes, language: str = "auto") -> Tuple[str, str]:
    """转录 WAV 字节并返回 (text, lang_code)。

    与 transcribe() 的区别：language 默认 "auto"（自动检测语言），
    返回 (纯净文本, SenseVoice 语言码如 zh/en/ja/ko/yue)。
    失败时返回 ("", "")。
    """
    model = _load_model()
    if model is None:
        return "", ""

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp.write(wav_bytes)
        tmp.close()
        result = model.generate(
            input=tmp.name,
            language=language,
            use_itn=True,
            ban_emo_unk=True,
        )
        if result and len(result) > 0:
            raw = result[0].get("text", "").strip()
            lang_code = ""
            m = re.match(r"<\|([^|>]+)\|>", raw)
            if m:
                lang_code = m.group(1)
                raw = re.sub(r"<\|[^|>]+\|>", "", raw).strip()
            # 仅剩标点/单字 = 静音或噪音
            if not raw or len(raw) <= 1:
                return "", lang_code
            return raw, lang_code
    except Exception as e:
        logger.warning(f"FunASR 转录失败: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    return "", ""


def is_available() -> bool:
    """检测 FunASR 是否可用（模型已加载或可加载）。"""
    return _load_model() is not None


def warmup():
    """预热模型：非 Windows 默认允许，Windows 仅显式 opt-in。

    某些 Windows Torch/Transformers 组合会在启动阶段与 embedding 模型
    并发构造时发生进程级 heap corruption，Python 的 try/except 无法恢复。
    Windows 改为首次实际转录时按需加载；设置
    ``LIANXIN_ENABLE_BACKGROUND_MODEL_WARMUP=1`` 才恢复旧预热行为。
    """
    if os.name == "nt" and os.environ.get("LIANXIN_ENABLE_BACKGROUND_MODEL_WARMUP") != "1":
        logger.info("Windows 已跳过 FunASR 后台预热，将在首次语音识别时加载")
        return
    import threading
    def _load():
        try:
            logger.info("🔥 后台预热 FunASR 模型…")
            m = _load_model()
            if m:
                logger.info("🔥 FunASR 预热完成")
            else:
                logger.warning("⚠️ FunASR 预热返回 NULL，语音识别可能不可用")
        except Exception as e:
            logger.warning(f"⚠️ FunASR 预热失败: {e}")
    t = threading.Thread(target=_load, daemon=True)
    t.start()


def check_model_status() -> dict:
    """
    检查 FunASR 模型状态（供 UI 选项卡调用）
    
    Returns:
        dict: {
            "loaded": bool,      # 模型是否已加载
            "device": str,       # 当前设备 (cuda:0/cpu)
            "reason": str        # 未加载的原因（如果 loaded=False）
        }
    """
    global _model
    
    result = {
        "loaded": False,
        "device": "",
        "reason": ""
    }
    
    # 检查是否已加载
    if _model is not None:
        result["loaded"] = True
        
        try:
            from config import resolve_device
            result["device"] = resolve_device("funasr")
        except Exception:
            result["device"] = "unknown"
        
        return result
    
    # 检查依赖是否安装
    try:
        import funasr
    except ImportError:
        result["reason"] = "FunASR 库未安装 (pip install funasr)"
        return result
    
    # 检查模型文件是否存在（缓存目录）
    try:
        from pathlib import Path
        import os
        
        # 常见的模型缓存路径
        cache_paths = [
            Path.home() / ".cache" / "modelscope" / "hub" / "iic" / "SenseVoiceSmall",
            Path(os.environ.get("MODELSCOPE_CACHE", "")) / "hub" / "iic" / "SenseVoiceSmall",
            Path(tempfile.gettempdir()) / "modelscope" / "hub" / "iic" / "SenseVoiceSmall",
        ]
        
        for cache_path in cache_paths:
            if cache_path.exists():
                result["reason"] = f"模型文件存在于 {cache_path}，但尚未加载到内存"
                return result
        
        result["reason"] = "模型文件未下载（首次使用时会自动下载约200MB）"
        
    except Exception as e:
        result["reason"] = f"检查失败: {e}"
    
    return result


def download_model():
    """
    下载/更新 FunASR SenseVoice-Small 模型（生成器，返回进度百分比）
    
    Yields:
        int: 下载进度 (0-100)
    
    Raises:
        Exception: 下载失败时抛出异常
    """
    yield 0
    
    try:
        from funasr import AutoModel
        from config import resolve_device
        dev = resolve_device("funasr")
        
        yield 10
        
        # 强制重新加载模型（会触发下载）
        global _model, _load_attempted
        _model = None
        _load_attempted = False
        
        yield 30
        
        model = AutoModel(
            model="iic/SenseVoiceSmall",
            device=dev,
            disable_pbar=True,
            disable_update=False,  # 允许更新检查
            trust_remote_code=True,
        )
        
        yield 90
        
        _model = model
        _load_attempted = True
        
        yield 100
        
    except ImportError:
        raise Exception("FunASR 库未安装，请运行: pip install funasr")
    except Exception as e:
        raise Exception(f"模型下载/加载失败: {e}")
