"""Lazy, serialized Torch runtime initialization for Windows.

Torch and Transformers are native-heavy.  The application registers a Qt
main-thread callback at startup, while model workers call ``ensure_ready``
when they actually need Torch.  This keeps the idle chat process light and
preserves the existing Windows requirement that native Torch initialization
happens on the main thread.

加固（针对「静默闪退」日志排查结论）：
- import torch 前做内存预检：可用内存过低时直接失败，避免原生 DLL 加载 OOM；
- 初始化卡顿超过阈值时用 faulthandler 转储全线程堆栈到 fault.log，留下线索；
- 超时/失败后置 _failed，避免后续调用反复挂起主线程。
"""

from __future__ import annotations

import threading
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class TorchInitRequest:
    """Completion state passed from a model worker to the UI thread."""

    done: threading.Event = field(default_factory=threading.Event)
    success: bool = False
    error: Optional[BaseException] = None


_state_lock = threading.Lock()
_ready = False
_failed = False  # 初始化失败后不再反复尝试
_inflight: Optional[TorchInitRequest] = None
_main_thread_id: Optional[int] = None
_main_thread_initializer: Optional[Callable[[TorchInitRequest], None]] = None
_logger = logging.getLogger("TorchRuntime")

# 卡顿转储阈值（秒）：主线程初始化超过该时长即转储全线程堆栈
_STALL_DUMP_SECONDS = 8.0
# 内存预检阈值（MB）：可用内存低于该值时不冒险加载 torch
_MIN_AVAILABLE_MB = 512.0


def register_main_thread_initializer(
    initializer: Callable[[TorchInitRequest], None],
) -> None:
    """Register the Qt-owned callback used for deferred initialization."""
    global _main_thread_id, _main_thread_initializer
    _main_thread_id = threading.get_ident()
    _main_thread_initializer = initializer


def _check_memory_headroom() -> None:
    """内存预检：可用内存过低时抛 MemoryError，避免原生加载阶段 OOM。"""
    try:
        from utils.memory_guard import get_memory_status

        status = get_memory_status()
        if not status:
            return
        available = status.get("available_mb")
        if available is not None and available < _MIN_AVAILABLE_MB:
            raise MemoryError(
                f"可用内存不足（{available:.0f}MB < {_MIN_AVAILABLE_MB:.0f}MB），"
                "跳过 Torch 初始化以避免原生 OOM"
            )
    except MemoryError:
        raise
    except Exception:
        pass  # 内存检测自身失败不阻塞初始化


def _spawn_stall_dumper(request: TorchInitRequest) -> None:
    """卡顿转储：主线程初始化超时后，把全线程堆栈写入 fault.log。"""

    def _watch() -> None:
        if request.done.wait(_STALL_DUMP_SECONDS):
            return
        _logger.warning("Torch 初始化卡顿超过 %.0fs，转储线程堆栈", _STALL_DUMP_SECONDS)
        try:
            from utils.memory_guard import dump_all_threads

            dump_all_threads("Torch 初始化卡顿（主线程 import torch 超时）")
        except Exception:
            pass

    threading.Thread(target=_watch, name="torch-stall-dumper", daemon=True).start()


def _initialize_now(request: TorchInitRequest) -> None:
    _logger.info("Torch 初始化开始 thread=%s", threading.current_thread().name)
    _spawn_stall_dumper(request)
    try:
        _check_memory_headroom()
        import torch

        # Importing distributed here preserves the compatibility setup used
        # by sentence-transformers without constructing a model.
        try:
            import torch.distributed  # noqa: F401
        except Exception:
            pass
        request.success = True
        _logger.info("Torch 初始化完成 thread=%s", threading.current_thread().name)
    except BaseException as exc:  # native import failures must reach waiters
        request.error = exc
        with _state_lock:
            global _failed
            _failed = True
        _logger.exception("Torch 初始化失败 thread=%s", threading.current_thread().name)
    finally:
        request.done.set()


def ensure_ready(timeout: float = 90.0) -> bool:
    """Ensure Torch is initialized once, on the registered UI thread."""
    global _ready, _inflight, _failed

    with _state_lock:
        if _ready:
            return True
        if _failed:
            raise RuntimeError("Torch 初始化此前已失败，本地模型相关功能已禁用")
        if _inflight is None:
            request = TorchInitRequest()
            _inflight = request
            owner = True
        else:
            request = _inflight
            owner = False

    if owner:
        if _main_thread_initializer and threading.get_ident() != _main_thread_id:
            _logger.debug(
                "Torch 请求切换到主线程 initializer caller=%s",
                threading.current_thread().name,
            )
            _main_thread_initializer(request)
            completed = request.done.wait(timeout)
        else:
            _initialize_now(request)
            completed = True
        if not completed:
            request.error = TimeoutError("Torch initialization timed out")
            with _state_lock:
                _failed = True  # 超时同样视为失败，避免反复挂起

        with _state_lock:
            if request.success:
                _ready = True
            _inflight = None
    else:
        completed = request.done.wait(timeout)
        if not completed:
            raise TimeoutError("Torch initialization timed out")

    if request.error is not None:
        raise request.error
    return request.success


def is_ready() -> bool:
    """Return whether Torch has been initialized without importing it."""
    with _state_lock:
        return _ready


def is_failed() -> bool:
    """Return whether Torch initialization has previously failed."""
    with _state_lock:
        return _failed