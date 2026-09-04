"""Lazy, serialized Torch runtime initialization for Windows.

Torch and Transformers are native-heavy.  The application registers a Qt
main-thread callback at startup, while model workers call ``ensure_ready``
when they actually need Torch.  This keeps the idle chat process light and
preserves the existing Windows requirement that native Torch initialization
happens on the main thread.
"""

from __future__ import annotations

import threading
import logging
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
_inflight: Optional[TorchInitRequest] = None
_main_thread_id: Optional[int] = None
_main_thread_initializer: Optional[Callable[[TorchInitRequest], None]] = None
_logger = logging.getLogger("TorchRuntime")


def register_main_thread_initializer(
    initializer: Callable[[TorchInitRequest], None],
) -> None:
    """Register the Qt-owned callback used for deferred initialization."""
    global _main_thread_id, _main_thread_initializer
    _main_thread_id = threading.get_ident()
    _main_thread_initializer = initializer


def _initialize_now(request: TorchInitRequest) -> None:
    _logger.info("Torch 初始化开始 thread=%s", threading.current_thread().name)
    try:
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
        _logger.exception("Torch 初始化失败 thread=%s", threading.current_thread().name)
    finally:
        request.done.set()


def ensure_ready(timeout: float = 90.0) -> bool:
    """Ensure Torch is initialized once, on the registered UI thread."""
    global _ready, _inflight

    with _state_lock:
        if _ready:
            return True
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
