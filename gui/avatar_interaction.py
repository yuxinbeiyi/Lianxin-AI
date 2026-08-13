"""头像拍一拍互动：独立于正常聊天队列的轻量异步控制器。"""
import random
import time
import uuid
from datetime import datetime
from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

from config import get_chat_avatar_config, get_user_name


_USER_TAPS_LIANXIN_FALLBACKS = [
    "你是不是又开始摸鱼了？怎么突然拍我？",
    "拍完就想跑？这一下我可记住了。",
    "呦，这不是雨心博士吗？今天怎么突然想起拍我了？",
    "我刚才还在认真想事情，差点被你拍散了。",
    "嗯，这一下收到了。你今天心情还好吗？",
]

_USER_HEADPATS_LIANXIN_FALLBACKS = [
    "嗯……轻一点嘛，不过这次就原谅你了。",
    "被你摸到了。今天可以稍微陪你久一点。",
    "好啦好啦，摸完记得继续陪我说话。",
]

_LIANXIN_TAPS_USER_FALLBACKS = [
    "刚刚那一下是我拍的，提醒你一下，我还在这里。",
    "我主动拍了拍你，别走神太久呀。",
    "轻轻拍你一下，今天也要记得照顾好自己。",
]

_LIANXIN_HEADPATS_USER_FALLBACKS = [
    "摸摸头，今天辛苦了，我在这里陪你。",
    "刚刚是我摸了摸你的头，给你一点安静的鼓励。",
    "轻轻摸一下，别把所有事情都一个人扛着。",
]


class AvatarInteractionWorker(QThread):
    response_ready = pyqtSignal(str)
    failed = pyqtSignal()

    def __init__(self, agent, prompt, invalid_markers=(), parent=None):
        super().__init__(parent)
        self.agent = agent
        self.prompt = prompt
        self.invalid_markers = tuple(str(marker).lower() for marker in invalid_markers)

    def run(self):
        # 拍一拍不写入正常会话，但必须走真实 LLM 链路。部分网关会把
        # API 错误包装成普通字符串，因此不能只判断返回值是否为空。
        try:
            from brain.agent import AgentCore

            class _EphemeralHistory:
                db_path = ":memory:"
                def sync_legacy_channel_maps(self):
                    return None
                def new_session(self, *args, **kwargs):
                    return 0
                def update_title(self, *args, **kwargs):
                    return None
                def save_message(self, *args, **kwargs):
                    return 0
                def get_latest_session_id(self, *args, **kwargs):
                    return None
                def get_messages(self, *args, **kwargs):
                    return []
                def get_latest_message_id(self, *args, **kwargs):
                    return 0
                def get_latest_compression_snapshot(self, *args, **kwargs):
                    return None

            last_error = None
            for attempt in range(1, 4):
                try:
                    isolated = AgentCore(
                        disable_tools=True,
                        track_emotion=False,
                        owner_scope=False,
                        source_channel="avatar_interaction",
                        history_manager=_EphemeralHistory(),
                    )
                    isolated.history = []
                    isolated._session_titled = True
                    isolated._conversation_summary = ""
                    nonce = int(time.time() * 1000) % 1000000
                    prompt = (
                        f"{self.prompt}\n本次互动编号：{nonce}。"
                        "请结合当前情境重新组织措辞，不要复用常见固定台词。"
                    )
                    text = (isolated.chat(prompt, disable_tools=True) or "").strip()
                    lowered = text.lower()
                    error_markers = (
                        "api", "调用失败", "请求失败", "服务异常", "网络异常",
                        "no user query", "authenticationerror", "connection slots",
                    )
                    if text and not any(marker in lowered for marker in error_markers + self.invalid_markers):
                        print(f"[拍一拍] LLM 动态回应成功 attempt={attempt}, len={len(text)}", flush=True)
                        self.response_ready.emit(text[:180])
                        return
                    last_error = text or "LLM 返回空文本"
                    print(f"[拍一拍] LLM 未生成有效文本 attempt={attempt}: {last_error}", flush=True)
                except Exception as exc:
                    last_error = exc
                    print(f"[拍一拍] LLM 调用异常 attempt={attempt}: {exc}", flush=True)
                if attempt < 3:
                    time.sleep(0.8 * attempt)
            print(f"[拍一拍] 动态回复最终失败，转入备用回应: {last_error}", flush=True)
        except Exception as exc:
            print(f"[拍一拍] 动态回复初始化失败，转入备用回应: {exc}", flush=True)
        self.failed.emit()


class AvatarInteractionController(QObject):
    thinking_started = pyqtSignal(str)
    response_ready = pyqtSignal(str, bool)  # text, 保留兼容的旧反拍标记
    interaction_blocked = pyqtSignal(str)
    interaction_accepted = pyqtSignal(str, str, str, str)  # action, target, source, counter_action

    def __init__(self, agent, stats, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.stats = stats
        self._worker = None
        self._busy = False
        self._last_trigger_ms = 0
        self._tap_streak = 0
        self._last_tap_at = 0.0
        self._cooldown_seconds = float(get_chat_avatar_config().get("tap_cooldown_seconds", 1.5) or 1.5)
        self._recent_events = []
        self._last_action = "tap"
        self._last_source = "user"
        self._fallback_used = False
        self._planned_counter_action = ""
        self._interaction_id = ""

    def _time_context(self):
        hour = datetime.now().hour
        if hour >= 23 or hour < 6:
            return "深夜"
        today = datetime.now().strftime("%m-%d")
        try:
            if self.stats.get_first_meet_date() == datetime.now().strftime("%Y-%m-%d"):
                return "相识纪念日"
        except Exception:
            pass
        solar = {"01-01": "元旦", "02-14": "情人节", "05-20": "特别日期",
                 "10-01": "国庆节", "12-25": "圣诞节"}
        return solar.get(today, "普通日期")

    def _remember_event(self, action, actor, target, source):
        now = time.time()
        self._recent_events.append({
            "at": now, "action": action, "actor": actor,
            "target": target, "source": source,
            "context": self._time_context(), "streak": self._tap_streak,
        })
        self._recent_events = [e for e in self._recent_events if now - e["at"] <= 300][-5:]

    def recent_context(self):
        """返回给下一轮正常聊天的短期互动上下文。"""
        now = time.time()
        self._recent_events = [e for e in self._recent_events if now - e["at"] <= 300]
        if not self._recent_events:
            return ""
        lines = []
        for event in self._recent_events[-3:]:
            actor = "用户" if event["actor"] == "user" else "莲心"
            target = "用户" if event["target"] == "user" else "莲心"
            action = "拍了拍" if event["action"] == "tap" else "摸了摸"
            lines.append(f"{actor}{action}{target}（{event['context']}，连续第{event['streak']}次）")
        return (
            "【最近头像互动】\n" + "；".join(lines) +
            "。如果用户提到刚才的互动，请自然承认并延续，不要否认动作，也不要讨论自己是否有实体身体。"
        )

    def _emotion_context(self):
        values = {}
        try:
            from brain.emotional import get_manager
            state = get_manager().state
            values = {
                "情绪基调": round(float(state.valence), 2),
                "唤醒度": round(float(state.arousal), 2),
                "骄傲": round(float(state.pride), 2),
                "防御感": round(float(state.guardedness), 2),
                "连接需求": round(float(state.connection), 2),
                "沉浸度": round(float(state.immersion), 2),
                "信任": round(float(state.trust), 2),
                "亲密度": round(float(state.intimacy), 2),
            }
        except Exception:
            pass
        try:
            from brain.persona.runtime import active_assistant_name
            values["当前人格名称"] = active_assistant_name()
        except Exception:
            values["当前人格名称"] = "莲心"
        try:
            values["上一轮情绪标签"] = str(getattr(self.agent, "_last_emotion", "") or "默认")
        except Exception:
            pass
        return values

    def _counter_probability(self, context):
        probability = 0.28
        probability += max(0.0, context.get("亲密度", 0.5) - 0.5) * 0.45
        probability += max(0.0, context.get("情绪基调", 0.0)) * 0.12
        probability -= max(0.0, context.get("防御感", 0.0) - 0.35) * 0.28
        probability += min(0.18, max(0, self._tap_streak - 1) * 0.06)
        if self._time_context() == "深夜":
            probability -= 0.08
        elif self._time_context() in ("相识纪念日", "元旦", "情人节", "特别日期", "国庆节", "圣诞节"):
            probability += 0.08
        return max(0.08, min(0.68, probability))

    def _fallback_choices(self):
        if self._last_actor == "assistant":
            return (
                _LIANXIN_HEADPATS_USER_FALLBACKS
                if self._last_action == "headpat" else _LIANXIN_TAPS_USER_FALLBACKS
            )
        return (
            _USER_HEADPATS_LIANXIN_FALLBACKS
            if self._last_action == "headpat" else _USER_TAPS_LIANXIN_FALLBACKS
        )

    def _build_prompt(self, context):
        user_name = str(get_user_name() or "主人")
        if self._last_actor == "assistant":
            action_description = (
                f"莲心刚刚主动轻轻摸了摸{user_name}的头像。"
                if self._last_action == "headpat"
                else f"莲心刚刚主动拍了拍{user_name}的头像。"
            )
            prohibited = (
                "禁止出现“你拍我”“你怎么拍我”“被你拍到”“你摸我”或“被你摸头”等"
                "把莲心写成被互动对象的表达。"
            )
            tone = "摸头应是温柔陪伴或安慰；拍一拍可以是调戏玩笑或关心。"
        else:
            action_description = (
                f"{user_name}刚刚摸了摸莲心的头像。"
                if self._last_action == "headpat"
                else f"{user_name}刚刚拍了拍莲心的头像。"
            )
            prohibited = "不要把动作方向写反，也不要编造莲心主动拍了对方。"
            tone = "可以自然回应被互动的感受，保持调戏和玩笑感。"
        base = (
            "事实不可改变：\n"
            f"- 发起者：{'莲心' if self._last_actor == 'assistant' else user_name}\n"
            f"- 对象：{user_name if self._last_target == 'user' else '莲心'}\n"
            f"- 动作：{'摸头' if self._last_action == 'headpat' else '拍一拍'}\n"
            f"- 事件：{action_description}\n"
            "请以莲心第一人称写 1 到 2 句自然、口语化的主动互动短句。"
            f"{tone}{prohibited}"
            "不要提到系统、模型、提示词、事件日志，不要调用工具，不要输出标题或标签。\n"
            f"当前时间语境：{self._time_context()}；连续互动次数：{self._tap_streak}\n"
            f"当前情绪与关系数据：{context}\n"
            "这些数据只用于调整语气，不要在回复中直接复述数值。"
        )
        recent = self._recent_conversation()
        if recent:
            base = base + "\n\n" + recent + (
                "\n请结合上面的近期对话，自然承接刚才的话题来回应这次互动，"
                "不要表现得像刚被惊扰、完全不记得刚才聊过什么。"
            )
        return base

    _CONTEXT_TURNS = 6  # 拍一拍/摸头回应参考的最近对话轮数

    def _recent_conversation(self) -> str:
        """从主会话历史里取最近几轮对话，让互动回应延续语境而不是“失忆”。"""
        try:
            history = getattr(self.agent, "history", None)
        except Exception:
            history = None
        if not history:
            return ""
        lines = []
        for m in history[-self._CONTEXT_TURNS:]:
            role = m.get("role", "") if isinstance(m, dict) else ""
            content = m.get("content", "") if isinstance(m, dict) else str(m)
            content = str(content or "").strip()
            if not content:
                continue
            name = "莲心" if role == "assistant" else "用户"
            lines.append(f"[{name}]: {content[:200]}")
        if not lines:
            return ""
        return "[近期对话（仅用于延续语境，不要复述）]\n" + "\n".join(lines)

    def _invalid_response_markers(self):
        if self._last_actor != "assistant":
            return ()
        if self._last_action == "headpat":
            return ("你摸我", "被你摸", "被你摸头", "摸完记得")
        return ("你拍我", "你怎么拍我", "被你拍", "拍完就想跑")

    def _plan_counter_action(self, context, cfg):
        """用户互动莲心时，先决定是否反拍/反摸，再开始生成台词。"""
        if (
            self._last_actor == "user" and self._last_target == "assistant"
            and bool(cfg.get("counter_tap", True))
            and random.random() < self._counter_probability(context)
        ):
            return self._last_action
        return ""

    def _begin_response(self, context, cfg):
        """在反击动画结束后再开始 LLM 或备用回复。"""
        if not self._busy:
            return
        if not cfg.get("dynamic_response", True):
            self._fallback_used = True
            self._finish(random.choice(self._fallback_choices()))
            return
        self._fallback_used = False
        thinking_text = (
            "莲心正在想该怎么轻轻回应你……"
            if self._last_actor == "assistant" and self._last_action == "headpat" else
            "莲心正在组织刚才主动互动的回应……"
            if self._last_actor == "assistant" else
            "莲心正在想该怎么回应你……"
        )
        self.thinking_started.emit(thinking_text)
        self._worker = AvatarInteractionWorker(
            self.agent, self._build_prompt(context), self._invalid_response_markers(), self,
        )
        self._worker.response_ready.connect(self._finish)
        self._worker.failed.connect(self._finish_fallback)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def trigger(self, role="assistant", action="tap", source="user"):
        cfg = get_chat_avatar_config()
        if not cfg.get("interactions_enabled", True):
            self.interaction_blocked.emit("头像互动已在设置中关闭")
            return False
        if role not in ("assistant", "user"):
            return False
        if source == "user" and role == "user":
            self.interaction_blocked.emit("只有莲心可以拍一拍或摸你的头像")
            return False
        self._last_role = role
        now_ms = time.monotonic() * 1000
        if self._last_trigger_ms and now_ms - self._last_trigger_ms < self._cooldown_seconds * 1000:
            self.interaction_blocked.emit("互动冷却中，请稍等一下")
            return False
        # 使用单实例忙碌门控，避免连续双击堆积多个模型请求。
        if self._busy:
            self.interaction_blocked.emit("莲心正在想刚才这一拍怎么回应")
            return False
        now = time.monotonic()
        if now - self._last_tap_at > 8:
            self._tap_streak = 0
        self._tap_streak += 1
        self._last_tap_at = now
        self._busy = True
        self._last_trigger_ms = now_ms
        self._last_action = action
        self._last_source = source
        self._interaction_id = uuid.uuid4().hex
        target = role
        actor = "user" if source == "user" else "assistant"
        self._last_actor = actor
        self._last_target = target
        interaction_type = (
            "assistant_headpat_user" if action == "headpat" and actor == "assistant" else
            "user_headpat" if action == "headpat" else
            "assistant_tap_user" if actor == "assistant" else "user_tap"
        )
        self._remember_event(action, actor, target, source)
        self.stats.record_avatar_detail(
            interaction_type, actor=actor, target="user" if target == "user" else "assistant",
            source=source, reaction=action, streak=self._tap_streak,
            context={"time": self._time_context()})
        try:
            from brain.interaction_events import record_interaction
            record_interaction(
                feature="avatar", event_type="avatar_interaction",
                source_id=f"avatar:{self._interaction_id}:user",
                summary="完成了一次头像互动", searchable=False,
                event_key=f"avatar:{self._interaction_id}:user",
                metadata={
                    "schema_version": 2, "action": action, "actor": actor,
                    "target": target, "source": source, "is_counter": False,
                },
            )
        except Exception as exc:
            print(f"[成就记录] 头像事件记录失败: {exc}")
        print(f"[拍一拍] 互动开始 role={role}, dynamic={cfg.get('dynamic_response', True)}", flush=True)
        context = self._emotion_context()
        self._planned_counter_action = self._plan_counter_action(context, cfg)
        if self._planned_counter_action:
            self.stats.record_avatar_detail(
                "counter_tap" if self._planned_counter_action == "tap" else "counter_headpat",
                actor="assistant", target="user", source="counter",
                reaction=self._planned_counter_action, streak=self._tap_streak,
                context={"time": self._time_context()},
            )
            try:
                from brain.interaction_events import record_interaction
                record_interaction(
                    feature="avatar", event_type="avatar_interaction",
                    source_id=f"avatar:{self._interaction_id}:counter",
                    summary="莲心回应了一次头像互动", searchable=False,
                    event_key=f"avatar:{self._interaction_id}:counter",
                    metadata={
                        "schema_version": 2,
                        "action": self._planned_counter_action,
                        "actor": "assistant", "target": "user",
                        "source": "counter", "is_counter": True,
                    },
                )
            except Exception as exc:
                print(f"[成就记录] 头像回应事件记录失败: {exc}")
        self.interaction_accepted.emit(action, target, source, self._planned_counter_action)
        # 留出反击动画时间，保证用户先看到动作，再看到思考与台词。
        if self._planned_counter_action:
            QTimer.singleShot(460, lambda: self._begin_response(context, cfg))
        else:
            self._begin_response(context, cfg)
        return True

    def _finish_fallback(self):
        self._fallback_used = True
        self._finish(random.choice(self._fallback_choices()))

    def _finish(self, text):
        if not self._busy:
            return
        self._busy = False
        try:
            self.stats.record_avatar_outcome(llm=not self._fallback_used, fallback=self._fallback_used)
        except Exception:
            pass
        print(
            f"[拍一拍] 动态回应完成 counter_action={self._planned_counter_action or 'none'}, "
            f"len={len(text.strip())}", flush=True,
        )
        self.response_ready.emit(text.strip() or random.choice(self._fallback_choices()), False)
        self._worker = None

    def trigger_outbound(self, action="tap"):
        """莲心主动对用户头像执行动作。"""
        return self.trigger(role="user", action=action, source="assistant")

    def trigger_headpat(self, role="assistant", source="user"):
        return self.trigger(role=role, action="headpat", source=source)
