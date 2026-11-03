"""可解释、可撤销的人格演化层。

成长记录不改写 PersonaProfile 的核心文本；它作为请求级动态上下文叠加，
从而保留稳定人格、完整审计和安全回滚能力。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.paths import get_user_data_dir


LOW_RISK_KINDS = {"interaction_style", "topic_interest", "proactive_preference", "knowledge_hygiene"}
VALID_STATUSES = {"pending", "applied", "reverted", "dismissed", "expired", "superseded"}
ALLOWED_FIELDS = {"response_length", "response_structure", "interaction_tone", "teasing_level", "topic_interest", "proactive_preference", "fact_verification", "support_style"}
NON_DURABLE = r"今天|今晚|这次|这一[轮次]|本轮|这周|本周|这几天|这段时间|最近|暂时|暂且|目前|现在|假如|假设|举例|比如|例如|示例|开玩笑|不要记|别记|不用记|不保存|不要保存|不希望你|不是让你|不是要你"
SENSITIVE = r"密码|口令|api[_ -]?key|access[_ -]?token|身份证"
SCENES = {"all", "main_chat", "proactive", "qq_chat", "tree_hole", "study_room", "time_capsule", "bilibili_comment"}
PREFERENCE_TEXT = {
    ("response_length", "compact"): "日常交流默认简洁，用户要求详细或任务需要时充分展开。",
    ("response_length", "detailed"): "默认补充必要背景和步骤，用户要求简短时收敛。",
    ("response_structure", "conclusion_first"): "先给结论，再补充必要依据。",
    ("interaction_tone", "fewer_emojis"): "减少表情符号，保持自然克制。",
    ("teasing_level", "gentler"): "减少针对用户的挖苦，保留人格本色，认真求助时优先提供帮助。",
    ("support_style", "listen_first"): "用户倾诉或低落时，先倾听和回应感受，给建议前确认是否需要。",
    ("fact_verification", "search_before_answer"): "未知事实先核验，不能确认时明确说明不确定；真实性是基础要求，不依赖成长开关。",
}


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
    scope: str = "persona"
    scene: str = "all"
    source_kind: str = "legacy"
    source_session_id: int = 0
    source_message_ids: tuple[int, ...] = ()
    source_channel: str = ""
    expires_at: str = ""


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
                "scope": "TEXT NOT NULL DEFAULT 'persona'",
                "scene": "TEXT NOT NULL DEFAULT 'all'",
                "source_kind": "TEXT NOT NULL DEFAULT 'legacy'",
                "source_session_id": "INTEGER NOT NULL DEFAULT 0",
                "source_message_ids": "TEXT NOT NULL DEFAULT '[]'",
                "source_channel": "TEXT NOT NULL DEFAULT ''",
                "expires_at": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE persona_growth_events ADD COLUMN {name} {definition}")
            conn.execute("CREATE TABLE IF NOT EXISTS persona_growth_evidence (event_id INTEGER NOT NULL, evidence_key TEXT NOT NULL, UNIQUE(event_id,evidence_key))")
            evidence_columns = {row["name"] for row in conn.execute("PRAGMA table_info(persona_growth_evidence)")}
            for name in ("source_ref", "evidence_text"):
                if name not in evidence_columns:
                    conn.execute(f"ALTER TABLE persona_growth_evidence ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
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
            scope=str(row["scope"]), scene=str(row["scene"]), source_kind=str(row["source_kind"]),
            source_session_id=int(row["source_session_id"] or 0),
            source_message_ids=tuple(json.loads(row["source_message_ids"] or "[]")),
            source_channel=str(row["source_channel"]), expires_at=str(row["expires_at"]),
        )

    def create(self, *, persona_id: str, kind: str, title: str, detail: str,
               evidence: str = "", confidence: float = 0.5, field: str = "",
               old_value: str = "", proposed_value: str = "", risk: str = "low",
               evidence_ref: str = "", supersedes_id: int = 0, evidence_count: int = 1,
               scope: str = "persona", scene: str = "all", source_kind: str = "legacy",
               source_session_id: int = 0, source_message_ids=(), source_channel: str = "",
               expires_at: str = "", deduplicate: bool = False) -> GrowthEvent | None:
        if scope not in {"shared", "persona"} or scene not in SCENES:
            raise ValueError("无效的成长作用范围")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if deduplicate and conn.execute(
                """SELECT 1 FROM persona_growth_events WHERE scope=? AND scene=? AND field=?
                   AND (scope='shared' OR persona_id=?) AND proposed_value=?
                   AND (status IN ('pending','applied') OR (?!='legacy' AND ?!='' AND evidence_ref=?)) LIMIT 1""",
                (scope, scene, field, persona_id, proposed_value, source_kind, evidence_ref, evidence_ref),
            ).fetchone():
                conn.commit()
                return None
            cursor = conn.execute(
                """INSERT INTO persona_growth_events
                   (persona_id,kind,title,detail,evidence,confidence,status,created_at,
                    field,old_value,proposed_value,risk,evidence_ref,supersedes_id,evidence_count,
                    scope,scene,source_kind,source_session_id,source_message_ids,source_channel,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (persona_id, kind, title[:120], detail[:1200], evidence[:1600],
                 max(0.0, min(1.0, float(confidence))), "pending", _now(), field[:80],
                 old_value[:300], proposed_value[:500], risk[:32], evidence_ref[:300], int(supersedes_id or 0),
                 max(1, int(evidence_count)), scope, scene, source_kind, int(source_session_id or 0),
                 json.dumps(list(source_message_ids)), source_channel[:80], expires_at),
            )
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (cursor.lastrowid,)).fetchone()
            conn.commit()
            return self._event(row)
        finally:
            conn.close()

    def list(self, persona_id: str | None, limit: int = 100) -> list[GrowthEvent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM persona_growth_events WHERE (? IS NULL OR scope='shared' OR persona_id=?) ORDER BY id DESC LIMIT ?",
                (persona_id, persona_id, max(1, int(limit))),
            ).fetchall()
            return [self._event(row) for row in rows]
        finally:
            conn.close()

    def set_status(self, event_id: int, status: str, *, expected_statuses: set[str] | None = None) -> GrowthEvent | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"未知成长状态: {status}")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            before = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            if before is None:
                conn.rollback()
                return None
            if expected_statuses is not None and before["status"] not in expected_statuses:
                raise ValueError("成长状态已变化，请刷新后重试")
            if before["status"] == status:
                conn.commit()
                return self._event(before)
            applied_at = _now() if status == "applied" else ""
            version_id = int(before["version_id"] or 0)
            if status == "applied" and before["status"] != "applied":
                if before["expires_at"]:
                    expires = datetime.fromisoformat(before["expires_at"])
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if expires <= datetime.now(timezone.utc):
                        raise ValueError("该偏好已到期，不能重新采纳")
                replaced = conn.execute(
                    "SELECT id FROM persona_growth_events WHERE status='applied' AND id!=? AND scope=? AND scene=? AND field=? AND (scope='shared' OR persona_id=?)",
                    (event_id, before["scope"], before["scene"], before["field"], before["persona_id"]),
                ).fetchall() if before["field"] else []
                for previous in replaced:
                    conn.execute("UPDATE persona_growth_events SET status='superseded' WHERE id=?", (previous["id"],))
                parent = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) AS id FROM persona_growth_versions WHERE persona_id=?",
                    (before["persona_id"],),
                ).fetchone()["id"]
                changes = json.dumps({
                    "field": before["field"], "old_value": before["old_value"],
                    "proposed_value": before["proposed_value"], "event_id": int(event_id),
                    "replaced_ids": [previous["id"] for previous in replaced],
                }, ensure_ascii=False)
                cursor = conn.execute(
                    "INSERT INTO persona_growth_versions (persona_id,parent_version_id,event_id,changes_json,created_at) VALUES (?,?,?,?,?)",
                    (before["persona_id"], int(parent), int(event_id), changes, _now()),
                )
                version_id = int(cursor.lastrowid)
            elif status == "reverted" and before["status"] == "applied":
                version = conn.execute("SELECT changes_json FROM persona_growth_versions WHERE id=?", (version_id,)).fetchone()
                changes = json.loads(version["changes_json"]) if version else {}
                for previous_id in changes.get("replaced_ids", []):
                    conn.execute("UPDATE persona_growth_events SET status='applied' WHERE id=? AND status='superseded'", (previous_id,))
            conn.execute("UPDATE persona_growth_events SET status=?, applied_at=?, version_id=? WHERE id=?",
                         (status, applied_at, version_id, int(event_id)))
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            conn.commit()
            return self._event(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def active(self, persona_id: str) -> list[GrowthEvent]:
        conn = self._connect()
        try:
            return [self._event(row) for row in conn.execute(
                "SELECT * FROM persona_growth_events WHERE status='applied' AND (scope='shared' OR persona_id=?) ORDER BY id DESC", (persona_id,),
            )]
        finally:
            conn.close()

    def get(self, event_id: int) -> GrowthEvent | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            return self._event(row) if row else None
        finally:
            conn.close()

    def matching(self, persona_id: str, scope: str, scene: str, field: str) -> list[GrowthEvent]:
        conn = self._connect()
        try:
            return [self._event(row) for row in conn.execute(
                "SELECT * FROM persona_growth_events WHERE scope=? AND scene=? AND field=? AND (scope='shared' OR persona_id=?) ORDER BY id DESC",
                (scope, scene, field, persona_id),
            )]
        finally:
            conn.close()

    def support(self, event_id: int, evidence_key: str = "", *, source_ref: str = "", evidence_text: str = "") -> GrowthEvent | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            seen = source_ref and conn.execute("SELECT 1 FROM persona_growth_evidence WHERE event_id=? AND source_ref=?", (event_id, source_ref)).fetchone()
            inserted = conn.execute("INSERT OR IGNORE INTO persona_growth_evidence (event_id,evidence_key,source_ref,evidence_text) VALUES (?,?,?,?)", (int(event_id), evidence_key, source_ref, evidence_text[:1600])).rowcount if evidence_key and not seen else 0
            if inserted:
                count = conn.execute("SELECT COUNT(*) FROM persona_growth_evidence WHERE event_id=?", (event_id,)).fetchone()[0]
                conn.execute("UPDATE persona_growth_events SET evidence_count=MAX(evidence_count,?) WHERE id=?", (count, int(event_id)))
            row = conn.execute("SELECT * FROM persona_growth_events WHERE id=?", (int(event_id),)).fetchone()
            conn.commit()
            return self._event(row) if row else None
        finally:
            conn.close()

    def evidence_sources(self, event_ids: list[int]) -> dict[int, list[dict]]:
        if not event_ids:
            return {}
        conn = self._connect()
        try:
            result = {}
            placeholders = ",".join("?" for _ in event_ids)
            for row in conn.execute(f"SELECT event_id,source_ref,evidence_text FROM persona_growth_evidence WHERE event_id IN ({placeholders}) ORDER BY rowid", event_ids):
                result.setdefault(row["event_id"], []).append({"source_ref": row["source_ref"], "evidence_text": row["evidence_text"]})
            return result
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
        reverted = []
        while True:
            conn = self._connect()
            try:
                row = conn.execute("SELECT id FROM persona_growth_events WHERE persona_id=? AND status='applied' AND version_id>? ORDER BY version_id DESC LIMIT 1",
                                   (persona_id, max(0, int(version_id)))).fetchone()
            finally:
                conn.close()
            if row is None:
                return reverted
            reverted.append(self.set_status(int(row["id"]), "reverted", expected_statuses={"applied"}))


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

    def review_expired(self, persona_id: str, days: int | None = None) -> list[GrowthEvent]:
        """Expire stale applied preferences without deleting their audit trail."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max(1, int(days or 90)))
        expired = []
        for event in self.store.active(persona_id):
            try:
                if days is None and event.source_kind != "legacy" and not event.expires_at:
                    continue
                timestamp = datetime.fromisoformat(event.expires_at or event.applied_at or event.created_at)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if timestamp.astimezone(timezone.utc) < (now if event.expires_at else cutoff):
                updated = self.store.set_status(event.id, "expired")
                if updated:
                    expired.append(updated)
        return expired

    def summary(self, persona_id: str) -> dict:
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        conn = self.store._connect()
        try:
            rows = conn.execute("SELECT status,COUNT(*) AS count FROM persona_growth_events WHERE scope='shared' OR persona_id=? GROUP BY status", (persona_id,))
            counts = {status: 0 for status in VALID_STATUSES}
            counts.update({row["status"]: row["count"] for row in rows})
            weekly = conn.execute("SELECT COUNT(*) FROM persona_growth_events WHERE (scope='shared' OR persona_id=?) AND julianday(created_at)>=julianday(?)", (persona_id, week_ago.isoformat())).fetchone()[0]
        finally:
            conn.close()
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
                evidence_ref: str = "", scope: str = "shared", scene: str = "all",
                source_kind: str = "legacy", source_session_id: int = 0,
                source_message_ids=(), source_channel: str = "", expires_at: str = "") -> GrowthEvent | None:
        settings = self.settings()
        if settings["mode"] == "off" or self.growth_is_paused():
            return None
        if field and field not in ALLOWED_FIELDS:
            return None
        existing = self.store.matching(persona_id, scope, scene, field)
        if source_kind != "legacy" and evidence_ref and any(
                item.evidence_ref == evidence_ref and item.field == field and item.proposed_value == proposed_value
                for item in existing):
            return None
        if any(item.field == field and item.proposed_value == proposed_value
               and item.status in {"pending", "applied"} for item in existing):
            return None
        conflict = next((item for item in existing if item.field == field and item.status == "applied"
                         and item.proposed_value != proposed_value), None)
        if conflict:
            risk = "confirmation_required"
            old_value = conflict.proposed_value
        event = self.store.create(persona_id=persona_id, kind=kind, title=title,
                                  detail=detail, evidence=evidence, confidence=confidence,
                                  field=field, old_value=old_value, proposed_value=proposed_value,
                                  risk=risk, evidence_ref=evidence_ref,
                                  supersedes_id=conflict.id if conflict else 0,
                                  scope=scope, scene=scene, source_kind=source_kind,
                                  source_session_id=source_session_id, source_message_ids=source_message_ids,
                                  source_channel=source_channel, expires_at=expires_at, deduplicate=True)
        return event

    def observe_feedback(self, persona_id: str, user_message: str, *, source_session_id: int = 0,
                         source_message_id: int | None = None, source_channel: str = "",
                         scene: str = "main_chat") -> GrowthEvent | None:
        """Only explicit user feedback can create an automatic candidate in v1."""
        text = str(user_message or "").strip()
        if (not text or len(text) > 500 or self.settings()["mode"] == "off"
                or self.growth_is_paused()):
            return None
        if re.search(NON_DURABLE + r'|[“「"]|他说|她说|有人说', text):
            return None
        if re.search(SENSITIVE, text, re.IGNORECASE):
            return None
        if not re.search(r"以后|今后|往后|长期|默认|一直|每次|总是", text):
            return None
        if source_message_id and any(
                item.source_channel == source_channel and item.source_session_id == source_session_id
                and source_message_id in item.source_message_ids for item in self.store.list(None, 10000)):
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
            proposal = dict(kind="knowledge_hygiene",
                                title="\u56de\u7b54\u672a\u77e5\u4e8b\u5b9e\u524d\u5148\u6838\u9a8c",
                                detail="\u9047\u5230\u65e0\u6cd5\u786e\u8ba4\u7684\u4e8b\u5b9e\u3001\u4f5c\u54c1\u3001\u4eba\u7269\u3001\u6570\u5b57\u3001\u65e5\u671f\u6216\u5b9e\u65f6\u4fe1\u606f\u65f6\uff0c\u5148\u641c\u7d22\u5e76\u6838\u5bf9\u53ef\u9760\u6765\u6e90\uff1b\u65e0\u6cd5\u786e\u8ba4\u65f6\u660e\u786e\u8bf4\u660e\u4e0d\u786e\u5b9a\uff0c\u4e0d\u80fd\u51ed\u7a7a\u8865\u5168\u7b54\u6848\u3002",
                                field="fact_verification",
                                old_value="\u672a\u542f\u7528", proposed_value="search_before_answer", risk="low",
                                )
        else:
            proposal = self._interpret_feedback(text)
        positive = ("以后你可以", "希望你", "你可以多", "更喜欢你", "请你以后",
                    "不要胡编", "不要编造", "先搜索", "先查证", "先核实",
                    "核对来源", "不了解就查", "不确定时查询", "不要凭空猜",
                    "回答前确认事实")
        if not any(token in text.replace(" ", "") for token in positive) and not re.search(r"以后|今后|往后|默认", text):
            return None
        if proposal is None:
            return None
        scope = "persona" if re.search(r"这个人格|当前人格|这个模式|当前模式|这种风格|Naga|NAGA|毒舌模式", text) else "shared"
        target_scene = scene if re.search(r"只在这里|仅在这里|这个界面", text) else "all"
        evidence_key = hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()
        event = self.propose(persona_id=persona_id, evidence=text,
                             evidence_ref=f"chat:{source_channel}:{source_session_id}:{source_message_id or evidence_key}",
                             confidence=0.9, scope=scope, scene=target_scene, source_kind="explicit_feedback",
                             source_session_id=source_session_id,
                             source_message_ids=(source_message_id,) if source_message_id else (),
                             source_channel=source_channel, **proposal)
        if event is None:
            existing = next((item for item in self.store.matching(persona_id, scope, target_scene, proposal["field"])
                             if item.proposed_value == proposal["proposed_value"]
                             and item.status in {"pending", "applied"}), None)
            event = existing
        if event is not None:
            event = self.store.support(event.id, evidence_key,
                                       source_ref=f"chat:{source_channel}:{source_session_id}:{source_message_id}" if source_message_id else "",
                                       evidence_text=text)
        settings = self.settings()
        if (event and event.status == "pending" and settings["mode"] == "low_risk_auto"
                and event.kind in LOW_RISK_KINDS and event.risk == "low" and event.evidence_count >= 2):
            return self.store.set_status(event.id, "applied")
        return event

    @staticmethod
    def _interpret_feedback(text: str) -> dict | None:
        """Conservative, allow-listed v1 interpreter; never turns free text into a prompt command."""
        normalized = text.replace(" ", "")
        if re.search(r"(?:不要|别|不想|不需要).{0,8}(?:简短|简洁|精简|详细|展开|结论在前|先给结论)", normalized):
            return None
        if re.search(r"倾诉|难过|低落", normalized) and re.search(r"先听|倾听|别急.*建议|不要.*建议", normalized):
            return {"kind": "interaction_style", "title": "倾诉时先倾听", "detail": PREFERENCE_TEXT[("support_style", "listen_first")],
                    "field": "support_style", "old_value": "standard", "proposed_value": "listen_first", "risk": "low"}
        if re.search(r"少.*(?:挖苦|嘲讽)|不要.*(?:挖苦|嘲讽)", normalized):
            return {"kind": "interaction_style", "title": "减少针对用户的挖苦", "detail": PREFERENCE_TEXT[("teasing_level", "gentler")],
                    "field": "teasing_level", "old_value": "standard", "proposed_value": "gentler", "risk": "low"}
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

    def effective(self, persona_id: str, scene: str = "main_chat") -> list[GrowthEvent]:
        self.review_expired(persona_id)
        candidates = [item for item in self.store.active(persona_id) if item.scene in {"all", scene}]
        candidates.sort(key=lambda item: (item.scene != "all", item.scope == "persona", item.id), reverse=True)
        resolved = {}
        for item in candidates:
            resolved.setdefault(item.field or item.title, item)
        return list(resolved.values())

    def ingest_candidates(self, candidates, rows: list[dict], *, persona_id: str,
                          source_session_id: int, source_channel: str) -> list[GrowthEvent]:
        if not isinstance(candidates, list) or not persona_id:
            return []
        users = {row["id"]: str(row.get("content", "")) for row in rows if row.get("role") == "user"}
        accepted = []
        for candidate in candidates[:8]:
            if not isinstance(candidate, dict):
                continue
            source_ids = candidate.get("source_message_ids", [])
            if (not isinstance(source_ids, list) or not source_ids or len(source_ids) > 8
                    or any(type(source_id) is not int or source_id not in users for source_id in source_ids)):
                continue
            quote = candidate.get("evidence_quote", "")
            if not isinstance(quote, str) or not 6 <= len(quote) <= 500 or not any(quote in users[source_id] for source_id in source_ids):
                continue
            if re.search(NON_DURABLE + "|" + SENSITIVE, "\n".join(users[source_id] for source_id in source_ids), re.IGNORECASE):
                continue
            field, value = candidate.get("field"), candidate.get("proposed_value")
            if not isinstance(field, str) or not isinstance(value, str) or (field, value) not in PREFERENCE_TEXT:
                continue
            if field == "fact_verification":
                continue
            scope, scene = candidate.get("scope", "shared"), candidate.get("scene", "all")
            if not isinstance(scope, str) or not isinstance(scene, str) or scope not in {"shared", "persona"} or scene not in SCENES:
                continue
            if scope == "persona" and not all(
                    any(row.get("id") == source_id and row.get("persona_id") == persona_id for row in rows)
                    for source_id in source_ids):
                continue
            event = self.propose(
                persona_id=persona_id, kind="interaction_style", title=PREFERENCE_TEXT[(field, value)][:32],
                detail=PREFERENCE_TEXT[(field, value)], evidence=quote, confidence=0.5,
                field=field, proposed_value=value, risk="confirmation_required", scope=scope, scene=scene,
                source_kind="extracted_candidate", source_session_id=source_session_id,
                source_message_ids=tuple(sorted(set(source_ids))), source_channel=source_channel,
                evidence_ref=f"extraction:{source_channel}:{source_session_id}:" + ",".join(map(str, sorted(set(source_ids)))),
            )
            if event:
                accepted.append(event)
        return accepted

    def review(self, event_id: int, action: str) -> GrowthEvent:
        if not isinstance(action, str) or action not in {"apply", "revert", "dismiss"}:
            raise ValueError("无效的成长操作")
        event = self.store.get(event_id)
        if event is None:
            raise ValueError("成长记录不存在")
        allowed = {"apply": {"pending", "reverted"}, "revert": {"applied"}, "dismiss": {"pending"}}
        if event.status not in allowed[action]:
            raise ValueError("成长状态已变化，请刷新后重试")
        return self.store.set_status(event_id, {"apply": "applied", "revert": "reverted", "dismiss": "dismissed"}[action], expected_statuses=allowed[action])

    def dynamic_context(self, persona_id: str, scene: str = "main_chat") -> str:
        # A lightweight periodic review happens whenever this persona is used.
        # Expired entries remain in the audit trail but stop influencing replies.
        traits = self.effective(persona_id, scene)
        if not traits:
            return ""
        lines = ["【已确认的成长偏好】", "这是同一个莲心学会的相处习惯，可调整人格档案的软默认，不改身份、隐私、权限和安全边界。本轮明确要求优先，情绪只能在此基础上调节表达。"]
        lines.extend(f"- {item.field or item.title}: {item.proposed_value} — {PREFERENCE_TEXT.get((item.field, item.proposed_value), item.detail)}" for item in traits[:12])
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
        active = self.effective(persona_id, "proactive")
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
