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

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
