"""
ref_transcriber.py — 参考音频转录与缓存。

GPT-SoVITS v3/v4 的 S2 不支持 ref_free，要求参考文本（prompt_text）与
参考音频内容一致。莲心的参考音频是角色声线，默认没有标注文本，此前把
「文件名」当参考文本传给 v3/v4，导致条件错位、合成退化（噪音/静音）。

本模块用 FunASR (SenseVoice-Small，莲心自带的 STT 引擎) 自动转录参考
音频，并把结果缓存到 ref_wavs/config.json 的 ref_wavs 字段；手动填写过
text 的条目不会被覆盖。v2Pro 走 ref_free 模式，不受影响。
"""
import io
import json
import logging
import os
import shutil
from typing import Dict, Tuple

logger = logging.getLogger("lianxin.ref_transcriber")

# SenseVoice 语言码 → GPT-SoVITS prompt_language 标签（dict_language_v2 的 key）
_LANG_LABEL = {
    "zh": "中文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
    "yue": "粤语",
}
_DEFAULT_LABEL = "多语种混合"  # GPT-SoVITS 按 auto 切分识别，作为未知语言的兜底

# 自定义目录参考音频的转录记忆（避免每次合成都重复转录）
_custom_memo: Dict[str, Tuple[str, str]] = {}


def _load_audio_16k(wav_path: str) -> bytes:
    """把参考音频加载为 16kHz 单声道 16bit WAV 字节（FunASR 输入格式）。

    用 torchaudio 读取，兼容非标准 WAV 头（如助手.wav、霓虹歌姬.wav）。
    不使用 librosa：lianxin 环境的 lzma DLL 缺失会导致其导入失败。
    """
    import torch
    import torchaudio
    import torchaudio.functional as F

    wav, sr = torchaudio.load(wav_path)
    if wav.shape[0] > 1:
        wav = wav.mean(0, keepdim=True)
    if sr != 16000:
        wav = F.resample(wav, sr, 16000)
    pcm = (torch.clamp(wav, -1.0, 1.0) * 32767).to(torch.int16)
    buf = io.BytesIO()
    torchaudio.save(buf, pcm, 16000, format="wav", encoding="PCM_S")
    return buf.getvalue()


def transcribe_ref_wav(wav_path: str) -> Tuple[str, str]:
    """转录单个参考音频，返回 (text, lang_label)。失败时 text 为空。"""
    from brain import stt_funasr

    try:
        wav_bytes = _load_audio_16k(wav_path)
    except Exception as e:
        logger.warning(f"参考音频加载失败 {os.path.basename(wav_path)}: {e}")
        return "", _DEFAULT_LABEL

    text, lang_code = stt_funasr.transcribe_with_lang(wav_bytes)
    if not text:
        return "", _DEFAULT_LABEL
    return text, _LANG_LABEL.get(lang_code, _DEFAULT_LABEL)


def _read_cache(config_path: str) -> Dict[str, dict]:
    """读取 config.json 中的 ref_wavs 缓存（{相对路径: {text, lang}}）。"""
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("ref_wavs") or {}
        return {k: v for k, v in raw.items() if isinstance(v, dict)}
    except Exception as e:
        logger.warning(f"读取参考音频缓存失败: {e}")
        return {}


def _write_cache(config_path: str, cache: Dict[str, dict]):
    """写回 config.json 的 ref_wavs 字段，保留原有说明等其它键。"""
    try:
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            shutil.copyfile(config_path, config_path + ".bak")
        else:
            data = {}
    except Exception:
        data = {}
    data["ref_wavs"] = cache
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"写入参考音频缓存失败: {e}")


def get_ref_transcripts(ref_dir: str, force: bool = False) -> Dict[str, dict]:
    """确保 ref_dir 下所有 wav 都有转录缓存，返回 {相对路径: {text, lang}}。

    - 已配置非空 text 的条目保留（含用户手动填写），不重新转录
    - force=True 时强制重新转录全部（转录失败的保留旧条目）
    - 新转录结果写回 config.json
    """
    config_path = os.path.join(ref_dir, "config.json")
    cache = _read_cache(config_path)
    changed = False

    for root, dirs, files in os.walk(ref_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fname in sorted(files):
            if not fname.lower().endswith(".wav"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, ref_dir).replace("\\", "/")
            entry = cache.get(rel) or {}
            if not force and (entry.get("text") or "").strip():
                continue
            text, lang = transcribe_ref_wav(full)
            if text:
                cache[rel] = {"text": text, "lang": lang}
                changed = True
                logger.info(f"参考音频转录: {rel} → {text[:40]} ({lang})")
            else:
                logger.warning(f"参考音频转录失败: {rel}")

    if changed:
        _write_cache(config_path, cache)
    return cache


def get_transcript(wav_path: str) -> Tuple[str, str]:
    """按路径查单个参考音频的转录，优先读缓存，缺失则转录并写回。

    用于 ref_wav_override（用户在界面上手动指定的参考音频）：它可能不在
    ref_wavs 目录内，无法被 get_ref_transcripts 扫到。若文件所在目录有
    config.json 缓存则复用；否则现场转录（内存记忆，避免重复合成都转录）。
    """
    abs_path = os.path.abspath(wav_path)
    if not os.path.isfile(abs_path):
        return "", _DEFAULT_LABEL

    ref_dir = os.path.dirname(abs_path)
    config_path = os.path.join(ref_dir, "config.json")
    if os.path.isfile(config_path):
        cache = _read_cache(config_path)
        rel = os.path.relpath(abs_path, ref_dir).replace("\\", "/")
        entry = cache.get(rel) or {}
        if (entry.get("text") or "").strip():
            return entry["text"], entry.get("lang", _DEFAULT_LABEL)
        text, lang = transcribe_ref_wav(abs_path)
        if text:
            cache[rel] = {"text": text, "lang": lang}
            _write_cache(config_path, cache)
        return text, lang

    # 自定义目录：不往未知目录写 config.json，仅内存记忆
    if abs_path in _custom_memo:
        return _custom_memo[abs_path]
    text, lang = transcribe_ref_wav(abs_path)
    _custom_memo[abs_path] = (text, lang)
    return text, lang
