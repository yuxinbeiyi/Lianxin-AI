# brain/llm_gate.py
"""主对话 LLM 门闩：标记 AgentCore.chat() 是否正在运行。

MusicWatcher 等后台异步 LLM 调用用它来与主对话错峰，
避免在中转站单并发限制下抢占主对话的 API 名额。
"""

import threading

_lock = threading.Lock()
_active = 0  # 正在运行的主对话请求数（计数以兼容多线程/多通道）


def mark_main_request_start():
    global _active
    with _lock:
        _active += 1


def mark_main_request_end():
    global _active
    with _lock:
        if _active > 0:
            _active -= 1


def main_request_active() -> bool:
    with _lock:
        return _active > 0


def active_count() -> int:
    with _lock:
        return _active
