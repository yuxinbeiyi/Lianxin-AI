"""
事件管理器
负责接收手势事件并分发给所有订阅者（日志、模拟莲心、UI 等）。

EventManager 不判断手势，只负责：
- 去重（同一事件不重复分发）
- 时间戳
- 事件分发
- 日志记录
"""

import logging
import os
from datetime import datetime
from typing import Callable, Optional

from ..config import LOGS_DIR, LOG_FILENAME, LOG_LEVEL, MAX_LOG_COUNT
from .gesture_event import GestureEvent


# 事件回调类型：event -> None
EventHandler = Callable[[GestureEvent], None]


class EventManager:
    """手势事件管理器。

    用法：
        em = EventManager()
        em.add_handler("logger", my_logger_func)
        em.add_handler("mock_lianxin", mock_reply_func)
        em.dispatch(event)
    """

    def __init__(self, max_log_count: int = MAX_LOG_COUNT):
        self._handlers: dict[str, EventHandler] = {}
        self._event_history: list[GestureEvent] = []
        self._max_log_count = max_log_count
        self._last_event: Optional[GestureEvent] = None
        self._logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """设置文件日志。"""
        logger = logging.getLogger("gesture_lab")
        logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        logger.handlers.clear()

        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOGS_DIR / LOG_FILENAME
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(handler)
        except Exception:
            pass  # 日志文件创建失败不影响功能

        return logger

    @property
    def history(self) -> list[GestureEvent]:
        """事件历史（最新的在前）。"""
        return list(reversed(self._event_history))

    @property
    def last_event(self) -> Optional[GestureEvent]:
        """最近一次事件。"""
        return self._last_event

    def add_handler(self, name: str, handler: EventHandler):
        """注册事件处理器。"""
        self._handlers[name] = handler
        self._logger.info(f"注册事件处理器: {name}")

    def remove_handler(self, name: str):
        """移除事件处理器。"""
        if name in self._handlers:
            del self._handlers[name]
            self._logger.info(f"移除事件处理器: {name}")

    def dispatch(self, event: GestureEvent):
        """分发一个手势事件到所有处理器。

        任何处理器抛出的异常都会被捕获并记录，不会中断其他处理器。
        """
        self._last_event = event
        self._event_history.append(event)

        # 限制历史长度
        if len(self._event_history) > self._max_log_count:
            self._event_history = self._event_history[-self._max_log_count:]

        # 写日志
        self._logger.info(
            f"手势事件: {event.gesture} "
            f"(置信度={event.confidence:.2f}, "
            f"手数量={event.hand_count})"
        )

        # 分发给所有处理器
        for name, handler in list(self._handlers.items()):
            try:
                handler(event)
            except Exception as exc:
                self._logger.error(f"事件处理器 {name} 异常: {exc}")

    def clear_history(self):
        """清空事件历史。"""
        self._event_history.clear()
        self._last_event = None
        self._logger.info("事件历史已清空")
