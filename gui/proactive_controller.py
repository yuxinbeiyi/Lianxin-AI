"""主动行为结果的界面交付控制器。"""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable


class ProactivePresentationController:
    """把主动行为结果交付到桌面、历史记录和跨端通道。"""

    def __init__(
        self,
        *,
        scheduler,
        chat_widget,
        history_manager_func: Callable,
        session_id_func: Callable[[], int],
        history_context_func: Callable[[str], None] | None = None,
        speak_func: Callable[[str], None],
        is_minimized_func: Callable[[], bool],
        flash_taskbar_func: Callable[..., None],
        qq_bridge_func: Callable,
        dialog_func: Callable,
        next_track_func: Callable[[], None],
        observations_dir: Path | None = None,
    ):
        self._scheduler = scheduler
        self._chat_widget = chat_widget
        self._history_manager_func = history_manager_func
        self._session_id_func = session_id_func
        self._history_context_func = history_context_func
        self._speak_func = speak_func
        self._is_minimized_func = is_minimized_func
        self._flash_taskbar_func = flash_taskbar_func
        self._qq_bridge_func = qq_bridge_func
        self._dialog_func = dialog_func
        self._next_track_func = next_track_func
        self._observations_dir = observations_dir or (Path.home() / ".lianxin" / "observations")

        self._observation_tip = None
        self._last_behavior = "normal"
        self._last_slack_action = ""
        self._pending_mooyu_sources: list = []
        self._last_observation_context = ""
        self._last_observation_context_at = 0.0
        # 待并入"莲心自己的那句话"的观察图片（见 handle_proactive_response）。
        self._pending_observation = None
        self._pending_observation_at = 0.0

    @staticmethod
    def _clean_text(text: str) -> str:
        text = str(text or "")
        # Internal source labels belong to diagnostics, never to user-facing speech.
        text = re.sub(
            r"^\s*(?:[\[\uff3b](?:\u6478\u9c7c|\u4e3b\u52a8|\u89c2\u5bdf|B\u7ad9\u51b2\u6d6a)[\]\uff3d]\s*)+",
            "",
            text,
        )
        text = re.sub(r"[【［\[]表情[：:]\s*[^】\]］\]]*[】\]］\]]?", "", text).strip()
        return re.sub(r"\n\s*\n", "\n", text).strip()

    def set_observation_tip(self, tip):
        self.clear_observation_tip()
        self._observation_tip = tip

    def clear_observation_tip(self):
        tip = self._observation_tip
        self._observation_tip = None
        if tip is None:
            return
        try:
            tip.hide()
            tip.deleteLater()
        except RuntimeError:
            pass

    def set_behavior(self, behavior: str):
        behavior = behavior or "normal"
        if behavior != self._last_behavior:
            self._pending_mooyu_sources.clear()
        self._last_behavior = behavior

    def set_slack_action(self, action: str):
        self._last_slack_action = action or ""

    def handle_observation_result(self, desc: str):
        from brain.observation_quality import normalize_observation
        desc = normalize_observation(desc)
        if desc:
            self._scheduler.set_last_observation(desc)
            self._remember_observation(desc)

    def _remember_observation(self, desc: str):
        """Keep observation results available for an immediate follow-up question."""
        from brain.observation_quality import observation_preview
        content = observation_preview(self._clean_text(desc), 360)
        if not content:
            return
        now = time.monotonic()
        if (
            content == self._last_observation_context
            and now - self._last_observation_context_at < 10
        ):
            return
        self._last_observation_context = content
        self._last_observation_context_at = now
        # 只注入模型上下文，不再单独落库：观察内容改由莲心自己的那句话
        # （handle_proactive_response）连同图片一起呈现，避免历史里多出一条
        # "莲心刚才观察到：…"的机械旁白。
        self._remember_assistant_context(f"[观察] 莲心刚才观察到：{content[:1500]}")

    def handle_observation_image(self, img_path: str, desc: str):
        """收下"莲心看到的画面"，暂存到 her 下一句话里一起呈现。

        不在这里落库/显示：图片等 handle_proactive_response 拿到莲心自己的
        那句话，再合成一条"她的话 + 她看到的图"的消息（见 _take_pending_observation）。
        """
        try:
            from brain.observation_quality import normalize_observation
            desc = normalize_observation(desc)
            self.clear_observation_tip()
            from brain.observation import OBSERVATIONS_DIR, adopt_observation_image
            self._observations_dir = OBSERVATIONS_DIR
            stored = adopt_observation_image(str(img_path))
            if not stored:
                raise RuntimeError(f"图片收纳失败：{img_path}")
            self._pending_observation = {"path": stored, "desc": desc}
            self._pending_observation_at = time.monotonic()
            self._remember_observation(desc)
        except Exception as exc:
            self._chat_widget.add_system_tip(f"[观察图片显示失败: {exc}]")

    def _take_pending_observation(self):
        """取出待合并的观察图片；超过 120s 未被合并则作废，避免挂到后续无关回复上。"""
        observation = self._pending_observation
        self._pending_observation = None
        if not observation:
            return None
        if time.monotonic() - self._pending_observation_at > 120.0:
            return None
        return observation

    def handle_proactive_response(self, text: str):
        self.clear_observation_tip()
        if not text:
            return
        text = self._clean_text(text)
        # Empty/placeholder proactive output must never become a user-visible message.
        if re.fullmatch(r"[（(]?[^（）()\n]{1,20}沉默了[）)]?", text):
            self._pending_mooyu_sources = []
            return
        pending_sources = self._pending_mooyu_sources
        self._pending_mooyu_sources = []

        if self._scheduler.desktop_enabled:
            if pending_sources:
                self._chat_widget.add_mooyu_data_sources(pending_sources)
            observation = self._take_pending_observation()
            delivered = False
            if observation:
                # "她的话 + 她看到的图"合成一条消息：图片气泡的正文就是莲心这句话。
                try:
                    delivered = bool(self._chat_widget.add_observation_bubble(
                        observation["path"], text, observation.get("desc") or "",
                    ))
                except Exception as exc:
                    print(f"[观察] 图片气泡交付失败: {exc}", flush=True)
            if not delivered:
                self._save_message(text)
            # SQLite 持久化不会自动更新当前 AgentCore.history；同步内存上下文，
            # 让用户紧接着追问时模型能看到刚才的主动发言。
            self._remember_assistant_context(f"[主动] {text}")
            if not observation:
                self._chat_widget.add_ai_message(text)
            self._notify_minimized()
            self._speak_func(text)

        send_to_qq = self._scheduler.qq_enabled
        if self._last_behavior == "observe" and not self._scheduler.observe_send_to_qq:
            send_to_qq = False
        bridge = self._qq_bridge_func()
        if send_to_qq and bridge and bridge.isRunning():
            bridge.send_to_owner(text)

        dialog = self._dialog_func()
        if dialog and dialog.isVisible():
            try:
                dialog._refresh_bl_tags()
                dialog._refresh_bl_history()
            except Exception:
                pass

    def handle_proactive_error(self, err: str):
        self.clear_observation_tip()
        self._chat_widget.add_system_tip(f"主动消息生成失败：{err}")
        self._pending_mooyu_sources.clear()

    def handle_mooyu_data_sources(self, _action_name: str, sources: list):
        self._chat_widget.add_mooyu_data_sources(sources)

    def handle_mooyu_duty_data_source(
        self, name: str, preview: str, is_error: bool, elapsed_ms: float
    ):
        from utils.mooyu_data import MooyuDataSource, MOOYU_SOURCE_FRIENDLY

        self._pending_mooyu_sources.append(MooyuDataSource(
            source_name=name,
            friendly_name=MOOYU_SOURCE_FRIENDLY.get(name, name),
            preview=preview,
            is_error=is_error,
            elapsed_ms=elapsed_ms,
        ))

    def handle_slack_response(self, text: str):
        if not text:
            return
        text = self._clean_text(text)
        self._save_message(text)
        self._remember_assistant_context(f"[摸鱼] {text}")
        self._chat_widget.add_ai_message(text)
        self._notify_minimized()
        self._speak_func(text)

        action = self._last_slack_action
        self._last_slack_action = ""
        if action == "supplement_diary":
            self._scheduler.record_diary_supplement()
        elif action == "next_song":
            self._next_track_func()

    @staticmethod
    def handle_slack_error(err: str):
        print(f"[摸鱼] 生成失败: {err}")

    def _save_message(self, content: str):
        history = self._history_manager_func()
        if history is not None:
            history.save_message(
                self._session_id_func(), "assistant", content
            )

    def _remember_assistant_context(self, content: str):
        """Mirror proactive output into the live AgentCore conversation."""
        if self._history_context_func is None:
            return
        try:
            self._history_context_func(content)
        except Exception:
            # A presentation callback must never break delivery, speech, or QQ.
            pass

    def _notify_minimized(self):
        if self._is_minimized_func():
            self._flash_taskbar_func(flash_count=0)
