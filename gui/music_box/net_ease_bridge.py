"""NetEaseMusicBridge - 莲心面板/音乐空间 <-> 网易云 8765 播放器桥接

仿照 MusicBoxBridge 的 Mode A 嵌入式 + Mode B 沉浸式音乐空间结构
将原本的 pygame 本地播放替换为 netease-music-mcp 的 HTTP 服务
（http://127.0.0.1:8765），控制直连不绕 LLM

特性：
- 内置 QTimer 每 1s 轮询 GET /api/status + /api/queue，
  映射成前端 app.js 认识的 state 并发射 state_changed
- getState() 供 QWebChannel 前端异步拉取
"""
import json
import threading
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from utils.paths import get_user_data_dir

_COVER_CACHE_DIR = get_user_data_dir() / "netease_cover_cache"


class NetEaseMusicBridge(QObject):
    """直连 8765 网易云播放器的桥接"""

    state_changed = pyqtSignal(str)          # 最新 state JSON（推送前端）
    open_space_requested = pyqtSignal()      # 请求打开音乐空间
    close_space_requested = pyqtSignal()     # 请求关闭音乐空间
    minimize_space_requested = pyqtSignal()  # 请求最小化音乐空间
    maximize_space_requested = pyqtSignal()  # 请求最大化/还原音乐空间

    def __init__(self, base_url="http://127.0.0.1:8765", parent=None,
                 space_settings_provider=None, space_settings_saver=None,
                 poll_interval_ms=1000):
        super().__init__(parent)
        self._base_url = base_url.rstrip("/")
        self._space_settings_provider = space_settings_provider
        self._space_settings_saver = space_settings_saver
        self._token = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._queue = []
        self._queue_index = -1
        self._queue_mode = "sequence"
        self._last_state = {}
        self._auto_next_armed = True
        self._auto_next_cooldown_until = 0.0
        self._poll_inflight = False
        self._last_cover_url = None
        self._last_cover_local = ""
        self._last_lyrics = []
        self._last_lyrics_sig = ""
        try:
            _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_once)
        self._poll_timer.start(max(500, int(poll_interval_ms)))

        # 封面缓存定时清理：每小时一轮 + 启动时一次（封面可随时重新下载）
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.timeout.connect(self._cleanup_cover_cache)
        self._cleanup_timer.start(60 * 60 * 1000)
        self._cleanup_cover_cache()

    # ---------- HTTP ----------
    def _headers(self):
        return {
            "x-web-player-client": self._token,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    def _get(self, path, timeout=(0.5, 3.0)):
        if requests is None:
            return None
        try:
            resp = requests.get(self._base_url + path, headers=self._headers(), timeout=timeout)
            if resp.ok:
                return resp.json()
        except Exception:
            return None
        return None

    def _post(self, path, payload=None, timeout=(0.5, 3.0)):
        if requests is None:
            return None
        try:
            resp = requests.post(self._base_url + path, headers=self._headers(),
                                 json=payload or {}, timeout=timeout)
            if resp.ok:
                return resp.json()
        except Exception:
            return None
        return None

    # ---------- 控制直连 8765（不绕 LLM） ----------
    @pyqtSlot()
    def togglePlay(self):
        self._post("/api/pause")

    @pyqtSlot()
    def play(self):
        data = self._get("/api/status")
        st = (data or {}).get("status") or {}
        if st.get("paused"):
            self._post("/api/pause")

    @pyqtSlot()
    def pause(self):
        data = self._get("/api/status")
        st = (data or {}).get("status") or {}
        if st.get("playing") and not st.get("paused"):
            self._post("/api/pause")

    @pyqtSlot()
    def next(self):
        self._post("/api/next")

    @pyqtSlot()
    def previous(self):
        self._post("/api/prev")

    @pyqtSlot(float)
    def seek(self, position):
        try:
            seconds = max(0, int(float(position or 0)))
        except (TypeError, ValueError):
            return
        self._post("/api/seek", {"seconds": seconds})

    @pyqtSlot(float)
    def setVolume(self, volume):
        try:
            vol = max(0.0, min(1.0, float(volume or 0)))
        except (TypeError, ValueError):
            return
        self._post("/api/volume", {"volume": int(round(vol * 100))})

    @pyqtSlot(str)
    def setPlayMode(self, mode):
        server_mode = "shuffle" if str(mode) == "random" else "sequence"
        self._post("/api/play-mode", {"mode": server_mode})

    @pyqtSlot(int)
    def selectTrack(self, index):
        try:
            idx = int(index or 0)
        except (TypeError, ValueError):
            return
        with self._lock:
            if 0 <= idx < len(self._queue):
                track_id = self._queue[idx].get("id")
            else:
                track_id = None
        if track_id:
            self._post("/api/play-track", {"id": track_id})

    @pyqtSlot()
    def toggleFavorite(self):
        # 本地收藏已弃用（由网易云侧处理），保留空实现避免前端报错
        pass

    @pyqtSlot()
    def importMusic(self):
        # 本地导入已弃用（由网易云侧处理），保留空实现避免前端报错
        pass

    @pyqtSlot()
    def manageQuarantine(self):
        # 本地隔离已弃用（由网易云侧处理），保留空实现避免前端报错
        pass

    # ---------- 音乐空间窗口管理 ----------
    @pyqtSlot()
    def openMusicSpace(self):
        self.open_space_requested.emit()

    @pyqtSlot()
    def closeMusicSpace(self):
        self.close_space_requested.emit()

    @pyqtSlot()
    def minimizeMusicSpace(self):
        self.minimize_space_requested.emit()

    @pyqtSlot()
    def maximizeMusicSpace(self):
        self.maximize_space_requested.emit()

    # ---------- 音乐空间左侧工具栏 ----------
    @pyqtSlot()
    def openWebPlayer(self):
        """在默认浏览器打开 Web 网易云播放器（8765）"""
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl("http://127.0.0.1:8765/"))
        except Exception as exc:
            print(f"[网易云] 打开 Web 播放器失败: {exc}", flush=True)

    @pyqtSlot()
    def killMpv(self):
        """杀掉后台 mpv 播放器，并立即向前端推送停止态（不必等轮询）"""
        try:
            from utils.net_ease_cleanup import stop_netease_mpv
            stop_netease_mpv()
        except Exception as exc:
            print(f"[网易云] 停止 mpv 失败: {exc}", flush=True)
        try:
            last = self._last_state or {}
            try:
                volume = max(0.0, min(1.0, float(last.get("volume") or 0.6)))
            except (TypeError, ValueError):
                volume = 0.6
            stopped = {
                "playing": False,
                "current_index": -1,
                "title": "",
                "artist": "",
                "album": "",
                "duration": 0,
                "position": 0,
                "playlist": [],
                "loop_mode": "list",
                "volume": volume,
                "has_playlist": False,
                "error": "",
                "favorite": False,
                "coverUrl": "",
                "space_background": "",
                "wallpaper": "",
                "space_settings": self._space_settings_payload(),
            }
            self.state_changed.emit(json.dumps(stopped, ensure_ascii=False))
        except Exception as exc:
            print(f"[网易云] 推送停止态失败: {exc}", flush=True)

    # ---------- 封面下载缓存到本地（绕开 LocalContentCanAccessRemoteUrls=False） ----------
    def _local_cover_url(self, remote_url):
        if not remote_url:
            return ""
        if remote_url.startswith(("file://", "data:", "http://127.0.0.1", "http://localhost")):
            return remote_url
        if remote_url == self._last_cover_url and self._last_cover_local:
            return self._last_cover_local
        try:
            import hashlib
            digest = hashlib.md5(remote_url.encode("utf-8")).hexdigest()[:16]
            target = _COVER_CACHE_DIR / (digest + ".jpg")
            if not target.exists():
                resp = requests.get(remote_url, timeout=6)
                if resp.ok and resp.content:
                    target.write_bytes(resp.content)
            if target.exists():
                self._last_cover_url = remote_url
                self._last_cover_local = target.resolve().as_uri()
                return self._last_cover_local
        except Exception:
            pass
        return ""

    # ---------- 封面缓存定时清理 ----------
    def _cleanup_cover_cache(self):
        """删除 mtime 超 7 天或超过 100 张的封面缓存（封面可随时重新下载）。"""
        try:
            if not _COVER_CACHE_DIR.exists():
                return
            entries = []
            for child in _COVER_CACHE_DIR.iterdir():
                try:
                    if child.is_file():
                        entries.append((child.stat().st_mtime, child))
                except Exception:
                    continue
            if not entries:
                return
            entries.sort(key=lambda e: e[0], reverse=True)  # 最新在前
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
            print(f"[网易云] 封面缓存清理失败: {exc}", flush=True)

    # ---------- 状态收集与轮询 ----------
    def collect_state(self) -> dict:
        """从 8765 拉取状态并映射成前端 state"""
        try:
            status_data = self._get("/api/status") or {}
            queue_data = self._get("/api/queue") or {}
        except Exception:
            status_data, queue_data = {}, {}
        st = status_data.get("status") or {}
        playback = status_data.get("playback") or {}
        listening = status_data.get("listening") or {}
        # 从 /api/status 的 listening 透传歌词/曲风/纯音乐标记（8765 与桥接共用同一数据源）
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
        sig = "|".join("{:.2f}:{}".format(x["time"], x["text"]) for x in lyrics)
        if sig != self._last_lyrics_sig:
            self._last_lyrics = lyrics
            self._last_lyrics_sig = sig
        style = str(listening.get("style") or "") if isinstance(listening, dict) else ""
        placeholder_set = {"纯音乐，请欣赏", "暂无歌词", "纯音乐", "（暂无歌词）"}
        has_lyric = any(x["text"] not in placeholder_set for x in self._last_lyrics)
        instrumental = not has_lyric
        q = queue_data.get("queue") if isinstance(queue_data.get("queue"), list) else []
        index = int(queue_data.get("index", -1) or -1)
        mode = str(queue_data.get("mode", "sequence"))

        with self._lock:
            self._queue = q
            self._queue_index = index
            self._queue_mode = mode

        playing = bool(st.get("playing")) and not bool(st.get("paused"))
        position = float(st.get("position") or 0)
        duration = float(st.get("duration") or 0)
        if not duration and playback.get("durationMs"):
            duration = float(playback.get("durationMs")) / 1000.0
        volume = float(st.get("volume") or 80)
        if volume > 1.0:
            volume = volume / 100.0

        playlist_items = []
        for i, t in enumerate(q):
            dur = float(t.get("durationMs") or 0) / 1000.0
            playlist_items.append({
                "title": t.get("name") or "未知曲目",
                "duration": dur,
                "index": i,
                "favorite": False,
            })

        state = {
            "playing": playing,
            "current_index": index,
            "title": playback.get("name") or "",
            "artist": playback.get("artist") or "",
            "album": playback.get("album") or "",
            "duration": duration,
            "position": position,
            "playlist": playlist_items,
            "loop_mode": "random" if mode == "shuffle" else "list",
            "volume": max(0.0, min(1.0, volume)),
            "has_playlist": bool(q),
            "error": "",
            "favorite": False,
            "coverUrl": self._local_cover_url(playback.get("coverUrl") or ""),
            "lyrics": self._last_lyrics,
            "style": style,
            "instrumental": instrumental,
            "space_background": "",
            "wallpaper": "",
            "space_settings": self._space_settings_payload(),
        }
        self._last_state = state
        return state

    def _space_settings_payload(self):
        try:
            if self._space_settings_provider is None:
                return None
            return self._space_settings_provider() or {}
        except Exception:
            return None

    def _maybe_auto_next(self, state):
        """When close to the end of the current track, auto-advance to the next one.

        Mirrors the 8765 page's autoAdvance, but triggered from this bridge's 1s
        poll loop so the LianXin panel can keep the queue flowing even when the
        8765 page is not open. Uses the same armed/cooldown debounce pattern and
        flags the request with auto:true so the server dedups against the page.
        """
        try:
            playing = bool(state.get("playing"))
            duration = float(state.get("duration") or 0)
            position = float(state.get("position") or 0)
            if not playing or duration <= 0:
                return
            now = time.monotonic()
            if position < duration - 1:
                self._auto_next_armed = True
            if (self._auto_next_armed
                    and now >= self._auto_next_cooldown_until
                    and position >= duration - 0.5):
                self._auto_next_armed = False
                self._auto_next_cooldown_until = now + 5.0
                self._post("/api/next", {"auto": True})
        except Exception as exc:
            print(f"[网易云] auto-next failed: {exc}")

    def _poll_once(self):
        """Schedule one poll on a background thread (never blocks the GUI thread)."""
        if self._poll_inflight:
            return
        self._poll_inflight = True
        threading.Thread(target=self._poll_worker, name="netease-poll", daemon=True).start()

    def _poll_worker(self):
        try:
            state = self.collect_state()
            self._maybe_auto_next(state)
            payload = json.dumps(state, ensure_ascii=False)
            self.state_changed.emit(payload)
        except Exception as exc:
            print(f"[netease] poll failed: {exc}")
        finally:
            self._poll_inflight = False

    # ---------- 供 QWebChannel 前端调用 ----------
    @pyqtSlot(result=str)
    def getState(self):
        try:
            return json.dumps(self.collect_state(), ensure_ascii=False)
        except Exception as exc:
            print(f"[网易云] getState failed: {exc}")
            return json.dumps(self._last_state or {}, ensure_ascii=False)

    @pyqtSlot(result=str)
    def getSpaceSettings(self):
        try:
            data = self._space_settings_payload()
            return json.dumps(data or {}, ensure_ascii=False)
        except Exception as exc:
            print(f"[网易云] getSpaceSettings failed: {exc}")
            return "{}"

    @pyqtSlot(str, float, float, str, result=str)
    def saveSpaceSettings(self, wallpaper, wallpaper_opacity, content_mask_opacity, fit):
        try:
            if self._space_settings_saver is None:
                return "{}"
            data = self._space_settings_saver(
                wallpaper, wallpaper_opacity, content_mask_opacity, fit) or {}
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            print(f"[网易云] saveSpaceSettings failed: {exc}")
            return "{}"

    def shutdown(self):
        try:
            self._poll_timer.stop()
            self._cleanup_timer.stop()
        except Exception:
            pass
