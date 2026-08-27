"""Core data models for the Ripple emotional system v3."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


# Version 2 adds the signed pride axis and keeps guardedness as a non-negative
# safety axis. Older snapshots are loaded with pride=0 and normalized safely.
STATE_SCHEMA_VERSION = 2
DEFAULT_PERSONA_ID = "default-lianxin"
DEFAULT_SUBJECT_ID = "owner"


def clamp(value: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return max(low, min(high, number))


@dataclass
class AffectDelta:
    """A bounded appraisal result applied atomically to one state snapshot."""

    connection: float = 0.0
    pride: float = 0.0
    guardedness: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0
    immersion: float = 0.0
    trust: float = 0.0
    intimacy: float = 0.0
    rupture: float = 0.0
    repair: float = 0.0
    event_type: str = "neutral"
    confidence: float = 0.5
    significance: float = 0.0
    summary: str = ""

    def bounded(self) -> "AffectDelta":
        return AffectDelta(
            connection=clamp(self.connection, -0.60, 0.30),
            pride=clamp(self.pride, -0.30, 0.30),
            guardedness=clamp(self.guardedness, -0.30, 0.30),
            valence=clamp(self.valence, -0.35, 0.35),
            arousal=clamp(self.arousal, -0.35, 0.35),
            immersion=clamp(self.immersion, -0.50, 0.50),
            trust=clamp(self.trust, -0.08, 0.05),
            intimacy=clamp(self.intimacy, -0.08, 0.08),
            rupture=clamp(self.rupture, -0.40, 0.50),
            repair=clamp(self.repair, -0.20, 0.40),
            event_type=str(self.event_type or "neutral")[:64],
            confidence=clamp(self.confidence, 0.0, 1.0),
            significance=clamp(self.significance, 0.0, 1.0),
            summary=str(self.summary or "")[:240],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.bounded())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "AffectDelta":
        payload = payload if isinstance(payload, Mapping) else {}
        numeric = (
            "connection", "pride", "guardedness", "valence", "arousal", "immersion",
            "trust", "intimacy", "rupture", "repair", "confidence",
            "significance",
        )
        values = {name: payload.get(name, getattr(cls(), name)) for name in numeric}
        values["event_type"] = payload.get("event_type", "neutral")
        values["summary"] = payload.get("summary", "")
        return cls(**values).bounded()


@dataclass
class EmotionalStateV3:
    """Persistent state split into affect, motivation, and relationship layers."""

    persona_id: str = DEFAULT_PERSONA_ID
    subject_id: str = DEFAULT_SUBJECT_ID

    # Fast affective layer.
    valence: float = 0.0
    arousal: float = 0.0
    # Jiwen-compatible pride axis: negative = relaxed/ready to yield, positive = prideful/defensive.
    pride: float = 0.0
    guardedness: float = 0.12

    # Homeostatic and activity layer.
    connection: float = 0.08
    immersion: float = 0.0

    # Slow relationship layer.
    trust: float = 0.62
    intimacy: float = 0.56
    rupture: float = 0.0
    repair: float = 0.0

    last_activity_type: str = ""
    last_activity_label: str = ""
    last_activity_at: float = 0.0
    last_user_message: str = ""
    last_update: float = field(default_factory=time.time)
    last_interaction: float = field(default_factory=time.time)

    # 最近一次用户回归前的空闲时长（小时），用于重逢反应判定。
    last_idle_hours: float = 0.0
    # 重逢反应剩余轮次：回归后前 2 轮呈现想念/抱怨/委屈，随后自然消退。
    reunion_turns_remaining: int = 0
    last_proactive_at: float = 0.0
    enabled: bool = True
    schema_version: int = STATE_SCHEMA_VERSION

    def normalize(self) -> "EmotionalStateV3":
        self.persona_id = str(self.persona_id or DEFAULT_PERSONA_ID)[:128]
        self.subject_id = str(self.subject_id or DEFAULT_SUBJECT_ID)[:128]
        self.connection = clamp(self.connection, 0.0, 1.0)
        self.immersion = clamp(self.immersion, 0.0, 1.0)
        self.valence = clamp(self.valence, -1.0, 1.0)
        self.arousal = clamp(self.arousal, -1.0, 1.0)
        self.pride = clamp(self.pride, -1.0, 1.0)
        # 防御感是独立的非负安全轴；Jiwen 的正负语义由 pride 承担。
        self.guardedness = clamp(self.guardedness, 0.0, 1.0)
        self.trust = clamp(self.trust, 0.0, 1.0)
        self.intimacy = clamp(self.intimacy, 0.0, 1.0)
        self.rupture = clamp(self.rupture, 0.0, 1.0)
        self.repair = clamp(self.repair, 0.0, 1.0)
        self.last_activity_type = str(self.last_activity_type or "")[:64]
        self.last_activity_label = str(self.last_activity_label or "")[:160]
        self.last_user_message = str(self.last_user_message or "")[:500]

        self.last_idle_hours = clamp(self.last_idle_hours, 0.0, 24.0 * 365.0)
        self.reunion_turns_remaining = max(0, int(self.reunion_turns_remaining or 0))
        now = time.time()
        for name in ("last_update", "last_interaction", "last_activity_at", "last_proactive_at"):
            value = clamp(getattr(self, name), 0.0, now + 300.0)
            setattr(self, name, value)
        if self.last_update <= 0:
            self.last_update = now
        if self.last_interaction <= 0:
            self.last_interaction = self.last_update
        self.enabled = bool(self.enabled)
        self.schema_version = STATE_SCHEMA_VERSION
        return self

    def to_dict(self) -> dict[str, Any]:
        self.normalize()
        return asdict(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "EmotionalStateV3":
        payload = payload if isinstance(payload, Mapping) else {}
        allowed = cls.__dataclass_fields__
        values = {name: payload[name] for name in allowed if name in payload}
        return cls(**values).normalize()

    @property
    def relationship_score(self) -> float:
        score = self.trust * 0.55 + self.intimacy * 0.45
        score -= self.rupture * 0.35
        score += min(self.repair, self.rupture) * 0.10
        return clamp(score, 0.0, 1.0)

    @property
    def relationship_stage(self) -> str:
        score = self.relationship_score
        if score >= 0.88:
            return "灵魂伴侣"
        if score >= 0.74:
            return "挚友"
        if score >= 0.60:
            return "朋友"
        if score >= 0.42:
            return "相识"
        return "初见"

    @property
    def mood_cluster(self) -> str:
        v, a = self.valence, self.arousal
        if v > 0.18 and a > 0.18:
            return "excited"
        if v > 0.18 and a < -0.18:
            return "content"
        if v > 0.18:
            return "pleased"
        if v < -0.18 and a > 0.18:
            return "agitated"
        if v < -0.18 and a < -0.18:
            return "depressed"
        if v < -0.18:
            return "sullen"
        if a > 0.18:
            return "restless"
        if a < -0.18:
            return "calm"
        return "neutral"

    def apply(self, delta: AffectDelta) -> None:
        self.normalize()
        delta = delta.bounded()
        self.connection += delta.connection
        self.pride += delta.pride
        self.guardedness += delta.guardedness
        self.valence += delta.valence
        self.arousal += delta.arousal
        self.immersion += delta.immersion
        self.trust += delta.trust
        self.intimacy += delta.intimacy
        self.rupture += delta.rupture
        self.repair += delta.repair

        # Repair can heal rupture, but cannot erase it instantly.
        if delta.repair > 0:
            self.rupture -= delta.repair * 0.45
        if delta.rupture > 0:
            self.repair -= delta.rupture * 0.25
        self.normalize()


@dataclass(frozen=True)
class ProactiveMotive:
    level: str
    urgency: float
    should_contact: bool
    should_self_regulate: bool
    reason: str
