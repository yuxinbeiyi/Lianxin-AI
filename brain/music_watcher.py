# brain/music_watcher.py
"""MusicWatcher：监听本机网易云播放状态（.listening-state.json）。

用户通过 netease-music-mcp 的 Web 播放器/点歌播放音乐后，该服务在后台轮询
状态文件：检测到新歌开始，等几秒后根据当前歌词调用 LLM 生成一句莲心风格的
听歌反馈，通过 on_feedback 回调推送给界面（仿 heartbeat 的主动消息通道）。
停止播放时状态文件会被删除，watcher 据此判断"没有在听歌"。
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("MusicWatcher")

# netease-music-mcp 写入的播放状态文件（相对莲心项目根目录）
_DEFAULT_STATE_FILE = (
    Path(__file__).resolve().parent.parent
    / "参考项目" / "netease-music-mcp-main" / ".listening-state.json"
)

_MUSIC_SYSTEM = """你是莲心，正和用户一起听歌。根据当前歌曲和歌词，用你自己的性格（口语化、短句、可以调侃或吐槽、偶尔用颜文字）说一句 20~50 字的听歌感想或评论，像朋友在旁边一起听一样自然。

要求：
- 围绕歌词、曲风或歌手的某个细节聊，不要复述歌名/歌手本身，不要写成报告。
- 禁止 AI 腔：不说"这首歌展现了""希望你喜欢""如果需要""总之"。
- 不要说"需要帮忙吗""要不要我..."这类服务性话语。
- 如果这首歌没什么好说的，只回复 EMPTY。"""


class MusicWatcher:
    def __init__(
        self,
        state_file: Path = None,
        on_feedback: Callable[[str], None] = None,
        poll_interval: float = 3.0,
        feedback_delay: float = 5.0,
        min_interval_seconds: float = 120.0,
        enabled_check: Callable[[], bool] = None,
    ):
        self._state_file = Path(state_file or _DEFAULT_STATE_FILE)
        self._on_feedback = on_feedback
        self._poll_interval = poll_interval
        self._feedback_delay = feedback_delay
        self._min_interval = min_interval_seconds
        self._enabled_check = enabled_check or (lambda: True)
        self._last_key: Optional[tuple] = None
        self._pending_key: Optional[tuple] = None
        self._pending_since: float = 0.0
        self._last_feedback_at: float = 0.0
        self._reported: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="music-watcher", daemon=True
        )
        self._thread.start()
        print("[MusicWatcher] 已启动，监听 " + str(self._state_file))
        logger.info("[MusicWatcher] 已启动，监听 %s", self._state_file)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning("[MusicWatcher] 轮询异常: %s", exc)
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        state = self._read_state()
        key = self._key_of(state)
        trigger = str(state.get("trigger") or "") if state else ""
        now = time.time()
        if key and bool(state.get("active")):
            if key != self._last_key:
                self._last_key = key
                print("[MusicWatcher] 检测到新歌: " + str(state.get("name")) + " id=" + str(state.get("id")))
                if self._enabled_check():
                    if trigger == "manual":
                        # 用户手动切歌：立即反馈，绕过 5s 延迟、去重与最小间隔
                        self._pending_key = None
                        self._fire(state, force=True)
                        return
                    if key not in self._reported:
                        self._pending_key = key
                        self._pending_since = now
            if self._pending_key == key and now - self._pending_since >= self._feedback_delay:
                self._pending_key = None
                if now - self._last_feedback_at >= self._min_interval:
                    self._fire(state)
        elif not key:
            self._last_key = None
            self._pending_key = None

    def _key_of(self, state: Optional[dict]) -> Optional[tuple]:
        if not state:
            return None
        sid = state.get("id")
        if not sid:
            return None
        return (str(sid), bool(state.get("active")))

    def _read_state(self) -> Optional[dict]:
        try:
            if not self._state_file.exists():
                return None
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[MusicWatcher] 读取状态失败: %s", exc)
            return None

    def _fire(self, state: dict, force: bool = False) -> None:
        # 与主对话错峰：主对话请求进行中时延后反馈，避免抢占中转站单并发。
        from brain.llm_gate import main_request_active
        if main_request_active():
            self._pending_key = self._key_of(state)
            self._pending_since = time.time()
            print("[MusicWatcher] 主对话进行中，暂缓听歌反馈", flush=True)
            return
        text = self._generate_feedback(state)
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
            if len(self._reported) > 200:
                self._reported.clear()
            print("[MusicWatcher] 听歌反馈: " + str(text))
            if self._on_feedback:
                self._on_feedback(text)
        else:
            logger.info("[MusicWatcher] 本轮未生成反馈，等待后续轮询")
            print("[MusicWatcher] 本轮未生成反馈，等待后续轮询")

    def _generate_feedback(self, state: dict) -> Optional[str]:
        try:
            name = state.get("name") or "未知歌曲"
            artist = state.get("artist") or "未知歌手"
            style = state.get("style") or ""
            first = state.get("firstLyrics") or []
            if isinstance(first, list):
                lyric_text = " / ".join(str(x) for x in first[:4])
            else:
                lyric_text = str(first or "（暂无歌词）")

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
                f"当前歌曲：{name}\n歌手：{artist}\n曲风：{style}\n"
                f"接下来几句歌词：{lyric_text}"
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

    @staticmethod
    def _fallback_feedback(name: str, style: str, lyric_text: str) -> str:
        """为纯音乐或稀疏歌词保留一条简短的主动反馈。"""
        if lyric_text and lyric_text not in {"纯音乐，请欣赏", "（暂无歌词）"}:
            return f"《{name}》这段旋律挺有画面感，先安静听一会儿。"
        if style:
            return f"《{name}》的{style}很适合当下这段时间，先陪你听着。"
        return f"《{name}》是纯音乐，旋律先替我们把气氛撑住了。"
