"""
TtsEngine — 莲心语音合成引擎。

统一 TTS 入口：优先使用 GPT-SoVITS（声音克隆 + 情绪表达），
不可用时自动回退到 Edge-TTS（云端标准发音）。

设计参考：openclaw_girlfriend 项目的 tts_call.py
  - 直接 Python 调用 GPT_SoVITS.inference_webui.get_tts_wav
  - 参考音频（ref_wav）按情绪分类，关键词匹配自动选择
  - 不需要停/启 llama-server（莲心使用 DeepSeek API，无本地 LLM 显存争抢）
"""

import json
import logging
import os
import re
import random
import subprocess
import sys
import tempfile
import threading
import threading
import queue
import time
import atexit
from pathlib import Path
from typing import Optional, Callable
import warnings
warnings.simplefilter("ignore", ResourceWarning)


logger = logging.getLogger("TtsEngine")

# ── 情绪名称映射 ──────────────────────────────────────────
MOOD_LABELS = {
    "casual":    "日常温柔",
    "tsundere":  "傲娇",
    "romantic":  "深情",
    "long":      "长句稳定",
    "angry":     "生气",
}

# ── 情绪关键词（中文为主，兼容日文）──────────────────────
_MOOD_KEYWORDS = {
    "casual": [
        "早", "晚安", "辛苦", "谢谢", "今天", "天气", "吃饭", "日常",
        "开心", "加油", "好的", "嗯", "明白", "知道", "可以", "好吧",
        "おはよう", "お疲れ", "ありがとう", "がんば",
    ],
    "tsundere": [
        "笨蛋", "哼", "随便你", "才不", "别以为", "烦", "走开", "讨厌",
        "傻瓜", "白痴", "蠢", "谁要", "才没有", "无所谓",
        "バカ", "キモ", "うるさい", "変な", "ふん",
    ],
    "romantic": [
        "喜欢", "爱", "想你", "宝贝", "亲爱的", "永远", "幸福",
        "温柔", "抱", "亲", "心", "想念", "陪伴",
        "好き", "大好き", "愛", "君のこと", "あなた",
    ],
    "long": [],  # 长句通过文本长度自动匹配
    "angry": [
        "生气", "愤怒", "气死", "烦死了", "滚", "火大", "恼火", "怒",
        "太过分", "真是的", "受不了", "别说了", "你干嘛", "烦人",
        "可恶", "岂有此理", "搞什么", "别惹我", "火大",
        "むかつく", "イライラ", "腹立つ", "くそ",
    ],
}

# ── 长句阈值 ─────────────────────────────────────────────
_LONG_TEXT_THRESHOLD = 80  # 超过此字符数自动归类为 long


def _slugify(text: str, max_len: int = 30) -> str:
    """从文本提取安全的文件名片段（保留中文/英文/数字）。"""
    cleaned = re.sub(r'[^\w一-鿿 ]', ' ', text, flags=re.ASCII)
    cleaned = re.sub(r'\s+', '_', cleaned).strip('_')
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip('_')
    return cleaned or 'untitled'


def _detect_mood(text: str, mood_hint: Optional[str] = None) -> Optional[str]:
    """根据文本内容自动匹配情绪。mood_hint='auto' 或 None 时自动匹配。"""
    if mood_hint and mood_hint != "auto" and mood_hint in MOOD_LABELS:
        return mood_hint

    # 长句优先
    if len(text) > _LONG_TEXT_THRESHOLD:
        return "long"

    # 关键词匹配
    text_lower = text.lower()
    scores = {}
    for mood, keywords in _MOOD_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if mood == "long":
            continue  # long 已单独处理
        scores[mood] = score

    max_score = max(scores.values()) if scores else 0
    if max_score > 0:
        candidates = [m for m, s in scores.items() if s == max_score]
        return random.choice(candidates)

    return "casual"  # 默认日常


def _normalize_audio(audio, target_peak: int = 28000):
    """音量归一化：将所有句子的峰值统一拉到 target_peak，消除句间音量波动。"""
    import numpy as np
    original_max = np.max(np.abs(audio))
    if original_max > 0:
        gain = target_peak / original_max
        # 只做衰减/小幅提升，避免噪音也被过度放大
        gain = min(gain, 3.0)
        audio_float = audio.astype(np.float32) * gain
        audio_float = np.clip(audio_float, -32768, 32767).astype(np.int16)
        audio = audio_float
    return audio



# ══════════════════════════════════════════════════════════
# GPT-SoVITS 懒加载 + 持久 worker（共享进程）
# ══════════════════════════════════════════════════════════

_gpt_sovits_state = None  # None=未尝试, True=可用, False=不可用

# 持久 worker 进程（模块级共享，跨 TtsEngine 实例复用）
_worker_process: Optional[subprocess.Popen] = None
_stderr_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()  # 保护 worker stdin/stdout 不被多线程并发读写（桌面+QQ同时用GPT-SoVITS时）
_worker_idle_timer: Optional[threading.Timer] = None
_worker_last_used = 0.0
_worker_version: Optional[str] = None  # 当前 worker 加载的 GPT-SoVITS 模型版本


def _get_runtime_python(gs_path: str) -> Optional[str]:
    """获取 GPT-SoVITS 自带 Python 运行环境路径。"""
    runtime_dir = os.path.join(gs_path, "runtime")
    # Windows
    py = os.path.join(runtime_dir, "python.exe")
    if os.path.isfile(py):
        return os.path.abspath(py)
    # Linux/Mac
    py = os.path.join(runtime_dir, "python")
    if os.path.isfile(py):
        return os.path.abspath(py)
    return None
def reset_gpt_sovits_cache():
    """重置 GPT-SoVITS 可用性缓存（配置路径变更后调用）。"""
    global _gpt_sovits_state
    _gpt_sovits_state = None

def _is_gpt_sovits_available() -> bool:
    """检查 GPT-SoVITS 子进程模式是否可用（懒加载，结果缓存）。

    检测条件：
      1. gpt_sovits_path 已配置且目录存在
      2. runtime/python.exe（即 GPT-SoVITS 自带的 Python 环境）存在
      3. 有参考音频可用
    """
    global _gpt_sovits_state
    if _gpt_sovits_state is not None:
        return _gpt_sovits_state

    from config import get_tts_config
    cfg = get_tts_config()
    gs_path = cfg.get("gpt_sovits_path", "").strip()

    if not gs_path or not os.path.isdir(gs_path):
        logger.info("GPT-SoVITS 路径未配置或不存在")
        _gpt_sovits_state = False
        return False

    # 检查 GPT-SoVITS 自带运行环境（子进程模式，不在 conda 中 import torch）
    runtime_python = _get_runtime_python(gs_path)
    if not runtime_python:
        logger.info(f"GPT-SoVITS runtime 目录不存在: {gs_path}/runtime/")
        _gpt_sovits_state = False
        return False

    # 检查是否有参考音频
    ref_dir = _get_ref_wavs_dir()
    if ref_dir:
        refs = _load_ref_config(ref_dir)
        if not refs:
            logger.warning("GPT-SoVITS 路径已配置但无参考音频")
            _gpt_sovits_state = False
            return False

    _gpt_sovits_state = True
    logger.info(f"GPT-SoVITS 就绪（子进程模式）: {runtime_python}")
    return True


# ── 持久 worker 进程管理（模块级共享） ──────────────────────


def _drain_stderr(proc: subprocess.Popen):
    """读取并记录 worker 的 stderr 日志（后台线程）。"""
    try:
        for line in proc.stderr:
            logger.debug(f"[GPT-SoVITS] {line.rstrip()}")
    except Exception:
        pass


def _ensure_worker() -> subprocess.Popen:
    """确保持久 worker 进程在运行。模型只在此处加载一次。"""
    global _worker_process, _stderr_thread, _worker_version

    from config import get_tts_config
    cfg = get_tts_config()

    # 模型版本（v2Pro/v3/v4）——变更时需重启 worker 重新加载对应模型
    version = (cfg.get("gpt_sovits_version") or "v2Pro").strip() or "v2Pro"
    if (_worker_process is not None and _worker_process.poll() is None
            and _worker_version != version):
        logger.info(f"GPT-SoVITS 模型版本变更 {_worker_version} → {version}，重启 worker")
        _close_worker()

    if _worker_process is not None and _worker_process.poll() is None:
        return _worker_process

    gs_path = cfg.get("gpt_sovits_path", "").strip()
    runtime_python = _get_runtime_python(gs_path)

    if not runtime_python:
        raise RuntimeError("GPT-SoVITS runtime 不可用，请检查配置路径")

    worker_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "tts_sovits_worker.py"
    )
    if not os.path.isfile(worker_script):
        raise RuntimeError(f"Worker 脚本不存在: {worker_script}")

    env = os.environ.copy()
    env["GPT_SOVITS_PATH"] = gs_path
    env["GPT_SOVITS_VERSION"] = version
    env["GPT_SOVITS_GPT_PATH"] = str(cfg.get("gpt_sovits_gpt_path", "") or "").strip()
    env["GPT_SOVITS_SOVITS_PATH"] = str(cfg.get("gpt_sovits_sovits_path", "") or "").strip()

    minimum_vram = int(cfg.get("gpt_sovits_min_free_vram_mb", 2048) or 0)
    from utils.model_resource_manager import get_model_resource_manager
    resource_manager = get_model_resource_manager()
    admission = resource_manager.acquire(
        "gpt_sovits", minimum_free_mb=minimum_vram, fallback="edge"
    )
    if not admission.granted:
        raise RuntimeError(
            f"GPT-SoVITS GPU admission denied: {admission.reason}; falling back to Edge-TTS"
        )

    try:
        logger.info(f"启动 GPT-SoVITS 持久 worker（版本 {version}，模型加载中，首次约 5-15 秒）")
        _worker_process = subprocess.Popen(
            [runtime_python, worker_script, "--persistent"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=gs_path,
        )
        _worker_version = version
    except Exception:
        resource_manager.release("gpt_sovits")
        raise
    # 后台线程 drain stderr，防止管道缓冲区满阻塞 worker
    _stderr_thread = threading.Thread(
        target=_drain_stderr, args=(_worker_process,), daemon=True
    )
    _stderr_thread.start()
    logger.info(f"GPT-SoVITS worker 已启动（PID: {_worker_process.pid}）")
    return _worker_process


def _close_worker():
    """关闭持久 worker 进程。"""
    global _worker_process, _worker_idle_timer, _worker_last_used, _worker_version
    if _worker_idle_timer is not None:
        _worker_idle_timer.cancel()
        _worker_idle_timer = None
    if _worker_process is not None:
        try:
            _worker_process.terminate()
            _worker_process.wait(timeout=5)
        except Exception:
            try:
                _worker_process.kill()
            except Exception:
                pass
        _worker_process = None
        _worker_last_used = 0.0
        _worker_version = None
        logger.info("GPT-SoVITS worker 已关闭")
    from utils.model_resource_manager import get_model_resource_manager
    get_model_resource_manager().release("gpt_sovits")


def _schedule_worker_idle_shutdown() -> None:
    """Schedule worker release without interrupting an active synthesis."""
    global _worker_idle_timer
    from config import get_tts_config
    timeout = int(get_tts_config().get("gpt_sovits_idle_timeout_seconds", 300) or 0)
    if timeout <= 0:
        return
    if _worker_idle_timer is not None:
        _worker_idle_timer.cancel()
    _worker_idle_timer = threading.Timer(timeout, _close_worker_if_idle)
    _worker_idle_timer.daemon = True
    _worker_idle_timer.start()


def _close_worker_if_idle() -> None:
    global _worker_idle_timer
    with _worker_lock:
        _worker_idle_timer = None
        if _worker_process is None:
            return
        from config import get_tts_config
        timeout = int(get_tts_config().get("gpt_sovits_idle_timeout_seconds", 300) or 0)
        if timeout <= 0 or time.monotonic() - _worker_last_used < timeout:
            _schedule_worker_idle_shutdown()
            return
        logger.info("GPT-SoVITS worker idle timeout reached; releasing GPU resources")
        _close_worker()


def _mark_worker_used() -> None:
    global _worker_last_used
    _worker_last_used = time.monotonic()
    _schedule_worker_idle_shutdown()


def release_gpt_sovits_worker() -> None:
    """Explicitly release the independent GPT-SoVITS process and GPU model."""
    with _worker_lock:
        _close_worker()


atexit.register(_close_worker)


# ══════════════════════════════════════════════════════════
# 参考音频管理
# ══════════════════════════════════════════════════════════

def _get_ref_wavs_dir() -> Optional[str]:
    """查找参考音频目录。搜索顺序：1. 配置路径 2. skill 内置路径 3. 用户数据目录。"""
    # 1. 用户配置的路径
    from config import get_tts_config
    cfg = get_tts_config()
    ref_dir = cfg.get("ref_audio_dir", "").strip()
    if ref_dir and os.path.isdir(ref_dir):
        return ref_dir

    # 2. skill 安装目录下的 ref_wavs
    skill_ref = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "skills", "语音合成", "ref_wavs"
    )
    if os.path.isdir(skill_ref):
        return skill_ref

    # 3. 用户数据目录 ~/.lianxin/tts/ref_wavs/
    user_ref = os.path.join(str(Path.home()), ".lianxin", "tts", "ref_wavs")
    if os.path.isdir(user_ref):
        return user_ref

    return None


def _load_ref_config(ref_dir: str) -> dict:
    """加载参考音频映射表（ref_wavs/config.json）。返回 {style: [{path, text, lang}]}。"""
    config_path = os.path.join(ref_dir, "config.json")
    if not os.path.isfile(config_path):
        # 没有 config.json 时，尝试自动扫描目录
        return _scan_ref_wavs(ref_dir)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"读取参考音频配置失败: {e}")
        return _scan_ref_wavs(ref_dir)

    result = {}
    raw = data.get("ref_wavs", data)
    for key, info in raw.items():
        # key 可能是 "casual/xxx.wav" 或 mood 名
        if "/" in key:
            mood = key.split("/", 1)[0]
        else:
            mood = info.get("mood", "casual")
        full_path = os.path.join(ref_dir, key)
        if not os.path.isfile(full_path):
            # 尝试在对应情绪目录下查找
            alt_path = os.path.join(ref_dir, mood, os.path.basename(key))
            if os.path.isfile(alt_path):
                full_path = alt_path
            else:
                continue
        entry = {
            "path": full_path,
            "text": info.get("text", ""),
            "lang": info.get("lang", "中文"),
            "mood": mood,
        }
        result.setdefault(mood, []).append(entry)

    return result if result else _scan_ref_wavs(ref_dir)


def _scan_ref_wavs(ref_dir: str) -> dict:
    """自动扫描目录结构，按情绪分类参考音频。
    支持的目录结构：
      ref_wavs/
        casual/   *.wav
        tsundere/ *.wav
        romantic/ *.wav
        long/     *.wav
        或直接放在 ref_wavs/ 下

    参考文本用 FunASR 自动转录（缓存于 config.json），v3/v4 依赖它；
    v2Pro 的 ref_free 模式会忽略文本。
    """
    from brain.ref_transcriber import get_ref_transcripts
    transcripts = get_ref_transcripts(ref_dir)  # {相对路径: {text, lang}}

    result = {}
    supported = ("casual", "tsundere", "romantic", "long")

    # 按情绪子目录扫描
    for mood in supported:
        mood_dir = os.path.join(ref_dir, mood)
        if os.path.isdir(mood_dir):
            entries = []
            for fname in sorted(os.listdir(mood_dir)):
                if fname.lower().endswith(".wav"):
                    rel = f"{mood}/{fname}"
                    info = transcripts.get(rel, {})
                    entries.append({
                        "path": os.path.join(mood_dir, fname),
                        "text": info.get("text", ""),
                        "lang": info.get("lang", "中文"),
                        "mood": mood,
                    })
            if entries:
                result[mood] = entries

    # 如果子目录为空，扫描根目录
    if not result:
        entries = []
        for fname in sorted(os.listdir(ref_dir)):
            if fname.lower().endswith(".wav") and fname != "config.json":
                info = transcripts.get(fname, {})
                entries.append({
                    "path": os.path.join(ref_dir, fname),
                    "text": info.get("text", ""),
                    "lang": info.get("lang", "中文"),
                    "mood": "casual",
                })
        if entries:
            result["casual"] = entries

    return result


# ── 内存缓存：ref 配置只在引擎初始化时加载一次 ──────────
_ref_cache = None


def _get_refs() -> dict:
    """获取参考音频配置（带缓存）。"""
    global _ref_cache
    if _ref_cache is not None:
        return _ref_cache

    ref_dir = _get_ref_wavs_dir()
    if ref_dir:
        _ref_cache = _load_ref_config(ref_dir)
    else:
        _ref_cache = {}
    return _ref_cache

# ── 参考音频会话缓存：同一 mood 首次随机选，后续复用 ──
_pick_ref_cache: dict = {}


def reset_pick_ref_cache():
    """重置参考音频缓存（用户手动切音色后调用）。"""
    _pick_ref_cache.clear()

def _pick_ref(text: str, mood_hint: Optional[str] = None) -> Optional[dict]:
    """根据文本和情绪提示选择参考音频。
    返回 {"path": str, "text": str, "lang": str} 或 None。
    同一 mood 首次随机选，后续复用同一 WAV，保证音色一致。
    """
    global _pick_ref_cache

    # ── 用户手动指定了参考音频 → 直接使用，跳过情绪匹配 ──
    from config import get_tts_config
    cfg = get_tts_config()
    override = cfg.get("ref_wav_override", "").strip()
    if override and os.path.isfile(override):
        from brain.ref_transcriber import get_transcript
        text, lang = get_transcript(override)
        return {"path": override, "text": text, "lang": lang}

    refs = _get_refs()
    if not refs:
        return None

    # 确定目标情绪
    target_mood = _detect_mood(text, mood_hint) or "casual"

    # ── 缓存命中：同一 mood 复用上次选的 WAV ──
    if target_mood in _pick_ref_cache:
        return _pick_ref_cache[target_mood]

    # 优先选目标情绪的参考音频
    candidates = refs.get(target_mood, [])
    if candidates:
        chosen = random.choice(candidates)
        _pick_ref_cache[target_mood] = chosen
        return chosen

    # 没有对应情绪的参考音频 → 从所有音频中随机选一个
    all_entries = []
    for entries in refs.values():
        all_entries.extend(entries)
    if all_entries:
        chosen = random.choice(all_entries)
        _pick_ref_cache[target_mood] = chosen
        return chosen

    return None



# ══════════════════════════════════════════════════════════
# Edge-TTS 回退
# ══════════════════════════════════════════════════════════

def _fallback_edge_tts(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bool:
    """Edge-TTS 语音合成（原 generate_audio.py 实现）。返回 bool。"""
    import asyncio
    import edge_tts
    import random
    from brain.audio_utils import _configure_pydub_ffmpeg

    mp3_path = output_path + ".mp3"
    max_retries = 3
    last_error = None
    try:
        _configure_pydub_ffmpeg()
        from pydub import AudioSegment
        for attempt in range(max_retries + 1):
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        edge_tts.Communicate(text, voice).save(mp3_path)
                    )
                finally:
                    loop.close()
                if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) < 256:
                    raise RuntimeError("Edge-TTS returned an empty audio file")
                audio = AudioSegment.from_mp3(mp3_path)
                audio = audio.set_frame_rate(24000).set_channels(1).set_sample_width(2)
                audio.export(output_path, format="wav")
                return True
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    base_delay = 1.5 * (2 ** attempt)
                    jitter = random.uniform(0, 0.5)
                    delay = base_delay + jitter
                    logger.warning(
                        "Edge-TTS 暂时失败（第 %s/%s 次）：%s；%.1fs 后重试",
                        attempt + 1, max_retries, exc, delay,
                    )
                    time.sleep(delay)
        logger.error(f"Edge-TTS 合成失败: {last_error}")
        return False
    finally:
        for p in (mp3_path,):
            try:
                os.unlink(p)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
# TtsEngine
# ══════════════════════════════════════════════════════════

class TtsEngine:
    """莲心语音合成引擎，统一 TTS 入口。

    使用方式：
        engine = TtsEngine()
        success = engine.synthesize("你好", "output.wav", mood="casual")

    引擎选择逻辑：
        1. GPT-SoVITS（如果已安装且 GPU 可用）→ 声音克隆 + 情绪表达
        2. Edge-TTS（标准云端 TTS）→ 自动回退
    """

    def __init__(self):
        self._config = None
        self._gpt_available = None  # 每次调用前检测

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def engine_name(self) -> str:
        """返回当前可用的引擎名称：'gpt_sovits' / 'edge_tts' / 'unavailable'。"""
        if _is_gpt_sovits_available():
            return "gpt_sovits"
        if self._get_edge_tts_available():
            return "edge_tts"
        return "unavailable"

    @property
    def gpt_sovits_available(self) -> bool:
        """GPT-SoVITS 是否可用（懒加载）。"""
        return _is_gpt_sovits_available()

    @property
    def ref_style_count(self) -> int:
        """可用的参考音频风格数量。"""
        refs = _get_refs()
        return len(refs)

    @property
    def ref_audio_count(self) -> int:
        """参考音频文件总数。"""
        refs = _get_refs()
        return sum(len(v) for v in refs.values())

    # ── 主合成方法 ────────────────────────────────────────

    def synthesize(self, text: str, output_path: str,
                   mood: Optional[str] = None,
                   speed: Optional[float] = None,
                   sample_rate: int = 24000) -> bool:
        """将文字合成语音并写入 WAV 文件。

        Args:
            text: 要合成的文字
            output_path: 输出的 WAV 文件路径
            mood: 情绪/音色（auto/casual/tsundere/romantic/long/None=自动）
            speed: 语速（0.5-2.0，GPT-SoVITS 专用）
            sample_rate: 采样率（默认 24000Hz，QQ 兼容）

        Returns:
            True 表示成功，False 表示失败
        """
        # 读取引擎配置，决定是否使用 GPT-SoVITS
        from config import get_tts_config
        cfg = get_tts_config()
        voice = cfg.get("edge_tts_voice", "zh-CN-XiaoxiaoNeural")

        # 优先 GPT-SoVITS（除非用户强制 Edge-TTS）
        if cfg.get("engine") != "edge_tts" and _is_gpt_sovits_available():
            try:
                return self._synthesize_gpt_sovits(text, output_path, mood, speed)
            except Exception as e:
                logger.warning(f"GPT-SoVITS 合成失败，回退 Edge-TTS: {e}")

        # 使用 Edge-TTS
        return _fallback_edge_tts(text, output_path, voice)

    def synthesize_gpt_wav(self, text: str, output_path: str,
                           mood: Optional[str] = None,
                           speed: Optional[float] = None) -> bool:
        """仅使用 GPT-SoVITS 合成 WAV，不在此方法内回退其他引擎。

        桌面端可以直接播放 WAV，因此不应为了播放而转成 MP3。调用方可根据
        False 返回值自行选择 Edge-TTS，同时准确知道实际使用了哪个引擎。
        """
        if not _is_gpt_sovits_available():
            logger.info("GPT-SoVITS 不可用，跳过 WAV 合成")
            return False
        try:
            return self._synthesize_gpt_sovits(text, output_path, mood, speed)
        except Exception as e:
            logger.warning(f"GPT-SoVITS WAV 合成失败: {e}")
            return False



    def synthesize_to_mp3(self, text: str, output_path: str,
                          mood: Optional[str] = None,
                          speed: Optional[float] = None) -> bool:
        """合成语音并输出 MP3 文件（桌面端 VoiceSpeaker 使用）。"""
        # 先合成 WAV，再转 MP3
        wav_tmp = output_path + ".wav"
        try:
            success = self.synthesize(text, wav_tmp, mood=mood, speed=speed)
            if not success:
                return False
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(wav_tmp)
            audio.export(output_path, format="mp3")
            return True
        except Exception as e:
            logger.error(f"MP3 合成失败: {e}")
            return False
        finally:
            try:
                os.unlink(wav_tmp)
            except Exception:
                pass

    def warmup(self):
        """预热 GPT-SoVITS 引擎：提前启动 worker，加载模型到 GPU。
        在后台线程调用，不阻塞 UI。仅当配置了预热且 GPT-SoVITS 可用时生效。"""
        if not _is_gpt_sovits_available():
            logger.info("warmup：GPT-SoVITS 不可用，跳过预热")
            return
        try:
            logger.info("warmup：预热 GPT-SoVITS worker…")
            with _worker_lock:
                _ensure_worker()
                _mark_worker_used()
            logger.info("warmup：GPT-SoVITS worker 已就绪")
        except Exception as e:
            logger.warning(f"warmup：预热失败 {e}")

    @staticmethod
    def list_ref_styles() -> list:
        """列出所有可用语音风格。返回 [{mood, label, file_count, description}]。"""
        refs = _get_refs()
        descriptions = {
            "casual": "日常温柔语气，适合普通对话",
            "tsundere": "傲娇强势语气，带点小脾气",
            "romantic": "深情温柔语气，适合表达感情",
            "long": "长句稳定发音，适合较长的文本",
            "angry": "生气愤怒语气，适合表达不满或烦躁",
        }
        result = []
        for mood, entries in refs.items():
            result.append({
                "mood": mood,
                "label": MOOD_LABELS.get(mood, mood),
                "file_count": len(entries),
                "description": descriptions.get(mood, ""),
            })
        if not result:
            result.append({
                "mood": "edge_tts",
                "label": "标准语音",
                "file_count": 0,
                "description": "Edge-TTS 云端标准发音（当前无参考音频时的默认模式）",
            })
        return result

    @staticmethod
    def detect_mood(text: str, mood_hint: Optional[str] = None) -> str:
        """自动检测文本适合的情绪。"""
        return _detect_mood(text, mood_hint) or "casual"

    # ── 内部方法 ──────────────────────────────────────────

    def _synthesize_gpt_sovits(self, text: str, output_path: str,
                                mood: Optional[str] = None,
                                speed: Optional[float] = None) -> bool:
        """使用 GPT-SoVITS 合成语音（持久 worker 进程，模型只加载一次）。"""
        # 选择参考音频（在 conda 侧完成）
        ref = _pick_ref(text, mood)
        if not ref:
            raise RuntimeError("无参考音频，无法使用 GPT-SoVITS")

        detected_mood = _detect_mood(text, mood) or "casual"

        # 确保 worker 在运行（首次启动会加载模型，后续直接复用）
        worker = _ensure_worker()

        from config import get_tts_config
        cfg = get_tts_config()
        request = json.dumps({
            "text": text,
            "ref_wav": ref["path"],
            "ref_text": ref.get("text", ""),
            "ref_lang": ref.get("lang", "中文"),
            "output_path": output_path,
            "mood": detected_mood,
            "sample_steps": cfg.get("sample_steps", 32),
            "speed": speed or cfg.get("speed", 1.0),
            "temperature": cfg.get("temperature", 0.3),
            "top_k": cfg.get("top_k", 5),
            "top_p": cfg.get("top_p", 0.9),
            "how_to_cut": cfg.get("how_to_cut", "不切"),
            "pause_second": cfg.get("pause_second", 0.3),
        })


        logger.info(
            f"GPT-SoVITS 合成: mood={detected_mood}, "
            f"ref={os.path.basename(ref['path'])}, text_len={len(text)}"
        )

        # 发送请求到 worker（加锁防止多线程并发读写同一个子进程 stdin/stdout）
        with _worker_lock:
            try:
                worker.stdin.write(request + "\n")
                worker.stdin.flush()
            except BrokenPipeError:
                # worker 挂了，重启一次
                _close_worker()
                worker = _ensure_worker()
                worker.stdin.write(request + "\n")
                worker.stdin.flush()

            # 读取响应（带超时，使用线程避免 Windows select 限制）
            result_queue: "queue.Queue[str | None]" = queue.Queue()

            def _reader():
                try:
                    result_queue.put(worker.stdout.readline())
                except Exception:
                    result_queue.put(None)

            reader = threading.Thread(target=_reader, daemon=True)
            reader.start()
            reader.join(timeout=300)  # 首运行含模型加载，最多等 5 分钟

            if reader.is_alive():
                _close_worker()
                raise RuntimeError("GPT-SoVITS 合成超时（300s）")

            response_line = result_queue.get_nowait()
            _mark_worker_used()

        # 解析 JSON 响应（锁外解析，不阻塞其他线程）
        try:
            data = json.loads(response_line.strip())
        except json.JSONDecodeError:
            _close_worker()
            raise RuntimeError(f"解析 worker 响应失败: {response_line}")

        if data.get("success"):
            return True
        else:
            raise RuntimeError(data.get("error", "未知错误"))
        try:
            data = json.loads(response_line.strip())
        except json.JSONDecodeError:
            self._close_worker()
            raise RuntimeError(f"解析 worker 响应失败: {response_line}")

        if data.get("success"):
            return True
        else:
            raise RuntimeError(data.get("error", "未知错误"))

    def _get_edge_tts_available(self) -> bool:
        """检测 Edge-TTS 是否可用。"""
        try:
            import edge_tts  # noqa
            return True
        except ImportError:
            return False
