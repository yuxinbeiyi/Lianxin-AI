"""Lianxin UI compatibility API.

This is an additive bridge for the React/Tauri interface.  It deliberately
reuses the existing AgentCore, HistoryManager, note manager and task store;
the legacy PyQt application remains unchanged and can continue to use them.
Run with: ``python api_server.py``.
"""

from __future__ import annotations

import json
import hashlib
import base64
import mimetypes
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


LEGACY_FEATURES = {
    "ripple", "persona", "memory-constellation", "prism-memory",
    "history", "note", "workflow", "duty", "proactive", "alarm", "reminder",
    "settings", "api", "network", "capability", "vision", "voice-stt", "sound",
    "qq", "wechat", "study-room", "time-capsule", "data-tide", "camera", "galgame",
}
WEBENGINE_FEATURES = {"ripple", "memory-constellation", "study-room", "time-capsule", "data-tide"}

from utils.note_manager import read_note, write_note
from brain.task_store import get_task_store


class LianxinBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chat_lock = threading.Lock()
        self._agent = None
        self._busy = False
        self._voice = None
        self._voice_lock = threading.RLock()
        self._voice_events = deque(maxlen=100)
        self._proactive = None

    def agent(self):
        with self._lock:
            if self._agent is None:
                from brain.agent import AgentCore
                self._agent = AgentCore()
            return self._agent

    def status(self) -> dict:
        agent = self.agent()
        return {
            "online": True,
            "sessionId": getattr(agent, "_session_id", None),
            "busy": self._busy,
            "source": "python-core",
        }

    def sessions(self) -> list[dict]:
        return self.agent().get_history_manager().get_sessions()

    def messages(self, session_id: int) -> list[dict]:
        return self.agent().get_history_manager().get_messages(session_id)

    def new_session(self) -> dict:
        agent = self.agent()
        with self._chat_lock:
            agent.new_session()
        return {"id": agent._session_id}

    def select_session(self, session_id: int) -> dict:
        with self._chat_lock:
            from brain.agent import AgentCore
            self._agent = AgentCore(session_id=int(session_id))
            return {"id": self._agent._session_id}

    def chat(self, text: str) -> dict:
        if not text.strip():
            raise ValueError("消息不能为空")
        with self._chat_lock:
            agent = self.agent()
            self._busy = True
            try:
                response = agent.chat(text)
            finally:
                self._busy = False
        return {
            "sessionId": agent._session_id,
            "message": {"role": "assistant", "content": response or ""},
        }

    def chat_stream(self, text: str):
        """Run AgentCore in a worker and forward tool lifecycle events as SSE data."""
        if not text.strip():
            raise ValueError("消息不能为空")
        from queue import Queue

        events = Queue()
        agent = self.agent()

        def on_round_start(round_num):
            events.put({"type": "tool_round_start", "round": int(round_num)})

        def on_tool_call(name, args):
            safe_args = args or {}
            if str(name or "").startswith("browser_"):
                try:
                    from brain.browser_security import redact_browser_args
                    safe_args = redact_browser_args(name, safe_args)
                except Exception:
                    safe_args = {"redacted": True}
            events.put({
                "type": "tool_call", "name": str(name), "args": safe_args,
            })

        def on_tool_result(name, result, is_error=False, elapsed_ms=0):
            preview = str(result or "")[:240]
            if len(str(result or "")) > 240:
                preview += "..."
            events.put({
                "type": "tool_result", "name": str(name), "preview": preview,
                "isError": bool(is_error), "elapsedMs": float(elapsed_ms or 0),
            })

        def run():
            with self._chat_lock:
                self._busy = True
                try:
                    response = agent.chat(
                        text,
                        on_round_start=on_round_start,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                    )
                    events.put({
                        "type": "completed", "sessionId": agent._session_id,
                        "message": {"role": "assistant", "content": response or ""},
                    })
                except Exception as exc:
                    events.put({"type": "error", "error": str(exc)})
                finally:
                    self._busy = False
                    events.put(None)

        threading.Thread(target=run, name="lianxin-api-chat", daemon=True).start()
        yield {"type": "started"}
        while True:
            event = events.get()
            if event is None:
                break
            yield event

    def background_state(self, include_data: bool = True) -> dict:
        """Expose the legacy wallpaper settings to the WebView as a data URL."""
        # The settings dialog runs in a separate legacy process.  Constructing
        # a fresh manager here avoids serving the API process's stale singleton.
        from utils.settings import SettingsManager

        settings = SettingsManager()
        source = str(settings.background_source or "")
        source_type = settings.background_source_type
        path = Path(source).expanduser()
        if source_type in {"folder_first", "folder_random"} and path.is_dir():
            images = sorted(
                item for item in path.iterdir()
                if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
            )
            path = images[0] if images else Path()
        fingerprint = ""
        data_url = ""
        try:
            stat = path.stat()
            fingerprint = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
            if include_data and stat.st_size <= 12 * 1024 * 1024:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                data_url = f"data:{mime};base64,{encoded}"
        except (OSError, ValueError):
            pass
        return {
            "enabled": bool(settings.background_enabled),
            "opacity": float(settings.background_opacity),
            "fitMode": settings.background_fit_mode,
            "fingerprint": fingerprint,
            "dataUrl": data_url,
        }

    @staticmethod
    def _image_payload(path_value: str, include_data: bool) -> dict:
        path = Path(path_value).expanduser() if path_value else Path()
        fingerprint = ""
        data_url = ""
        try:
            if not path.is_file():
                return {"fingerprint": "", "dataUrl": ""}
            stat = path.stat()
            fingerprint = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
            if include_data and stat.st_size <= 12 * 1024 * 1024:
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                data_url = f"data:{mime};base64,{encoded}"
        except (OSError, ValueError):
            pass
        return {"fingerprint": fingerprint, "dataUrl": data_url}

    def avatar_state(self, include_data: bool = True) -> dict:
        """Expose the legacy chat/avatar settings without changing their owner."""
        from config import get_avatar_config, get_chat_avatar_config

        chat_config = get_chat_avatar_config()
        character_config = get_avatar_config()
        chat_assistant_path = str(chat_config.get("assistant_path") or "")
        character_path = ""
        if character_config.get("mode") == "static":
            character_path = str(character_config.get("static_image_path") or "")
        if not chat_assistant_path:
            chat_assistant_path = character_path
        if not chat_assistant_path:
            chat_assistant_path = str(Path(__file__).resolve().parent / "assets" / "莲心形象透明背景.png")

        assistant = self._image_payload(chat_assistant_path, include_data)
        user = self._image_payload(str(chat_config.get("user_path") or ""), include_data)
        fingerprint = "|".join((assistant["fingerprint"], user["fingerprint"], str(chat_config.get("enabled", True)), str(chat_config.get("size", 60)), str(chat_config.get("gap", 10)), str(chat_config.get("border", True))))
        return {
            "enabled": bool(chat_config.get("enabled", True)),
            "size": int(chat_config.get("size", 60) or 60),
            "gap": int(chat_config.get("gap", 10) or 10),
            "border": bool(chat_config.get("border", True)),
            "assistantDataUrl": assistant["dataUrl"],
            "userDataUrl": user["dataUrl"],
            "fingerprint": fingerprint,
        }

    def music_state(self) -> dict:
        # Prefer the real 8765 player API; the state file remains a safe fallback.
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=1.5) as response:
                status = json.loads(response.read().decode("utf-8"))
            st = status.get("status") or {}
            playback = status.get("playback") or {}
            return {
                "active": bool(st.get("playing")) and not bool(st.get("paused")),
                "playing": bool(st.get("playing")), "paused": bool(st.get("paused")),
                "name": playback.get("name") or "", "title": playback.get("name") or "",
                "artist": playback.get("artist") or "", "album": playback.get("album") or "",
                "progress": float(st.get("position") or 0), "duration": float(st.get("duration") or 0),
                "volume": float(st.get("volume") or 0), "source": "netease-8765",
            }
        except Exception:
            pass
        try:
            from brain.music_watcher import _DEFAULT_STATE_FILE
            candidates = [_DEFAULT_STATE_FILE]
        except Exception:
            candidates = []
        candidates += [
            Path(__file__).parent / "参考项目" / "netease-music-mcp-main" / ".listening-state.json",
            Path(__file__).parent / "参考项目" / "netease-music-mcp-main" / "listening-state.json",
        ]
        for path in candidates:
            try:
                if path.exists():
                    return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        return {"active": False}

    def music_control(self, action: str, payload: dict | None = None) -> dict:
        import urllib.request
        routes = {"toggle": "/api/pause", "play": "/api/pause", "pause": "/api/pause", "next": "/api/next", "previous": "/api/prev"}
        route = routes.get(action)
        if not route:
            raise ValueError(f"不支持的音乐操作: {action}")
        request = urllib.request.Request(
            "http://127.0.0.1:8765" + route,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
        return self.music_state()

    def voice_state(self) -> dict:
        with self._voice_lock:
            return {"active": bool(self._voice), "state": getattr(self._voice, "state", "STOPPED")}

    def voice_events(self, after: int = 0) -> dict:
        with self._voice_lock:
            events = [event for event in self._voice_events if int(event["id"]) > int(after)]
        return {"items": events, "latest": events[-1]["id"] if events else int(after)}

    def start_voice(self) -> dict:
        with self._voice_lock:
            if self._voice is not None:
                return self.voice_state()
            from brain.voice_duplex import VoiceDuplexManager
            def on_state(state):
                self._voice_events.append({"id": time.time_ns(), "type": "voice.state", "state": state})
            def on_transcript(text):
                self._voice_events.append({"id": time.time_ns(), "type": "voice.transcript", "content": text})
                threading.Thread(target=self._voice_chat, args=(text,), daemon=True).start()
            manager = VoiceDuplexManager(on_state_change=on_state, on_transcript=on_transcript)
            if not manager.start():
                raise RuntimeError("语音输入启动失败：WebRTC VAD 或麦克风不可用")
            self._voice = manager
            return self.voice_state()

    def _voice_chat(self, text: str):
        try:
            result = self.chat(text)
            self._voice_events.append({"id": time.time_ns(), "type": "voice.reply", "content": result["message"]["content"]})
        except Exception as exc:
            self._voice_events.append({"id": time.time_ns(), "type": "voice.error", "error": str(exc)})

    def stop_voice(self) -> dict:
        with self._voice_lock:
            if self._voice is not None:
                self._voice.stop()
                self._voice = None
            return self.voice_state()

    def proactive(self) -> dict:
        with self._lock:
            if self._proactive is None:
                from utils.proactive_chat import ProactiveChatScheduler
                self._proactive = ProactiveChatScheduler()
            scheduler = self._proactive
            return {"desktopEnabled": scheduler.desktop_enabled, "qqEnabled": scheduler.qq_enabled,
                    "musicFeedbackEnabled": scheduler.music_feedback_enabled,
                    "minIntervalMinutes": scheduler.min_interval_minutes,
                    "frequency": scheduler.frequency}

    def set_proactive(self, enabled: bool) -> dict:
        with self._lock:
            if self._proactive is None:
                self.proactive()
            self._proactive.desktop_enabled = bool(enabled)
            self._proactive.save_settings()
        return self.proactive()

    def management(self) -> dict:
        return {"modules": [
            {"id": "ripple", "label": "涟漪情感系统", "available": True},
            {"id": "persona", "label": "人格枢控", "available": True},
            {"id": "memory-constellation", "label": "星图系统", "available": True},
            {"id": "prism-memory", "label": "棱镜记忆系统", "available": True},
            {"id": "history", "label": "历史记录", "available": True},
            {"id": "note", "label": "备忘本", "available": True},
            {"id": "alarm", "label": "闹钟与提醒", "available": True},
            {"id": "workflow", "label": "任务运行中心", "available": True},
            {"id": "duty", "label": "后台职责中心", "available": True},
            {"id": "proactive", "label": "主动聊天", "available": True},
            {"id": "study-room", "label": "莲心自习室", "available": True},
            {"id": "time-capsule", "label": "时间胶囊", "available": True},
            {"id": "data-tide", "label": "数据潮汐", "available": True},
            {"id": "capability", "label": "能力中枢", "available": True},
            {"id": "vision", "label": "视觉理解", "available": True},
            {"id": "voice-stt", "label": "语音转录", "available": True},
            {"id": "voice", "label": "语音聊天", "available": True, "state": self.voice_state()},
            {"id": "sound", "label": "声音设置", "available": True},
            {"id": "settings", "label": "全局设置", "available": True},
            {"id": "api", "label": "API Key", "available": True},
            {"id": "network", "label": "网络设置", "available": True},
            {"id": "qq", "label": "QQ 聊天", "available": True},
            {"id": "wechat", "label": "微信聊天", "available": True},
            {"id": "music", "label": "网易云音乐", "available": bool(self.music_state().get("source"))},
            {"id": "python-api", "label": "Python 核心 API", "available": True},
        ]}

    def open_legacy_window(self, feature: str) -> dict:
        if feature not in LEGACY_FEATURES:
            raise ValueError(f"不支持的原版界面: {feature}")
        if feature in WEBENGINE_FEATURES:
            try:
                import PyQt5.QtWebEngineWidgets  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "原版 WebEngine 窗口需要 PyQtWebEngine，请在莲心运行环境执行: "
                    "python -m pip install PyQtWebEngine==5.15.7"
                ) from exc
        launcher = Path(__file__).with_name("legacy_ui_launcher.py")
        log_dir = Path(__file__).with_name("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"legacy_ui_{feature}.log"
        log_file = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(launcher), feature],
            cwd=str(launcher.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        # The child owns its process lifetime, but the parent must release its
        # inherited file handle immediately after spawning it.
        log_file.close()
        return {"feature": feature, "pid": process.pid, "mode": "legacy-popup"}

    def five_axis(self) -> dict:
        try:
            from brain.emotional import get_manager
            manager = get_manager()
            info = manager.get_debug_info() or {}
            axes = info.get("axes") or {}
            return {
                "axes": {
                    "connection": float(axes.get("connection", 0.0)),
                    "pride": float(axes.get("pride", 0.0)),
                    "valence": float(axes.get("valence", 0.0)),
                    "arousal": float(axes.get("arousal", 0.0)),
                    "immersion": float(axes.get("immersion", 0.0)),
                },
                "mood": str(info.get("middle_layer") or "中性"),
            }
        except Exception as exc:
            return {"axes": {}, "mood": "状态暂不可用", "error": str(exc)}

    def task_snapshot(self) -> dict:
        data = self.tasks()
        data["workflowRuns"] = __import__("brain.workflow", fromlist=["get_workflow_store"]).get_workflow_store().list_runs(limit=30)
        return data

    def tasks(self) -> dict:
        store = get_task_store()
        try:
            from brain.task_tracker import get_task_tracker
            tracker = get_task_tracker()
            live_todos = tracker.get_todos()
            completed, total, active = tracker.get_progress()
        except Exception:
            live_todos, completed, total, active = [], 0, 0, ""
        return {
            "todos": store.list_todos(),
            "liveTodos": live_todos,
            "autoTasks": store.list_auto_tasks(),
            "logs": store.list_auto_task_logs(limit=50),
            "progress": {"completed": completed, "total": total, "active": active},
        }


bridge = LianxinBridge()


class Handler(BaseHTTPRequestHandler):
    server_version = "LianxinBridge/1.0"

    def log_message(self, *_args):
        return

    def _send(self, payload, status=200, content_type="application/json"):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/app/status":
                return self._send(bridge.status())
            if path == "/api/conversations":
                query = parse_qs(urlparse(self.path).query)
                keyword = (query.get("q") or [""])[0].strip()
                items = bridge.agent().get_history_manager().search_sessions(keyword) if keyword else bridge.sessions()
                return self._send({"items": items})
            if path.startswith("/api/conversations/") and path.endswith("/messages"):
                sid = int(path.split("/")[3])
                return self._send({"items": bridge.agent().get_history_manager().get_messages_with_ids(sid)})
            if path == "/api/note":
                return self._send({"content": read_note()})
            if path == "/api/tasks":
                return self._send(bridge.tasks())
            if path == "/api/music/state":
                return self._send(bridge.music_state())
            if path == "/api/voice/status":
                return self._send(bridge.voice_state())
            if path == "/api/voice/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.voice_events(int((query.get("after") or [0])[0])))
            if path == "/api/proactive/state":
                return self._send(bridge.proactive())
            if path == "/api/management/state":
                return self._send(bridge.management())
            if path == "/api/settings/background":
                query = parse_qs(urlparse(self.path).query)
                include_data = (query.get("include") or ["1"])[0] != "0"
                return self._send(bridge.background_state(include_data=include_data))
            if path == "/api/settings/avatars":
                query = parse_qs(urlparse(self.path).query)
                include_data = (query.get("include") or ["1"])[0] != "0"
                return self._send(bridge.avatar_state(include_data=include_data))
            if path == "/api/legacy/windows":
                return self._send({"items": [item for item in bridge.management()["modules"] if item["id"] in LEGACY_FEATURES]})
            if path == "/api/emotion/five-axis":
                return self._send(bridge.five_axis())
            if path == "/api/tasks/snapshot":
                return self._send(bridge.task_snapshot())
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/conversations":
                return self._send(bridge.new_session(), 201)
            if path.startswith("/api/conversations/") and path.endswith("/select"):
                sid = int(path.split("/")[3])
                return self._send(bridge.select_session(sid))
            if path == "/api/chat/message":
                return self._send(bridge.chat(str(body.get("message", ""))))
            if path == "/api/chat/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for event in bridge.chat_stream(str(body.get("message", ""))):
                    if event.get("type") == "completed":
                        event = {**event, "message": event.get("message", {})}
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return
            if path == "/api/note":
                write_note(str(body.get("content", "")))
                return self._send({"content": read_note()})
            if path == "/api/music/control":
                return self._send(bridge.music_control(str(body.get("action", "")), body))
            if path == "/api/voice/start":
                return self._send(bridge.start_voice())
            if path == "/api/voice/stop":
                return self._send(bridge.stop_voice())
            if path == "/api/proactive/toggle":
                return self._send(bridge.set_proactive(bool(body.get("enabled"))))
            if path == "/api/legacy/open":
                return self._send(bridge.open_legacy_window(str(body.get("feature", ""))), 201)
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_PATCH(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path.startswith("/api/conversations/"):
                sid = int(path.split("/")[3])
                manager = bridge.agent().get_history_manager()
                if "title" in body:
                    manager.update_session_title(sid, str(body["title"]))
                if body.get("togglePin"):
                    manager.toggle_pin(sid)
                return self._send({"session": manager.get_session(sid)})
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/conversations/"):
                sid = int(path.split("/")[3])
                bridge.agent().get_history_manager().delete_session(sid)
                return self._send({"deleted": sid})
            return self._send({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send({"error": str(exc)}, 500)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    print("[LianxinBridge] listening on http://127.0.0.1:8766", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
