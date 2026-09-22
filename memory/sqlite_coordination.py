"""Process-wide coordination for SQLite databases shared across modules."""

from __future__ import annotations

import threading
import sqlite3
from pathlib import Path
from typing import Any


_registry_lock = threading.Lock()
_database_locks: dict[str, threading.RLock] = {}


def get_database_lock(db_path: Path | str) -> threading.RLock:
    """Return one re-entrant lock for every resolved on-disk database path."""
    raw_path = str(db_path)
    key = raw_path if raw_path == ":memory:" else str(Path(raw_path).resolve())
    with _registry_lock:
        lock = _database_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _database_locks[key] = lock
        return lock


class CoordinatedConnection(sqlite3.Connection):
    """SQLite connection that serializes writes to the same database in-process."""

    _coordination_lock: threading.RLock
    _coordination_held: bool

    @staticmethod
    def _statement_kind(sql: str) -> str:
        text = str(sql or "").lstrip()
        while text.startswith("--"):
            newline = text.find("\n")
            text = text[newline + 1:].lstrip() if newline >= 0 else ""
        return text.split(None, 1)[0].upper() if text else ""

    @classmethod
    def _is_read_only(cls, sql: str) -> bool:
        return cls._statement_kind(sql) in {"SELECT", "EXPLAIN"}

    def _acquire_for_write(self) -> None:
        if not self._coordination_held:
            timeout = getattr(self, "_coordination_timeout", 5.0)
            if not self._coordination_lock.acquire(timeout=timeout):
                raise sqlite3.OperationalError(
                    "数据库写锁等待超时（>%.1fs），可能有长事务占用同一数据库"
                    % timeout
                )
            self._coordination_held = True

    def _release_after_transaction(self) -> None:
        if self._coordination_held:
            self._coordination_held = False
            self._coordination_lock.release()

    def execute(self, sql: str, parameters: Any = (), /):
        if not self._is_read_only(sql):
            self._acquire_for_write()
        try:
            cursor = super().execute(sql, parameters)
        except Exception:
            if self._coordination_held:
                try:
                    super().rollback()
                finally:
                    self._release_after_transaction()
            raise
        if (
            self._statement_kind(sql) in {"COMMIT", "END", "ROLLBACK"}
            or not self.in_transaction
        ):
            self._release_after_transaction()
        return cursor

    def executemany(self, sql: str, seq_of_parameters: Any, /):
        if not self._is_read_only(sql):
            self._acquire_for_write()
        try:
            cursor = super().executemany(sql, seq_of_parameters)
        except Exception:
            if self._coordination_held:
                try:
                    super().rollback()
                finally:
                    self._release_after_transaction()
            raise
        if not self.in_transaction:
            self._release_after_transaction()
        return cursor

    def executescript(self, sql_script: str, /):
        self._acquire_for_write()
        try:
            return super().executescript(sql_script)
        except Exception:
            try:
                super().rollback()
            finally:
                self._release_after_transaction()
            raise
        finally:
            # executescript commits any pending transaction before running.
            self._release_after_transaction()

    def commit(self) -> None:
        try:
            super().commit()
        finally:
            self._release_after_transaction()

    def rollback(self) -> None:
        try:
            super().rollback()
        finally:
            self._release_after_transaction()

    def close(self) -> None:
        try:
            if self.in_transaction:
                super().rollback()
        finally:
            self._release_after_transaction()
            super().close()


def connect_database(
    db_path: Path | str,
    *,
    timeout: float = 5.0,
    lock_timeout: float = 5.0,
    check_same_thread: bool = True,
    isolation_level: str | None = "",
) -> CoordinatedConnection:
    """Open a coordinated SQLite connection for a shared application database."""
    conn = sqlite3.connect(
        str(db_path),
        timeout=timeout,
        check_same_thread=check_same_thread,
        isolation_level=isolation_level,
        factory=CoordinatedConnection,
    )
    conn._coordination_lock = get_database_lock(db_path)
    conn._coordination_held = False
    conn._coordination_timeout = lock_timeout
    # PRAGMA setup must not enter the application write-lock protocol.
    sqlite3.Connection.execute(
        conn, f"PRAGMA busy_timeout={max(1, int(timeout * 1000))}"
    )
    return conn
