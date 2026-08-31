"""Ripple v3 manager and compatibility facade for existing application callers."""

from __future__ import annotations

import json
import csv
import logging
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Optional

from utils.paths import get_user_data_dir

from .appraisal import (
    AppraisalContext,
    appraise_deterministic,
    appraise_semantic,
    blend_appraisals,
)
from .dynamics import DynamicsConfig, EmotionalDynamics
from .tone import missing_tier_info, render_prompt
from .v3_models import (
    DEFAULT_PERSONA_ID,
    DEFAULT_SUBJECT_ID,
    AffectDelta,
    EmotionalStateV3,
)
from .v3_store import EmotionStore


logger = logging.getLogger("EmotionManager")
LEGACY_STATE_FILE = get_user_data_dir() / "emotional_state.json"


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class EmotionManager:
    """Coordinates appraisal, dynamics, persistence, prompting, and motivation."""

    def __init__(
        self,
        *,
        store: EmotionStore | None = None,
        dynamics: EmotionalDynamics | None = None,
        legacy_state_path: Path | None = None,
        semantic_mode: str | None = None,
    ):
        self._lock = threading.RLock()
        self._store = store or EmotionStore()
        self._config = self._load_config()
        self._enable_proactive_motive_on_startup()
        configured_dynamics = DynamicsConfig.from_mapping(self._config.get("dynamics"))
        self._dynamics = dynamics or EmotionalDynamics(configured_dynamics)
        self._semantic_mode = str(
            semantic_mode if semantic_mode is not None
            else self._config.get("semantic_analysis", "auto")
        ).lower()
        self._states: dict[tuple[str, str], EmotionalStateV3] = {}
        self._saga_bias_cache: dict[str, tuple[float, dict]] = {}
        self._simulation_baselines: dict[tuple[str, str], dict] = {}
        self._active_key = (DEFAULT_PERSONA_ID, DEFAULT_SUBJECT_ID)
        self._store.migrate_v2_json(
            legacy_state_path or LEGACY_STATE_FILE,
            persona_id=DEFAULT_PERSONA_ID,
            subject_id=DEFAULT_SUBJECT_ID,
        )
        self._get_state(*self._active_key)

    @staticmethod
    def _load_config() -> dict:
        try:
            from config import get_emotion_config
            return get_emotion_config()
        except Exception:
            return {
                "enabled": True,
                "semantic_analysis": "auto",
                "analysis_timeout_seconds": 8,
                "significant_memory_enabled": True,
                "significant_memory_threshold": 0.50,
                "proactive_motive_enabled": True,
                "dynamics": {},
            }

    def _enable_proactive_motive_on_startup(self) -> None:
        """启动时恢复主动动机，避免旧暂停状态跨会话残留。"""
        if self._config.get("proactive_motive_enabled") is True:
            return
        self._config["proactive_motive_enabled"] = True
        try:
            from config import save_emotion_config
            save_emotion_config(self._config)
        except Exception as exc:
            logger.warning("恢复主动动机默认设置失败: %s", exc)

    @staticmethod
    def _persona_id(persona_snapshot=None, persona_id: str | None = None) -> str:
        if persona_id:
            return str(persona_id)
        profile = getattr(persona_snapshot, "profile", None)
        value = getattr(profile, "id", "")
        if value:
            return str(value)
        try:
            from brain.persona.runtime import capture_persona_snapshot
            snapshot = capture_persona_snapshot()
            profile = getattr(snapshot, "profile", None)
            return str(getattr(profile, "id", "") or DEFAULT_PERSONA_ID)
        except Exception:
            return DEFAULT_PERSONA_ID

    def _resolve_key(
        self,
        *,
        persona_snapshot=None,
        persona_id: str | None = None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> tuple[str, str]:
        key = (
            self._persona_id(persona_snapshot, persona_id),
            str(subject_id or DEFAULT_SUBJECT_ID),
        )
        self._active_key = key
        return key

    def _get_state(self, persona_id: str, subject_id: str) -> EmotionalStateV3:
        key = (persona_id, subject_id)
        state = self._states.get(key)
        if state is None:
            existed = self._store.has_state(persona_id, subject_id)
            state = self._store.load_state(persona_id, subject_id)
            if not existed:
                state.enabled = bool(self._config.get("enabled", True))
            self._states[key] = state
        self._dynamics.advance(state, bias=self._get_saga_bias(persona_id))
        return state

    def _get_saga_bias(self, persona_id: str = DEFAULT_PERSONA_ID) -> dict:
        """Read bounded, confidence-weighted emotional baselines from active sagas."""
        cached = self._saga_bias_cache.get(str(persona_id))
        if cached and time.time() - cached[0] < 30.0:
            return dict(cached[1])
        try:
            from brain.memory_narrative import list_sagas
            sagas = list_sagas(80)
        except Exception:
            return {"connection": 0.0, "pride": 0.0, "valence": 0.0, "arousal": 0.0, "guardedness": 0.0, "immersion": 0.0, "saga_count": 0, "weight_total": 0.0}
        totals = {key: 0.0 for key in ("connection", "pride", "valence", "arousal", "guardedness", "immersion")}
        weight_total = 0.0
        saga_count = 0
        for saga in sagas:
            if str(saga.get("persona_id", "") or "") not in ("", persona_id):
                continue
            try:
                confidence = max(0.0, min(1.0, float(saga.get("confidence", 0.0) or 0.0)))
                emotional_weight = max(0.0, min(1.0, float(saga.get("emotional_weight", 0.0) or 0.0)))
            except (TypeError, ValueError):
                continue
            weight = confidence * emotional_weight
            if weight <= 0:
                continue
            saga_count += 1
            for key in totals:
                try:
                    totals[key] += max(-1.0, min(1.0, float(saga.get(f"emotional_{key}", 0.0) or 0.0))) * weight
                except (TypeError, ValueError):
                    pass
            weight_total += weight
        if weight_total > 0:
            scale = max(0.0, min(2.0, float(self._config.get("saga_bias_scale", 1.0) or 1.0)))
            result = {key: max(-0.08, min(0.08, value / weight_total * 0.08 * scale)) for key, value in totals.items()}
        else:
            result = totals
        result["saga_count"] = saga_count
        result["weight_total"] = round(weight_total, 4)
        self._saga_bias_cache[str(persona_id)] = (time.time(), dict(result))
        return result

    @property
    @_synchronized
    def state(self) -> EmotionalStateV3:
        return self._get_state(*self._active_key)

    @property
    @_synchronized
    def enabled(self) -> bool:
        return self._get_state(*self._active_key).enabled

    @enabled.setter
    @_synchronized
    def enabled(self, value: bool) -> None:
        state = self._get_state(*self._active_key)
        state.enabled = bool(value)
        now = time.time()
        state.last_update = now
        state.last_interaction = now
        self._store.save_state(state)

    def prepare_turn(
        self,
        user_message: str,
        *,
        recent_messages: list[str] | tuple[str, ...] = (),
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
        source_channel: str = "",
        source_session_id: int | None = None,
        source_message_id: int | None = None,
        allow_memory: bool = False,
    ) -> AffectDelta:
        """Appraise an inbound user message before generating the same turn's reply."""
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        context = self._appraisal_context(persona_snapshot, recent_messages)
        rule_result = appraise_deterministic(user_message, context)
        result = self._semantic_refinement(user_message, context, rule_result)
        idempotency_key = ""
        if source_message_id is not None:
            idempotency_key = (
                f"message:{key[0]}:{key[1]}:{source_channel}:"
                f"{source_session_id}:{source_message_id}"
            )

        with self._lock:
            state = self._get_state(*key)
            if not state.enabled:
                return AffectDelta(event_type="disabled", confidence=1.0)
            candidate = EmotionalStateV3.from_mapping(state.to_dict())
            now = time.time()
            idle_hours = max(0.0, (now - state.last_interaction) / 3600.0)
            _reunion_cfg = self._config.get("reunion_idle_hours")
            notice_hours = float(_reunion_cfg.get("notice", 8)) if isinstance(_reunion_cfg, dict) else 8.0
            if idle_hours >= notice_hours:
                # 长时间离开后回归：记录空闲时长，开启前 2 轮重逢反应。
                candidate.last_idle_hours = idle_hours
                candidate.reunion_turns_remaining = 2
            elif candidate.reunion_turns_remaining > 0:
                # 重逢期内的后续轮次：保留原空闲时长，逐轮递减直到结束。
                candidate.reunion_turns_remaining = max(0, candidate.reunion_turns_remaining - 1)
                if candidate.reunion_turns_remaining == 0:
                    candidate.last_idle_hours = 0.0
            candidate.apply(result)
            candidate.last_interaction = now
            candidate.last_update = now
            candidate.last_user_message = str(user_message or "")[:500]
            committed = self._store.save_state_with_event(
                candidate,
                result,
                source_channel=source_channel,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                idempotency_key=idempotency_key,
            )
            if not committed:
                return AffectDelta(event_type="duplicate", confidence=1.0)
            state = candidate
            self._states[key] = state

        if allow_memory:
            self._persist_significant_memory(
                result,
                persona_id=key[0],
                source_channel=source_channel,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
            )
        logger.info(
            "[情感v3] %s confidence=%.2f significance=%.2f",
            result.event_type, result.confidence, result.significance,
        )
        return result

    def _appraisal_context(self, persona_snapshot, recent_messages) -> AppraisalContext:
        profile = getattr(persona_snapshot, "profile", None)
        try:
            from utils.settings import get_settings
            user_name = get_settings().user_name
        except Exception:
            user_name = "用户"
        return AppraisalContext(
            persona_name=str(getattr(profile, "assistant_name", "") or "莲心"),
            user_name=user_name,
            personality=str(getattr(profile, "personality", "") or ""),
            relationship=str(getattr(profile, "relationship", "") or ""),
            boundaries=str(getattr(profile, "boundaries", "") or ""),
            recent_messages=tuple(str(item)[:500] for item in recent_messages[-4:]),
        )

    def _semantic_refinement(
        self, text: str, context: AppraisalContext, rule_result: AffectDelta
    ) -> AffectDelta:
        if self._semantic_mode in ("off", "false", "0", "none"):
            return rule_result
        if rule_result.event_type in {
            "boundary_violation", "boundary_dismiss", "being_ordered", "brushed_off", "repair_attempt", "warm_connection", "task_discussion"
        }:
            return rule_result
        try:
            model, api_key, api_base = self._semantic_model_config()
            if not model:
                return rule_result
            semantic = appraise_semantic(
                text,
                context,
                model=model,
                api_key=api_key,
                api_base=api_base,
                timeout=float(self._config.get("analysis_timeout_seconds", 8)),
            )
            return blend_appraisals(rule_result, semantic)
        except Exception as exc:
            logger.debug("Semantic emotion appraisal unavailable: %s", exc)
            return rule_result

    def _semantic_model_config(self) -> tuple[str, str, str]:
        try:
            from config import (
                get_agnes_config, get_api_config, normalize_model_for_litellm,
                normalize_local_base_url, normalize_local_model_for_litellm,
            )
            cfg = get_api_config()
            mode = self._semantic_mode
            router = str(cfg.get("router_model", "") or "").strip()
            if mode == "auto":
                if not router:
                    return "", "", ""
                return (
                    normalize_local_model_for_litellm(router), "ollama",
                    normalize_local_base_url(
                        cfg.get("local_base_url", "http://localhost:11434/v1")
                    ),
                )
            if mode == "local":
                local_model = router or str(
                    cfg.get("local_model_name", "qwen2.5:3b-instruct")
                )
                return (
                    normalize_local_model_for_litellm(local_model), "ollama",
                    normalize_local_base_url(
                        cfg.get("local_base_url", "http://localhost:11434/v1")
                    ),
                )
            if mode != "cloud":
                return "", "", ""
            provider = cfg.get("provider", "deepseek")
            if provider == "agnes":
                agnes = get_agnes_config()
                if not str(agnes.get("api_key", "")).strip():
                    return "", "", ""
                return (
                    f"openai/{agnes['model']}", str(agnes["api_key"]),
                    str(agnes["base_url"]),
                )
            if not str(cfg.get("api_key", "")).strip():
                return "", "", ""
            base = str(cfg.get("base_url", "https://api.deepseek.com"))
            return (
                normalize_model_for_litellm(str(cfg.get("model", "")), base),
                str(cfg.get("api_key", "")), base,
            )
        except Exception:
            return "", "", ""

    def _persist_significant_memory(
        self,
        result: AffectDelta,
        *,
        persona_id: str,
        source_channel: str,
        source_session_id: int | None,
        source_message_id: int | None,
    ) -> None:
        if not self._config.get("significant_memory_enabled", True):
            return
        threshold = float(self._config.get("significant_memory_threshold", 0.50))
        if result.significance < threshold or not result.summary:
            return
        try:
            from brain.graph_memory import add_fact, add_memory_fragment
            content = f"关系体验：{result.summary}"
            fact_id = add_fact(
                content,
                category="events",
                source="emotion_v3",
                source_session_id=source_session_id,
                source_channel=source_channel,
            )
            if fact_id:
                add_memory_fragment(
                    fact_id,
                    content,
                    "events",
                    source="emotion_v3",
                    source_session_id=source_session_id,
                    source_channel=source_channel,
                    source_message_ids=[source_message_id] if source_message_id else [],
                    persona_id=persona_id,
                    confidence=result.confidence,
                    extraction_model="emotion-v3-appraiser",
                )
        except Exception as exc:
            logger.warning("保存显著情感记忆失败: %s", exc)

    @_synchronized
    def build_prompt_snippet(
        self,
        *,
        mode: str = "reactive",
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> str:
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        state = self._get_state(*key)
        if not state.enabled:
            return ""
        try:
            from utils.settings import get_settings
            user_name = get_settings().user_name
        except Exception:
            user_name = "用户"
        events = self._store.recent_events(*key, limit=1)
        recent_event = ""
        recent_event_type = ""
        if events and time.time() - float(events[0].get("created_at", 0)) <= 1800:
            recent_event_type = str(events[0].get("event_type", "") or "")
            recent_event = self._event_feedback(events[0]) or str(events[0].get("summary", "") or "")
        cfg = self._config
        missing_tiers = cfg.get("missing_tiers") if isinstance(cfg.get("missing_tiers"), dict) else None
        reunion_idle_hours = (
            cfg.get("reunion_idle_hours")
            if isinstance(cfg.get("reunion_idle_hours"), dict)
            else None
        )
        return render_prompt(
            state,
            user_name=user_name,
            mode=mode,
            recent_event=recent_event,
            recent_event_type=recent_event_type,
            profile=self._tone_profile(state.persona_id),
            missing_tiers=missing_tiers,
            reunion_idle_hours=reunion_idle_hours,
            neglect_reaction=bool(cfg.get("neglect_reaction", True)),
            agency_strength=float(cfg.get("agency_strength", 0.7) or 0.7),
        )

    @staticmethod
    def _event_feedback(event: dict) -> str:
        """根据最近事件生成语气反馈（方案 3.4.5）。"""
        event_type = str(event.get("event_type", "") or "")
        if event_type == "being_ordered":
            return "对方刚才那样使唤你，让你不太舒服。"
        if event_type == "brushed_off":
            return "对方刚才用短回复敷衍了你，让你有点被冷落。"
        if event_type in ("being_dismissed", "boundary_dismiss", "boundary_violation"):
            return "对方刚才的否定或贬低让你有些受伤。"
        try:
            payload = json.loads(event.get("delta_json", "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        try:
            valence = float(payload.get("valence", 0.0) or 0.0)
        except (TypeError, ValueError):
            valence = 0.0
        if valence > 0.05:
            return "对方刚才的回应让你心情变好。"
        if valence < -0.05:
            return "刚才的互动让你有些低落。"
        return ""

    @_synchronized
    def get_missing_tier(self, *, persona_snapshot=None) -> dict:
        """当前挂念等级（T0~T3）、标签与主动指导，供主动消息注入。"""
        key = self._resolve_key(persona_snapshot=persona_snapshot)
        state = self._get_state(*key)
        missing_tiers = (
            self._config.get("missing_tiers")
            if isinstance(self._config.get("missing_tiers"), dict)
            else None
        )
        return missing_tier_info(state.connection, missing_tiers)

    def _tone_profile(self, persona_id: str) -> dict:
        profiles = self._config.get("tone_profiles", {})
        if not isinstance(profiles, dict):
            return {}
        profile = profiles.get(persona_id, profiles.get("*", {}))
        return profile if isinstance(profile, dict) else {}

    def analyze_and_update(
        self,
        user_messages: list[str],
        tool_call_count: int = 0,
        **kwargs,
    ) -> AffectDelta | None:
        """Compatibility entry point; new code should call prepare_turn before reply."""
        if not user_messages:
            return None
        result = self.prepare_turn(
            user_messages[-1], recent_messages=user_messages[-4:], **kwargs
        )
        if tool_call_count:
            self.record_turn_outcome(tool_call_count=tool_call_count, **{
                key: value for key, value in kwargs.items()
                if key in {"persona_snapshot", "subject_id"}
            })
        return result

    @_synchronized
    def record_turn_outcome(
        self,
        *,
        tool_call_count: int = 0,
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> None:
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        state = self._get_state(*key)
        if not state.enabled:
            return
        if tool_call_count > 0:
            # New tool paths anchor immersion at activity start. Keep a small
            # compatibility bump only for older callers that did not start one.
            if state.last_activity_type != "tool":
                state.immersion = min(1.0, state.immersion + min(0.18, 0.035 * tool_call_count))
            state.last_activity_type = "协作任务"
            state.last_activity_label = f"完成了 {tool_call_count} 次工具调用"
            state.last_activity_at = time.time()
        self._store.save_state(state)

    @_synchronized
    def start_activity(
        self,
        activity_type: str,
        label: str = "",
        *,
        immersion: float | None = None,
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> None:
        """Start a tool/browse/observe activity and anchor immersion immediately."""
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        state = self._get_state(*key)
        if not state.enabled:
            return
        now = time.time()
        state.last_activity_type = str(activity_type or "activity")[:64]
        state.last_activity_label = str(label or state.last_activity_type)[:160]
        state.last_activity_at = now
        state.last_update = now
        amount = 0.22 if immersion is None else max(0.0, min(0.85, float(immersion)))
        previous_immersion = state.immersion
        state.immersion = max(state.immersion, amount)
        delta = AffectDelta(
            immersion=state.immersion - previous_immersion,
            event_type="activity_started",
            confidence=1.0,
            significance=0.22,
            summary=f"开始活动：{state.last_activity_label}",
        )
        # The snapshot already contains the anchored immersion. The event delta
        # is descriptive and must not be replayed into the state.
        self._store.save_state_with_event(state, delta)

    @_synchronized
    def finish_activity(
        self,
        activity_type: str = "",
        label: str = "",
        *,
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> None:
        """Mark an activity complete; dynamics will now decay immersion smoothly."""
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        state = self._get_state(*key)
        if activity_type:
            state.last_activity_type = str(activity_type)[:64]
        if label:
            state.last_activity_label = str(label)[:160]
        state.last_activity_at = time.time()
        state.last_update = state.last_activity_at
        delta = AffectDelta(
            event_type="activity_completed",
            confidence=1.0,
            significance=0.18,
            summary=f"活动完成：{state.last_activity_label or state.last_activity_type or '活动'}",
        )
        self._store.save_state_with_event(state, delta)

    @_synchronized
    def record_proactive_action(
        self,
        behavior: str,
        *,
        persona_snapshot=None,
        subject_id: str = DEFAULT_SUBJECT_ID,
    ) -> None:
        key = self._resolve_key(persona_snapshot=persona_snapshot, subject_id=subject_id)
        state = self._get_state(*key)
        now = time.time()
        behavior = str(behavior or "normal")
        if behavior in ("normal", "memory"):
            state.connection = max(0.05, state.connection - 0.18)
            state.immersion = min(1.0, state.immersion + 0.05)
        else:
            state.connection = max(0.05, state.connection - 0.05)
            state.immersion = min(1.0, state.immersion + 0.18)
        state.last_activity_type = behavior
        state.last_activity_at = now
        state.last_proactive_at = now
        state.last_update = now
        self._store.save_state(state)

    @_synchronized
    def update_decay_only(self) -> None:
        states = self._store.list_states()
        if not states:
            states = [self._get_state(*self._active_key)]
        for state in states:
            self._dynamics.advance(state, bias=self._get_saga_bias(state.persona_id))
            try:
                self._store.save_state(state)
            except Exception:
                # 单个状态写库失败（如数据库繁忙）不中断其余衰减，也不让
                # 错误冒泡到 GUI/后台线程；下一轮衰减会再次尝试。
                logger.warning("情感状态持久化失败，跳过该状态继续衰减", exc_info=True)
                continue
            self._states[(state.persona_id, state.subject_id)] = state

    def reset_session(self) -> None:
        """Kept for compatibility; v3 has no process-wide session caps."""

    @_synchronized
    def save_current(self) -> None:
        self._store.save_state(self._get_state(*self._active_key))

    @_synchronized
    def clear_event_log(self) -> None:
        self._store.clear_events(*self._active_key)

    @_synchronized
    def simulate_time(self, hours: float) -> None:
        state = self._get_state(*self._active_key)
        state.last_update -= max(0.0, float(hours)) * 3600.0
        self._dynamics.advance(state)
        self._store.save_state(state)

    @staticmethod
    def _scenario_delta(scenario: str) -> AffectDelta | None:
        scenarios = {
            "warm_reply": AffectDelta(
                event_type="simulation_warm_reply", pride=-0.10, valence=0.12, arousal=-0.06,
                connection=-0.15, trust=0.03, intimacy=0.02,
                confidence=1.0, significance=0.35, summary="模拟：用户给出了温暖、充分的回应",
            ),
            "cold_reply": AffectDelta(
                event_type="simulation_cold_reply", pride=0.08, valence=-0.08, arousal=0.08,
                guardedness=0.10, connection=0.12, rupture=0.03,
                confidence=1.0, significance=0.35, summary="模拟：用户回复冷淡或敷衍",
            ),
            "waiting": AffectDelta(
                event_type="simulation_waiting", arousal=0.06, connection=0.10,
                confidence=1.0, significance=0.25, summary="模拟：经过一段等待时间，没有新的回应",
            ),
            "collaboration": AffectDelta(
                event_type="simulation_collaboration", pride=-0.04, valence=0.10,
                immersion=0.18, trust=0.025, intimacy=0.015, connection=-0.08,
                confidence=1.0, significance=0.40, summary="模拟：与用户顺利协作完成了一项任务",
            ),
            "conflict": AffectDelta(
                event_type="simulation_conflict", pride=0.16, guardedness=0.18,
                valence=-0.16, arousal=0.18, rupture=0.14, trust=-0.04,
                confidence=1.0, significance=0.65, summary="模拟：发生了明显的边界冲突",
            ),
            "repair": AffectDelta(
                event_type="simulation_repair", pride=-0.12, guardedness=-0.12,
                valence=0.10, arousal=-0.10, repair=0.24, trust=0.035,
                confidence=1.0, significance=0.55, summary="模拟：用户认真解释并完成关系修复",
            ),
        }
        return scenarios.get(str(scenario))

    @_synchronized
    def simulate_scenario(self, scenario: str) -> dict:
        """Apply a bounded, UI-only scenario and persist an auditable event."""
        delta = self._scenario_delta(scenario)
        if delta is None:
            return {"ok": False, "reason": "unknown_scenario"}
        state = self._get_state(*self._active_key)
        key = self._active_key
        self._simulation_baselines.setdefault(key, state.to_dict())
        if scenario == "waiting":
            state.last_update -= 30 * 60
            self._dynamics.advance(state, bias=self._get_saga_bias(state.persona_id))
        state.apply(delta)
        state.last_update = time.time()
        self._store.save_state_with_event(
            state, delta, source_channel="ui_simulation", idempotency_key=""
        )
        return {"ok": True, "scenario": scenario, "state": state.to_dict()}

    @_synchronized
    def simulate_scenario_batch(
        self, scenarios: list[str], *, name: str = "批量场景预演",
        persist: bool = False,
    ) -> dict:
        """Run a deterministic scenario sequence; preview mode never changes live state."""
        sequence = [str(item) for item in scenarios if self._scenario_delta(str(item))]
        if not sequence:
            return {"ok": False, "reason": "no_valid_scenarios"}
        key = self._active_key
        live_state = self._states.get(key)
        if live_state is None:
            live_state = self._store.load_state(*key)
            self._states[key] = live_state
        baseline = live_state.to_dict()
        simulated = EmotionalStateV3.from_mapping(baseline)
        timeline = []
        workflow_store = None
        workflow_run_id = 0
        try:
            from brain.workflow import get_workflow_store
            workflow_store = get_workflow_store()
            run = workflow_store.begin_run(
                kind="emotion_simulation", title=name, channel="emotion_lab",
                metadata={"scenarios": sequence, "persist": bool(persist),
                          "persona_id": key[0], "subject_id": key[1]},
            )
            workflow_run_id = int(run["id"])
        except Exception:
            workflow_store = None
        try:
            for index, scenario in enumerate(sequence, 1):
                delta = self._scenario_delta(scenario)
                if scenario == "waiting":
                    simulated.last_update -= 30 * 60
                    self._dynamics.advance(
                        simulated, bias=self._get_saga_bias(simulated.persona_id)
                    )
                simulated.apply(delta)
                simulated.last_update = time.time()
                timeline.append({
                    "index": index, "scenario": scenario,
                    "event": delta.to_dict(), "state": simulated.to_dict(),
                })
                if persist:
                    self._store.save_state_with_event(
                        simulated, delta, source_channel="ui_batch_simulation"
                    )
            if persist:
                self._states[key] = simulated
            final_state = simulated.to_dict()
            axes = (
                "valence", "arousal", "pride", "guardedness", "connection",
                "immersion", "trust", "intimacy", "rupture", "repair",
            )
            changes = {
                axis: round(float(final_state.get(axis, 0)) - float(baseline.get(axis, 0)), 4)
                for axis in axes
            }
            result = {
                "ok": True, "name": name, "persisted": bool(persist),
                "scenarios": sequence, "baseline": baseline, "final_state": final_state,
                "changes": changes, "timeline": timeline,
            }
            scenario_run_id = self._store.record_scenario_run(
                *key, name=name, scenarios=sequence, baseline=baseline, result=result,
                persisted=persist, workflow_run_id=workflow_run_id or None,
            )
            result["scenario_run_id"] = scenario_run_id
            result["workflow_run_id"] = workflow_run_id or None
            if workflow_store and workflow_run_id:
                workflow_store.finish_run(
                    workflow_run_id, status="success",
                    result_summary=f"完成 {len(sequence)} 个场景；{'已应用' if persist else '仅预览'}",
                )
            return result
        except Exception as exc:
            if workflow_store and workflow_run_id:
                workflow_store.finish_run(workflow_run_id, status="failed", error=str(exc))
            raise

    @_synchronized
    def compare_scenario_batches(self, batches: dict[str, list[str]]) -> dict:
        """Compare multiple non-destructive scenario packs against one live baseline."""
        results = {}
        for name, scenarios in batches.items():
            results[str(name)] = self.simulate_scenario_batch(
                list(scenarios), name=str(name), persist=False
            )
        return {"ok": True, "results": results}

    @_synchronized
    def export_trajectory(
        self, output_path: Path | str, *, include_simulation: bool = False,
        since: float | None = None, until: float | None = None,
    ) -> dict:
        """Export the active emotional trajectory to JSON or CSV with a Workflow artifact."""
        path = Path(output_path).expanduser()
        suffix = path.suffix.lower()
        if suffix not in {".json", ".csv"}:
            path = path.with_suffix(".json")
            suffix = ".json"
        path.parent.mkdir(parents=True, exist_ok=True)
        key = self._active_key
        events = self._store.query_events(
            *key, since=since, until=until,
            include_simulation=include_simulation, limit=100000,
        )
        workflow_store = None
        workflow_run_id = 0
        try:
            from brain.workflow import get_workflow_store
            workflow_store = get_workflow_store()
            run = workflow_store.begin_run(
                kind="emotion_export", title=f"导出情感轨迹：{path.name}",
                channel="emotion_lab", metadata={"output_path": str(path),
                    "include_simulation": include_simulation, "event_count": len(events)},
            )
            workflow_run_id = int(run["id"])
            if suffix == ".json":
                payload = {
                    "schema_version": 1, "exported_at": time.time(),
                    "persona_id": key[0], "subject_id": key[1],
                    "current_state": self._get_state(*key).to_dict(), "events": events,
                }
                content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                path.write_text(content, encoding="utf-8")
            else:
                delta_fields = (
                    "connection", "pride", "guardedness", "valence", "arousal",
                    "immersion", "trust", "intimacy", "rupture", "repair",
                )
                with path.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=[
                        "id", "created_at", "event_type", "source_channel",
                        "source_session_id", "source_message_id", "confidence",
                        "significance", "summary", *delta_fields,
                    ])
                    writer.writeheader()
                    for event in events:
                        try:
                            delta = json.loads(event.get("delta_json", "{}"))
                        except (TypeError, ValueError, json.JSONDecodeError):
                            delta = {}
                        writer.writerow({
                            **{name: event.get(name, "") for name in writer.fieldnames},
                            **{name: delta.get(name, 0) for name in delta_fields},
                        })
            if workflow_store and workflow_run_id:
                workflow_store.add_artifact(
                    workflow_run_id, artifact_type="emotion_trajectory",
                    name=path.name, uri=str(path),
                    metadata={"format": suffix[1:], "event_count": len(events)},
                )
                workflow_store.finish_run(
                    workflow_run_id, status="success",
                    result_summary=f"已导出 {len(events)} 条情感事件",
                )
            return {"ok": True, "path": str(path), "event_count": len(events),
                    "workflow_run_id": workflow_run_id or None}
        except Exception as exc:
            if workflow_store and workflow_run_id:
                workflow_store.finish_run(workflow_run_id, status="failed", error=str(exc))
            raise

    @_synchronized
    def restore_simulation(self) -> dict:
        """Restore the state captured before the first UI simulation."""
        key = self._active_key
        baseline = self._simulation_baselines.pop(key, None)
        if not baseline:
            return {"ok": False, "reason": "no_simulation_baseline"}
        state = EmotionalStateV3.from_mapping(baseline)
        self._states[key] = state
        delta = AffectDelta(
            event_type="simulation_restore", confidence=1.0, significance=0.2,
            summary="已撤销本轮模拟，恢复模拟前状态",
        )
        self._store.save_state_with_event(state, delta, source_channel="ui_simulation")
        return {"ok": True, "state": state.to_dict()}

    @_synchronized
    def clear_simulation_events(self) -> int:
        self._simulation_baselines.pop(self._active_key, None)
        return self._store.clear_simulation_events(*self._active_key)

    @_synchronized
    def configure_dynamics(self, **values) -> None:
        merged = dict(self._config.get("dynamics", {}))
        merged.update(values)
        config = DynamicsConfig.from_mapping(merged)
        self._dynamics = EmotionalDynamics(config)
        self._config["dynamics"] = {
            name: getattr(config, name) for name in config.__dataclass_fields__
        }
        try:
            from config import save_emotion_config
            save_emotion_config(self._config)
        except Exception as exc:
            logger.warning("保存涟漪 v3 参数失败: %s", exc)

    @_synchronized
    def configure_settings(
        self,
        *,
        semantic_analysis: str | None = None,
        analysis_timeout_seconds: float | None = None,
        significant_memory_enabled: bool | None = None,
        significant_memory_threshold: float | None = None,
        proactive_motive_enabled: bool | None = None,
        saga_bias_scale: float | None = None,
        dynamics: dict | None = None,
        tone_profile: dict | None = None,
    ) -> None:
        if semantic_analysis is not None:
            mode = str(semantic_analysis).lower().strip()
            if mode in {"off", "auto", "local", "cloud"}:
                self._semantic_mode = mode
                self._config["semantic_analysis"] = mode
        if analysis_timeout_seconds is not None:
            self._config["analysis_timeout_seconds"] = max(
                2.0, min(30.0, float(analysis_timeout_seconds))
            )
        if significant_memory_enabled is not None:
            self._config["significant_memory_enabled"] = bool(significant_memory_enabled)
        if significant_memory_threshold is not None:
            self._config["significant_memory_threshold"] = max(
                0.50, min(1.0, float(significant_memory_threshold))
            )
        if proactive_motive_enabled is not None:
            self._config["proactive_motive_enabled"] = bool(proactive_motive_enabled)
        if saga_bias_scale is not None:
            self._config["saga_bias_scale"] = max(0.0, min(2.0, float(saga_bias_scale)))
        if isinstance(dynamics, dict) and dynamics:
            self.configure_dynamics(**dynamics)
        if tone_profile is not None:
            profiles = self._config.setdefault("tone_profiles", {})
            if isinstance(profiles, dict):
                profiles[self._active_key[0]] = tone_profile if isinstance(tone_profile, dict) else {}
        try:
            from config import save_emotion_config
            save_emotion_config(self._config)
        except Exception as exc:
            logger.warning("保存涟漪 v3 设置失败: %s", exc)

    @_synchronized
    def get_config(self) -> dict:
        enabled = bool(self._config.get("proactive_motive_enabled", True))
        return {
            **self._config,
            "semantic_analysis": self._semantic_mode,
            "saga_bias": self._get_saga_bias(self._active_key[0]),
            "dynamics": dict(self._config.get("dynamics", {})),
        }

    def check_tool_allowed(self, tool_name: str) -> tuple[bool, str]:
        """Emotion no longer controls capabilities; security policy owns tool access."""
        return True, ""

    @property
    @_synchronized
    def proactive_allowed(self) -> bool:
        state = self._get_state(*self._active_key)
        if not state.enabled:
            return True
        return not (state.rupture >= 0.78 and state.repair < 0.08)

    @_synchronized
    def get_proactive_motive(self, *, persona_snapshot=None) -> dict:
        key = self._resolve_key(persona_snapshot=persona_snapshot)
        motive = self._dynamics.motive(self._get_state(*key))
        enabled = bool(self._config.get("proactive_motive_enabled", True))
        return {
            "level": motive.level,
            "urgency": motive.urgency,
            "should_contact": motive.should_contact and enabled,
            "should_self_regulate": motive.should_self_regulate and enabled,
            "reason": motive.reason if enabled else "主动动机已在设置中暂停。",
            "enabled": enabled,
        }

    @property
    @_synchronized
    def proactive_interval_multiplier(self) -> float:
        connection = self._get_state(*self._active_key).connection
        contact_threshold = self._dynamics.config.contact_threshold
        if connection >= self._dynamics.config.urgent_threshold:
            return 0.55
        if connection >= contact_threshold:
            return 0.75
        if connection < 0.25:
            return 1.35
        return 1.0

    @staticmethod
    def _legacy_debug_values(state: EmotionalStateV3) -> tuple[dict, dict, str]:
        needs = {
            "respect": round(state.trust * 100, 1),
            "needed": round((1.0 - state.connection) * 100, 1),
            "autonomy": round((1.0 - max(0.0, state.guardedness)) * 100, 1),
            "novelty": round(state.immersion * 100, 1),
            "security": round(state.trust * (1.0 - state.rupture) * 100, 1),
        }
        emotions = {
            "frustration": round(max(0.0, state.arousal - state.valence) * 50, 1),
            "hurt": round(max(0.0, -state.valence) * (40 + state.rupture * 40), 1),
            "anger": round(max(0.0, state.arousal) * state.rupture * 100, 1),
            "loneliness": round(state.connection * 100, 1),
            "excitement": round(max(0.0, state.valence + state.arousal * 0.35) * 70, 1),
        }
        labels = {
            "excited": "明亮活跃", "content": "舒展满足", "pleased": "轻快",
            "agitated": "烦躁", "depressed": "低落", "sullen": "微沉",
            "restless": "躁动", "calm": "平静", "neutral": "平稳",
        }
        return needs, emotions, labels.get(state.mood_cluster, "平稳")

    @_synchronized
    def get_debug_info(self, *, persona_snapshot=None) -> dict:
        key = self._resolve_key(persona_snapshot=persona_snapshot)
        state = self._get_state(*key)
        needs, emotions, middle = self._legacy_debug_values(state)
        events = self._store.recent_events(*key, limit=30)
        saga_bias = self._get_saga_bias(state.persona_id)
        event_stats = self._store.event_stats(
            *key,
            significant=float(self._config.get("significant_memory_threshold", 0.50)),
        )
        axes = {
            "connection": round(state.connection, 4),
            "pride": round(state.pride, 4),
            "guardedness": round(state.guardedness, 4),
            "valence": round(state.valence, 4),
            "arousal": round(state.arousal, 4),
            "immersion": round(state.immersion, 4),
        }
        motive = self._dynamics.motive(state)
        return {
            "version": 3,
            "persona_id": state.persona_id,
            "subject_id": state.subject_id,
            "axes": axes,
            "axis_details": self._axis_details(axes, events, saga_bias),
            "relationship": {
                "trust": round(state.trust, 4),
                "intimacy": round(state.intimacy, 4),
                "rupture": round(state.rupture, 4),
                "repair": round(state.repair, 4),
                "score": round(state.relationship_score, 4),
            },
            "needs": needs,
            "emotions": emotions,
            "deep_layer": round(state.trust * 100, 1),
            "middle_layer": middle,
            "relationship_stage": state.relationship_stage,
            "enabled": state.enabled,
            "semantic_analysis": self._semantic_mode,
            "days_since_start": 0,
            "session_caps": {name: 0.0 for name in needs},
            "memory_count": event_stats["significant"],
            "event_count": event_stats["total"],
            "consecutive_commands": 0,
            "hours_since_interaction": round((time.time() - state.last_interaction) / 3600, 2),
            "last_interaction_hours": round((time.time() - state.last_interaction) / 3600, 2),
            "recent_events": [
                {
                    "type": event.get("event_type", ""),
                    "time": event.get("created_at", 0),
                    "delta": self._event_valence(event),
                    "deltas": self._event_deltas(event),
                    "detail": event.get("summary", ""),
                    "severity": round(float(event.get("significance", 0)) * 5),
                    "source_message_id": event.get("source_message_id"),
                    "state": self._event_state(event),
                }
                for event in events
            ],
            "saga_bias": saga_bias,
            "mood": {"cluster": state.mood_cluster, "label": middle},
            "motive": {
                "level": motive.level,
                "urgency": round(motive.urgency, 4),
                "action": "contact" if motive.should_contact else ("self_regulate" if motive.should_self_regulate else "observation"),
                "reason": motive.reason,
                "will_execute": bool(self._config.get("proactive_motive_enabled", True) and (motive.should_contact or motive.should_self_regulate)),
            },
            "influence": {
                "conversation": self._recent_influence(events),
                "time_drift": self._event_type_influence(events, ("decay", "drift", "idle")),
                "long_story": {key: round(float(saga_bias.get(key, 0.0) or 0.0), 4) for key in ("pride", "valence", "arousal", "guardedness", "connection", "immersion")} | {
                    "count": int(saga_bias.get("saga_count", 0) or 0),
                    "weight_total": float(saga_bias.get("weight_total", 0.0) or 0.0),
                },
            },
            "simulation": {
                "active": key in self._simulation_baselines,
                "can_restore": key in self._simulation_baselines,
            },
            "sync": {"status": "live", "updated_at": time.time(), "poll_interval_ms": 1500},
        }

    @classmethod
    def _axis_details(cls, axes: dict, events: list[dict], bias: dict) -> dict:
        thresholds = {
            "connection": {"observation": 0.35, "contact": 0.20, "urgent": 0.80},
            "pride": {"center": 0.0, "block_contact": 0.50, "defensive": 0.42},
            "guardedness": {"caution": 0.42, "repair": 0.58},
            "valence": {}, "arousal": {"regulation": 0.58}, "immersion": {"activity": 0.30},
        }
        result = {}
        for name, value in axes.items():
            deltas = []
            source = "baseline"
            for event in events[:8]:
                try:
                    delta = float(json.loads(event.get("delta_json", "{}")).get(name, 0.0) or 0.0)
                except (TypeError, ValueError, json.JSONDecodeError):
                    delta = 0.0
                if abs(delta) > 0.0001:
                    deltas.append(delta)
                    source = event.get("event_type", source)
            total = sum(deltas)
            result[name] = {
                "value": value,
                "normalized": round((value + 1.0) / 2.0, 4) if name in {"pride", "valence", "arousal", "guardedness"} else value,
                "delta": round(deltas[0], 4) if deltas else 0.0,
                "trend": "up" if total > 0.001 else ("down" if total < -0.001 else "steady"),
                "velocity": round(total / max(1, len(deltas)), 5),
                "thresholds": thresholds.get(name, {}),
                "source": source,
                "long_story_bias": round(float(bias.get(name, 0.0) or 0.0), 4),
            }
        return result

    @staticmethod
    def _recent_influence(events: list[dict]) -> dict:
        result = {"pride": 0.0, "valence": 0.0, "arousal": 0.0, "connection": 0.0, "guardedness": 0.0, "immersion": 0.0}
        for event in events[:8]:
            if str(event.get("source_channel", "")) == "ui_simulation":
                continue
            try:
                payload = json.loads(event.get("delta_json", "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for key in result:
                result[key] += float(payload.get(key, 0.0) or 0.0)
        return {key: round(value, 4) for key, value in result.items()}

    @staticmethod
    def _event_type_influence(events: list[dict], types: tuple[str, ...]) -> dict:
        result = {"connection": 0.0, "pride": 0.0, "valence": 0.0, "arousal": 0.0, "guardedness": 0.0, "immersion": 0.0}
        for event in events[:8]:
            if not any(token in str(event.get("event_type", "")).lower() for token in types):
                continue
            try:
                payload = json.loads(event.get("delta_json", "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for key in result:
                result[key] += float(payload.get(key, 0.0) or 0.0)
        return {key: round(value, 4) for key, value in result.items()}

    @staticmethod
    def _event_valence(event: dict) -> float:
        try:
            return float(json.loads(event.get("delta_json", "{}")) .get("valence", 0)) * 100
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0.0

    @staticmethod
    def _event_deltas(event: dict) -> dict:
        """Expose every meaningful event delta for the debug console."""
        try:
            payload = json.loads(event.get("delta_json", "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        axis_names = (
            "connection", "pride", "guardedness", "valence", "arousal", "immersion",
            "trust", "intimacy", "rupture", "repair",
        )
        result = {}
        for name in axis_names:
            try:
                value = float(payload.get(name, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(value) > 0.0001:
                result[name] = round(value, 4)
        return result

    @staticmethod
    def _event_state(event: dict) -> dict:
        try:
            payload = json.loads(event.get("resulting_state_json", "") or "{}")
            return {
                "valence": float(payload.get("valence", 0)),
                "arousal": float(payload.get("arousal", 0)),
                "pride": float(payload.get("pride", 0)),
                "guardedness": float(payload.get("guardedness", 0)),
                "connection": float(payload.get("connection", 0)),
                "immersion": float(payload.get("immersion", 0)),
                "trust": float(payload.get("trust", 0)),
                "intimacy": float(payload.get("intimacy", 0)),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @_synchronized
    def set_axes(self, **values) -> None:
        state = self._get_state(*self._active_key)
        for name in ("connection", "pride", "guardedness", "valence", "arousal", "immersion"):
            if name in values:
                setattr(state, name, float(values[name]))
        state.normalize()
        self._store.save_state(state)

    @_synchronized
    def set_relationship(self, **values) -> None:
        state = self._get_state(*self._active_key)
        for name in ("trust", "intimacy", "rupture", "repair"):
            if name in values:
                setattr(state, name, float(values[name]))
        state.normalize()
        self._store.save_state(state)

    def set_needs(self, **values) -> None:
        mapping = {}
        if "respect" in values:
            self.set_relationship(trust=float(values["respect"]) / 100.0)
        if "needed" in values:
            mapping["connection"] = 1.0 - float(values["needed"]) / 100.0
        if "autonomy" in values:
            mapping["guardedness"] = 1.0 - float(values["autonomy"]) / 100.0
        if "novelty" in values:
            mapping["immersion"] = float(values["novelty"]) / 100.0
        if mapping:
            self.set_axes(**mapping)

    def set_emotion(self, **values) -> None:
        valence = (float(values.get("excitement", 0)) - float(values.get("hurt", 0))) / 100.0
        arousal = (
            float(values.get("anger", 0)) + float(values.get("frustration", 0))
        ) / 100.0
        self.set_axes(valence=valence, arousal=arousal)

    def set_deep_trust(self, value: float) -> None:
        self.set_relationship(trust=float(value) / 100.0)

    @_synchronized
    def reset_state(self) -> None:
        old = self._get_state(*self._active_key)
        self._store.delete_scope(*self._active_key)
        state = EmotionalStateV3(
            persona_id=self._active_key[0],
            subject_id=self._active_key[1],
            enabled=old.enabled,
        )
        self._states[self._active_key] = state
        self._store.save_state(state)

    def _apply_event_v2(self, event) -> bool:
        """Debug compatibility for old event buttons during the UI transition."""
        event_type = str(getattr(event, "type", "legacy_event"))
        primary = float(getattr(event, "primary_delta", 0) or 0) / 40.0
        deep = float(getattr(event, "deep_delta", 0) or 0) / 100.0
        negative = primary < 0 or deep < 0
        delta = AffectDelta(
            connection=0.05 if negative else -0.10,
            pride=0.12 if negative else -0.05,
            guardedness=min(0.25, abs(primary)) if negative else -0.04,
            valence=max(-0.30, primary) if negative else min(0.20, primary),
            arousal=min(0.25, abs(primary)) if negative else 0.02,
            trust=deep,
            intimacy=deep * 0.6,
            rupture=min(0.4, abs(deep) * 4) if negative else 0.0,
            repair=min(0.3, deep * 3) if deep > 0 else 0.0,
            event_type=event_type,
            confidence=1.0,
            significance=min(1.0, float(getattr(event, "severity", 1)) / 5.0),
            summary=str(getattr(event, "detail", "") or event_type),
        ).bounded()
        with self._lock:
            state = self._get_state(*self._active_key)
            state.apply(delta)
            self._store.append_event(state, delta)
            self._store.save_state(state)
        return True


_manager: Optional[EmotionManager] = None
_manager_lock = threading.Lock()


def get_manager() -> EmotionManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = EmotionManager()
    return _manager
