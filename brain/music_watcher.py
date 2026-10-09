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
import random
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from utils.paths import get_netease_state_file, get_user_data_dir
from brain.music_lyrics import clean_lines, format_for_prompt, is_instrumental

logger = logging.getLogger("MusicWatcher")

# netease-music-mcp 写入的播放状态文件（路径可配置，见 utils.paths.get_netease_state_file）
_DEFAULT_STATE_FILE = get_netease_state_file()

_MUSIC_SYSTEM = """你是莲心，正和用户一起听歌。根据当前歌曲和歌词，用你自己的性格（口语化、短句、可以调侃或吐槽、偶尔用颜文字）说一句 20~50 字的听歌感想或评论，像朋友在旁边一起听一样自然。

要求：
- 围绕歌词、曲风或歌手的某个细节聊，不要复述歌名/歌手本身，不要写成报告。
- 禁止 AI 腔：不说"这首歌展现了""希望你喜欢""如果需要""总之"。
- 不要说"需要帮忙吗""要不要我..."这类服务性话语。
- 如果是纯音乐或没有可用歌词，就别谈歌词，聊编曲、乐器、氛围或情绪。
- 默认不要写问句；只有用户刚发过消息、你们正在对话时，才可以问一句。
- 如果这首歌没什么好说的，只回复 EMPTY。"""

# 输出不合格时的纠错提示：首次不合格会带着它重试一次。
_RETRY_HINTS = {
    "truncated": "刚才那句说到一半就断了。重新用一句完整的话说，20~50 字，结尾要有标点。",
    "dangling": "刚才那句没收尾。重新用一句完整的话说，20~50 字，结尾要有标点。",
    "question": "刚才是问句，但此刻不该向用户提问。改成一句陈述句，不要出现问号。",
}

# 以这些词结尾说明话没说完（截断/断句），不要直接发出去。
_DANGLING_RE = re.compile(
    r"(地|得|和|跟|与|把|被|让|给|而|却|然后|而且|以及|或者|因为|所以|如果|不过|只是|"
    r"虽然|于是|接着|比如|像|居然|竟然|简直|甚至|尤其|除了|为了|要是|万一|省得|并且|"
    r"反正|毕竟|到底|关于|对于|随着|通过|等到|即使|哪怕)$"
)

# 问句判定：出现问号，或以"吗"收尾。
_QUESTION_RE = re.compile(r"[?？]|吗[。！…~～]?$")


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
        user_active_check: Callable[[], bool] = None,
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
        self._user_active_check = user_active_check
        self._recent: list = []          # 最近几条反馈（曲名 + 说过的话），供下一句参考
        self._last_template: str = ""
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
                    # analyzing 改由 _fire 在真正发起请求前推送：原先在起跑线就推，
                    # 界面会先显示"正在整理感受"再干等（最长 30s 超时）才出结果。
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
        # firstLyrics 一律保持"字符串列表"语义；结构化歌词走 lyric_lines，
        # 避免把 [{"time":..,"text":..}] 直接 str() 拼进 prompt（曾导致模型朗读字典）。
        lyric_lines = clean_lines(result.get("lyrics") or result.get("firstLyrics") or [])
        result["lyric_lines"] = lyric_lines
        result["firstLyrics"] = [entry["text"] for entry in lyric_lines[:4]]
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
        self._emit_status("analyzing", state)
        text = self._generate_feedback(state, rapid_skips=rapid_skips)
        if not text and force:
            # 手动切歌保底：LLM 失败/EMPTY 也保证给一句反馈，避免"反馈为空，跳过"
            name = state.get("name") or "未知歌曲"
            style = state.get("style") or ""
            text = self._fallback_feedback(name, style, self._is_instrumental_state(state))
        if text:
            self._last_feedback_at = time.time()
            self._reported.add(self._key_of(state))
            self._remember_feedback(state, text)
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
            now = time.time()
            lyric_lines = self._lyric_lines_of(state)
            instrumental = bool(state.get("instrumental")) or is_instrumental(lyric_lines)
            position = self._playback_position(state)
            duration = self._duration_seconds(state)
            lyric_block = "" if instrumental else format_for_prompt(lyric_lines, position)
            wiki = state.get("wiki") or {}
            genre = ""
            if isinstance(wiki, dict):
                if wiki.get("genre"):
                    genre = str(wiki["genre"])
                elif isinstance(wiki.get("genres"), list):
                    genre = "、".join(str(x) for x in wiki["genres"][:3])
            played = int(self._played_seconds(state, now))
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
            style_line = f"\n平台标签（可能不准，不确定就别猜曲风）：{style}" if style else ""
            progress_line = ""
            if duration > 0:
                ratio = max(0.0, min(100.0, position / duration * 100.0))
                progress_line = f"\n播放进度：约{int(position)}秒 / 共{int(duration)}秒（{ratio:.0f}%）"
            if instrumental:
                lyric_section = "这首歌没有人声或没有可用歌词：别谈歌词内容，聊编曲、乐器、氛围或情绪。"
            elif lyric_block:
                lyric_section = "以下是按当前播放进度取的几句歌词（→ 标的是正在唱的那句）：\n" + lyric_block
            else:
                lyric_section = "这首歌暂时取不到可用歌词，不要编造歌词内容。"
            user_text = (
                f"当前歌曲：{name}\n歌手：{artist}\n专辑：{album}"
                + style_line
                + (f"\n类型标签：{genre}" if genre else "")
                + f"\n本次已听：约{played}秒" + progress_line
                + "\n" + lyric_section
                + rapid_hint
            )
            messages = [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text + self._history_hint(state)},
            ]
            allow_question = self._user_recently_active()
            last_reason = ""
            for attempt in (1, 2):
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    api_key=api_cfg["api_key"],
                    api_base=api_cfg["base_url"],
                    temperature=0.7,
                    max_tokens=200,
                    timeout=30,
                )
                choice = response.choices[0]
                raw = (choice.message.content or "").strip()
                ok, reviewed, reason = self._review_feedback(
                    raw, getattr(choice, "finish_reason", "") or "", allow_question,
                )
                if ok:
                    return reviewed
                last_reason = reason
                if reason == "empty":
                    logger.info("[MusicWatcher] 模型返回 EMPTY，使用保底反馈")
                    break
                if attempt == 1:
                    logger.info("[MusicWatcher] 听歌反馈不合格(%s)，带纠错提示重试一次", reason)
                    messages = messages + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": _RETRY_HINTS.get(reason, _RETRY_HINTS["dangling"])},
                    ]
            logger.info("[MusicWatcher] 听歌反馈不可用(%s)，改用保底反馈", last_reason or "empty")
            return self._fallback_feedback(name, style, instrumental)
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
        # 注意：state["position"]/["progress"] 是"播放位置"而不是"已听时长"，
        # 跳转/续播时会失真（曾出现"才十三秒"的错判），这里不再拿它兜底。
        if self._song_started_at <= 0:
            # watcher 刚启动、还没记录到新歌起点时不要返回 epoch 级的天文数字。
            return 0.0
        return max(0.0, now - self._song_started_at)

    @staticmethod
    def _lyric_lines_of(state: dict) -> list:
        lines = state.get("lyric_lines")
        if lines is None:
            lines = clean_lines(state.get("lyrics") or state.get("firstLyrics") or [])
        return list(lines or [])

    @classmethod
    def _is_instrumental_state(cls, state: dict) -> bool:
        if state.get("instrumental") is not None:
            return bool(state.get("instrumental"))
        return is_instrumental(cls._lyric_lines_of(state))

    @staticmethod
    def _playback_position(state: dict) -> float:
        """歌曲播放位置（秒），用来挑"即将唱到"的歌词。"""
        for key in ("position", "progress"):
            value = state.get(key)
            if value is None:
                continue
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _duration_seconds(state: dict) -> float:
        try:
            return max(0.0, float(state.get("duration") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _user_recently_active(self) -> bool:
        """用户是否刚跟莲心说过话（决定这条听歌反馈能不能提问）。"""
        if self._user_active_check is None:
            return False
        try:
            return bool(self._user_active_check())
        except Exception:
            return False

    def _remember_feedback(self, state: dict, text: str) -> None:
        """记住最近几条反馈，供下一句保持前后一致（避免自相矛盾/重复）。"""
        _, track_id = self._track_id_of(state)
        self._recent.append({
            "track_id": track_id,
            "name": state.get("name") or state.get("title") or "",
            "text": str(text or "").strip(),
            "at": time.time(),
        })
        del self._recent[:-3]

    def _history_hint(self, state: dict) -> str:
        """把最近说过的话拼成上下文：同一首别反复说，也别跟前一首矛盾。"""
        if not self._recent:
            return ""
        _, track_id = self._track_id_of(state)
        same = None
        previous = None
        for item in reversed(self._recent):
            if track_id and item.get("track_id") == track_id:
                if same is None:
                    same = item
            elif previous is None:
                previous = item
            if same is not None and previous is not None:
                break
        parts = []
        if same is not None:
            parts.append("这首歌你之前评论过：“" + same["text"] + "”。换个角度说，别重复同样的说法，也别自相矛盾。")
        if previous is not None:
            parts.append("你上一首在听《" + str(previous.get("name") or "") + "》，你当时说：“" + previous["text"] + "”。")
        if not parts:
            return ""
        return "\n（上下文：" + " ".join(parts) + "）"

    @classmethod
    def _review_feedback(cls, raw: str, finish_reason: str, allow_question: bool) -> tuple:
        """检查模型输出是否可用，返回 (是否合格, 文本, 不合格原因)。"""
        text = str(raw or "").strip()
        if not text or text.upper() == "EMPTY":
            return False, "", "empty"
        if str(finish_reason or "").strip().lower() == "length":
            return False, text, "truncated"
        if not allow_question and _QUESTION_RE.search(text):
            return False, text, "question"
        if _DANGLING_RE.search(text):
            return False, text, "dangling"
        return True, cls._cap_length(text), ""

    @staticmethod
    def _cap_length(text: str, limit: int = 140) -> str:
        """过长的输出截到最后一个句末标点，别把半句发出去。"""
        if len(text) <= limit:
            return text
        window = text[:limit]
        cut = max(window.rfind(mark) for mark in "。！？…~～")
        return window[:cut + 1] if cut > 0 else window

    def _fallback_feedback(self, name: str, style: str, instrumental: bool = False) -> str:
        """LLM 连续失败时的保底反馈；同一句模板不连着用两次。"""
        if instrumental:
            options = [
                f"《{name}》没有词，旋律自己把气氛撑住了，我跟着听。",
                f"《{name}》是纯器乐，{style}那点底色挺贴当下。" if style else f"《{name}》是纯器乐，我跟着听一会儿。",
                f"《{name}》没歌词可聊，那就只听编排了，挺耐听的。",
            ]
        else:
            options = [
                f"《{name}》这段旋律挺有画面感，先安静听一会儿。",
                f"《{name}》的{style}配着这些词，先陪你听着。" if style else f"《{name}》配着这些词，先陪你听着。",
                f"《{name}》我先不说话，跟着听一段。",
            ]
        options = [opt for opt in options if opt and opt != self._last_template] or options
        picked = random.choice(options)
        self._last_template = picked
        return picked
