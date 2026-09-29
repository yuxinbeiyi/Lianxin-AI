"""Open one of the existing PyQt feature windows for the new UI.

The React/Tauri shell uses this process boundary temporarily so the original
feature windows keep their established layout and behavior during migration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import traceback
import urllib.request
import urllib.error


WEBENGINE_FEATURES = {
    "ripple", "memory-constellation", "study-room", "time-capsule", "data-tide",
}


def _configure_webengine(feature: str) -> None:
    """Configure Qt WebEngine before importing QApplication on Windows."""
    if feature not in WEBENGINE_FEATURES:
        return

    # The constellation pages use Canvas and remain functional without GPU
    # compositing. This avoids a native Chromium GPU-process failure on
    # machines whose graphics driver rejects Qt WebEngine's default path.
    existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    if "--disable-gpu" not in existing_flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{existing_flags} --disable-gpu".strip()


def _http_proactive_trigger(mode: str):
    """POST /api/proactive/trigger 向 api_server 触发主动聊天（方案 B 的跨进程触发通道）。"""
    try:
        payload = json.dumps({"mode": mode}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:8766/api/proactive/trigger",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            pass
    except Exception as exc:
        print(f"[预警] HTTP 触发失败: {exc}", file=sys.stderr)


def build_window(feature: str):
    if feature == "ripple":
        from gui.ripple_constellation_web import RippleConstellationWebWindow
        return RippleConstellationWebWindow()
    if feature == "persona":
        from gui.persona_hub import PersonaHub
        return PersonaHub()
    if feature == "memory-constellation":
        from gui.memory_constellation_web import MemoryConstellationWebWindow
        return MemoryConstellationWebWindow()
    if feature == "prism-memory":
        from gui.memory_settings_dialog import MemorySettingsDialog
        return MemorySettingsDialog()
    if feature == "history":
        from gui.history_dialog import HistoryDialog
        from memory.history_manager import HistoryManager
        return HistoryDialog(HistoryManager(), current_session_id=-1)
    if feature == "note":
        from gui.note_dialog import NoteDialog
        return NoteDialog()
    if feature == "workflow":
        from gui.workflow_center import WorkflowCenter
        return WorkflowCenter()
    if feature == "duty":
        from gui.duty_center import DutyCenter
        from utils.duty_scheduler import DutyScheduler
        return DutyCenter(DutyScheduler())
    if feature == "proactive":
        from gui.proactive_dialog import ProactiveDialog
        from utils.proactive_chat import ProactiveChatScheduler
        dialog = ProactiveDialog(ProactiveChatScheduler())
        # 不再用原本的局部调度，而是转发给后端 api_server 的常驻主动运行时（方案 B）
        dialog.debug_trigger.connect(lambda: _http_proactive_trigger("normal"))
        dialog.debug_observe_signal.connect(_http_proactive_trigger)
        return dialog
    if feature == "alarm":
        from gui.alarm_dialog import AlarmDialog
        from utils.alarm_manager import AlarmManager
        from utils.reminder_manager import ReminderManager
        from utils.todo_manager import get_todo_manager
        return AlarmDialog(AlarmManager(), todo_manager=get_todo_manager(), reminder_manager=ReminderManager())
    if feature == "reminder":
        from gui.reminder_dialog import ReminderDialog
        return ReminderDialog()
    if feature == "settings":
        from gui.settings_dialog import SettingsDialog
        return SettingsDialog()
    if feature == "api":
        from gui.api_config_dialog import ApiConfigDialog
        return ApiConfigDialog()
    if feature == "network":
        from gui.network_settings_dialog import NetworkSettingsDialog
        return NetworkSettingsDialog()
    if feature == "capability":
        from gui.capability_center import CapabilityCenter
        return CapabilityCenter()
    if feature == "vision":
        from gui.vision_panel import VisionPanel
        return VisionPanel()
    if feature == "camera":
        # 原版功能中心"摄像头"按钮打开的是视觉感知面板（人脸/手势/OLED 表情），
        # 拍照 OCR（CameraDialog）走聊天链路，不作为独立功能区入口。
        from gui.vision_panel import VisionPanel
        return VisionPanel()
    if feature == "voice-stt":
        from gui.voice_stt_dialog import VoiceSTTDialog
        return VoiceSTTDialog()
    if feature == "sound":
        from gui.sound_settings_dialog import SoundSettingsDialog
        return SoundSettingsDialog()
    if feature == "qq":
        from gui.qq_settings_dialog import QqSettingsDialog
        return QqSettingsDialog()
    if feature == "wechat":
        from gui.wechat_settings_dialog import WeChatSettingsDialog
        return WeChatSettingsDialog()
    if feature == "study-room":
        from gui.study_room import StudyRoomWebWindow
        return StudyRoomWebWindow()
    if feature == "time-capsule":
        from gui.time_capsule.web_window import TimeCapsuleWindow
        return TimeCapsuleWindow()
    if feature == "data-tide":
        from gui.achievement.web_window import AchievementWindow
        return AchievementWindow()
    if feature == "video-call":
        from gui.video_call_window import VideoCallWindow
        return VideoCallWindow()
    if feature == "galgame":
        from gui.galgame.galgame_dialog import GalgameDialog
        return GalgameDialog()
    raise ValueError(f"unsupported legacy feature: {feature}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python legacy_ui_launcher.py <feature>", file=sys.stderr)
        return 2
    feature = sys.argv[1]
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"legacy_ui_{feature}.log"

    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n--- launch ---\n")
        log.write(f"python={sys.executable}\n")
        log.write(f"feature={feature}\n")
        try:
            _configure_webengine(feature)
            from PyQt5.QtCore import QTimer, Qt
            from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

            if feature in WEBENGINE_FEATURES:
                QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
                try:
                    from PyQt5.QtWebEngine import QtWebEngine
                    QtWebEngine.initialize()
                except ImportError:
                    pass

            app = QApplication(sys.argv)
            loading = QWidget()
            loading.setWindowTitle("莲心")
            loading.setMinimumSize(280, 110)
            loading.setStyleSheet("QWidget { background: #151B2B; color: #E6EDF7; } QLabel { padding: 18px; font-size: 14px; }")
            loading_layout = QVBoxLayout(loading)
            display_name = "桌宠模式" if feature == "galgame" else feature
            loading_label = QLabel(f"正在打开 {display_name}…")
            loading_label.setAlignment(Qt.AlignCenter)
            loading_layout.addWidget(loading_label)
            loading.show()
            windows = [loading]
            setattr(app, "_lianxin_windows", windows)

            def launch_feature():
                try:
                    window = build_window(feature)
                    windows.append(window)
                    loading.close()
                    window.show()
                    window.raise_()
                    window.activateWindow()

                    if feature == "galgame":
                        from PyQt5.QtCore import QObject, pyqtSignal
                        from gui.galgame.tachie_window import TachieWindow

                        class GalgameBridge(QObject):
                            reply_ready = pyqtSignal(str)
                            error = pyqtSignal(str)

                            def send(self, text: str):
                                threading.Thread(target=self._request, args=(text,), daemon=True).start()

                            def _request(self, text: str):
                                try:
                                    payload = json.dumps({"message": text}, ensure_ascii=False).encode("utf-8")
                                    request = urllib.request.Request(
                                        "http://127.0.0.1:8766/api/chat/message",
                                        data=payload,
                                        headers={"Content-Type": "application/json"},
                                        method="POST",
                                    )
                                    with urllib.request.urlopen(request, timeout=120) as response:
                                        result = json.loads(response.read().decode("utf-8"))
                                    reply = str(result.get("message", {}).get("content", ""))
                                    if reply.strip():
                                        tts_payload = json.dumps({"text": reply}, ensure_ascii=False).encode("utf-8")
                                        tts_request = urllib.request.Request(
                                            "http://127.0.0.1:8766/api/tts/speak",
                                            data=tts_payload,
                                            headers={"Content-Type": "application/json"},
                                            method="POST",
                                        )
                                        with urllib.request.urlopen(tts_request, timeout=10):
                                            pass
                                    self.reply_ready.emit(reply)
                                except (OSError, ValueError, KeyError) as exc:
                                    self.error.emit(str(exc))

                        class GalgamePoller(QObject):
                            """轮询当前会话，把莲心最新主动消息显示到桌宠对话窗。"""
                            def __init__(self, win, tachie):
                                super().__init__(win)
                                self._win = win
                                self._tachie = tachie
                                self._sid = None
                                self._last_id = 0
                                self._last_text = ""
                                self._tts_speaking = False
                                self._timer = QTimer(self)
                                self._timer.timeout.connect(self._poll)
                                self._timer.start(500)

                            def note_displayed(self, text: str):
                                self._last_text = text

                            def _poll(self):
                                try:
                                    with urllib.request.urlopen("http://127.0.0.1:8766/api/app/status", timeout=2) as response:
                                        status = json.loads(response.read().decode("utf-8"))
                                    sid = status.get("sessionId")
                                    if not sid:
                                        return
                                    with urllib.request.urlopen(
                                        f"http://127.0.0.1:8766/api/conversations/{sid}/messages", timeout=2
                                    ) as response:
                                        data = json.loads(response.read().decode("utf-8"))
                                    items = data.get("items") or []
                                    last_assistant = None
                                    for item in items:
                                        if item.get("role") == "assistant" and str(item.get("content") or "").strip():
                                            last_assistant = item
                                    if self._sid != sid:
                                        self._sid = sid
                                        if last_assistant is None:
                                            self._last_id = 0
                                            self._last_text = ""
                                            return
                                        self._last_id = int(last_assistant.get("id") or 0)
                                        self._last_text = str(last_assistant.get("content") or "").strip()
                                        return
                                    if last_assistant is None:
                                        return
                                    mid = int(last_assistant.get("id") or 0)
                                    content = str(last_assistant.get("content") or "").strip()
                                    if mid > self._last_id:
                                        self._last_id = mid
                                        if content != self._last_text:
                                            self._last_text = content
                                            self._win.show_reply(content)
                                    with urllib.request.urlopen("http://127.0.0.1:8766/api/tts/status", timeout=2) as response:
                                        tts = json.loads(response.read().decode("utf-8"))
                                    speaking = bool(tts.get("speaking"))
                                    if speaking != self._tts_speaking:
                                        self._tts_speaking = speaking
                                        if speaking:
                                            self._tachie.stop_breathing()
                                            self._tachie.start_talking()
                                        else:
                                            self._tachie.stop_talking()
                                            self._tachie.start_breathing()
                                except Exception:
                                    pass

                        assets_dir = Path(__file__).resolve().parent / "gui" / "galgame" / "assets"
                        tachie = TachieWindow(assets_dir)
                        bridge = GalgameBridge(tachie)
                        poller = GalgamePoller(window, tachie)

                        def _move_dialog():
                            screen = app.primaryScreen().availableGeometry()
                            dx = tachie.x() - window.width() - 20
                            if dx < screen.left():
                                dx = screen.left() + 8
                            dy = max(screen.top(), tachie.y() + tachie.height() - window.height())
                            window.move(dx, dy)

                        def _place_bottom_right():
                            screen = app.primaryScreen().availableGeometry()
                            tw = tachie.width() or 110
                            th = tachie.height() or 150
                            tachie.move(screen.right() - tw - 40, screen.bottom() - th - 60)
                            _move_dialog()
                            tachie.show()
                            tachie.raise_()

                        window.message_submitted.connect(bridge.send)

                        def _on_reply(text: str):
                            window.show_reply(text)
                            poller.note_displayed(text)

                        bridge.reply_ready.connect(_on_reply)
                        bridge.error.connect(lambda message: window.set_status(f"聊天失败：{message}"))
                        tachie.toggle_dialog_requested.connect(
                            lambda: window.setVisible(not window.isVisible())
                        )
                        tachie.close_requested.connect(window.close)
                        tachie.close_requested.connect(app.quit)
                        tachie.position_changed.connect(lambda x, y: _move_dialog())
                        QTimer.singleShot(80, _place_bottom_right)
                        # Keep both top-level widgets and the bridge alive for the app lifetime.
                        window._lianxin_galgame_objects = (tachie, bridge, poller)

                    log.write("window=created\n")
                    log.flush()
                except Exception:
                    loading_label.setText(f"{feature} 打开失败，请查看日志")
                    traceback.print_exc(file=log)
                    log.flush()

            QTimer.singleShot(0, launch_feature)
            log.write("loading=shown\n")
            log.flush()
            return app.exec_()
        except Exception:
            traceback.print_exc(file=log)
            log.flush()
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
