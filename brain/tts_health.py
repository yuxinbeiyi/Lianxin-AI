"""TTS 健康状态：错误分类 + 会话级熔断。

Edge-TTS 是共享的云端网络服务。永久性错误（403/401/404、握手被拒）重试没有任何
意义，只会让每个分句白等数秒并刷爆日志。这里把错误分成两类，并为永久性失败提供
熔断窗口（默认 300 秒，冷却后半开探测），同时保留一个可读的失败原因，供上层给
用户一次性提示。
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("lianxin.tts_health")

PERMANENT = "permanent"
TRANSIENT = "transient"

DEFAULT_OPEN_SECONDS = 300.0
DEFAULT_PERMANENT_THRESHOLD = 2

# 熔断期间给用户的一次性可见提示（每轮故障只发一次，之后静默降级为文字）。
UNAVAILABLE_NOTICE = "（语音合成暂时不可用，这条先用文字说）"

# 这些标记出现在异常文本里时，说明是服务端拒绝，重试无意义。
_PERMANENT_MARKERS = (
    "403",
    "401",
    "404",
    "invalid response status",
    "wsserverhandshakeerror",
    "handshake",
    "forbidden",
    "unauthorized",
    "trustedclienttoken",
    "sec-ms-gec",
)


def classify_error(exc: object) -> str:
    """把一次 TTS 异常判为 permanent 或 transient。"""
    if exc is None:
        return TRANSIENT
    text = f"{exc.__class__.__name__}: {exc}".lower()
    for marker in _PERMANENT_MARKERS:
        if marker in text:
            return PERMANENT
    return TRANSIENT


def describe_error(exc: object) -> str:
    """把异常翻译成一句可读原因。"""
    text = f"{exc.__class__.__name__}: {exc}"
    lowered = text.lower()
    if "403" in lowered:
        return "微软 Edge 语音服务拒绝了本机请求（HTTP 403）"
    if "401" in lowered:
        return "语音服务鉴权失败（HTTP 401）"
    if "404" in lowered:
        return "语音服务接口不存在（HTTP 404）"
    if "timeout" in lowered or "timed out" in lowered:
        return "语音服务连接超时"
    return text[:120]


class TtsCircuitBreaker:
    """永久性失败连续达阈值后打开，窗口内直接拒绝，冷却后半开探测。"""

    def __init__(self, open_seconds: float = DEFAULT_OPEN_SECONDS,
                 threshold: int = DEFAULT_PERMANENT_THRESHOLD):
        self._lock = threading.RLock()
        self._open_seconds = float(open_seconds)
        self._threshold = int(threshold)
        self._consecutive = 0
        self._open_until = 0.0
        self._reason = ""
        self._generation = 0           # 每开一次熔断 +1
        self._notified_generation = 0  # 已经提示过的轮次

    # ── 查询 ──────────────────────────────────────────────

    def open_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._open_until - time.monotonic())

    @property
    def is_open(self) -> bool:
        return self.open_remaining() > 0.0

    def allow(self) -> bool:
        return self.open_remaining() <= 0.0

    def reason(self) -> str:
        with self._lock:
            return self._reason

    def snapshot(self) -> dict:
        with self._lock:
            remaining = max(0.0, self._open_until - time.monotonic())
            return {
                "open": remaining > 0.0,
                "remaining_seconds": round(remaining, 1),
                "consecutive_permanent": self._consecutive,
                "reason": self._reason,
            }

    def take_notification(self) -> str:
        """每轮故障只返回一次提示文案，取走后返回空串。"""
        with self._lock:
            if self._open_until <= time.monotonic():
                return ""
            if self._generation <= self._notified_generation:
                return ""
            self._notified_generation = self._generation
            return UNAVAILABLE_NOTICE

    # ── 记录 ──────────────────────────────────────────────

    def record_success(self) -> None:
        with self._lock:
            if self._consecutive or self._open_until:
                logger.info("TTS 熔断器已复位（合成成功）")
            self._consecutive = 0
            self._open_until = 0.0
            self._reason = ""

    def record_failure(self, kind: str, exc: object = None) -> None:
        """暂时性错误不影响熔断，只有永久性错误才累积计数。"""
        if kind != PERMANENT:
            return
        with self._lock:
            self._consecutive += 1
            self._reason = describe_error(exc)
            if self._consecutive >= self._threshold and self._open_until <= time.monotonic():
                self._open_until = time.monotonic() + self._open_seconds
                self._generation += 1
                logger.warning(
                    "Edge-TTS 连续 %d 次永久性失败，熔断 %.0f 秒：%s",
                    self._consecutive, self._open_seconds, self._reason,
                )

    def reset(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._open_until = 0.0
            self._reason = ""
            self._generation = 0
            self._notified_generation = 0


_BREAKER = TtsCircuitBreaker()


def get_breaker() -> TtsCircuitBreaker:
    return _BREAKER


def tts_allowed() -> bool:
    return _BREAKER.allow()


def note_success() -> None:
    _BREAKER.record_success()


def note_failure(exc: object = None, kind: str = None) -> str:
    """记录一次失败并返回分类结果。"""
    resolved = kind or classify_error(exc)
    _BREAKER.record_failure(resolved, exc)
    return resolved


def unavailable_reason() -> str:
    """处于熔断时返回可读原因，否则返回空串。"""
    return _BREAKER.reason() if _BREAKER.is_open else ""


def take_notification() -> str:
    """取走本轮故障的一次性可见提示；无提示时返回空串。"""
    return _BREAKER.take_notification()


# ── 运行期阶段提示（供界面显示） ──────────────────────────
# GPT-SoVITS 冷启动要加载模型，可能耗时数十秒。界面必须有可见提示，
# 否则用户只会以为莲心卡死了。/tts/status 每次轮询会读取这里。
_STAGE_LOCK = threading.Lock()
_STAGE = dict(key='', text='')

GPT_SOVITS_LOADING_TEXT = '莲心正在加载GPT-SoVITS'


def set_stage(key: str, text: str) -> None:
    '''设置当前阶段提示（例如 GPT-SoVITS 加载中）。'''
    with _STAGE_LOCK:
        _STAGE['key'] = str(key or '')
        _STAGE['text'] = str(text or '')


def clear_stage(key: str = '') -> None:
    '''清除阶段提示；传入 key 时只清匹配的那个，避免误清其它阶段。'''
    with _STAGE_LOCK:
        if key and _STAGE.get('key') != key:
            return
        _STAGE['key'] = ''
        _STAGE['text'] = ''


def current_stage() -> dict:
    '''返回当前阶段提示副本（无提示时 text 为空串）。'''
    with _STAGE_LOCK:
        return dict(_STAGE)
# ── 引擎失败一行提示（桌面界面 / QQ 各取一次）────────────────
# 取消跨引擎回退后，合成失败不再有任何兜底：界面必须如实显示一行原因，
# 否则用户只会觉得莲心突然哑了。这里按引擎节流，避免逐分句刷屏。
ENGINE_FAILURE_TEXTS = {
    "edge_tts": "莲心的 edge-tts 合成暂时失败了",
    "gpt_sovits": "莲心的 GPT-SoVITS 合成暂时失败了",
}
DEFAULT_ENGINE_FAILURE_TEXT = "莲心的语音合成暂时失败了"
ENGINE_FAILURE_MIN_INTERVAL = 25.0
# QQ 端需要一句自解释的提示，免得群里的人以为莲心坏了。
ENGINE_FAILURE_QQ_SUFFIX = "，这条先用文字说"

_FAILURE_LOCK = threading.Lock()
_ENGINE_FAILURE = {"id": 0, "engine": "", "reason": "", "text": "", "at": 0.0}


def engine_failure_text(engine: str) -> str:
    """返回某引擎失败时给用户看的那一行文案。"""
    key = str(engine or "").strip().lower()
    return ENGINE_FAILURE_TEXTS.get(key, DEFAULT_ENGINE_FAILURE_TEXT)


def note_engine_failure(engine: str, reason: str = "") -> None:
    """记录一次合成失败；同一引擎在节流窗口内只保留一条待显示提示。"""
    key = str(engine or "").strip().lower() or "unknown"
    now = time.monotonic()
    with _FAILURE_LOCK:
        same_engine = _ENGINE_FAILURE.get("engine") == key and int(_ENGINE_FAILURE.get("id") or 0) > 0
        if same_engine and now - float(_ENGINE_FAILURE.get("at") or 0.0) < ENGINE_FAILURE_MIN_INTERVAL:
            return
        _ENGINE_FAILURE.update({
            "id": int(_ENGINE_FAILURE.get("id") or 0) + 1,
            "engine": key,
            "reason": str(reason or "")[:200],
            "text": engine_failure_text(key),
            "at": now,
        })


def engine_failure_state() -> dict:
    """查看当前待显示的失败提示（非破坏性，按 id 去重由调用方负责）。"""
    with _FAILURE_LOCK:
        return dict(_ENGINE_FAILURE)


def clear_engine_failure() -> None:
    """清空待显示的失败提示（合成恢复正常后可调用）。"""
    with _FAILURE_LOCK:
        _ENGINE_FAILURE.update({"id": 0, "engine": "", "reason": "", "text": "", "at": 0.0})
