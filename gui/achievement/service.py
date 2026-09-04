"""Local, privacy-preserving projections for the Achievement Record."""
from __future__ import annotations

import json
import csv
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.accompany_stats import AccompanyStats
from utils.music_stats import MusicStats
from utils.paths import get_user_data_dir


METRIC_COLUMNS = (
    "chat_turns", "focus_completed", "focus_seconds", "capsules", "notes",
    "images", "tools", "avatar_interactions", "avatar_user_taps",
    "avatar_user_headpats", "avatar_counter_taps", "avatar_counter_headpats",
    "avatar_assistant_taps", "avatar_assistant_headpats",
    "presence_seconds", "active_events",
)

AVATAR_METRIC_MAP = {
    "user_taps": "avatar_user_taps",
    "user_headpats": "avatar_user_headpats",
    "counter_taps": "avatar_counter_taps",
    "counter_headpats": "avatar_counter_headpats",
    "assistant_taps": "avatar_assistant_taps",
    "assistant_headpats": "avatar_assistant_headpats",
}


class AchievementService:
    """Projects immutable interaction facts into a fast, local achievement view.

    ``interaction_events.db`` remains the cross-feature fact source.  This
    database deliberately holds only idempotent projections, unlocks and safe
    display summaries; it never copies chat text or private note content.
    """

    def __init__(self, db_path: str | Path | None = None, events_path: str | Path | None = None):
        self.data_dir = get_user_data_dir()
        self.db_path = Path(db_path) if db_path else self.data_dir / "achievement_record.db"
        self.events_path = Path(events_path) if events_path else self.data_dir / "interaction_events.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._migrate_legacy_stats()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self):
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS unlocks (
                    achievement_id TEXT PRIMARY KEY, unlocked_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    local_date TEXT PRIMARY KEY,
                    chat_turns INTEGER NOT NULL DEFAULT 0,
                    focus_completed INTEGER NOT NULL DEFAULT 0,
                    focus_seconds INTEGER NOT NULL DEFAULT 0,
                    capsules INTEGER NOT NULL DEFAULT 0,
                    notes INTEGER NOT NULL DEFAULT 0,
                    images INTEGER NOT NULL DEFAULT 0,
                    tools INTEGER NOT NULL DEFAULT 0,
                    avatar_interactions INTEGER NOT NULL DEFAULT 0,
                    avatar_user_taps INTEGER NOT NULL DEFAULT 0,
                    avatar_user_headpats INTEGER NOT NULL DEFAULT 0,
                    avatar_counter_taps INTEGER NOT NULL DEFAULT 0,
                    avatar_counter_headpats INTEGER NOT NULL DEFAULT 0,
                    avatar_assistant_taps INTEGER NOT NULL DEFAULT 0,
                    avatar_assistant_headpats INTEGER NOT NULL DEFAULT 0,
                    presence_seconds INTEGER NOT NULL DEFAULT 0,
                    active_events INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS journey_entries (
                    event_id INTEGER PRIMARY KEY,
                    local_date TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_feature TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_journey_date ON journey_entries(local_date, occurred_at DESC);
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)")}
            for column in METRIC_COLUMNS:
                if column not in existing and column != "local_date":
                    conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

    def _migrate_legacy_stats(self):
        """Create a one-time legacy baseline before projecting new avatar facts."""
        with self._connection() as conn:
            legacy_row = conn.execute("SELECT value FROM metadata WHERE key='legacy_stats'").fetchone()
            if legacy_row:
                try:
                    payload = json.loads(legacy_row["value"])
                except (TypeError, json.JSONDecodeError):
                    payload = {}
            else:
                stats = AccompanyStats()
                payload = {
                    "total_seconds": stats.get_current_total_seconds(),
                    "session_count": stats.get_stats().get("session_count", 0),
                    "first_meet_date": stats.get_first_meet_date(),
                    "avatar": stats.get_avatar_interaction_summary(),
                }
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("legacy_stats", json.dumps(payload, ensure_ascii=False)))
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('legacy_migrated', ?)", (datetime.now().isoformat(timespec="seconds"),))
            if conn.execute("SELECT 1 FROM metadata WHERE key='avatar_v2_migrated'").fetchone():
                return

            avatar = payload.get("avatar") or {}
            baseline = {
                "total": int(avatar.get("total", avatar.get("interaction_count", 0)) or 0),
                "user_taps": int(avatar.get("user_taps", avatar.get("user_tap_count", 0)) or 0),
                "user_headpats": int(avatar.get("user_headpats", avatar.get("user_headpat_count", 0)) or 0),
                "counter_taps": int(avatar.get("counter_taps", avatar.get("counter", avatar.get("assistant_counter_tap_count", 0))) or 0),
                "counter_headpats": int(avatar.get("counter_headpats", avatar.get("counter_headpat_count", 0)) or 0),
                "assistant_taps": int(avatar.get("assistant_taps", avatar.get("assistant_tap_user_count", 0)) or 0),
                "assistant_headpats": int(avatar.get("assistant_headpats", avatar.get("assistant_headpat_user_count", 0)) or 0),
            }
            has_legacy_avatar = any(baseline.values())
            cutoff_id = self._event_max_id() if has_legacy_avatar else 0
            if has_legacy_avatar and cutoff_id is None:
                return
            if has_legacy_avatar:
                reset_fields = ["avatar_interactions", *AVATAR_METRIC_MAP.values()]
                conn.execute("UPDATE daily_metrics SET " + ", ".join(f"{field}=0" for field in reset_fields))
                avatar_event_ids = self._avatar_event_ids(cutoff_id)
                if avatar_event_ids is None:
                    return
                for event_id in avatar_event_ids:
                    conn.execute("INSERT OR IGNORE INTO processed_events(event_id, processed_at) VALUES (?, ?)", (event_id, datetime.now().isoformat(timespec="seconds")))
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", ("avatar_legacy_baseline", json.dumps(baseline, ensure_ascii=False)))
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('avatar_cutover_event_id', ?)", (str(cutoff_id),))
            conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('avatar_v2_migrated', ?)", (datetime.now().isoformat(timespec="seconds"),))

    def _event_max_id(self):
        if not self.events_path.exists():
            return 0
        conn = None
        try:
            conn = sqlite3.connect(str(self.events_path), timeout=3)
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM interaction_events").fetchone()
            return int(row[0] or 0)
        except sqlite3.Error:
            return None
        finally:
            if conn is not None:
                conn.close()

    def _avatar_event_ids(self, cutoff_id):
        if not cutoff_id or not self.events_path.exists():
            return []
        conn = None
        try:
            conn = sqlite3.connect(str(self.events_path), timeout=3)
            rows = conn.execute("SELECT id FROM interaction_events WHERE id <= ? AND feature='avatar' AND event_type='avatar_interaction'", (int(cutoff_id),)).fetchall()
            return [int(row[0]) for row in rows]
        except sqlite3.Error:
            return None
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _definitions():
        return [
            ("hello_world", "你好，世界", "相遇", "第一次打开莲心", "shell", "sessions", 1, False),
            ("first_words", "第一句悄悄话", "相遇", "完成第一次对话", "shell", "chats", 1, False),
            ("ten_conversations", "聊了十次天", "相遇", "完成 10 次对话", "shell", "chats", 10, False),
            ("hundred_conversations", "百句潮声", "陪伴", "完成 100 次对话", "star", "chats", 100, False),
            ("thousand_conversations", "无话不谈", "陪伴", "完成 1000 次对话", "star", "chats", 1000, False),
            ("ten_thousand_conversations", "人生搭子", "陪伴", "完成 10000 次对话", "star", "chats", 10000, False),
            ("seven_sunsets", "七次日落", "陪伴", "在近 14 天里相伴 7 天", "star", "active_14", 7, False),
            ("warm_month", "温柔的一个月", "陪伴", "在近 45 天里相伴 30 天", "star", "active_45", 30, False),
            ("one_hour", "海风一小时", "陪伴", "累计前台陪伴满 1 小时", "star", "seconds", 3600, False),
            ("ten_hours", "潮汐相伴", "陪伴", "累计前台陪伴满 10 小时", "star", "seconds", 36000, False),
            ("fifty_hours", "海岸常在", "陪伴", "累计前台陪伴满 50 小时", "star", "seconds", 180000, False),
            ("first_focus", "安静的一页", "共同成长", "完成第一次专注", "anchor", "focus", 1, False),
            ("focus_ten", "专注的海岸", "共同成长", "完成 10 次专注", "anchor", "focus", 10, False),
            ("focus_hour", "一小时的灯塔", "共同成长", "累计专注满 1 小时", "anchor", "focus_seconds", 3600, False),
            ("focus_ten_hours", "沉静海面", "共同成长", "累计专注满 10 小时", "anchor", "focus_seconds", 36000, False),
            ("focus_hundred_hours", "携手共进", "共同成长", "累计专注满 100 小时", "anchor", "focus_seconds", 360000, False),
            ("focus_thousand_hours", "学海无涯", "共同成长", "累计专注满 1000 小时", "anchor", "focus_seconds", 3600000, False),
            ("first_capsule", "寄给未来", "时间胶囊", "封存第一枚时间胶囊", "bottle", "capsules", 1, False),
            ("capsule_five", "五封漂流信", "时间胶囊", "封存 5 枚时间胶囊", "bottle", "capsules", 5, False),
            ("capsule_twelve", "十二个月亮瓶", "时间胶囊", "封存 12 枚时间胶囊", "bottle", "capsules", 12, False),
            ("capsule_hundred", "写满一本", "时间胶囊", "封存 100 枚时间胶囊", "bottle", "capsules", 100, False),
            ("first_note", "树洞回声", "树洞", "留下第一张树洞纸条", "boat", "notes", 1, False),
            ("notes_ten", "纸船成群", "树洞", "留下 10 张树洞纸条", "boat", "notes", 10, False),
            ("image_seen", "所见所闻", "探索", "第一次成功识图", "scope", "images", 1, False),
            ("image_ten", "收藏十个瞬间", "探索", "完成 10 次图片识别", "scope", "images", 10, False),
            ("tool_explorer", "打开望远镜", "探索", "第一次主动使用工具探索世界", "scope", "tools", 1, False),
            ("tool_ten", "远方的回信", "探索", "完成 10 次主动工具探索", "scope", "tools", 10, False),
            ("tool_hundred", "熟练搭档", "探索", "完成 100 次主动工具探索", "scope", "tools", 100, False),
            ("music_hour", "孤华乐章", "音乐盒", "与莲心一起听歌累计 1 小时", "music", "music_seconds", 3600, False),
            ("music_ten_hours", "耳机另一端", "音乐盒", "与莲心一起听歌累计 10 小时", "music", "music_seconds", 36000, False),
            ("meet_month", "相遇满月", "相遇", "从相遇那天起满 30 天", "shell", "days_since_meet", 30, False),
            ("meet_hundred", "第一百天", "相遇", "从相遇那天起满 100 天", "shell", "days_since_meet", 100, False),
            ("meet_year", "一年的海", "相遇", "从相遇那天起满一年", "shell", "days_since_meet", 365, False),
            ("tap_tap", "轻轻拍一拍", "彩蛋", "和莲心完成第一次头像互动", "pearl", "avatar", 1, True),
            ("twenty_taps", "海浪的回声", "彩蛋", "完成 20 次头像互动", "pearl", "avatar", 20, True),
            ("first_tap", "第一声回响", "互动回声", "用户第一次拍了拍莲心", "pearl", "avatar_user_taps", 1, False),
            ("tap_ten", "十次轻响", "互动回声", "用户拍莲心达到 10 次", "pearl", "avatar_user_taps", 10, False),
            ("tap_fifty", "海浪不息", "互动回声", "用户拍莲心达到 50 次", "pearl", "avatar_user_taps", 50, False),
            ("first_headpat", "掌心的温度", "互动回声", "用户第一次摸了摸莲心", "pearl", "avatar_user_headpats", 1, False),
            ("headpat_ten", "温柔十次", "互动回声", "用户摸莲心达到 10 次", "pearl", "avatar_user_headpats", 10, False),
            ("counter_tap_five", "莲心的回应", "互动回声", "莲心反拍用户达到 5 次", "pearl", "avatar_counter_taps", 5, True),
            ("counter_headpat_five", "被风轻轻摸过", "互动回声", "莲心反摸用户达到 5 次", "pearl", "avatar_counter_headpats", 5, True),
            ("avatar_duet", "双向回声", "互动回声", "用户与莲心都完成过头像互动", "pearl", "avatar_duet", 1, True),
        ]

    def _legacy(self):
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='legacy_stats'").fetchone()
        try:
            return json.loads(row["value"]) if row else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _metadata(event):
        try:
            value = json.loads(event.get("metadata_json") or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _safe_entry(event):
        """Whitelisted narrative entries. Never use event.content in this UI."""
        kind = str(event["event_type"])
        feature = str(event["feature"])
        titles = {
            "chat_turn_completed": ("一次对话", "今天又和莲心聊了一会儿。"),
            "focus_completed": ("完成专注", "留出了一段安静专注的时间。"),
            "user_diary_sealed": ("封存时间胶囊", "写下了一页想留给未来的文字。"),
            "tree_note_created": ("树洞纸条", "在树洞里放下了一张小纸条。"),
            "vision_completed": ("看见新事物", "和莲心一起看了一张图片。"),
            "tool_called": ("一次探索", "和莲心一起探索了一个小问题。"),
            "avatar_interaction": ("轻轻互动", "和莲心完成了一次小小的互动。"),
        }
        if kind not in titles:
            return None
        title, summary = titles[kind]
        if kind == "focus_completed":
            seconds = int(AchievementService._metadata(event).get("duration_seconds", 0) or 0)
            if seconds:
                summary = f"完成了 {max(1, seconds // 60)} 分钟的专注。"
        return title, summary, feature

    @staticmethod
    def _increment(conn, day: str, **values):
        conn.execute("INSERT OR IGNORE INTO daily_metrics(local_date) VALUES (?)", (day,))
        fields = [(name, int(value)) for name, value in values.items() if name in METRIC_COLUMNS and int(value)]
        if fields:
            sets = ", ".join(f"{name} = {name} + ?" for name, _ in fields)
            conn.execute(f"UPDATE daily_metrics SET {sets} WHERE local_date = ?", [value for _, value in fields] + [day])

    @staticmethod
    def _split_presence(conn, start: datetime, end: datetime):
        cursor = start
        while cursor.date() < end.date():
            boundary = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=cursor.tzinfo)
            AchievementService._increment(conn, cursor.date().isoformat(), presence_seconds=max(0, int((boundary - cursor).total_seconds())))
            cursor = boundary
        AchievementService._increment(conn, cursor.date().isoformat(), presence_seconds=max(0, int((end - cursor).total_seconds())))

    def _project_event(self, conn, event):
        kind, feature, day = str(event["event_type"]), str(event["feature"]), str(event["local_date"])
        meta = self._metadata(event)
        values = {"active_events": 1}
        if kind == "chat_turn_completed": values["chat_turns"] = 1
        elif kind == "focus_completed":
            values.update(focus_completed=1, focus_seconds=max(0, int(meta.get("duration_seconds", 0) or 0)))
        elif kind == "user_diary_sealed": values["capsules"] = 1
        elif kind == "tree_note_created": values["notes"] = 1
        elif kind == "vision_completed": values["images"] = 1
        elif kind == "tool_called" and bool(meta.get("user_initiated", False)): values["tools"] = 1
        elif kind == "avatar_interaction":
            values["avatar_interactions"] = 1
            action = str(meta.get("action") or "tap")
            actor = str(meta.get("actor") or "")
            target = str(meta.get("target") or "")
            source = str(meta.get("source") or "")
            if actor == "user" and target == "assistant":
                values["avatar_user_headpats" if action == "headpat" else "avatar_user_taps"] = 1
            elif actor == "assistant" and target == "user":
                if source == "counter" or bool(meta.get("is_counter")):
                    values["avatar_counter_headpats" if action == "headpat" else "avatar_counter_taps"] = 1
                else:
                    values["avatar_assistant_headpats" if action == "headpat" else "avatar_assistant_taps"] = 1
        elif kind == "presence_segment":
            try:
                start = datetime.fromisoformat(str(meta["started_at"])); end = datetime.fromisoformat(str(meta["ended_at"]))
                if end > start: self._split_presence(conn, start, end)
            except (KeyError, TypeError, ValueError):
                pass
            values = {}
        self._increment(conn, day, **values)
        entry = self._safe_entry(event)
        if entry:
            title, summary, safe_feature = entry
            conn.execute("""INSERT OR IGNORE INTO journey_entries
                         (event_id, local_date, occurred_at, kind, title, summary, source_feature, source_id)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                         (event["id"], day, event["occurred_at"], kind, title, summary, safe_feature, str(event.get("source_id") or "")))

    def sync(self):
        """Incrementally consume unseen facts. Safe to call from every refresh."""
        if not self.events_path.exists():
            return
        try:
            with self._connection() as conn:
                row = conn.execute("SELECT value FROM metadata WHERE key='last_interaction_event_id'").fetchone()
                last_id = int(row["value"]) if row else 0
            facts = sqlite3.connect(str(self.events_path), timeout=3)
            try:
                facts.row_factory = sqlite3.Row
                events = [dict(row) for row in facts.execute(
                    "SELECT * FROM interaction_events WHERE id > ? ORDER BY id ASC", (last_id,)
                )]
            finally:
                facts.close()
        except sqlite3.Error:
            return
        if not events:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._connection() as conn:
            for event in events:
                if conn.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event["id"],)).fetchone():
                    continue
                self._project_event(conn, event)
                conn.execute("INSERT OR IGNORE INTO processed_events(event_id, processed_at) VALUES (?, ?)", (event["id"], now))
                conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('last_interaction_event_id', ?)", (str(event["id"]),))

    def _daily_rows(self, start: str | None = None):
        query, args = "SELECT * FROM daily_metrics", []
        if start:
            query += " WHERE local_date >= ?"; args.append(start)
        query += " ORDER BY local_date ASC"
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(query, args)]

    def journey_page(self, offset: int = 0, limit: int = 20,
                     categories: list[str] | None = None, day: str = ""):
        """Return safe journal cards with bounded pagination."""
        clauses, args = [], []
        category_kinds = {
            "chat": "chat_turn_completed", "focus": "focus_completed",
            "capsule": "user_diary_sealed", "tree": "tree_note_created",
            "explore": "vision_completed", "tool_called": "tool_called",
            "avatar": "avatar_interaction",
        }
        selected = [category_kinds[item] for item in (categories or []) if item in category_kinds]
        if selected:
            clauses.append("kind IN (" + ",".join("?" for _ in selected) + ")"); args.extend(selected)
        if day:
            clauses.append("local_date = ?"); args.append(str(day))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        args.extend([max(0, int(offset)), max(1, min(50, int(limit)))])
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM journey_entries" + where +
                " ORDER BY occurred_at DESC LIMIT ? OFFSET ?", args
            ).fetchall()
            count = conn.execute("SELECT COUNT(*) FROM journey_entries" + where, args[:-2]).fetchone()[0]
        return {"items": [dict(row) for row in rows], "total": int(count), "offset": max(0, int(offset)), "limit": max(1, min(50, int(limit)))}

    def export_metrics(self, export_format: str = "json") -> str:
        """Export aggregated metrics only; never export journal or chat text."""
        rows = self._daily_rows()
        export_dir = self.data_dir / "achievement_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if str(export_format).lower() == "csv":
            path = export_dir / f"achievement_metrics_{stamp}.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                fields = ["local_date", *METRIC_COLUMNS]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader(); writer.writerows({field: row.get(field, 0) for field in fields} for row in rows)
        else:
            path = export_dir / f"achievement_metrics_{stamp}.json"
            path.write_text(json.dumps({"exported_at": datetime.now().isoformat(timespec="seconds"), "metrics": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _unlock(self, metrics):
        now = datetime.now().isoformat(timespec="seconds")
        with self._connection() as conn:
            for key, _, _, _, _, metric, needed, _ in self._definitions():
                if metrics.get(metric, 0) >= needed:
                    conn.execute("INSERT OR IGNORE INTO unlocks(achievement_id, unlocked_at) VALUES (?, ?)", (key, now))
            rows = [dict(row) for row in conn.execute("SELECT achievement_id, unlocked_at, is_read FROM unlocks")]
            if not conn.execute("SELECT 1 FROM metadata WHERE key='unlock_notices_initialized'").fetchone():
                conn.execute("UPDATE unlocks SET is_read = 1")
                conn.execute("INSERT INTO metadata(key, value) VALUES ('unlock_notices_initialized', ?)", (now,))
                rows = [dict(row) for row in conn.execute("SELECT achievement_id, unlocked_at, is_read FROM unlocks")]
            return {row["achievement_id"]: row for row in rows}

    def mark_unlocks_read(self, achievement_ids: list[str]):
        allowed = {item[0] for item in self._definitions()}
        ids = [str(item) for item in achievement_ids if str(item) in allowed]
        if not ids:
            return
        with self._connection() as conn:
            conn.executemany("UPDATE unlocks SET is_read = 1 WHERE achievement_id = ?", [(item,) for item in ids])

    def state(self):
        self.sync()
        rows = self._daily_rows()
        today = date.today().isoformat()
        by_day = {row["local_date"]: row for row in rows}
        total = Counter()
        for row in rows:
            total.update({
                "chats": row["chat_turns"], "focus": row["focus_completed"],
                "focus_seconds": row["focus_seconds"], "capsules": row["capsules"],
                "notes": row["notes"], "images": row["images"], "tools": row["tools"],
                "avatar": row["avatar_interactions"],
                **{key: row.get(column, 0) for key, column in AVATAR_METRIC_MAP.items()},
            })
        legacy, stats = self._legacy(), AccompanyStats()
        first = stats.get_first_meet_date() or legacy.get("first_meet_date", "")
        try: days_since = (date.today() - date.fromisoformat(first)).days + 1 if first else 0
        except ValueError: days_since = 0
        legacy_avatar = legacy.get("avatar") or {}
        with self._connection() as conn:
            baseline_row = conn.execute("SELECT value FROM metadata WHERE key='avatar_legacy_baseline'").fetchone()
        try:
            avatar_baseline = json.loads(baseline_row["value"]) if baseline_row else {}
        except (TypeError, json.JSONDecodeError):
            avatar_baseline = {}
        baseline = {
            "total": int(avatar_baseline.get("total", legacy_avatar.get("total", 0)) or 0),
            **{key: int(avatar_baseline.get(key, legacy_avatar.get(key, 0)) or 0) for key in AVATAR_METRIC_MAP},
        }
        projected_presence = sum(int(row["presence_seconds"] or 0) for row in rows)
        legacy_presence = int(legacy.get("total_seconds", 0) or 0)
        # 音乐盒已有独立本地统计，成就只读取累计秒数，不读取曲目或播放历史。
        try:
            music_seconds = max(0, int(MusicStats().data.get("total_seconds", 0) or 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            music_seconds = 0
        metrics = dict(total)
        avatar_detail = {key: baseline[key] + int(total.get(key, 0)) for key in AVATAR_METRIC_MAP}
        metrics.update({f"avatar_{key}": value for key, value in avatar_detail.items()})
        metrics.update(
            seconds=legacy_presence + projected_presence,
            today_seconds=int(by_day.get(today, {}).get("presence_seconds", 0)),
            sessions=stats.get_stats().get("session_count", 0),
            avatar=baseline["total"] + int(total.get("avatar", 0)),
            avatar_detail=avatar_detail,
            music_seconds=music_seconds,
            days_since_meet=days_since,
            active_dates=len([row for row in rows if row["active_events"] > 0]),
        )
        metrics["avatar_duet"] = int(
            (avatar_detail["user_taps"] + avatar_detail["user_headpats"]) > 0
            and (avatar_detail["counter_taps"] + avatar_detail["counter_headpats"]
                 + avatar_detail["assistant_taps"] + avatar_detail["assistant_headpats"]) > 0
        )
        metrics["active_14"] = sum(1 for day_key, row in by_day.items() if day_key >= (date.today() - timedelta(days=13)).isoformat() and row["active_events"] > 0)
        metrics["active_45"] = sum(1 for day_key, row in by_day.items() if day_key >= (date.today() - timedelta(days=44)).isoformat() and row["active_events"] > 0)
        unlocked = self._unlock(metrics)
        all_achievements = []
        for key, title, chapter, description, art, metric, needed, hidden in self._definitions():
            unlock = unlocked.get(key)
            all_achievements.append({"id": key, "title": title, "chapter": chapter, "description": description, "art": art, "current": min(metrics.get(metric, 0), needed), "target": needed, "unlocked_at": unlock["unlocked_at"] if unlock else None, "is_read": bool(unlock and unlock["is_read"]), "hidden": hidden})
        achievements = [item for item in all_achievements if not item["hidden"] or item["unlocked_at"]]
        trail = []
        for offset in range(29, -1, -1):
            day_key = (date.today() - timedelta(days=offset)).isoformat(); row = by_day.get(day_key, {})
            trail.append({"date": day_key, "chats": row.get("chat_turns", 0), "focus": row.get("focus_completed", 0), "capsules": row.get("capsules", 0), "notes": row.get("notes", 0), "tools": row.get("tools", 0)})
        with self._connection() as conn:
            entries = [dict(row) for row in conn.execute("SELECT * FROM journey_entries ORDER BY occurred_at DESC LIMIT 80")]
            journey_total = int(conn.execute("SELECT COUNT(*) FROM journey_entries").fetchone()[0])
        today_row = by_day.get(today, {})
        visual_stats = stats.get_visual_stats()
        return {"user_name": "", "first_meet_date": first, "metrics": metrics,
                "visual_stats": visual_stats,
                "avatar_summary": avatar_detail,
                "today": {"chats": today_row.get("chat_turns", 0), "focus": today_row.get("focus_completed", 0), "capsules": today_row.get("capsules", 0), "notes": today_row.get("notes", 0), "tools": today_row.get("tools", 0)},
                "active_days_30": sum(1 for day_key, row in by_day.items() if day_key >= (date.today() - timedelta(days=29)).isoformat() and row["active_events"] > 0),
                "trail": trail, "daily_metrics": rows, "achievements": achievements, "events": entries, "journey_total": journey_total,
                "new_unlocks": [item for item in all_achievements if item["unlocked_at"] and not item["is_read"]],
                # 收藏页只展示普通成就和已经发现的隐藏成就，分子分母必须同口径。
                "unlocked_count": len([item for item in achievements if item["unlocked_at"]]),
                "total_count": len(achievements),
                "discovered_hidden_count": len([item for item in all_achievements if item["hidden"] and item["unlocked_at"]]),
                "recent_unlock": next((item for item in sorted(achievements, key=lambda item: item["unlocked_at"] or "", reverse=True) if item["unlocked_at"]), None)}
