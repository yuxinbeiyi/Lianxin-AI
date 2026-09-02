"""QQ 与微信桥接的生命周期控制器。"""

import queue
import threading
from typing import Callable

from config import get_qq_bridge_config, get_wechat_bridge_config


_QQ_CONNECTING_STYLE = """
    QPushButton {
        background-color: #2D2D3F;
        color: #A0A0B0;
        border-radius: 6px;
        border: 1px solid #3D3D5A;
    }
    QPushButton:hover { background-color: #3D3D55; }
    QPushButton:pressed { background-color: #4D4D65; }
"""

_QQ_CONNECTED_STYLE = """
    QPushButton {
        background-color: #1A3D2A;
        color: white;
        border-radius: 16px;
        border: none;
        padding: 6px 12px;
    }
    QPushButton:hover { background-color: #153322; }
    QPushButton:pressed { background-color: #0F281A; }
"""

_QQ_IDLE_STYLE = """
    QPushButton {
        background-color: #2980B9;
        color: white;
        border-radius: 16px;
        border: none;
        padding: 6px 12px;
    }
    QPushButton:hover { background-color: #2471A3; }
    QPushButton:pressed { background-color: #1F618D; }
"""


class BridgeController:
    """集中管理桥接 Worker、状态展示和跨模块注册。"""

    def __init__(
        self,
        *,
        chat_widget,
        qq_button,
        warning_func: Callable[[str, str], None],
        qq_worker_factory: Callable | None = None,
        wechat_worker_factory: Callable | None = None,
        register_qq_bridge_func: Callable | None = None,
        qq_config_func: Callable[[], dict] = get_qq_bridge_config,
        wechat_config_func: Callable[[], dict] = get_wechat_bridge_config,
    ):
        self._chat_widget = chat_widget
        self._qq_button = qq_button
        self._warning_func = warning_func
        self._qq_worker_factory = qq_worker_factory or self._make_qq_worker
        self._wechat_worker_factory = wechat_worker_factory or self._make_wechat_worker
        self._register_qq_bridge_func = register_qq_bridge_func or self._register_qq_bridge
        self._qq_config_func = qq_config_func
        self._wechat_config_func = wechat_config_func

        self.qq_bridge = None
        self.wechat_bridge = None
        self._qq_connected = False
        self._qq_fast_reply_enabled = False
        self._stopping_qq = False
        self._stopping_wechat = False
        self._qq_log_queue = None
        self._qq_log_thread = None

    @staticmethod
    def _make_qq_worker():
        from workers.qq_bridge_worker import QQBridgeWorker
        return QQBridgeWorker()

    @staticmethod
    def _make_wechat_worker():
        from workers.wechat_bridge_worker import WeChatBridgeWorker
        return WeChatBridgeWorker()

    @staticmethod
    def _register_qq_bridge(worker):
        import brain.tools
        brain.tools._register_qq_bridge(worker)

    def should_auto_start_qq(self) -> bool:
        cfg = self._qq_config_func()
        return bool(cfg.get("enabled", False) and cfg.get("auto_start", False))

    def should_auto_start_wechat(self) -> bool:
        return bool(self._wechat_config_func().get("auto_start", False))

    def is_qq_running(self) -> bool:
        return bool(self.qq_bridge and self.qq_bridge.isRunning())

    def is_qq_connected(self) -> bool:
        return self.is_qq_running() and self._qq_connected

    def is_wechat_running(self) -> bool:
        return bool(self.wechat_bridge and self.wechat_bridge.is_running())

    def start_qq(self) -> bool:
        if self.is_qq_running():
            return False
        cfg = self._qq_config_func()
        if not cfg.get("qq_account"):
            self._warning_func("QQ聊天", "未配置 QQ 账号，请先在设置中填写 qq_account。")
            return False

        self._qq_connected = False
        self._qq_button.setText("QQ聊天 ◷")
        self._qq_button.setStyleSheet(_QQ_CONNECTING_STYLE)
        worker = self._qq_worker_factory()
        worker.set_fast_reply_enabled(self._qq_fast_reply_enabled)
        self.qq_bridge = worker
        self._register_qq_bridge_func(worker)

        self._start_qq_log_thread()
        worker.debug_log.connect(self._qq_log_queue.put_nowait)
        worker.connected.connect(self._on_qq_connected)
        worker.disconnected.connect(self._on_qq_disconnected)
        worker.error_occurred.connect(self._on_qq_error)
        worker.start()
        return True

    def stop_qq(self, notify: bool = True):
        worker = self.qq_bridge
        self._stopping_qq = True
        if worker and worker.isRunning():
            worker.stop()
            worker.wait(3000)
        self.qq_bridge = None
        self._qq_connected = False
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=False, connected=False, health="未连接",
                          last_activity_summary="QQ 桥接已停止")
        except Exception:
            pass
        self._register_qq_bridge_func(None)
        self._stop_qq_log_thread()
        if notify:
            self._chat_widget.add_system_tip("QQ 桥接已断开")
        self.update_qq_button()
        self._stopping_qq = False

    def reload_qq_timing_config(self) -> bool:
        if not self.is_qq_running():
            return False
        self.qq_bridge.reload_timing_config()
        return True

    def is_qq_fast_reply_enabled(self) -> bool:
        return self._qq_fast_reply_enabled

    def set_qq_fast_reply_enabled(self, enabled: bool):
        """Toggle app-session-only fast replies for the owner's private chat."""
        self._qq_fast_reply_enabled = bool(enabled)
        if self.qq_bridge:
            self.qq_bridge.set_fast_reply_enabled(self._qq_fast_reply_enabled)

    def reload_qq_bridge_config(self) -> bool:
        if not self.is_qq_running():
            return False
        self.qq_bridge.reload_bridge_config()
        return True

    def start_wechat(self) -> bool:
        if self.is_wechat_running():
            return False
        worker = self._wechat_worker_factory()
        self.wechat_bridge = worker
        worker.log_message.connect(self._chat_widget.add_system_tip)
        worker.connection_changed.connect(self._on_wechat_status)
        self._chat_widget.add_system_tip("🔄 微信桥接启动中...")
        worker.start_bridge()
        return True

    def stop_wechat(self, notify: bool = True):
        worker = self.wechat_bridge
        self._stopping_wechat = True
        if worker and worker.is_running():
            worker.stop_bridge()
        self.wechat_bridge = None
        if notify and not worker:
            self._chat_widget.add_system_tip("微信桥接已断开")
        self._stopping_wechat = False

    def reload_wechat_config(self) -> bool:
        if not self.wechat_bridge:
            return False
        self.wechat_bridge.reload_config()
        return True

    def shutdown(self):
        self.stop_wechat(notify=False)
        self.stop_qq(notify=False)

    def update_qq_button(self):
        enabled = bool(self._qq_config_func().get("enabled", False))
        if self.is_qq_connected():
            self._qq_button.setText("✅ QQ聊天")
            self._qq_button.setStyleSheet(_QQ_CONNECTED_STYLE)
        elif enabled:
            self._qq_button.setText("🔌 QQ聊天")
            self._qq_button.setStyleSheet(_QQ_IDLE_STYLE)
        else:
            self._qq_button.setText("🐧 QQ聊天")
            self._qq_button.setStyleSheet(_QQ_IDLE_STYLE)

    def _on_qq_connected(self):
        self._qq_connected = True
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=True, connected=True, url=self._qq_config_func().get("ws_url", ""),
                          last_activity_summary="QQ 桥接已连接")
        except Exception:
            pass
        self._chat_widget.add_system_tip("✅ QQ 桥接已连接，可通过 QQ 与莲心聊天")
        self.update_qq_button()

    def _on_qq_disconnected(self, reason: str):
        self._qq_connected = False
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=self.is_qq_running(), connected=False,
                          last_activity_summary=f"QQ 桥接已断开：{reason}")
        except Exception:
            pass
        if self._stopping_qq:
            return
        self._chat_widget.add_system_tip(f"QQ 桥接已断开：{reason}")
        self.update_qq_button()

    def _on_qq_error(self, err: str):
        self._qq_connected = False
        try:
            from brain.runtime_status import update_status
            update_status("qq", running=self.is_qq_running(), connected=False, health="错误",
                          last_activity_summary=f"QQ 桥接错误：{err}")
        except Exception:
            pass
        self._chat_widget.add_system_tip(f"⚠️ QQ 桥接错误：{err}")
        self.update_qq_button()

    def _on_wechat_status(self, connected: bool):
        if not connected and self._stopping_wechat:
            return
        message = "✅ 微信桥接已连接" if connected else "⚠️ 微信桥接已断开"
        self._chat_widget.add_system_tip(message)

    def _start_qq_log_thread(self):
        self._stop_qq_log_thread()
        self._qq_log_queue = queue.Queue()
        self._qq_log_thread = threading.Thread(target=self._qq_log_worker, daemon=True)
        self._qq_log_thread.start()

    def _stop_qq_log_thread(self):
        if self._qq_log_queue is not None:
            self._qq_log_queue.put_nowait(None)
        thread = self._qq_log_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._qq_log_queue = None
        self._qq_log_thread = None

    def _qq_log_worker(self):
        log_queue = self._qq_log_queue
        while log_queue is not None:
            message = log_queue.get()
            if message is None:
                return
            print(f"[QQ桥接] {message}")
