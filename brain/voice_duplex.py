
"""
全双工语音模块 — VAD + STT 前端
将语音转为文字后交给主窗口 AgentWorker 统一处理（LLM + TTS + 气泡）

核心理念:
  全双工: 一直监听麦克风，用户随时开口 → 思考中立刻打断

组件:
  WebRTCVADWorker    — 后台线程，持续监听麦克风，WebRTC VAD 实时检测语音/静音
  VoiceDuplexManager — VAD → STT → 交给主窗口（不做 LLM，不做 TTS）

状态流转:
  STOPPED ──start()──→ LISTENING ──VAD检测声音──→ (思考中打断)
      ↑                    ↑                          │
      │                    │              用户说完 (VAD静音 2s)
      │                    │                          ↓
      │                    │                   PROCESSING
      │                    │                   转录语音 → 交主窗口
      │                    │                          │
      │                    └────────── 主窗口处理 ────→ 气泡 + LLM + TTS
      │                                                │
      └──────────── stop() ────────────────────────────┘
"""

import os
import time
import queue
import threading
import logging
from typing import Optional, Callable

from brain.vad_webrtc import WebRTCVADWorker

logger = logging.getLogger("VoiceDuplex")

# ── 状态常量 ──────────────────────────────────────────
STATE_STOPPED    = "STOPPED"
STATE_LISTENING  = "LISTENING"
STATE_PROCESSING = "PROCESSING"


# 状态中文标签
STATE_LABELS = {
    STATE_STOPPED:    "待机",
    STATE_LISTENING:  "聆听中",
    STATE_PROCESSING: "思考中",
}

def _safe_call(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# VoiceDuplexManager
# ═══════════════════════════════════════════════════════

class VoiceDuplexManager:
    """全双工语音管理器 — 只做 VAD + STT，转录后交给主窗口。

    用法:
        manager = VoiceDuplexManager(
            on_transcript=lambda t: print(f"你说: {t}"),
            on_state_change=lambda s: print(STATE_LABELS[s]),
        )
        manager.start()
        ...
        manager.stop()
    """

    def __init__(self,
                 on_state_change: Optional[Callable] = None,
                 on_transcript: Optional[Callable] = None,
                 on_voice_start_ui: Optional[Callable] = None,
                 on_interrupt_tts: Optional[Callable] = None,
                 on_stt_ready: Optional[Callable] = None,
                 input_device_index: Optional[int] = None):
        self._on_state_change = on_state_change
        self._on_transcript   = on_transcript
        self._input_device_index = input_device_index
        self._on_voice_start_ui = on_voice_start_ui
        self._on_interrupt_tts  = on_interrupt_tts
        self._on_stt_ready = on_stt_ready
        self._state = STATE_STOPPED
        self._vad_worker: Optional[WebRTCVADWorker] = None

        self._audio_queue   = queue.Queue()
        self._lock = threading.Lock()
        self._vad_paused = False
        self._vad_cooldown_until = 0.0
        self._headphone_mode = False  # 耳机模式：允许TTS期间语音打断

    # ── 状态 ──────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, new: str):
        if new != self._state:
            old = self._state
            self._state = new
            logger.info(f"状态: {STATE_LABELS.get(old, old)} → {STATE_LABELS.get(new, new)}")
            if self._on_state_change:
                _safe_call(self._on_state_change, new)

    # ── 中断 / 耳机模式 ────────────────────────────────

    def interrupt(self):
        """打断当前操作。"""
        if self._state == STATE_STOPPED:
            return
        logger.info("🛑 中断！")
        self._set_state(STATE_LISTENING)

    def set_headphone_mode(self, enabled: bool):
        """设置耳机模式。耳机/耳麦场景下允许TTS期间语音打断（无回声风险）。"""
        self._headphone_mode = enabled
        logger.info(f"🎧 耳机模式: {'开启 (可打断TTS)' if enabled else '关闭 (扬声器安全模式)'}")

    def auto_detect_headphone(self) -> bool:
        """自动检测是否使用耳机。返回 True 表示检测到耳机。"""
        try:
            import sounddevice as sd
            # 检查默认输出设备名称
            device = sd.query_devices(kind='output')
            name = (device.get('name', '') or '').lower()
            keywords = ['headphone', 'headset', 'earphone', 'earbud',
                       '耳机', '耳麦', '头戴', '蓝牙']
            detected = any(kw in name for kw in keywords)
            if detected:
                logger.info(f"🎧 自动检测到耳机: {device.get('name', '')}")
                self._headphone_mode = True
            return detected
        except Exception:
            return False

    # ── TTS 协同（由主窗口在 TTS 播放前后调用）────────────

    # ── TTS 协同（由主窗口在 TTS 播放前后调用）────────────
    def pause_vad(self):
        """暂停 VAD 处理：TTS 播放期间丢弃所有麦克风音频，防止回声循环。"""
        with self._lock:
            self._vad_paused = True
            # 清除队列中可能已有的 TTS 回声
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break

    def resume_vad(self, cooldown: float = 2.0):
        """恢复 VAD 处理：TTS 结束后延迟 cooldown 秒才接受音频（防止延迟的 TTS 回声帧）。"""
        with self._lock:
            self._vad_paused = False
            self._vad_cooldown_until = time.time() + cooldown

    # ── VAD 回调 ──────────────────────────────────────
    def _on_voice_start(self):
        if self._on_voice_start_ui:
            _safe_call(self._on_voice_start_ui)

        # 耳机模式：TTS 期间用户开口 → 立刻打断（无回声风险）
        if self._vad_paused and self._headphone_mode:
            logger.info("🎧 耳机模式：TTS 中检测到用户语音 → 打断！")
            with self._lock:
                self._vad_paused = False
                self._vad_cooldown_until = 0.0
            if self._on_interrupt_tts:
                _safe_call(self._on_interrupt_tts)
            return

        # 思考中打断
        if self._state == STATE_PROCESSING:
            self.interrupt()

    def _on_voice_end(self, wav_bytes: bytes):
        with self._lock:
            if self._vad_paused:
                return  # 扬声器模式：丢弃 TTS 回声
            if time.time() < self._vad_cooldown_until:
                return  # TTS 刚结束 → 冷却期内丢弃延迟的回声尾帧
        self._audio_queue.put(wav_bytes)

    # ── 启动/停止 ─────────────────────────────────────

    def start(self) -> bool:
        """启动全双工语音。VAD 不可用时保持 STOPPED 并返回 False。"""
        if self._vad_worker is not None:
            return True

        worker = WebRTCVADWorker(
            input_device_index=self._input_device_index,
            on_voice_start=self._on_voice_start,
            on_voice_end=self._on_voice_end,
        )
        # 同步预检，避免后台线程立即退出但 UI 仍显示“聆听中”。
        if not worker._load_vad():
            logger.error("❌ 全双工语音启动失败：WebRTC VAD 不可用")
            self._set_state(STATE_STOPPED)
            return False

        self._vad_worker = worker
        worker.start()
        self._set_state(STATE_LISTENING)

        # 在后台提前加载 STT，避免用户第一次说话时才触发模型加载。
        # 语音采集和 UI 不被阻塞；VoiceDuplexManager 内部的模型锁负责
        # 与实际转录线程的安全衔接。
        threading.Thread(target=self._preload_stt, daemon=True).start()

        # STT 处理线程（只转录，不做 LLM/TTS）
        threading.Thread(target=self._process_loop, daemon=True).start()
        logger.info("✅ 全双工语音已启动")
        return True

    def _preload_stt(self):
        ready = False
        try:
            from brain.stt_funasr import is_available
            ready = bool(is_available())
            logger.info("✅ FunASR 预加载完成" if ready else "⚠️ FunASR 预加载失败")
        except Exception as exc:
            logger.warning(f"⚠️ FunASR 预加载异常: {exc}")
        if self._on_stt_ready:
            _safe_call(self._on_stt_ready, ready)

    def stop(self):
        self._set_state(STATE_STOPPED)

        if self._vad_worker:
            self._vad_worker.stop()
            self._vad_worker = None

        logger.info("🛑 全双工语音已停止")

    # ── 处理循环（STT → 交给主窗口）────────────────────

    def _process_loop(self):
        while self._state != STATE_STOPPED:
            try:
                wav_bytes = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if self._state == STATE_STOPPED:
                break

            self._set_state(STATE_PROCESSING)
            transcript = self._transcribe(wav_bytes)

            # 过滤无效转录：空文本、纯标签、过短
            t = (transcript or "").strip()
            if not t or len(t) <= 1:
                self._set_state(STATE_LISTENING)
                continue
            if t.startswith("<|") or t in ("。", "，", "？", "！"):
                self._set_state(STATE_LISTENING)
                continue

            logger.info(f"📝 转录: {transcript}")
            if self._on_transcript:
                _safe_call(self._on_transcript, transcript)
            self._set_state(STATE_LISTENING)

    def _transcribe(self, wav_bytes: bytes) -> str:
        """语音转文字：FunASR 本地主力 → 火山引擎云端备份。"""
        import time as _time

        # 调试录音（保存到用户数据目录，而非 Desktop）
        from utils.paths import get_user_data_dir
        debug_dir = get_user_data_dir() / "voice_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = str(debug_dir / f"lianxin_debug_{int(_time.time())}.wav")
        try:
            with open(debug_path, "wb") as f:
                f.write(wav_bytes)
            # 保留最近 10 个录音文件
            try:
                files = sorted(debug_dir.glob("lianxin_debug_*.wav"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[10:]:
                    old.unlink()
            except Exception:
                pass
        except Exception:
            pass

        # ── 动态 STT 引擎调度（根据配置自动选择和降级）───────
        from config import get_stt_engine_config, detect_best_stt_engine
        
        cfg = get_stt_engine_config()
        
        # 确定默认引擎（支持 auto 模式）
        default_engine = cfg.get("default_engine", "auto")
        if default_engine == "auto":
            default_engine = detect_best_stt_engine()
        
        # 获取引擎优先级列表
        priority = cfg.get("engine_priority", ["funasr", "volcano", "aliyun", "whisper"])
        engine_configs = cfg.get("engines", {})
        auto_fallback = cfg.get("auto_fallback", True)
        
        # 确保默认引擎在优先级最前面
        if default_engine in priority:
            priority.remove(default_engine)
            priority.insert(0, default_engine)
        
        for engine_name in priority:
            engine_cfg = engine_configs.get(engine_name, {})
            
            # 跳过未启用的引擎
            if not engine_cfg.get("enabled", False):
                if engine_name == "funasr":
                    pass  # FunASR 默认启用，即使配置中未显式设置
                else:
                    continue
            
            try:
                result = self._call_stt_engine(engine_name, wav_bytes, engine_cfg)
                if result and result.strip():
                    emoji = {"funasr": "🎯", "volcano": "☁️", "aliyun": "🔒", "whisper": "🔧"}
                    icon = emoji.get(engine_name, "🎤")
                    logger.info(f"{icon} {engine_name}: {result}")
                    return result
            except Exception as e:
                logger.warning(f"⚠️ {engine_name} 失败: {e}")
                
                # 如果不启用自动降级，直接停止
                if not auto_fallback:
                    break
        
        logger.debug("所有 STT 引擎均未返回结果")
        return ""
    
    def _call_stt_engine(self, name: str, wav_bytes: bytes, cfg: dict) -> str:
        """调用指定的 STT 引擎"""
        if name == "funasr":
            from brain.stt_funasr import transcribe
            return transcribe(wav_bytes)
        
        elif name == "volcano":
            from brain.stt_volcano import transcribe
            return transcribe(wav_bytes)
        
        elif name == "aliyun":
            from brain.stt_aliyun import transcribe
            return transcribe(wav_bytes, cfg)
        
        elif name == "whisper":
            from brain.stt_whisper import transcribe
            return transcribe(wav_bytes, cfg)
        
        raise ValueError(f"未知引擎: {name}")
