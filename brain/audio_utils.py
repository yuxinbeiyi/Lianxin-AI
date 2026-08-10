"""
QQ 桥接音频工具：SILK ↔ WAV 转换、Whisper STT、Edge-TTS。

所有模块懒加载，首次使用时才导入对应依赖。
"""

import io
import os
import logging
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lianxin.audio_utils")


# ── 常量 ──────────────────────────────────────────────────

SILK_SAMPLE_RATE = 24000  # QQ 语音固定采样率
SILK_BITRATE = 20000      # QQ 语音码率
SILK_CHANNELS = 1         # 单声道
SILK_SAMPLE_WIDTH = 2     # 16-bit


def _resolve_ffmpeg() -> Path:
    """Resolve FFmpeg from configuration, the active environment, or PATH."""
    candidates = []
    try:
        from config import get_tts_config
        configured = str(get_tts_config().get("ffmpeg_path", "") or "").strip()
        if configured:
            candidates.append(Path(configured))
    except Exception:
        pass

    env_root = Path(sys.prefix)
    candidates.extend((
        env_root / "Library" / "bin" / "ffmpeg.exe",
        env_root / "Scripts" / "ffmpeg.exe",
        env_root / "bin" / "ffmpeg",
    ))
    discovered = shutil.which("ffmpeg")
    if discovered:
        candidates.append(Path(discovered))

    ffmpeg = next((path for path in candidates if path.is_file()), None)
    if ffmpeg is None:
        raise RuntimeError(
            "未找到 FFmpeg：QQ 语音回复需要它将 Edge-TTS 音频转换为 SILK。"
            "请将 ffmpeg 安装到当前 Python 环境，或在 TTS 配置中设置 ffmpeg_path。"
        )
    return ffmpeg


def _configure_pydub_ffmpeg() -> str:
    """Bind pydub to FFmpeg from the active Python environment.

    Windows GUI launches do not always inherit the activated conda PATH. QQ
    voice replies need FFmpeg to turn Edge-TTS MP3 output into WAV, so resolve
    the binary explicitly instead of relying on the process PATH alone.
    """
    ffmpeg = _resolve_ffmpeg()
    ffmpeg_dir = str(ffmpeg.parent)
    path_value = os.environ.get("PATH", "")
    if ffmpeg_dir not in path_value.split(os.pathsep):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + path_value

    from pydub import AudioSegment

    AudioSegment.converter = str(ffmpeg)
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    if ffprobe.is_file():
        AudioSegment.ffprobe = str(ffprobe)
    return str(ffmpeg)


# ══════════════════════════════════════════════════════════
# SILK ↔ PCM / WAV
# ══════════════════════════════════════════════════════════

def silk_to_wav(silk_path: str, wav_path: str, sample_rate: int = SILK_SAMPLE_RATE):
    """SILK 文件 → WAV 文件。失败时抛出异常。"""
    import pysilk
    with open(silk_path, "rb") as f_in:
        with io.BytesIO() as buf:
            pysilk.decode(f_in, buf, sample_rate)
            pcm_data = buf.getvalue()
    if not pcm_data:
        raise ValueError("pysilk.decode 返回空数据")
    _pcm_to_wav(pcm_data, wav_path, sample_rate)


def wav_to_silk(wav_path: str, silk_path: str,
                sample_rate: int = SILK_SAMPLE_RATE,
                bitrate: int = SILK_BITRATE):
    """WAV 文件 → SILK 文件。自动重采样到 24000Hz（QQ 兼容）。末尾补 100ms 静音防截断。"""
    import pysilk
    from pydub import AudioSegment

    _configure_pydub_ffmpeg()

    # 读取 WAV，检查采样率，如果不是 24000Hz 则重采样
    audio = AudioSegment.from_wav(wav_path)
    if audio.frame_rate != sample_rate:
        logger.info(f"重采样: {audio.frame_rate}Hz → {sample_rate}Hz")
        audio = audio.set_frame_rate(sample_rate)

    # 导出为 24000Hz 16bit mono PCM
    pcm_data = audio.raw_data  # 已经是 16bit mono after from_wav
    # 补 100ms 静音，避免 pysilk 最后半帧被吞
    pad_samples = sample_rate // 10
    pcm_data += b'\x00' * (pad_samples * 2)  # 16bit = 2 bytes per sample
    with io.BytesIO(pcm_data) as f_in:
        with open(silk_path, "wb") as f_out:
            pysilk.encode(f_in, f_out, sample_rate, bitrate)


def _pcm_to_wav(pcm_bytes: bytes, wav_path: str,
                sample_rate: int = SILK_SAMPLE_RATE,
                channels: int = SILK_CHANNELS,
                sample_width: int = SILK_SAMPLE_WIDTH):
    """裸 PCM → WAV 文件。"""
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def _wav_to_pcm(wav_path: str) -> bytes:
    """WAV 文件 → 裸 PCM 字节。"""
    with wave.open(wav_path, "rb") as wf:
        return wf.readframes(wf.getnframes())


# ══════════════════════════════════════════════════════════
# Whisper STT（语音 → 文字）
# ══════════════════════════════════════════════════════════

_whisper_model = None
_whisper_device = "cpu"


def _add_nvidia_dll_dirs_to_path():
    """将 site-packages/nvidia/*/{bin,lib} 目录加入 PATH，使 CUDA DLL 可被加载。"""
    import os, site
    dirs = set()
    for site_dir in site.getsitepackages():
        nv = os.path.join(site_dir, "nvidia")
        if not os.path.isdir(nv):
            continue
        for entry in os.listdir(nv):
            for sub in ("bin", "lib"):
                dll_dir = os.path.join(nv, entry, sub)
                if os.path.isdir(dll_dir):
                    dirs.add(dll_dir)
    if dirs:
        existing = os.environ.get("PATH", "")
        for d in dirs:
            if d not in existing:
                existing = d + os.pathsep + existing
        os.environ["PATH"] = existing


def _ensure_cublas():
    """确保 cuBLAS DLL 可加载，必要时自动 pip 安装。"""
    import ctypes

    # 先尝试把已安装的 nvidia 包 DLL 目录加入 PATH
    _add_nvidia_dll_dirs_to_path()
    try:
        ctypes.CDLL("cublas64_12.dll")
        return True
    except OSError:
        pass

    # DLL 缺失 → 通过 pip 安装 nvidia-cublas-cu12
    import subprocess, sys
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install",
             "nvidia-cublas-cu12", "--quiet", "--no-warn-script-location"],
            timeout=120,
        )
        import importlib
        importlib.invalidate_caches()
        # 安装后再把 DLL 目录加入 PATH
        _add_nvidia_dll_dirs_to_path()
        try:
            ctypes.CDLL("cublas64_12.dll")
            return True
        except OSError:
            return False
    except Exception:
        return False


def _get_whisper_model(model_size: str = "medium"):
    """懒加载 faster-whisper 模型。优先加载本地或 HuggingFace 缓存目录。"""
    global _whisper_model, _whisper_device
    if _whisper_model is not None:
        return _whisper_model

    import os as _os
    _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    # 确保 CUDA 运行时库可用
    _ensure_cublas()

    from faster_whisper import WhisperModel

    # 查找已下载的模型路径
    model_path = model_size  # 默认让 faster-whisper 自己找
    base_dir = _os.path.dirname(__file__)

    # 1) 项目本地 models/whisper-{size}/
    local_dir = _os.path.abspath(_os.path.join(base_dir, "..", "models", f"whisper-{model_size}"))
    if _os.path.isdir(local_dir) and _os.path.isfile(_os.path.join(local_dir, "model.bin")):
        model_path = local_dir
    else:
        # 2) HuggingFace 系统缓存目录
        hf_cache = _os.path.join(_os.path.expanduser("~"), ".cache", "huggingface", "hub")
        hf_model_dir = _os.path.join(hf_cache, f"models--Systran--faster-whisper-{model_size}", "snapshots")
        if _os.path.isdir(hf_model_dir):
            for snap in _os.listdir(hf_model_dir):
                snap_dir = _os.path.join(hf_model_dir, snap)
                if _os.path.isfile(_os.path.join(snap_dir, "model.bin")):
                    model_path = snap_dir
                    break

    # 根据用户偏好选择设备
    from config import resolve_device
    dev = resolve_device("whisper")
    ct = "float16" if dev == "cuda:0" else "int8"
    try:
        _whisper_model = WhisperModel(model_path, device=dev, compute_type=ct)
        _whisper_device = dev
    except Exception:
        _whisper_model = WhisperModel(model_path, device="cpu", compute_type="int8")
        _whisper_device = "cpu"
    return _whisper_model


def transcribe(wav_path: str, language: str = "zh") -> str:
    """WAV 文件 → 文字。返回空字符串表示未识别到内容。"""
    model = _get_whisper_model()
    segments, _ = model.transcribe(wav_path, language=language, beam_size=5)
    return "".join(s.text for s in segments).strip()


# ══════════════════════════════════════════════════════════
# Edge-TTS（文字 → 语音 WAV）
# ══════════════════════════════════════════════════════════

def clean_tts_text(text: str) -> str:
    """清洗 TTS 文本：移除 Markdown、emoji、特殊符号，防止 TTS 乱读。
    增强版：处理表格、代码文件名、驼峰拆分、符号替换，让中英混读更自然。
    """
    import re
    if not text:
        return ""

    # 0. 先删分隔线
    text = re.sub(r'-{3,}|={3,}|~{3,}', '\n', text)

    # 1. Markdown 链接 [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # 2. 图片
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', '', text)
    # 3. 加粗斜体等
    text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^\*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'~~([^~]+)~~', r'\1', text)
    # 4. 行内代码
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 5. 标题标记
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 6. 代码块
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'~~~[\s\S]*?~~~', '', text)

    # 7. 移除所有 emoji（注意：范围不能覆盖 CJK 汉字区域）
    text = re.sub(
        r'[\U0001F300-\U0001F9FF]'       # 杂项表情符号和补充表情符号
        r'|[\U0001FA70-\U0001FAFF]'       # 表情符号扩展 A
        r'|[\U00002702-\U000027B0]'       # 丁贝符
        r'|[\U0001F1E0-\U0001F1FF]'       # 区域标志（国旗）
        r'|[\U0000FE00-\U0000FE0F]'       # 变异选择器
        r'|[❤️⭐✨💡🔥🎶🎵💤💢💦💨💫🌟]',  # 常见单个
        '', text
    )
    text = text.replace('‍', '').replace('﻿', '').replace('​', '')

    # 8. 颜文字
    text = re.sub(r'[\(（\[［][\s\-＝=]*[｀´・ω∀∂⊙◎●○■□△▲▼☆★♪♫♬αβγδεθλμπσφψ]+[\s\-＝=]*[\)）\]］]', '', text)

    # 9. 符号替换
    text = text.replace('——', '，')
    text = text.replace('–', '，')
    text = text.replace('—', '，')
    text = re.sub(r'(?<=[^\d])-(?=[^\d])', ' ', text)
    text = text.replace('_', ' ')
    text = text.replace('~', ' ')
    text = text.replace('|', '，')  # 表格竖线换成逗号，便于断句
    text = re.sub(r'\\+', ' ', text)
    text = text.replace('^', ' ')
    text = text.replace('@', ' at ')
    text = text.replace('&', ' and ')
    text = text.replace('+', ' plus ')
    text = text.replace('=', ' equals ')
    text = text.replace('#', ' ')
    text = text.replace('/', ' ')
    text = re.sub(r'\$\$?', ' ', text)
    text = re.sub(r'%', ' percent ', text)
    # 箭头
    text = re.sub(r'→|➔|➜', '到', text)
    text = re.sub(r'↘|↙', '', text)
    # 范围
    text = re.sub(r'(\d+)\s*~\s*(\d+)', r'\1 到 \2', text)
    text = re.sub(r'(\d+)\s*~\s*(\d+)', r'\1 到 \2', text)
    # 圆角数字圈
    text = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩]', '', text)

    # 10. 移除 URL
    text = re.sub(r'https?://[^\s,，。！？、\)）】]+', '', text)

    # 11. 规范化重复标点
    text = re.sub(r'[。！？；，]{2,}', lambda m: m.group(0)[0], text)

    # 12. 处理驼峰命名和点分隔文件名（events.py → events dot py）
    def split_camel_case(match):
        word = match.group(0)
        if len(word) <= 1:
            return word
        # 驼峰拆分
        import re
        word = re.sub('([a-z0-9])([A-Z])', r'\1 \2', word)
        return word.lower()

    def split_dot(match):
        return match.group(0).replace('.', ' dot ')

    # 匹配文件名样式
    text = re.sub(r'[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+', split_dot, text)
    # 拆分驼峰
    text = re.sub(r'[A-Z][a-zA-Z]+', split_camel_case, text)

    # 13. 折叠多个空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 14. 首尾空白
    return text.strip()



def tts_to_wav(text: str, wav_path: str, voice: str = None,
               mood: str = None, engine: str = "auto"):
    """文字 → TTS 语音文件（WAV 24000Hz 单声道 16bit）。

    Args:
        text: 要合成的文字
        wav_path: 输出 WAV 文件路径
        voice: Edge-TTS 音色名，为 None 时从配置读取
        mood: GPT-SoVITS 情绪（仅 engine="auto" 或 "gpt_sovits" 时生效）
        engine: "auto"=先试 GPT-SoVITS，失败回退 Edge-TTS
                "edge_tts"=强制使用 Edge-TTS（QQ 桥接用，避免后台线程 GPT-SoVITS 音色异常）
    """
    # 先清洗文本，防止 TTS 读符号/emoji
    text = clean_tts_text(text)

    # 从配置读取 TTS 引擎偏好和 Edge-TTS 音色
    try:
        from config import get_tts_config
        tts_cfg = get_tts_config()
        edge_voice = voice or tts_cfg.get("edge_tts_voice", "zh-CN-XiaoxiaoNeural")
    except Exception:
        edge_voice = voice or "zh-CN-XiaoxiaoNeural"

    # 尝试 TtsEngine（仅在 engine!="edge_tts" 时尝试）
    if engine != "edge_tts":
        try:
            from brain.tts_engine import TtsEngine
            eng = TtsEngine()
            if eng.gpt_sovits_available:
                success = eng.synthesize(text, wav_path, mood=mood)
                if success:
                    logger.info("TTS 使用 GPT-SoVITS")
                    return
        except Exception:
            pass

    # Edge-TTS
    logger.info(f"TTS 使用 Edge-TTS (voice={edge_voice})")
    import asyncio
    import edge_tts
    from pydub import AudioSegment

    _configure_pydub_ffmpeg()

    mp3_path = wav_path + ".mp3"
    try:
        asyncio.run(edge_tts.Communicate(text, edge_voice).save(mp3_path))
        audio = AudioSegment.from_mp3(mp3_path)
        audio = audio.set_frame_rate(24000).set_channels(1).set_sample_width(2)
        audio.export(wav_path, format="wav")
    finally:
        for p in (mp3_path,):
            try:
                os.unlink(p)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
# 格式检测
# ══════════════════════════════════════════════════════════

def detect_format(file_path: str) -> str:
    """检测音频文件格式（通过魔数）。返回 silk / amr / wav / unknown。"""
    with open(file_path, "rb") as f:
        header = f.read(16)
    if header.startswith(b"#!SILK_V3"):
        return "silk"
    if header.startswith(b"#!AMR"):
        return "amr"
    if header.startswith(b"RIFF"):
        return "wav"
    return "unknown"


def _amr_to_wav(amr_path: str, wav_path: str, sample_rate: int = 16000):
    """AMR 文件 → WAV 文件（通过 pydub + ffmpeg），默认 16kHz 对齐 Whisper。"""
    from pydub import AudioSegment
    audio = AudioSegment.from_file(amr_path, format="amr")
    audio = audio.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    audio.export(wav_path, format="wav")

# ── 繁→简转换 ───────────────────────────────────────────

_simplifier = None

def _to_simplified(text: str) -> str:
    """繁体中文 → 简体中文。懒加载 opencc。"""
    global _simplifier
    if _simplifier is None:
        try:
            from opencc import OpenCC
            _simplifier = OpenCC("t2s")
        except ImportError:
            # 自动安装
            import subprocess, sys
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install",
                     "opencc-python-reimplemented", "--quiet", "--no-warn-script-location"],
                    timeout=60,
                )
                from opencc import OpenCC
                _simplifier = OpenCC("t2s")
            except Exception:
                return text  # 安装失败则原样返回
    try:
        return _simplifier.convert(text)
    except Exception:
        return text


# ══════════════════════════════════════════════════════════
# 便捷函数：音频 → 文字 / 文字 → SILK
# ══════════════════════════════════════════════════════════

def convert_voice_to_text(audio_path: str, debug_log=None) -> str:
    """音频文件 → 文字。自动检测 SILK / AMR / WAV 格式并转换。"""
    fmt = detect_format(audio_path)
    wav_tmp = audio_path + ".wav"
    last_err = None

    try:
        if fmt == "amr":
            if debug_log:
                debug_log("[音频] 检测到 AMR 格式，用 pydub 转换")
            try:
                _amr_to_wav(audio_path, wav_tmp)
                if debug_log:
                    debug_log("[音频] AMR→WAV 完成")
                text = transcribe(wav_tmp)
                if debug_log:
                    debug_log(f"[音频] Whisper 转录: {text[:100] if text else '(空)'}")
                if text:
                    return _to_simplified(text)
            except Exception as e:
                last_err = e
            raise last_err or RuntimeError("AMR 转录失败")

        # SILK / unknown：原有多采样率回退逻辑
        _SAMPLE_RATES = (24000, 16000, 8000)
        for sr in _SAMPLE_RATES:
            try:
                silk_to_wav(audio_path, wav_tmp, sample_rate=sr)
            except Exception as e:
                last_err = e
                continue
            if debug_log:
                debug_log(f"[音频] SILK→WAV ({sr}Hz) 完成")
            try:
                text = transcribe(wav_tmp)
                if debug_log:
                    debug_log(f"[音频] Whisper 转录: {text[:100] if text else '(空)'}")
                if text:
                    return _to_simplified(text)
            except Exception as e:
                last_err = e
                continue
        raise last_err or RuntimeError("所有采样率尝试均失败")
    finally:
        for p in (wav_tmp,):
            try:
                os.unlink(p)
            except Exception:
                pass


def convert_text_to_voice(text: str, silk_path: str, debug_log=None) -> bool:
    """完整链路：TTS → WAV → SILK。成功返回 True。"""
    wav_tmp = silk_path + ".wav"
    try:
        # QQ replies must use the self-contained Edge-TTS route.  The desktop
        # TtsEngine has its own GPT-SoVITS fallback path and previously bypassed
        # the FFmpeg binding above when it was available.
        tts_to_wav(text, wav_tmp, engine="edge_tts")
        if debug_log:
            debug_log(f"[音频] TTS 完成: {text[:50]}... -> {wav_tmp}")
        wav_to_silk(wav_tmp, silk_path)
        if debug_log:
            debug_log(f"[音频] WAV→SILK 完成: {silk_path}")
        return True
    except Exception as e:
        print(f"[音频] 转换失败: {e}")  # print 兜底，防限频吞掉
        if debug_log:
            debug_log(f"[音频] 转换失败: {e}")
        return False
    finally:
        for p in (wav_tmp,):
            try:
                os.unlink(p)
            except Exception:
                pass
