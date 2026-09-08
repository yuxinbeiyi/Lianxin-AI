"""可解释、可撤销的人格演化层。

成长记录不改写 PersonaProfile 的核心文本；它作为请求级动态上下文叠加，
从而保留稳定人格、完整审计和安全回滚能力。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.paths import get_user_data_dir


LOW_RISK_KINDS = {"interaction_style", "topic_interest", "proactive_preference", "knowledge_hygiene"}
VALID_STATUSES = {"pending", "applied", "reverted", "dismissed", "expired"}
ALLOWED_FIELDS = {"response_length", "response_structure", "interaction_tone", "topic_interest", "proactive_preference", "fact_verification"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class GrowthEvent:
    id: int
    persona_id: str
    kind: str
    title: str
    detail: str
    evidence: str
    confidence: float
    status: str
    created_at: str
    applied_at: str = ""
    field: str = ""
    old_value: str = ""
    proposed_value: str = ""
    risk: str = "low"
    evidence_ref: str = ""
    version_id: int = 0
    supersedes_id: int = 0
    evidence_count: int = 1


@dataclass(frozen=True)
class PersonaGrowthVersion:
    id: int
    persona_id: str
    parent_version_id: int
    event_id: int
    changes_json: str
    created_at: str


class PersonaGrowthStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or (get_user_data_dir() / "persona_growth.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS persona_growth_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_persona_growth_persona_time
                    ON persona_growth_events(persona_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS persona_growth_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id TEXT NOT NULL,
                    parent_version_id INTEGER NOT NULL DEFAULT 0,
                    event_id INTEGER NOT NULL,
                    changes_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_persona_growth_versions_persona
                    ON persona_growth_versions(persona_id, id DESC);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(persona_growth_events)")}
            migrations = {
                "field": "TEXT NOT NULL DEFAULT ''",
                "old_value": "TEXT NOT NULL DEFAULT ''",
                "proposed_value": "TEXT NOT NULL DEFAULT ''",
                "risk": "TEXT NOT NULL DEFAULT 'low'",
                "evidence_ref": "TEXT NOT NULL DEFAULT ''",
                "version_id": "INTEGER NOT NULL DEFAULT 0",
                "supersedes_id": "INTEGER NOT NULL DEFAULT 0",
                "evidence_count": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE persona_growth_events ADD COLUMN {name} {definition}")
        finally:
            conn.close()

    @staticmethod
    def _event(row: sqlite3.Row) -> GrowthEvent:
        return GrowthEvent(
            id=int(row["id"]), persona_id=str(row["persona_id"]), kind=str(row["kind"]),
            title=str(row["title"]), detail=str(row["detail"]), evidence=str(row["evidence"]),
            confidence=float(row["confidence"]), status=str(row["status"]),
            created_at=str(row["created_at"]), applied_at=str(row["applied_at"]),
            field=str(row["field"] or ""), old_value=str(row["old_value"] or ""),
            proposed_value=str(row["proposed_value"] or ""), risk=str(row["risk"] or "low"),
            evidence_ref=str(row["evidence_ref"] or ""), version_id=int(row["version_id"] or 0),
            supersedes_id=int(row["supersedes_id"] or 0),
            evidence_count=int(row["evidence_count"] or 1),
        )

    def create(self, *, persona_id: str, kind: str, title: str, detail: str,
               evidence: str = "", confidence: float = 0.5, field: str = "",
               old_value: str = "", proposed_value: str = "", risk: str = "low",
               evidence_ref: str = "", supersedes_id: int = 0, evidence_count: int = 1) -> GrowthEvent:
        conn = self._connect()
        try:
            cursor = conn.execute(
                """INSERT INTO persona_growth_events
                   (persona_id,kind,title,detail,evidence,confidence,status,created_at,
                    field,old_value,proposed_value,risk,evidence_ref,supersedes_id,evidence_count)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (persona_id, kind, title[:120], detail[:1200], evidence[:1600],
                 max(0.0, min(1.0, float(confidence))), "pending", _now(), field[:80],
                 old_value[:300], proposed_value[:500], risk[:32], evidence_ref[:300], int(supersedes_id or 0),
                 max(1, int(evidence_count))),
            )
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._event(row)
        finally:
            conn.close()

    def list(self, persona_id: str, limit: int = 100) -> list[GrowthEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM persona_growth_events WHERE persona_id=? ORDER BY id DESC LIMIT ?",
                (persona_id, max(1, int(limit))),
            ).fetchall()
            return [self._event(row) for row in rows]
        finally:
            conn.close()

    def set_status(self, event_id: int, status: str) -> GrowthEvent | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"未知成长状态: {status}")
        conn = self._connect()
        try:
            before = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            if before is None:
                return None
            applied_at = _now() if status == "applied" else ""
            version_id = int(before["version_id"] or 0)
            if status == "applied" and before["status"] != "applied":
                parent = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) AS id FROM persona_growth_versions WHERE persona_id=?",
                    (before["persona_id"],),
                ).fetchone()["id"]
                changes = json.dumps({
                    "field": before["field"], "old_value": before["old_value"],
                    "proposed_value": before["proposed_value"], "event_id": int(event_id),
                }, ensure_ascii=False)
                cursor = conn.execute(
                    "INSERT INTO persona_growth_versions (persona_id,parent_version_id,event_id,changes_json,created_at) VALUES (?,?,?,?,?)",
                    (before["persona_id"], int(parent), int(event_id), changes, _now()),
                )
                version_id = int(cursor.lastrowid)
            conn.execute("UPDATE persona_growth_events SET status=?, applied_at=?, version_id=? WHERE id=?",
                         (status, applied_at, version_id, int(event_id)))
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            return self._event(row) if row else None
        finally:
            conn.close()

    def active(self, persona_id: str) -> list[GrowthEvent]:
        return [item for item in self.list(persona_id) if item.status == "applied"]

    def support(self, event_id: int) -> GrowthEvent | None:
        conn = self._connect()
        try:
            conn.execute("UPDATE persona_growth_events SET evidence_count=evidence_count+1 WHERE id=?", (int(event_id),))
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            return self._event(row) if row else None
        finally:
            conn.close()

    def list_versions(self, persona_id: str, limit: int = 100) -> list[PersonaGrowthVersion]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM persona_growth_versions WHERE persona_id=? ORDER BY id DESC LIMIT ?",
                                (persona_id, max(1, int(limit)))).fetchall()
            return [PersonaGrowthVersion(int(row["id"]), str(row["persona_id"]), int(row["parent_version_id"]),
                                         int(row["event_id"]), str(row["changes_json"]), str(row["created_at"]))
                    for row in rows]
        finally:
            conn.close()

    def rollback_to_version(self, persona_id: str, version_id: int) -> list[GrowthEvent]:
        """Stop effects applied after a version while preserving their audit records."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM persona_growth_events WHERE persona_id=? AND status='applied' AND version_id>?",
                                (persona_id, max(0, int(version_id)))).fetchall()
            conn.execute("UPDATE persona_growth_events SET status='reverted', applied_at='' WHERE persona_id=? AND status='applied' AND version_id>?",
                         (persona_id, max(0, int(version_id))))
            return [self._event(row) for row in rows]
        finally:
            conn.close()


class PersonaGrowthService:
    SETTINGS_FILE = "persona_growth_settings.json"

    def __init__(self, store: PersonaGrowthStore | None = None, settings_path: Path | None = None):
        self.store = store or PersonaGrowthStore()
        self.settings_path = settings_path or (get_user_data_dir() / self.SETTINGS_FILE)

    def settings(self) -> dict:
        defaults = {
            "mode": "confirm",  # off | confirm | low_risk_auto
            "allow_proactive_requests": True,
            "allow_photo_invites": False,
            "request_cooldown_hours": 72,
            "last_proactive_request_at": "",
            "growth_paused_until": "",
            "proactive_category_cooldowns": {},
            "proactive_rejections": {},
            "last_proactive_reason": {},
        }
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({key: data[key] for key in defaults if key in data})
        except Exception:
            pass
        return defaults

    def save_settings(self, values: dict) -> dict:
        current = self.settings()
        current.update({key: values[key] for key in current if key in values})
        if current["mode"] not in {"off", "confirm", "low_risk_auto"}:
            current["mode"] = "confirm"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current

    def growth_is_paused(self) -> bool:
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(self.settings()["growth_paused_until"])
        except (TypeError, ValueError):
            return False

    def pause_growth(self, hours: int) -> dict:
        until = datetime.now(timezone.utc) + timedelta(hours=max(0, int(hours)))
        return self.save_settings({"growth_paused_until": until.isoformat(timespec="seconds")})

    def export_events(self, persona_id: str) -> str:
        rows = [item.__dict__ for item in self.store.list(persona_id)]
        return json.dumps({"persona_id": persona_id, "events": rows, "versions": [item.__dict__ for item in self.store.list_versions(persona_id)]}, ensure_ascii=False, indent=2)

    def clear_events(self, persona_id: str) -> None:
        conn = self.store._connect()
        try:
            conn.execute("DELETE FROM persona_growth_versions WHERE persona_id=?", (persona_id,))
            conn.execute("DELETE FROM persona_growth_events WHERE persona_id=?", (persona_id,))
        finally:
            conn.close()

    def review_expired(self, persona_id: str, days: int = 90) -> list[GrowthEvent]:
        """Expire stale applied preferences without deleting their audit trail."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        expired = []
        for event in self.store.active(persona_id):
            try:
                timestamp = datetime.fromisoformat(event.applied_at or event.created_at)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if timestamp.astimezone(timezone.utc) < cutoff:
                updated = self.store.set_status(event.id, "expired")
                if updated:
                    expired.append(updated)
        return expired

    def summary(self, persona_id: str) -> dict:
        events = self.store.list(persona_id)
        counts = {status: sum(item.status == status for item in events) for status in VALID_STATUSES}
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        weekly = 0
        for event in events:
            try:
                created = datetime.fromisoformat(event.created_at)
                weekly += int(created.astimezone(timezone.utc) >= week_ago)
            except ValueError:
                pass
        settings = self.settings()
        rejections = settings.get("proactive_rejections", {}) or {}
        return {
            "counts": counts, "weekly_changes": weekly,
            "adoption_rate": round(counts["applied"] / max(1, counts["applied"] + counts["dismissed"] + counts["reverted"]), 3),
            "proactive_rejections": {str(key): int(value or 0) for key, value in rejections.items()},
            "local_only": True,
        }

    def propose(self, *, persona_id: str, kind: str, title: str, detail: str,
                evidence: str, confidence: float, field: str = "",
                old_value: str = "", proposed_value: str = "", risk: str = "low",
                evidence_ref: str = "") -> GrowthEvent | None:
        settings = self.settings()
        if settings["mode"] == "off" or self.growth_is_paused():
            return None
        if field and field not in ALLOWED_FIELDS:
            return None
        existing = self.store.list(persona_id)
        # Repeated sends and request retries must not create duplicate records.
        if any(item.field == field and item.proposed_value == proposed_value
               and item.status in {"pending", "applied"} for item in existing):
            return None
        conflict = next((item for item in existing if item.field == field and item.status == "applied"
                         and item.proposed_value != proposed_value), None)
        if conflict:
            risk = "confirmation_required"
            evidence = f"{evidence}；与已生效的「{conflict.title}」冲突，需用户确认。"
        event = self.store.create(persona_id=persona_id, kind=kind, title=title,
                                  detail=detail, evidence=evidence, confidence=confidence,
                                  field=field, old_value=old_value, proposed_value=proposed_value,
                                  risk=risk, evidence_ref=evidence_ref,
                                  supersedes_id=conflict.id if conflict else 0)
        return event

    def observe_feedback(self, persona_id: str, user_message: str) -> GrowthEvent | None:
        """Only explicit user feedback can create an automatic candidate in v1."""
        text = str(user_message or "").strip()
        if not text or len(text) > 500:
            return None
        # Fact-verification feedback is kept as code points to avoid source
        # encoding issues in older Windows checkouts.
        fact_feedback = tuple("".join(map(chr, codes)) for codes in (
            (19981, 35201, 32993, 32534), (19981, 35201, 32534, 36896),
            (20808, 25628, 32034), (20808, 26597, 35777), (20808, 26680, 23454),
            (19981, 20102, 35299, 23601, 26597), (19981, 30830, 23450, 26102, 26597),
        ))
        normalized_text = text.replace(" ", "")
        if any(token in normalized_text for token in fact_feedback):
            return self.propose(persona_id=persona_id, kind="knowledge_hygiene",
                                title="\u56de\u7b54\u672a\u77e5\u4e8b\u5b9e\u524d\u5148\u6838\u9a8c",
                                detail="\u9047\u5230\u65e0\u6cd5\u786e\u8ba4\u7684\u4e8b\u5b9e\u3001\u4f5c\u54c1\u3001\u4eba\u7269\u3001\u6570\u5b57\u3001\u65e5\u671f\u6216\u5b9e\u65f6\u4fe1\u606f\u65f6\uff0c\u5148\u641c\u7d22\u5e76\u6838\u5bf9\u53ef\u9760\u6765\u6e90\uff1b\u65e0\u6cd5\u786e\u8ba4\u65f6\u660e\u786e\u8bf4\u660e\u4e0d\u786e\u5b9a\uff0c\u4e0d\u80fd\u51ed\u7a7a\u8865\u5168\u7b54\u6848\u3002",
                                evidence="\u7528\u6237\u660e\u786e\u8981\u6c42\u4e8b\u5b9e\u6838\u9a8c",
                                confidence=0.9, field="fact_verification",
                                old_value="\u672a\u542f\u7528", proposed_value="search_before_answer", risk="low",
                                evidence_ref="chat_feedback")
        positive = ("以后你可以", "希望你", "你可以多", "更喜欢你", "请你以后",
                    "不要胡编", "不要编造", "先搜索", "先查证", "先核实",
                    "核对来源", "不了解就查", "不确定时查询", "不要凭空猜",
                    "回答前确认事实")
        if not any(token in text.replace(" ", "") for token in positive):
            return None
        proposal = self._interpret_feedback(text)
        if proposal is None:
            return None
        event = self.propose(persona_id=persona_id, evidence="用户明确给出的长期互动偏好",
                             evidence_ref="chat_feedback", confidence=0.9, **proposal)
        if event is None:
            existing = next((item for item in self.store.list(persona_id)
                             if item.field == proposal["field"] and item.proposed_value == proposal["proposed_value"]
                             and item.status in {"pending", "applied"}), None)
            event = self.store.support(existing.id) if existing else None
        settings = self.settings()
        if (event and event.status == "pending" and settings["mode"] == "low_risk_auto"
                and event.kind in LOW_RISK_KINDS and event.risk == "low" and event.evidence_count >= 2):
            return self.store.set_status(event.id, "applied")
        return event

    @staticmethod
    def _interpret_feedback(text: str) -> dict | None:
        """Conservative, allow-listed v1 interpreter; never turns free text into a prompt command."""
        normalized = text.replace(" ", "")
        if "先给结论" in normalized or "结论在前" in normalized:
            return {"kind": "interaction_style", "title": "回答先给结论", "detail": "回答时先给结论，再补充必要依据。",
                    "field": "response_structure", "old_value": "standard", "proposed_value": "conclusion_first", "risk": "low"}
        if any(word in normalized for word in ("简短", "简洁", "精简", "少说一点")):
            return {"kind": "interaction_style", "title": "回答更简洁", "detail": "默认优先给出简洁回答，用户要求时再展开。",
                    "field": "response_length", "old_value": "standard", "proposed_value": "compact", "risk": "low"}
        if any(word in normalized for word in ("详细", "展开说", "说详细")):
            return {"kind": "interaction_style", "title": "回答更详细", "detail": "默认补充更多背景和步骤，避免只有结论。",
                    "field": "response_length", "old_value": "standard", "proposed_value": "detailed", "risk": "low"}
        if any(word in normalized for word in ("少用表情", "不要太多表情", "少发表情")):
            return {"kind": "interaction_style", "title": "减少表情使用", "detail": "表达时少用表情符号，保持自然克制。",
                     "field": "interaction_tone", "old_value": "standard", "proposed_value": "fewer_emojis", "risk": "low"}
        if any(word in normalized for word in ("不要胡编", "不要编造", "先搜索", "先查证", "先核实",
                                               "核对来源", "不了解就查", "不确定时查询", "不要凭空猜",
                                               "回答前确认事实")):
            return {"kind": "knowledge_hygiene", "title": "回答未知事实前先核验",
                    "detail": "遇到无法确认的事实、作品、人物、数字、日期或实时信息时，先搜索并核对可靠来源；无法确认时明确说明不确定，不能凭空补全答案。",
                    "field": "fact_verification", "old_value": "未启用", "proposed_value": "search_before_answer", "risk": "low"}
        return None

    def dynamic_context(self, persona_id: str) -> str:
        # A lightweight periodic review happens whenever this persona is used.
        # Expired entries remain in the audit trail but stop influencing replies.
        self.review_expired(persona_id)
        traits = self.store.active(persona_id)
        if not traits:
            return ""
        lines = ["【已确认的成长偏好】", "以下是可调整的互动偏好，不得覆盖身份、隐私、权限与安全边界。"]
        lines.extend(f"- {item.field or item.title}: {item.proposed_value or item.detail}" for item in traits[:12])
        return "\n".join(lines)

    def next_proactive_request(self, persona_id: str) -> dict | None:
        settings = self.settings()
        if not settings["allow_proactive_requests"]:
            return None
        try:
            last_request = datetime.fromisoformat(str(settings["last_proactive_request_at"]))
            cooldown = timedelta(hours=max(1, int(settings["request_cooldown_hours"])))
            if datetime.now(last_request.tzinfo or timezone.utc) - last_request < cooldown:
                return None
        except (TypeError, ValueError):
            pass
        active = self.store.active(persona_id)
        cooldowns = settings.get("proactive_category_cooldowns", {}) or {}
        rejections = settings.get("proactive_rejections", {}) or {}
        def allowed(kind: str, hours: int) -> bool:
            if int(rejections.get(kind, 0) or 0) >= 2:
                return False
            try:
                return datetime.now(timezone.utc) >= datetime.fromisoformat(str(cooldowns.get(kind, "")))
            except (TypeError, ValueError):
                return True
        if any(item.kind == "topic_interest" for item in active):
            if allowed("curiosity", 72):
                return {"kind": "curiosity", "reason_source": "已确认的话题兴趣", "reason_summary": "基于你允许的兴趣分享偏好", "allow_skip": True, "instruction": "可以自然问问用户最近有没有一件有趣的事愿意分享；允许对方跳过。"}
        if settings["allow_photo_invites"] and allowed("photo_invite", 336):
            return {"kind": "photo_invite", "reason_source": "用户已开启照片邀请", "reason_summary": "照片邀请已由用户单独允许", "allow_skip": True, "instruction": "可温和邀请用户分享一张最近想让你看的照片；明确说明完全自愿、可以不发，且不要暗示保存或上传。"}
        if allowed("curiosity", 72):
            return {"kind": "curiosity", "reason_source": "主动诉求设置", "reason_summary": "用户允许偶尔的兴趣分享邀请", "allow_skip": True, "instruction": "可以偶尔问问用户最近有没有一件有趣的事愿意分享；允许对方跳过。"}
        return None

    def record_proactive_result(self, kind: str, action: str) -> dict:
        settings = self.settings()
        kind = str(kind or "curiosity")
        cooldowns = dict(settings.get("proactive_category_cooldowns", {}) or {})
        rejections = dict(settings.get("proactive_rejections", {}) or {})
        if action == "reject":
            rejections[kind] = int(rejections.get(kind, 0) or 0) + 1
        elif action == "accept":
            rejections[kind] = 0
        hours = 336 if kind == "photo_invite" else 72
        cooldowns[kind] = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
        return self.save_settings({"proactive_category_cooldowns": cooldowns, "proactive_rejections": rejections})


_service: PersonaGrowthService | None = None


def get_persona_growth_service() -> PersonaGrowthService:
    global _service
    if _service is None:
        _service = PersonaGrowthService()
    return _service
