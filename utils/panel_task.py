import copy
import threading
import time

from brain.browser_security import redact_browser_text


AUDIO_PANEL_LOCK = threading.Lock()


class PanelActionError(RuntimeError):
    pass


class PanelTask:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"status": "idle", "message": "尚未执行调试动作", "progress": None, "id": 0}

    def state(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def progress(self, message, percent=None):
        with self._lock:
            self._state.update(message=redact_browser_text(str(message), max_chars=3000), progress=percent)

    def committed(self, revision):
        with self._lock:
            self._state["committedRevision"] = revision

    def start(self, title, operation):
        if not AUDIO_PANEL_LOCK.acquire(blocking=False):
            raise ValueError("另一个语音配置/测试动作正在执行，请稍后重试")
        with self._lock:
            self._state = {"status": "running", "message": title, "progress": None, "id": time.time_ns()}

        def run():
            try:
                message = operation(self.progress)
                with self._lock:
                    self._state.update(status="done", message=str(message), progress=100)
            except Exception as error:
                with self._lock:
                    detail = str(error) if isinstance(error, PanelActionError) else f"操作未完成（{type(error).__name__}），请检查配置、依赖和本地日志"
                    self._state.update(status="error", message=f"{detail}；不表示已回滚", progress=None)
            finally:
                AUDIO_PANEL_LOCK.release()

        try:
            threading.Thread(target=run, name="lianxin-config-panel", daemon=True).start()
        except Exception:
            AUDIO_PANEL_LOCK.release()
            with self._lock:
                self._state.update(status="error", message="无法启动后台动作")
            raise
        return self.state()
