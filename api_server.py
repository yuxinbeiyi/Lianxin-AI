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
import random
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


LEGACY_FEATURES = {
    "ripple", "persona", "memory-constellation", "prism-memory",
    "history", "note", "workflow", "duty", "proactive", "alarm", "reminder",
    "settings", "api", "network", "capability", "vision", "voice-stt", "sound",
    "qq", "wechat", "study-room", "time-capsule", "data-tide", "camera", "galgame",
    "video-call",
}
WEBENGINE_FEATURES = {"ripple", "memory-constellation", "study-room", "time-capsule", "data-tide"}


# ---------- ????????? Tauri CSP ??????? ----------
_COVER_CACHE_DIR: Path | None = None


def _cover_cache_dir() -> Path:
    global _COVER_CACHE_DIR
    if _COVER_CACHE_DIR is None:
        try:
            from utils.paths import get_user_data_dir
            _COVER_CACHE_DIR = get_user_data_dir() / "netease_cover_cache"
        except Exception:
            _COVER_CACHE_DIR = Path.home() / ".lianxin" / "netease_cover_cache"
    _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _COVER_CACHE_DIR


def _download_cover(remote_url: str) -> Path:
    """???????????????????????"""
    import urllib.request
    digest = hashlib.md5(remote_url.encode("utf-8")).hexdigest()[:16]
    target = _cover_cache_dir() / (digest + ".jpg")
    if not target.exists():
        req = urllib.request.Request(remote_url, headers={"User-Agent": "Mozilla/5.0 (Lianxin)"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()
        if data:
            target.write_bytes(data)
    return target


def _cleanup_cover_cache() -> None:
    """?? mtime ? 7 ???? 100 ???????"""
    try:
        cache_dir = _cover_cache_dir()
        entries = []
        for child in cache_dir.iterdir():
            try:
                if child.is_file():
                    entries.append((child.stat().st_mtime, child))
            except Exception:
                continue
        if not entries:
            return
        entries.sort(key=lambda item: item[0], reverse=True)  # ????
        now = time.time()
        keep = []
        for mtime, child in entries:
            if mtime < now - 7 * 86400 or len(keep) >= 100:
                try:
                    child.unlink()
                except Exception:
                    pass
            else:
                keep.append(child)
    except Exception as exc:
        print(f"[LianxinBridge] cover cache cleanup failed: {exc}", flush=True)


def _cover_cleanup_loop() -> None:
    _cleanup_cover_cache()
    while True:
        time.sleep(60 * 60)
        _cleanup_cover_cache()

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
        self._avatar_lock = threading.RLock()
        self._avatar_last_trigger = 0.0
        self._avatar_busy = False
        self._avatar_streak = 0
        self._avatar_last_tap = 0.0
        self._avatar_stats = None
        self._tts_lock = threading.RLock()
        self._tts_worker = None
        self._netease_spawn_lock = threading.Lock()
        self._netease_proc = None

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

    def cancel_chat(self) -> dict:
        agent = self.agent()
        cancelled = bool(agent.cancel_active_request("用户在新版界面中停止了当前回复"))
        return {"cancelled": cancelled}

    def sessions(self) -> list[dict]:
        return self.agent().get_history_manager().get_sessions()

    def messages(self, session_id: int) -> list[dict]:
        items = self.agent().get_history_manager().get_messages_with_ids(session_id)
        for item in items:
            for attachment in item.get("attachments", []) or []:
                path = str(attachment.get("path") or "")
                if path:
                    attachment["url"] = f"http://127.0.0.1:8766/api/attachments?path={__import__('urllib.parse', fromlist=['quote']).quote(path)}"
        return items

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

    @staticmethod
    def _store_image_attachment(attachment: dict) -> tuple[Path, str]:
        """Persist a WebView image using the same managed directory as legacy UI."""
        data_url = str(attachment.get("dataUrl") or "")
        if not data_url.startswith("data:image/") or ";base64," not in data_url:
            raise ValueError("仅支持 base64 图片附件")
        header, encoded = data_url.split(";base64,", 1)
        mime = header[5:].lower()
        extension = mimetypes.guess_extension(mime) or ".png"
        if extension == ".jpe":
            extension = ".jpg"
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            raise ValueError("图片为空或超过 12 MB")
        root = Path.home() / ".lianxin" / "images"
        root.mkdir(parents=True, exist_ok=True)
        filename = Path(str(attachment.get("fileName") or "image")).stem[:80]
        target = root / f"{filename}-{uuid.uuid4().hex[:10]}{extension}"
        target.write_bytes(raw)
        return target, mime

    @staticmethod
    def _store_file_attachment(attachment: dict) -> Path:
        data_url = str(attachment.get("dataUrl") or "")
        if ";base64," not in data_url:
            raise ValueError("文件附件格式无效")
        _, encoded = data_url.split(";base64,", 1)
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError("文件为空或超过 20 MB")
        root = Path.home() / ".lianxin" / "files"
        root.mkdir(parents=True, exist_ok=True)
        filename = Path(str(attachment.get("fileName") or "attachment")).name[:100]
        target = root / f"{uuid.uuid4().hex[:10]}-{filename}"
        target.write_bytes(raw)
        return target

    def chat_stream(self, text: str, attachments: list[dict] | None = None,
                    forced_tool: str | None = None, preferred_tool: str | None = None):
        """Run AgentCore in a worker and forward tool lifecycle events as SSE data."""
        attachments = list(attachments or [])
        if not text.strip() and not attachments:
            raise ValueError("message or image attachment is required")
        from queue import Queue

        events = Queue()
        agent = self.agent()
        pending_tool_round = [None]

        def on_round_start(round_num):
            # AgentCore enters a model round even for ordinary text replies.
            # The legacy UI creates its debug group only after a real tool call.
            pending_tool_round[0] = int(round_num)

        def on_tool_call(name, args):
            round_num = pending_tool_round[0]
            if round_num is not None:
                events.put({"type": "tool_round_start", "round": round_num})
                pending_tool_round[0] = None
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
                    context_parts = []
                    stored_paths = []
                    for index, attachment in enumerate(attachments):
                        if str(attachment.get("kind") or "image") == "file":
                            try:
                                file_path = self._store_file_attachment(attachment)
                                stored_paths.append(file_path)
                                context_parts.append(
                                    f"[用户发送了文件]\n文件名：{attachment.get('fileName', '附件')}\n已保存路径：{file_path}\n"
                                    "如果需要读取文件内容，请使用可用的文件读取工具。"
                                )
                            except Exception as exc:
                                stored_paths.append("")
                                context_parts.append(f"[文件附件保存失败] {exc}")
                            events.put({"type": "file_attachment_saved", "index": index,
                                        "fileName": str(attachment.get("fileName") or "attachment")})
                            continue
                        events.put({"type": "image_analysis_started", "index": index,
                                    "fileName": str(attachment.get("fileName") or "image")})
                        try:
                            image_path, _ = self._store_image_attachment(attachment)
                            stored_paths.append(image_path)
                            from brain.vision import describe_image
                            description = describe_image(
                                str(image_path),
                                prompt=text.strip() or "请详细描述这张图片里的内容，并指出其中值得注意的文字、人物、物体和场景。",
                            )
                            context_parts.append(
                                f"[用户发送了图片，视觉分析结果如下]\n{description}\n[图片描述结束]"
                            )
                            events.put({"type": "image_analysis_result", "index": index,
                                        "fileName": str(attachment.get("fileName") or "image"),
                                        "description": description})
                        except Exception as exc:
                            stored_paths.append("")
                            error_text = str(exc)
                            context_parts.append(f"[图片分析失败] {error_text}")
                            events.put({"type": "image_analysis_result", "index": index,
                                        "fileName": str(attachment.get("fileName") or "image"),
                                        "error": error_text, "description": error_text})
                    agent_text = "\n\n".join(context_parts + ([text] if text.strip() else []))
                    if not agent_text.strip():
                        agent_text = "请根据你看到的图片自然地回应。"
                    response = agent.chat(
                        agent_text,
                        forced_tool=forced_tool or None,
                        preferred_tool=preferred_tool or None,
                        on_round_start=on_round_start,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                    )
                    events.put({
                        "type": "completed", "sessionId": agent._session_id,
                        "message": {"role": "assistant", "content": response or ""},
                    })
                    if attachments:
                        metadata = {"attachments": [
                            {"kind": str(item.get("kind") or "image"),
                             "fileName": str(item.get("fileName") or "attachment"),
                             "path": str(path)}
                            for item, path in zip(attachments, stored_paths)
                        ]}
                        agent.get_history_manager().update_latest_message_metadata(
                            agent._session_id, "user", metadata
                        )
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

    def capabilities(self) -> dict:
        from brain.capability_catalog import list_capabilities
        return {"items": [item.__dict__ for item in list_capabilities(include_disabled=True)]}

    def speak(self, text: str, voice: str = "") -> dict:
        if not str(text or "").strip():
            raise ValueError("朗读内容不能为空")
        self.stop_speaking()
        from voice.speaker import VoiceSpeaker
        speaker = VoiceSpeaker(voice=voice or "zh-CN-XiaoxiaoNeural")
        def run():
            try:
                speaker.speak(str(text))
            finally:
                with self._tts_lock:
                    if self._tts_worker is speaker:
                        self._tts_worker = None
        with self._tts_lock:
            self._tts_worker = speaker
        threading.Thread(target=run, name="lianxin-api-tts", daemon=True).start()
        return {"speaking": True}

    def stop_speaking(self) -> dict:
        from skills.语音合成.tools import stop_voice_playback
        stop_voice_playback()
        with self._tts_lock:
            speaker = self._tts_worker
            self._tts_worker = None
        if speaker is not None:
            try:
                speaker.stop()
            except Exception:
                pass
        return {"speaking": False}

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
            "chatOpacity": float(settings.chat_background_opacity),
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
        character_path = character_path or str(Path(__file__).resolve().parent / "assets" / "GIF" / "normal.gif")
        character = self._image_payload(character_path, include_data)
        fingerprint = "|".join((assistant["fingerprint"], user["fingerprint"], character["fingerprint"], str(chat_config.get("enabled", True)), str(chat_config.get("size", 60)), str(chat_config.get("gap", 10)), str(chat_config.get("border", True))))
        return {
            "enabled": bool(chat_config.get("enabled", True)),
            "size": int(chat_config.get("size", 60) or 60),
            "gap": int(chat_config.get("gap", 10) or 10),
            "border": bool(chat_config.get("border", True)),
            "assistantDataUrl": assistant["dataUrl"],
            "userDataUrl": user["dataUrl"],
            "characterDataUrl": character["dataUrl"],
            "characterFingerprint": character["fingerprint"],
            "fingerprint": fingerprint,
        }

    def avatar_action(self, action: str) -> dict:
        """Run the legacy chat-avatar interaction chain for the new shell."""
        sounds = {"tap": "拍一拍.mp3", "headpat": "ButtonAll.mp3"}
        filename = sounds.get(action)
        if not filename:
            raise ValueError(f"unsupported avatar action: {action}")
        from config import get_chat_avatar_config, get_user_name
        cfg = get_chat_avatar_config()
        if not cfg.get("interactions_enabled", True):
            return {"action": action, "accepted": False, "message": "头像互动已在设置中关闭"}
        with self._avatar_lock:
            now = time.monotonic()
            cooldown = float(cfg.get("tap_cooldown_seconds", 1.5) or 1.5)
            if self._avatar_busy or now - self._avatar_last_trigger < cooldown:
                return {"action": action, "accepted": False, "message": "互动冷却中，请稍等一下"}
            self._avatar_busy = True
            self._avatar_last_trigger = now
            if now - self._avatar_last_tap > 8:
                self._avatar_streak = 0
            self._avatar_streak += 1
            self._avatar_last_tap = now
        try:
            from utils.sound import play_sound
            play_sound(filename)
        except Exception as exc:
            sound_ok = False
            sound_error = str(exc)
        else:
            sound_ok = True
            sound_error = ""

        counter_action = ""
        if bool(cfg.get("counter_tap", True)) and random.random() < 0.28:
            counter_action = action

        fallback = {
            "tap": ["你拍到我了，我记住啦。", "拍完就不许跑远，我还在这里。"],
            "headpat": ["被你摸到了，今天也稍微陪你久一点。", "好啦好啦，摸到了，继续陪你聊天。"],
        }
        response = random.choice(fallback[action])
        used_fallback = True
        if cfg.get("dynamic_response", True):
            try:
                from brain.agent import AgentCore
                isolated = AgentCore(disable_tools=True, track_emotion=False, owner_scope=False, source_channel="avatar_interaction")
                recent = getattr(self.agent(), "history", [])[-6:]
                recent_text = "\n".join(f"{m.get('role')}: {str(m.get('content', ''))[:200]}" for m in recent if isinstance(m, dict))
                actor = str(get_user_name() or "主人")
                prompt = (
                    f"{actor}刚刚对莲心的圆形聊天头像进行了{'拍一拍' if action == 'tap' else '摸一摸'}。"
                    f"请以莲心第一人称，用1到2句自然、口语化的话回应。"
                    "不要提模型、系统、工具或提示词，不要否认这次互动，不要反转动作方向。"
                    f"最近对话上下文：{recent_text}"
                )
                generated = (isolated.chat(prompt, disable_tools=True) or "").strip()
                invalid = ("api", "调用失败", "请求失败", "系统错误", "不要提")
                if generated and not any(marker in generated.lower() for marker in invalid):
                    response = generated[:180]
                    used_fallback = False
            except Exception:
                pass
        try:
            from utils.accompany_stats import AccompanyStats
            if self._avatar_stats is None:
                self._avatar_stats = AccompanyStats()
            self._avatar_stats.record_avatar_detail(
                "user_tap" if action == "tap" else "user_headpat",
                actor="user", target="assistant", source="user", reaction=action,
                sound=sound_ok, llm=not used_fallback, fallback=used_fallback,
                streak=self._avatar_streak, context={"source": "react-chat-avatar"},
            )
            if counter_action:
                self._avatar_stats.record_avatar_detail(
                    "counter_tap" if action == "tap" else "counter_headpat",
                    actor="assistant", target="user", source="counter", reaction=action,
                    streak=self._avatar_streak, context={"source": "react-chat-avatar"},
                )
        except Exception:
            pass
        with self._avatar_lock:
            self._avatar_busy = False
        try:
            history = self.agent().get_history_manager()
            session_id = getattr(self.agent(), "_session_id", None)
            if session_id is not None:
                history.save_message(session_id, "assistant", response)
        except Exception:
            pass
        result = {"action": action, "accepted": True, "sound": sound_ok, "response": response, "counterAction": counter_action}
        if sound_error:
            result["soundError"] = sound_error
        return result

    # ---------- ?????????/??????? QSettings ??? ----------
    def _music_space_wallpapers(self) -> list:
        try:
            from utils.resource_path import get_asset_path
            directory = get_asset_path("\u4e3b\u754c\u9762\u80cc\u666f\u56fe")
        except Exception:
            directory = None
        items = [{"id": "default", "name": "\u9ed8\u8ba4\u58c1\u7eb8", "url": ""}]
        if directory is not None and directory.is_dir():
            allowed = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
            try:
                files = sorted(directory.iterdir(), key=lambda p: p.name.lower())
            except Exception:
                files = []
            for image in files:
                try:
                    if image.is_file() and image.suffix.lower() in allowed:
                        resolved = image.resolve()
                        items.append({"id": str(resolved), "name": image.stem,
                                      "url": "/api/music/wallpaper?path=" + quote(str(resolved), safe="")})
                except Exception:
                    continue
        return items

    def _music_space_settings(self) -> dict:
        try:
            from PyQt5.QtCore import QSettings
            settings = QSettings("Lianxin", "MusicBox")
        except Exception:
            settings = None
        current = str(settings.value("space_wallpaper", "default")) if settings is not None else "default"
        wallpapers = self._music_space_wallpapers()
        known = {item["id"] for item in wallpapers}
        if current != "default" and current not in known:
            p = Path(current)
            if p.is_file():
                wallpapers.append({"id": str(p.resolve()), "name": f"\u81ea\u5b9a\u4e49\uff1a{p.stem}",
                                   "url": "/api/music/wallpaper?path=" + quote(str(p.resolve()), safe="")})
        try:
            opacity = float(settings.value("space_wallpaper_opacity", 0.7)) if settings is not None else 0.7
        except (TypeError, ValueError):
            opacity = 0.75
        try:
            mask = float(settings.value("space_content_mask_opacity", 0.5)) if settings is not None else 0.5
        except (TypeError, ValueError):
            mask = 0.82
        return {
            "wallpapers": wallpapers,
            "settings": {
                "wallpaper": current,
                "wallpaper_opacity": opacity,
                "content_mask_opacity": mask,
                "fit": str(settings.value("space_wallpaper_fit", "cover")) if settings is not None else "cover",
            },
        }

    def save_music_space_settings(self, wallpaper, wallpaper_opacity, content_mask_opacity, fit) -> dict:
        try:
            from PyQt5.QtCore import QSettings
            settings = QSettings("Lianxin", "MusicBox")
        except Exception:
            return self._music_space_settings()
        path = str(wallpaper or "default")
        if path != "default":
            p = Path(path)
            if not p.is_file():
                path = "default"
        settings.setValue("space_wallpaper", path)
        settings.setValue("space_wallpaper_opacity", max(0.0, min(1.0, float(wallpaper_opacity))))
        settings.setValue("space_content_mask_opacity", max(0.0, min(1.0, float(content_mask_opacity))))
        settings.setValue("space_wallpaper_fit", "contain" if str(fit) == "contain" else "cover")
        settings.sync()
        return self._music_space_settings()

    def _proxy_cover_url(self, url: str) -> str:
        """?? URL ??????? URL ?? 8766 ?????Tauri CSP ??????"""
        if not url:
            return ""
        if url.startswith(("file://", "data:", "http://127.0.0.1", "http://localhost")):
            return url
        return "http://127.0.0.1:8766/api/music/cover?url=" + quote(url, safe="")

    def music_state(self) -> dict:
        # Prefer the real 8765 player API; the state file remains a safe fallback.
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=1.5) as response:
                status = json.loads(response.read().decode("utf-8"))
            st = status.get("status") or {}
            playback = status.get("playback") or {}
            listening = status.get("listening") or {}
            lyrics_raw = listening.get("lyrics") if isinstance(listening, dict) else None
            lyrics = []
            if isinstance(lyrics_raw, list):
                for ln in lyrics_raw:
                    if not isinstance(ln, dict):
                        continue
                    txt = str(ln.get("text") or "").strip()
                    if not txt:
                        continue
                    try:
                        t_sec = float(ln.get("time"))
                    except (TypeError, ValueError):
                        t_sec = 0.0
                    lyrics.append({"time": t_sec, "text": txt})
                lyrics.sort(key=lambda x: x["time"])
            style = str(listening.get("style") or "") if isinstance(listening, dict) else ""
            queue_info = {"queue": [], "index": -1, "mode": "sequence"}
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/queue", timeout=1.5) as qr:
                    queue_data = json.loads(qr.read().decode("utf-8"))
                queue_info = queue_data or queue_info
            except Exception:
                pass
            q = queue_info.get("queue") or []
            index = int(queue_info.get("index", -1) or -1)
            mode = str(queue_info.get("mode") or "sequence")
            playlist = []
            for i, t in enumerate(q):
                try:
                    dur = float(t.get("durationMs") or 0) / 1000.0
                except (TypeError, ValueError):
                    dur = 0.0
                playlist.append({
                    "id": t.get("id") or "",
                    "title": t.get("name") or "未知曲目",
                    "artist": t.get("artist") or "",
                    "duration": dur,
                    "index": i,
                })
            duration = float(st.get("duration") or 0)
            if not duration and playback.get("durationMs"):
                duration = float(playback.get("durationMs")) / 1000.0
            placeholder_set = {"纯音乐，请欣赏", "暂无歌词", "纯音乐", "（暂无歌词）"}
            has_lyric = any(x["text"] not in placeholder_set for x in lyrics)
            return {
                "active": bool(st.get("playing")) and not bool(st.get("paused")),
                "playing": bool(st.get("playing")), "paused": bool(st.get("paused")),
                "name": playback.get("name") or "", "title": playback.get("name") or "",
                "artist": playback.get("artist") or "", "album": playback.get("album") or "",
                "coverUrl": self._proxy_cover_url(playback.get("coverUrl") or ""),
                "progress": float(st.get("position") or 0), "duration": duration,
                "volume": float(st.get("volume") or 0), "source": "netease-8765",
                "playlist": playlist, "current_index": index, "mode": mode,
                "lyrics": lyrics, "style": style, "instrumental": not has_lyric,
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

    def _netease_online(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:8765/api/status", timeout=0.8) as resp:
                resp.read()
            return True
        except Exception:
            return False

    def ensure_netease_online(self, timeout: float = 8.0) -> bool:
        """测探 8765 是否在线；不在线时自动拉起 netease-music-mcp 的 Web 播放器。"""
        if self._netease_online():
            return True
        with self._netease_spawn_lock:
            if self._netease_online():
                return True
            try:
                server_js = (
                    Path(__file__).resolve().parent
                    / "参考项目" / "netease-music-mcp-main" / "src" / "server.js"
                )
                cwd = server_js.parent.parent
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    ["node", str(server_js), "--web-player", "--port", "8765"],
                    cwd=str(cwd),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                self._netease_proc = proc
            except Exception as exc:
                print(f"[LianxinBridge] 自动拉起网易云 8765 失败: {exc}", flush=True)
                return False
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._netease_online():
                    return True
                time.sleep(0.3)
            return False

    def music_ensure(self) -> dict:
        return {"online": self.ensure_netease_online(), "url": "http://127.0.0.1:8765/"}

    def music_control(self, action: str, payload: dict | None = None) -> dict:
        import urllib.request
        base = "http://127.0.0.1:8765"
        payload = payload or {}
        if action != "kill-mpv" and not self.ensure_netease_online():
            raise ValueError("网易云播放服务未启动且自动拉起失败，请确认 node 环境")
        if action == "select":
            try:
                index = int(payload.get("index", -1) or -1)
            except (TypeError, ValueError):
                index = -1
            track_id = None
            try:
                with urllib.request.urlopen(base + "/api/queue", timeout=1.5) as qr:
                    queue_data = json.loads(qr.read().decode("utf-8"))
                q = queue_data.get("queue") or []
                if 0 <= index < len(q):
                    track_id = q[index].get("id")
            except Exception:
                track_id = None
            if not track_id:
                raise ValueError("无法定位要播放的曲目")
            route, body = "/api/play-track", {"id": track_id}
        elif action == "seek":
            try:
                seconds = max(0, int(float(payload.get("position") or 0)))
            except (TypeError, ValueError):
                seconds = 0
            route, body = "/api/seek", {"seconds": seconds}
        elif action == "volume":
            try:
                vol = max(0.0, min(1.0, float(payload.get("volume") or 0)))
            except (TypeError, ValueError):
                vol = 0.8
            route, body = "/api/volume", {"volume": int(round(vol * 100))}
        elif action == "mode":
            m = str(payload.get("mode") or "")
            mode = "single" if m == "single" else ("shuffle" if m == "shuffle" else "sequence")
            route, body = "/api/play-mode", {"mode": mode}
        elif action == "kill-mpv":
            try:
                from utils.net_ease_cleanup import stop_netease_mpv
                stop_netease_mpv()
            except Exception as exc:
                print(f"[LianxinBridge] kill mpv failed: {exc}", flush=True)
            return {"active": False, "playing": False, "paused": False, "source": "netease-8765"}
        else:
            routes = {"toggle": "/api/pause", "play": "/api/pause", "pause": "/api/pause", "next": "/api/next", "previous": "/api/prev"}
            route = routes.get(action)
            if not route:
                raise ValueError(f"不支持的音乐操作: {action}")
            body = payload
        request = urllib.request.Request(
            base + route,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Lianxin-Bridge": "1"}, method="POST",
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
            {"id": "camera", "label": "摄像头", "available": True},
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

    def task_events(self, after: int = 0) -> dict:
        """Return incremental workflow/task events for the task center."""
        runs = self.task_snapshot().get("workflowRuns", [])
        items = []
        for run in runs:
            try:
                run_id = int(run.get("id", 0))
            except (TypeError, ValueError):
                continue
            if run_id > int(after):
                items.append({"id": run_id, "type": "workflow", "data": run})
        return {"items": sorted(items, key=lambda item: item["id"]), "latest": max([int(after)] + [item["id"] for item in items])}

    def time_capsule_state(self, day: str = "") -> dict:
        from datetime import date
        from gui.time_capsule.database import TimeCapsuleDatabase
        db = TimeCapsuleDatabase()
        selected = str(day or date.today().isoformat())
        return {"day": db.read_day(selected), "timeline": db.timeline(limit=60), "today": selected}

    def time_capsule_save(self, day: str, content: str) -> dict:
        from gui.time_capsule.database import TimeCapsuleDatabase
        return TimeCapsuleDatabase().save_user_content(str(day), str(content))

    def time_capsule_seal(self, day: str, content: str) -> dict:
        from gui.time_capsule.database import TimeCapsuleDatabase
        db = TimeCapsuleDatabase()
        db.save_user_content(str(day), str(content))
        return db.seal_day(str(day))

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
            if path == "/api/attachments":
                query = parse_qs(urlparse(self.path).query)
                target = Path((query.get("path") or [""])[0]).expanduser().resolve()
                allowed = [Path.home() / ".lianxin" / "images", Path.home() / ".lianxin" / "files"]
                if not any(target == root.resolve() or root.resolve() in target.parents for root in allowed) or not target.is_file():
                    return self._send({"error": "attachment not found"}, 404)
                raw = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw)
                return
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
            if path == "/api/music/ensure":
                return self._send(bridge.music_ensure())
            if path == "/api/music/cover":
                query = parse_qs(urlparse(self.path).query)
                url = (query.get("url") or [""])[0]
                if not url:
                    return self._send({"error": "url required"}, 400)
                try:
                    target = _download_cover(url)
                    raw = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "private, max-age=86400")
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                except Exception as exc:
                    return self._send({"error": str(exc)}, 502)
            if path == "/api/music/space-settings":
                return self._send(bridge._music_space_settings())
            if path == "/api/music/wallpaper":
                query = parse_qs(urlparse(self.path).query)
                raw_path = (query.get("path") or [""])[0]
                try:
                    target = Path(raw_path).expanduser().resolve()
                except Exception:
                    target = None
                if target is not None and target.is_file():
                    try:
                        from utils.resource_path import get_asset_path
                        root = get_asset_path("主界面背景图").resolve()
                    except Exception:
                        root = None
                    allowed = root is not None and (target == root or root in target.parents)
                    if allowed:
                        raw = target.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
                        self.send_header("Content-Length", str(len(raw)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "private, max-age=86400")
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                return self._send({"error": "wallpaper not found"}, 404)
            if path == "/api/music/state":
                return self._send(bridge.music_state())
            if path == "/api/voice/status":
                return self._send(bridge.voice_state())
            if path == "/api/voice/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.voice_events(int((query.get("after") or [0])[0])))
            if path == "/api/capabilities":
                return self._send(bridge.capabilities())
            if path == "/api/tasks/events":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.task_events(int((query.get("after") or [0])[0])))
            if path == "/api/time-capsule/state":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.time_capsule_state((query.get("day") or [""])[0]))
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
            if path == "/api/avatar/action":
                query = parse_qs(urlparse(self.path).query)
                return self._send(bridge.avatar_action((query.get("action") or [""])[0]))
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
            if path == "/api/chat/cancel":
                return self._send(bridge.cancel_chat())
            if path == "/api/chat/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                for event in bridge.chat_stream(
                    str(body.get("message", "")),
                    body.get("attachments") if isinstance(body.get("attachments"), list) else [],
                    str(body.get("forcedTool") or "") or None,
                    str(body.get("preferredTool") or "") or None,
                ):
                    if event.get("type") == "completed":
                        event = {**event, "message": event.get("message", {})}
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return
            if path == "/api/note":
                write_note(str(body.get("content", "")))
                return self._send({"content": read_note()})
            if path == "/api/music/space-settings":
                return self._send(bridge.save_music_space_settings(
                    str(body.get("wallpaper", "default")),
                    body.get("wallpaper_opacity", 0.7),
                    body.get("content_mask_opacity", 0.5),
                    str(body.get("fit", "cover")),
                ))
            if path == "/api/music/control":
                return self._send(bridge.music_control(str(body.get("action", "")), body))
            if path == "/api/voice/start":
                return self._send(bridge.start_voice())
            if path == "/api/voice/stop":
                return self._send(bridge.stop_voice())
            if path == "/api/tts/speak":
                return self._send(bridge.speak(str(body.get("text", "")), str(body.get("voice", ""))))
            if path == "/api/tts/stop":
                return self._send(bridge.stop_speaking())
            if path == "/api/time-capsule/save":
                return self._send(bridge.time_capsule_save(str(body.get("day", "")), str(body.get("content", ""))))
            if path == "/api/time-capsule/seal":
                return self._send(bridge.time_capsule_seal(str(body.get("day", "")), str(body.get("content", ""))))
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
    threading.Thread(target=_cover_cleanup_loop, name="cover-cache-cleanup", daemon=True).start()
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
