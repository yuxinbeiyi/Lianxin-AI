import sqlite3
from datetime import datetime
from pathlib import Path


class VisionDatabase:
    def __init__(self, path):
        self.path = Path(path)
        self.connection = None

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS recognition_event ("
            "id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, "
            "event_type TEXT NOT NULL, confidence REAL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS presence_daily ("
            "date TEXT PRIMARY KEY, total_seconds REAL NOT NULL DEFAULT 0, "
            "session_count INTEGER NOT NULL DEFAULT 0, last_enter TEXT, last_leave TEXT)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS work_session ("
            "id INTEGER PRIMARY KEY, date TEXT NOT NULL, start_time TEXT NOT NULL, "
            "end_time TEXT, duration REAL NOT NULL DEFAULT 0)"
        )
        self.connection.commit()

    def record_event(self, event_type, confidence=0.0):
        if self.connection is None:
            return
        self.connection.execute(
            "INSERT INTO recognition_event(timestamp, event_type, confidence) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), event_type, confidence),
        )
        self.connection.commit()

    def add_presence_time(self, seconds, session_started=False,
                          entered_at=None, left_at=None):
        if self.connection is None:
            return
        date = datetime.now().date().isoformat()
        self.connection.execute(
            "INSERT INTO presence_daily(date, total_seconds, session_count, last_enter, last_leave) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(date) DO UPDATE SET "
            "total_seconds=total_seconds+excluded.total_seconds, "
            "session_count=session_count+excluded.session_count, "
            "last_enter=COALESCE(excluded.last_enter, last_enter), "
            "last_leave=COALESCE(excluded.last_leave, last_leave)",
            (date, float(seconds), 1 if session_started else 0, entered_at, left_at),
        )
        self.connection.commit()

    def today_summary(self):
        if self.connection is None:
            return {"total_seconds": 0, "session_count": 0}
        date = datetime.now().date().isoformat()
        row = self.connection.execute(
            "SELECT total_seconds, session_count FROM presence_daily WHERE date=?", (date,)
        ).fetchone()
        return {"total_seconds": row[0], "session_count": row[1]} if row else {"total_seconds": 0, "session_count": 0}

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
