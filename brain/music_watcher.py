# brain/music_watcher.py
"""MusicWatcher：监听本机网易云播放状态（.listening-state.json）。

用户通过 netease-music-mcp 的 Web 播放器/点歌播放音乐后，该服务在后台轮询
状态文件：检测到新歌开始，等它稳定播放 10~20 秒（默认 15s）后再根据当前
歌曲信息/歌词调用 LLM 生成一句莲心风格的听歌反馈，通过 on_feedback 回调
推送给界面（仿 heartbeat 的主动消息通道）。快速切歌/被跳过的歌不会评论。
停止播放时状态文件会被删除，watcher 据此判断"没有在听歌"。
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from utils.paths import get_netease_state_file, get_user_data_dir

logger = logging.getLogger("MusicWatcher")

# netease-music-mcp 写入的播放状态文件（路径可配置，见 utils.paths.get_netease_state_file）
_DEFAULT_STATE_FILE = get_netease_state_file()

_MUSIC_SYSTEM = """你是莲心，正和用户一起听歌。根据当前歌曲和歌词，用你自己的性格（口语化、短句、可以调侃或吐槽、偶尔用颜文字）说一句 20~50 字的听歌感想或评论，像朋友在旁边一起听一样自然。

要求：
- 围绕歌词、曲风或歌手的某个细节聊，不要复述歌名/歌手本身，不要写成报告。
- 禁止 AI 腔：不说"这首歌展现了""希望你喜欢""如果需要""总之"。
- 不要说"需要帮忙吗""要不要我..."这类服务性话语。
- 如果这首歌没什么好说的，只回复 EMPTY。"""


class MusicWatcher:
    _instance_lock = threading.RLock()
    _active_instance = None
    _process_lock_handle = None

    def __init__(
        self,
        state_file: Path = None,
        on_feedback: Callable[[str], None] = None,
        poll_interval: float = 3.0,
        feedback_delay: float = 15.0,
        listen_min_seconds: float = 10.0,
        min_interval_seconds: float = 120.0,
        enabled_check: Callable[[], bool] = None,
        state_provider: Callable[[], Optional[dict]] = None,
        on_status: Callable[[str, dict], None] = None,
        tracker: object = None,
        stats: object = None,
        song_cooldown_seconds: float = 600.0,
        on_feedback_meta: Callable[[str, dict], None] = None,
    ):
        self._state_file = Path(state_file or _DEFAULT_STATE_FILE)
        self._on_feedback = on_feedback
        self._on_feedback_meta = on_feedback_meta
        self._poll_interval = poll_interval
        self._feedback_delay = feedback_delay
        self._listen_min = listen_min_seconds
        self._min_interval = min_interval_seconds
        self._enabled_check = enabled_check or (lambda: True)
        self._state_provider = state_provider
        self._on_status = on_status
        self._tracker = tracker
        self._stats = stats
        self._song_cooldown = max(0.0, float(song_cooldown_seconds or 0.0))
        self._status = "idle"
        self._last_key: Optional[tuple] = None
        self._pending_key: Optional[tuple] = None
        self._pending_since: float = 0.0
        self._song_started_at: float = 0.0
        self._consecutive_skips: int = 0
        self._last_feedback_at: float = 0.0
        self._reported: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._instance_lock:
            if self._active_instance is not None and self._active_instance is not self:
                logger.info("[MusicWatcher] 已有实例运行，跳过重复启动")
                return
            if not self._acquire_process_lock():
                logger.info("[MusicWatcher] 其他进程已有实例运行，跳过重复启动")
                return
            self._active_instance = self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="music-watcher", daemon=True
        )
        self._thread.start()
        print("[MusicWatcher] 已启动，监听 " + str(self._state_file))
        logger.info("[MusicWatcher] 已启动，监听 %s", self._state_file)

    def stop(self) -> None:
        self._stop.set()
        with self._instance_lock:
            if self._active_instance is self:
                self._active_instance = None
            self._release_process_lock()

    @classmethod
    def _acquire_process_lock(cls) -> bool:
        if cls._process_lock_handle is not None:
            return True
        try:
            import msvcrt
            path = get_user_data_dir() / "music_watcher.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return False
            cls._process_lock_handle = handle
            return True
        except Exception:
            return True

    @classmethod
    def _release_process_lock(cls) -> None:
        handle = cls._process_lock_handle
        cls._process_lock_handle = None
        if handle is None:
            return
        try:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning("[MusicWatcher] 轮询异常: %s", exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        state = self._read_state()
        if self._tracker is not None:
            try:
                self._tracker.tick(state or {})
            except Exception as exc:
                logger.debug("[MusicWatcher] 播放时长记录失败: %s", exc)
        key = self._key_of(state)
        now = time.time()
        if key and bool(state.get("active")):
            if key != self._last_key:
                prev_key = self._last_key
                self._last_key = key
                # 连续切歌计数：上一首从未被评论过 => 被用户跳过
                if prev_key is not None and prev_key not in self._reported:
                    self._consecutive_skips += 1
                    self._emit_status("skipped", state)
                else:
                    self._consecutive_skips = 0
                # 新歌统一进入"试听门槛"流程：切歌即取消上一首的 pending（跳过的歌不评论）
                self._pending_key = key
                self._pending_since = now
                self._song_started_at = now
                self._emit_status("waiting", state)
                print("[MusicWatcher] 检测到新歌: " + str(state.get("name")) + " id=" + str(state.get("id")))
            if key in self._reported:
                self._pending_key = None
            elif (self._pending_key == key
                    and now - self._pending_since >= self._feedback_delay
                    and self._played_seconds(state, now) >= self._listen_min
                    and now - self._last_feedback_at >= self._min_interval
                    and self._enabled_check()):
                self._pending_key = None
                if self._feedback_blocked(state):
                    self._emit_status("cooldown", state)
                    print("[MusicWatcher] 该曲已反馈或在按曲冷却期内，跳过", flush=True)
                else:
                    self._emit_status("analyzing", state)
                    self._fire(state, rapid_skips=self._consecutive_skips)
        elif not key:
            self._last_key = None
            self._pending_key = None
            self._emit_status("idle", state or {})

    def _emit_status(self, status: str, state: dict) -> None:
        if status == self._status:
            return
        self._status = status
        if self._on_status:
            try:
                self._on_status(status, dict(state or {}))
            except Exception as exc:
                logger.debug("[MusicWatcher] 状态回调失败: %s", exc)

    def _key_of(self, state: Optional[dict]) -> Optional[tuple]:
        if not state:
            return None
        sid = state.get("id") or state.get("songId") or state.get("trackId")
        if not sid:
            sid = state.get("name") or state.get("title")
        if not sid:
            return None
        session_id = self._session_id(state) if self._tracker is not None else None
        return (str(sid), bool(state.get("active")), str(session_id or ""))

    def _read_state(self) -> Optional[dict]:
        if self._state_provider is not None:
            try:
                state = self._state_provider()
                if state and (state.get("active") or state.get("source") or state.get("playing")):
                    return self._normalize_state(state)
            except Exception as exc:
                logger.debug("[MusicWatcher] 状态提供器读取失败，回退状态文件: %s", exc)
        try:
            if not self._state_file.exists():
                return None
            return self._normalize_state(json.loads(self._state_file.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("[MusicWatcher] 读取状态失败: %s", exc)
            return None

    @staticmethod
    def _normalize_state(state: dict) -> dict:
        """Normalize the 8765 API state and the legacy state-file shape."""
        result = dict(state)
        result.setdefault("name", result.get("title") or "")
        result.setdefault("title", result.get("name") or "")
        result.setdefault("id", result.get("songId") or result.get("trackId") or result.get("name"))
        result.setdefault("firstLyrics", result.get("lyrics") or [])
        result.setdefault("wiki", {})
        if "active" not in result:
            result["active"] = bool(result.get("playing")) and not bool(result.get("paused"))
        return result

    @staticmethod
    def _track_id_of(state: dict) -> tuple:
        state = state or {}
        current = state.get("currentTrack") if isinstance(state.get("currentTrack"), dict) else {}
        source = str(state.get("source") or "netease").split("-", 1)[0] or "netease"
        track_id = str(
            state.get("id") or state.get("songId") or current.get("id") or ""
        ).strip()
        if not track_id:
            track_id = str(
                state.get("name") or state.get("title") or current.get("title") or ""
            ).strip()
        return source, track_id

    def _session_id(self, state: dict):
        if self._tracker is None:
            return None
        try:
            return self._tracker.current_session_id(state)
        except Exception:
            return None

    def _feedback_blocked(self, state: dict) -> bool:
        """同一次播放会话已反馈，或该曲仍在按曲冷却期内 => 不再反馈。"""
        store = self._stats
        if store is None:
            return False
        source, track_id = self._track_id_of(state)
        if not track_id:
            return False
        session_id = self._session_id(state)
        try:
            if session_id and store.has_feedback(
                    source=source, track_id=track_id, session_id=session_id):
                return True
            last = store.last_feedback_time(source=source, track_id=track_id)
        except Exception:
            return False
        if last is not None and self._song_cooldown > 0:
            if (time.time() - last) < self._song_cooldown:
                return True
        return False

    def _fire(self, state: dict, force: bool = False, rapid_skips: int = 0) -> None:
        # 与主对话错峰：主对话请求进行中时延后反馈，避免抢占中转站单并发。
        from brain.llm_gate import main_request_active
        if main_request_active():
            self._pending_key = self._key_of(state)
            self._pending_since = time.time()
            self._emit_status("busy", state)
            print("[MusicWatcher] 主对话进行中，暂缓听歌反馈", flush=True)
            return
        text = self._generate_feedback(state, rapid_skips=rapid_skips)
        if not text and force:
            # 手动切歌保底：LLM 失败/EMPTY 也保证给一句反馈，避免"反馈为空，跳过"
            name = state.get("name") or "未知歌曲"
            style = state.get("style") or ""
            first = state.get("firstLyrics") or []
            lyric_text = (
                " / ".join(str(x) for x in first[:4])
                if isinstance(first, list)
                else str(first or "（暂无歌词）")
            )
            text = self._fallback_feedback(name, style, lyric_text)
        if text:
            self._last_feedback_at = time.time()
            self._reported.add(self._key_of(state))
            source, track_id = self._track_id_of(state)
            session_id = self._session_id(state)
            meta = {
                "source": source,
                "track_id": track_id,
                "session_id": session_id,
                "name": state.get("name") or state.get("title") or "",
            }
            if self._stats is not None and track_id:
                try:
                    self._stats.record_feedback(
                        source=source, track_id=track_id,
                        name=meta["name"], content=text,
                        session_id=session_id or str(int(time.time() * 1000)),
                    )
                except Exception as exc:
                    logger.debug("[MusicWatcher] 反馈落库失败: %s", exc)
            print("[MusicWatcher] 听歌反馈: " + str(text))
            if self._on_feedback_meta is not None:
                try:
                    self._on_feedback_meta(text, meta)
                except Exception as exc:
                    logger.debug("[MusicWatcher] 反馈回调失败: %s", exc)
            elif self._on_feedback:
                self._on_feedback(text)
            self._emit_status("ready", state)
        else:
            logger.info("[MusicWatcher] 本轮未生成反馈，等待后续轮询")
            print("[MusicWatcher] 本轮未生成反馈，等待后续轮询")

    def _generate_feedback(self, state: dict, rapid_skips: int = 0) -> Optional[str]:
        try:
            name = state.get("name") or "未知歌曲"
            artist = state.get("artist") or "未知歌手"
            album = state.get("album") or ""
            style = state.get("style") or ""
            first = state.get("firstLyrics") or []
            if isinstance(first, list):
                lyric_text = " / ".join(str(x) for x in first[:6])
            else:
                lyric_text = str(first or "（暂无歌词）")
            wiki = state.get("wiki") or {}
            genre = ""
            if isinstance(wiki, dict):
                if wiki.get("genre"):
                    genre = str(wiki["genre"])
                elif isinstance(wiki.get("genres"), list):
                    genre = "、".join(str(x) for x in wiki["genres"][:3])
            played = int(self._played_seconds(state, time.time()))
            rapid_hint = ""
            if rapid_skips >= 3:
                rapid_hint = (
                    "\n（额外提示：用户刚刚快速连续切换了至少 " + str(rapid_skips) + " 首歌，最后停在这首。"
                    "可以轻松调侃一句这种选歌过程，语气自然友好、别指责、别连续反问，也可以选择不提。）"
                )

            import litellm
            from config import get_api_config, get_user_name, normalize_model_for_litellm
            from brain.persona.runtime import (
                capture_persona_snapshot, compose_scene_prompt,
            )

            api_cfg = get_api_config()
            model = normalize_model_for_litellm(
                api_cfg.get("model", "deepseek-v4-flash"),
                api_cfg.get("base_url", ""),
            )
            snapshot = capture_persona_snapshot()
            system_text = compose_scene_prompt(
                _MUSIC_SYSTEM,
                user_name=get_user_name(),
                snapshot=snapshot,
                scene="proactive",
            )
            user_text = (
                f"当前歌曲：{name}\n歌手：{artist}\n专辑：{album}\n曲风：{style}"
                + (f"\n类型标签：{genre}" if genre else "")
                + f"\n已播放：约{played}秒\n接下来几句歌词：{lyric_text}"
                + rapid_hint
            )
            response = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                api_key=api_cfg["api_key"],
                api_base=api_cfg["base_url"],
                temperature=0.7,
                max_tokens=120,
                timeout=30,
            )
            raw = (response.choices[0].message.content or "").strip()
            if not raw or raw.upper() == "EMPTY":
                logger.info("[MusicWatcher] 模型返回 EMPTY，使用保底反馈")
                return self._fallback_feedback(name, style, lyric_text)
            return raw
        except Exception as exc:
            logger.warning("[MusicWatcher] LLM 生成反馈失败，将在后续轮询重试: %s", exc)
            print("[MusicWatcher] LLM 生成反馈失败，将在后续轮询重试: " + str(exc))
            return None

    def _played_seconds(self, state: dict, now: float) -> float:
        """当前歌曲已连续播放的秒数（优先用服务端 startedAt，缺省按本地检测时间估算）。"""
        if self._tracker is not None:
            try:
                value = self._tracker.current_seconds(state)
                if value is not None:
                    return float(value)
            except Exception:
                pass
        started = state.get("startedAt")
        if started:
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return max(0.0, now - dt.timestamp())
            except Exception:
                pass
        progress = state.get("progress")
        if progress is not None:
            try:
                return max(0.0, float(progress))
            except (TypeError, ValueError):
                pass
        return max(0.0, now - self._song_started_at)

    @staticmethod
    def _fallback_feedback(name: str, style: str, lyric_text: str) -> str:
        """为纯音乐或稀疏歌词保留一条简短的主动反馈。"""
        if lyric_text and lyric_text not in {"纯音乐，请欣赏", "（暂无歌词）"}:
            return f"《{name}》这段旋律挺有画面感，先安静听一会儿。"
        if style:
            return f"《{name}》的{style}很适合当下这段时间，先陪你听着。"
        return f"《{name}》是纯音乐，旋律先替我们把气氛撑住了。"
