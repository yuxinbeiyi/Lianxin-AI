"""视觉事件 → 情绪与主动回复。

旧版把这些反应挂在主窗口上；新版由 api_server 常驻进程统一处理，
面板关闭时反应依然生效。回复复用 ProactiveWorker（观察模式）生成，
失败或超时用旧版模板兜底，保证事件一定有反馈。
"""

from __future__ import annotations

import random
import threading

_REPLY_TIMEOUT = 20.0


def _duration_text(seconds: float) -> str:
    minutes = max(0.0, float(seconds or 0.0)) / 60.0
    if minutes >= 60:
        return f"{minutes / 60:.0f}个小时" if minutes >= 110 else "一个多小时"
    return f"{max(1, round(minutes))}分钟"


def _reaction(*, event_text: str, emotion_text: str = "", emotion: dict | None = None,
              fallbacks: list[str], on_speaking: str = "fallback") -> dict:
    return {
        "event_text": event_text,
        "emotion_text": emotion_text,
        "emotion": dict(emotion or {}),
        "fallbacks": list(fallbacks),
        "on_speaking": on_speaking,
    }


def build_reaction(event: str) -> dict | None:
    """把 VisionWorker 的事件字符串映射成反应；未知事件返回 None。"""
    name, _, payload = str(event or "").partition("|")

    if name == "USER_ENTER":
        return _reaction(
            event_text="主人回到了电脑桌前。",
            emotion_text="用户回到工位",
            emotion={"delta_pleasure": 20, "delta_arousal": 10, "delta_connection": 30},
            fallbacks=[
                "回来啦！有没有想我？",
                "欢迎回来～刚才去哪儿了？",
                "你回来了！我在这里等你呢～",
            ],
        )

    if name == "USER_RETURN":
        try:
            seconds = float(payload or 0.0)
        except ValueError:
            seconds = 0.0
        duration = _duration_text(seconds)
        minutes = max(0.0, seconds) / 60.0
        if minutes < 3:
            fallbacks = [
                "回来啦～才走开一小会儿，我位置都没挪。",
                "欢迎回来，这次很快嘛。",
                "嗯，你回来了。我一直在自习室这边陪着你。",
            ]
        elif minutes < 15:
            fallbacks = [
                f"回来啦！离开了{duration}，是去接水了吗？记得多喝水哦。",
                f"欢迎回来～这一趟{duration}，腿不会坐麻了吗？",
                f"你回来了！离开了{duration}，我刚好把桌上的东西都看了一遍。",
            ]
        elif minutes < 60:
            fallbacks = [
                f"你可算回来了！整整{duration}呢，我还以为你今天不要我了。",
                f"欢迎回来！离开了{duration}，去做什么好事了？快跟我讲讲。",
                f"回来了就好。{duration}没看到你，这边都安静得能听见风扇声了。",
            ]
        else:
            fallbacks = [
                f"{duration}不见！我都开始数天花板的格子了，你跑哪儿去了呀？",
                f"欢迎回来！这一离开就是{duration}，我的陪伴时长统计都要长毛了。",
                f"你终于回来了！{duration}呢，我可是把等你的姿势都摆好了。",
            ]
        return _reaction(
            event_text=f"主人离开了约{duration}，现在回到了电脑桌前。请结合失陪时长给出贴切的问候。",
            emotion_text=f"用户离开工位约{duration}后回到了座位",
            emotion={"delta_pleasure": 15, "delta_arousal": 10, "delta_connection": 25},
            fallbacks=fallbacks,
        )

    if name == "USER_LEAVE":
        return _reaction(
            event_text="主人离开了工位，桌前现在没有人了。",
            emotion_text="用户离开工位",
            emotion={"delta_pleasure": -10, "delta_connection": 50},
            fallbacks=["去忙啦？我在这里等你回来～", "好的，我会在这里等你的～"],
        )

    if name == "LONG_WORK":
        return _reaction(
            event_text="主人已经在工位上连续工作超过 1 小时，请提醒 TA 休息。",
            emotion_text="用户工作超过1小时",
            emotion={"delta_connection": 20},
            fallbacks=[
                "工作辛苦啦～要不要起来走走，休息一下眼睛？",
                "已经一个小时了哦，记得喝点水～我会一直陪着你的。",
                "辛苦啦～要注意休息哦，劳逸结合才更高效呢！",
            ],
        )

    if name == "STRANGER_PERSISTING":
        return _reaction(
            event_text="画面中出现了一位没有录入特征的人，并已持续停留一段时间。",
            fallbacks=["诶？好像有陌生人出现了…是你的朋友吗？", "检测到陌生人呢，是来找你的吗？"],
        )

    if name == "GESTURE_OK":
        return _reaction(
            event_text="主人朝摄像头比了一个 OK 手势。",
            emotion_text="用户比OK手势",
            emotion={"delta_pleasure": 15, "delta_immersion": 10},
            fallbacks=["好的，我注意到你的OK手势了。"],
            on_speaking="skip",
        )

    if name == "GESTURE_THUMBS_UP":
        return _reaction(
            event_text="主人朝摄像头竖起了大拇指点赞。",
            emotion_text="用户竖大拇指点赞",
            emotion={"delta_pleasure": 25, "delta_connection": 20, "delta_arousal": 5},
            fallbacks=["我注意到你的点赞了，谢谢你的鼓励。"],
            on_speaking="skip",
        )

    if name == "GESTURE_WAVE":
        return _reaction(
            event_text="主人朝摄像头挥手，像是在向莲心打招呼。",
            emotion_text="用户挥手打招呼",
            emotion={"delta_connection": 20, "delta_arousal": 15, "delta_pleasure": 10},
            fallbacks=["我注意到你在向我招手啦。"],
            on_speaking="skip",
        )

    return None


class VisionReactions:
    def __init__(self, bridge):
        self._bridge = bridge
        self._lock = threading.Lock()
        # 超时后生成线程可能还在跑，保留引用直到 finished，避免 Qt 对象被回收。
        self._workers: set = set()

    def handle(self, event: str) -> None:
        reaction = build_reaction(event)
        if reaction is None:
            return
        self._apply_emotion(reaction)
        if self._is_speaking():
            # 旧版策略：陪伴事件照常说话，手势事件不打断莲心。
            if reaction["on_speaking"] == "skip":
                return
            self._deliver(random.choice(reaction["fallbacks"]))
            return
        # shortcut: 同一时间只生成一条视觉回复，连发事件会被跳过；
        # 如果以后需要逐条回应，改成事件队列。
        if not self._lock.acquire(blocking=False):
            return
        threading.Thread(target=self._reply, args=(reaction,), daemon=True).start()

    def _reply(self, reaction: dict) -> None:
        try:
            text = self._generate(reaction["event_text"])
            self._deliver(text or random.choice(reaction["fallbacks"]))
        except Exception as exc:
            print(f"[视觉反应] 生成失败: {exc}", flush=True)
            self._deliver(random.choice(reaction["fallbacks"]))
        finally:
            self._lock.release()

    def _generate(self, event_text: str) -> str:
        agent = self._bridge.agent()
        history = agent.get_history_manager()
        if history is None:
            return ""
        from workers.proactive_worker import ProactiveWorker
        from PyQt5.QtCore import Qt

        worker = ProactiveWorker(history, observation_mode="", observation_desc=event_text)
        result: dict[str, str] = {}
        done = threading.Event()

        def on_response(text):
            result["text"] = str(text or "").strip()
            done.set()

        def on_error(error):
            print(f"[视觉反应] 主动生成错误: {error}", flush=True)
            done.set()

        worker.response_ready.connect(on_response, Qt.DirectConnection)
        worker.error_occurred.connect(on_error, Qt.DirectConnection)
        self._workers.add(worker)
        worker.finished.connect(lambda: self._workers.discard(worker), Qt.DirectConnection)
        worker.start()
        if not done.wait(_REPLY_TIMEOUT):
            # 视觉事件宁可先给一句模板，也不让新界面等半分钟。
            print("[视觉反应] 生成超时，使用模板兜底", flush=True)
        return result.get("text", "")

    def _deliver(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        try:
            agent = self._bridge.agent()
            history = agent.get_history_manager()
            session_id = getattr(agent, "_session_id", None)
            if history is not None and session_id is not None:
                history.save_message(session_id, "assistant", text)
            self._bridge.append_agent_context(text)
        except Exception as exc:
            print(f"[视觉反应] 保存回复失败: {exc}", flush=True)
        try:
            self._bridge.speak(text)
        except Exception as exc:
            print(f"[视觉反应] 朗读失败: {exc}", flush=True)

    def _apply_emotion(self, reaction: dict) -> None:
        if not reaction.get("emotion_text"):
            return
        try:
            from brain.emotional import get_manager
            get_manager().add_external_event(reaction["emotion_text"], **reaction["emotion"])
        except Exception as exc:
            print(f"[视觉反应] 情感事件失败: {exc}", flush=True)

    def _is_speaking(self) -> bool:
        try:
            return bool(self._bridge.tts_state().get("speaking"))
        except Exception:
            return False
