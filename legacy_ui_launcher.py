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
        return ProactiveDialog(ProactiveChatScheduler())
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
            loading_label = QLabel(f"正在打开 {feature}…")
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
                                    self.reply_ready.emit(str(result.get("message", {}).get("content", "")))
                                except (OSError, ValueError, KeyError) as exc:
                                    self.error.emit(str(exc))

                        assets_dir = Path(__file__).resolve().parent / "gui" / "galgame" / "assets"
                        tachie = TachieWindow(assets_dir)
                        bridge = GalgameBridge(tachie)
                        window.message_submitted.connect(bridge.send)
                        bridge.reply_ready.connect(window.show_reply)
                        bridge.error.connect(lambda message: window.set_status(f"聊天失败：{message}"))
                        tachie.toggle_dialog_requested.connect(
                            lambda: window.setVisible(not window.isVisible())
                        )
                        tachie.close_requested.connect(window.close)
                        tachie.close_requested.connect(app.quit)
                        tachie.position_changed.connect(
                            lambda x, y: window.move(int(x + tachie.width() + 20), int(y))
                        )
                        screen = app.primaryScreen().availableGeometry()
                        tachie.move(screen.left() + 80, screen.top() + 80)
                        window.move(tachie.x() + tachie.width() + 20, tachie.y())
                        tachie.show()
                        tachie.raise_()
                        # Keep both top-level widgets and the bridge alive for the app lifetime.
                        window._lianxin_galgame_objects = (tachie, bridge)
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
