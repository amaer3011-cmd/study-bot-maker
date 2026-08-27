from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from utils import CAIRO, cairo_now

logger = logging.getLogger("study-bot.storage")


class Store:
    """Fast session cache with SQLite persistence, migration, and history."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = RLock()
        self.memory: dict[int, dict] = {}
        self.memory_users: dict[int, dict] = {}
        self.memory_events: list[dict] = []
        self.connection: sqlite3.Connection | None = None
        self.sqlite_failure_count = 0
        self.last_sqlite_failure_at: str | None = None
        try:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(
                self.path,
                check_same_thread=False,
                isolation_level=None,
                cached_statements=128,
            )
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.execute("PRAGMA busy_timeout=3000")
            self.connection.execute("PRAGMA temp_store=MEMORY")
            self.connection.execute("PRAGMA cache_size=-8192")
            self.connection.execute("PRAGMA wal_autocheckpoint=1000")
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    chat_id INTEGER PRIMARY KEY,
                    step TEXT NOT NULL,
                    template_id INTEGER NOT NULL,
                    duration TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    tasks TEXT NOT NULL,
                    total_sessions INTEGER NOT NULL,
                    total_minutes INTEGER NOT NULL,
                    streak_days INTEGER NOT NULL,
                    last_study_date TEXT NOT NULL,
                    reminder_ends_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS study_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    task_count INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS media_cache (
                    cache_key TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS bot_users (
                    chat_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    last_name TEXT NOT NULL DEFAULT '',
                    is_blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )"""
            )
            self._migrate_schema()
        except Exception:
            logger.exception("SQLite unavailable; using memory only")
            self.connection = None
            self._record_sqlite_failure()

    def _migrate_schema(self) -> None:
        if not self.connection:
            return
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(sessions)")}
        if "updated_at" not in columns:
            self.connection.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        # Match the actual ORDER BY used by recent_history and make reminder
        # restoration a range lookup instead of a full sessions-table scan.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_chat_id ON study_history(chat_id, id DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_reminder_ends ON sessions(reminder_ends_at)"
        )
        # This legacy index is not used by the current ORDER BY and adds write
        # cost to every history insert. Keep only the matching id-based index.
        self.connection.execute("DROP INDEX IF EXISTS idx_history_chat")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON bot_users(last_seen_at DESC)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_users_created ON bot_users(created_at DESC)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_users_blocked ON bot_users(is_blocked)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON bot_events(created_at DESC)")

    def _record_sqlite_failure(self) -> None:
        self.sqlite_failure_count += 1
        self.last_sqlite_failure_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def fresh() -> dict:
        return {
            "step": "idle", "template_id": 1, "duration": "", "duration_minutes": 0,
            "start_time": "", "tasks": [], "total_sessions": 0, "total_minutes": 0,
            "streak_days": 0, "last_study_date": "", "reminder_ends_at": 0.0,
        }

    @staticmethod
    def _copy(data: dict) -> dict:
        result = dict(data)
        result["tasks"] = list(data.get("tasks", []))
        return result

    def get(self, chat_id: int) -> dict:
        cached = self.memory.get(chat_id)
        if cached is not None:
            return self._copy(cached)
        with self.lock:
            cached = self.memory.get(chat_id)
            if cached is not None:
                return self._copy(cached)
            if self.connection:
                try:
                    row = self.connection.execute(
                        "SELECT step, template_id, duration, duration_minutes, start_time, tasks, total_sessions, total_minutes, streak_days, last_study_date, reminder_ends_at FROM sessions WHERE chat_id=?",
                        (chat_id,),
                    ).fetchone()
                    if row:
                        keys = ["step", "template_id", "duration", "duration_minutes", "start_time", "tasks", "total_sessions", "total_minutes", "streak_days", "last_study_date", "reminder_ends_at"]
                        data = dict(zip(keys, row))
                        try:
                            data["tasks"] = json.loads(data["tasks"] or "[]")
                        except (TypeError, json.JSONDecodeError):
                            data["tasks"] = []
                        self.memory[chat_id] = data
                        return self._copy(data)
                except Exception:
                    logger.exception("SQLite read failed for chat %s; using memory", chat_id)
                    self.connection = None
                    self._record_sqlite_failure()
            data = self.fresh()
            self.memory[chat_id] = data
            return self._copy(data)

    def save(self, chat_id: int, data: dict) -> None:
        snapshot = self._copy(data)
        previous = self.memory.get(chat_id)
        self.memory[chat_id] = snapshot
        if not self.connection:
            return
        if previous is not None and previous == snapshot:
            # Nothing actually changed (e.g. a duplicate save call); skip the
            # disk write instead of rewriting an identical row.
            return
        with self.lock:
            try:
                self.memory[chat_id] = snapshot
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                values = (
                    chat_id, snapshot["step"], snapshot["template_id"], snapshot["duration"], snapshot["duration_minutes"],
                    snapshot["start_time"], json.dumps(snapshot["tasks"], ensure_ascii=False), snapshot["total_sessions"],
                    snapshot["total_minutes"], snapshot["streak_days"], snapshot["last_study_date"], snapshot["reminder_ends_at"], now,
                )
                self.connection.execute(
                    """INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET step=excluded.step, template_id=excluded.template_id,
                    duration=excluded.duration, duration_minutes=excluded.duration_minutes, start_time=excluded.start_time,
                    tasks=excluded.tasks, total_sessions=excluded.total_sessions, total_minutes=excluded.total_minutes,
                    streak_days=excluded.streak_days, last_study_date=excluded.last_study_date,
                    reminder_ends_at=excluded.reminder_ends_at, updated_at=excluded.updated_at""", values,
                )
            except Exception:
                logger.exception("SQLite write failed for chat %s; kept in memory", chat_id)
                self.connection = None
                self._record_sqlite_failure()

    def complete_session(self, chat_id: int, session: dict, end_time: str) -> None:
        """Persist session state and history in one transaction."""
        snapshot = self._copy(session)
        if not self.connection:
            self.memory[chat_id] = snapshot
            return

        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = (
            chat_id, snapshot["step"], snapshot["template_id"], snapshot["duration"],
            snapshot["duration_minutes"], snapshot["start_time"],
            json.dumps(snapshot["tasks"], ensure_ascii=False), snapshot["total_sessions"],
            snapshot["total_minutes"], snapshot["streak_days"], snapshot["last_study_date"],
            snapshot["reminder_ends_at"], created,
        )
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute(
                    """INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET step=excluded.step, template_id=excluded.template_id,
                    duration=excluded.duration, duration_minutes=excluded.duration_minutes, start_time=excluded.start_time,
                    tasks=excluded.tasks, total_sessions=excluded.total_sessions, total_minutes=excluded.total_minutes,
                    streak_days=excluded.streak_days, last_study_date=excluded.last_study_date,
                    reminder_ends_at=excluded.reminder_ends_at, updated_at=excluded.updated_at""",
                    values,
                )
                self.connection.execute(
                    "INSERT INTO study_history (chat_id, template_id, duration_minutes, task_count, start_time, end_time, created_at) VALUES (?,?,?,?,?,?,?)",
                    (chat_id, snapshot["template_id"], snapshot["duration_minutes"], len(snapshot["tasks"]), snapshot["start_time"], end_time, created),
                )
                self.connection.execute(
                    "DELETE FROM study_history WHERE chat_id=? AND id NOT IN (SELECT id FROM study_history WHERE chat_id=? ORDER BY id DESC LIMIT 200)",
                    (chat_id, chat_id),
                )
                self.connection.execute("COMMIT")
                self.memory[chat_id] = snapshot
            except Exception:
                try:
                    self.connection.execute("ROLLBACK")
                except Exception:
                    logger.exception("SQLite rollback failed for chat %s", chat_id)
                logger.exception("Could not complete study session for chat %s", chat_id)
                self.connection = None
                self._record_sqlite_failure()
                self.memory[chat_id] = snapshot

    def record_completion(self, chat_id: int, session: dict, end_time: str) -> None:
        if not self.connection:
            return
        try:
            created = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.lock:
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    self.connection.execute(
                        "INSERT INTO study_history (chat_id, template_id, duration_minutes, task_count, start_time, end_time, created_at) VALUES (?,?,?,?,?,?,?)",
                        (chat_id, session["template_id"], session["duration_minutes"], len(session["tasks"]), session["start_time"], end_time, created),
                    )
                    self.connection.execute(
                        "DELETE FROM study_history WHERE chat_id=? AND id NOT IN (SELECT id FROM study_history WHERE chat_id=? ORDER BY id DESC LIMIT 200)",
                        (chat_id, chat_id),
                    )
                    self.connection.execute("COMMIT")
                except Exception:
                    self.connection.execute("ROLLBACK")
                    raise
        except Exception:
            logger.exception("Could not record study history for chat %s", chat_id)

    def get_media_id(self, cache_key: str) -> str | None:
        if not self.connection:
            return None
        try:
            with self.lock:
                row = self.connection.execute(
                    "SELECT file_id FROM media_cache WHERE cache_key=?",
                    (cache_key,),
                ).fetchone()
            return row[0] if row else None
        except Exception:
            logger.debug("Could not read media cache", exc_info=True)
            return None

    def save_media_id(self, cache_key: str, file_id: str) -> None:
        if not self.connection or not file_id:
            return
        try:
            with self.lock:
                self.connection.execute(
                    "INSERT INTO media_cache(cache_key, file_id, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET file_id=excluded.file_id, updated_at=excluded.updated_at",
                    (cache_key, file_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
        except Exception:
            logger.debug("Could not persist media cache", exc_info=True)

    def upsert_user(
        self,
        chat_id: int,
        user_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> bool:
        """Register a chat and return True only the first time it is seen."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        normalized = {
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "username": username or "",
            "first_name": first_name or "",
            "last_name": last_name or "",
            "is_blocked": 0,
            "created_at": now,
            "last_seen_at": now,
        }
        if not self.connection:
            previous = self.memory_users.get(chat_id)
            if previous:
                normalized["created_at"] = previous["created_at"]
                normalized["is_blocked"] = previous.get("is_blocked", 0)
            self.memory_users[chat_id] = normalized
            return previous is None
        try:
            with self.lock:
                existing = self.connection.execute(
                    "SELECT 1 FROM bot_users WHERE chat_id=?", (chat_id,)
                ).fetchone()
                self.connection.execute(
                    """INSERT INTO bot_users(chat_id, user_id, username, first_name, last_name, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id,
                    username=excluded.username, first_name=excluded.first_name,
                    last_name=excluded.last_name, last_seen_at=excluded.last_seen_at""",
                    (chat_id, user_id, username or "", first_name or "", last_name or "", now, now),
                )
            return existing is None
        except Exception:
            logger.debug("Could not register user %s", chat_id, exc_info=True)
            return False

    def is_blocked(self, chat_id: int) -> bool:
        if not self.connection:
            return bool(self.memory_users.get(chat_id, {}).get("is_blocked", 0))
        try:
            with self.lock:
                row = self.connection.execute("SELECT is_blocked FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
            return bool(row and row[0])
        except Exception:
            logger.debug("Could not read blocked state for %s", chat_id, exc_info=True)
            return False

    def set_blocked(self, chat_id: int, blocked: bool) -> None:
        if not self.connection:
            user = self.memory_users.setdefault(
                chat_id,
                {
                    "chat_id": chat_id, "user_id": chat_id, "username": "", "first_name": "", "last_name": "",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "last_seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
            user["is_blocked"] = 1 if blocked else 0
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock:
                self.connection.execute(
                    "INSERT INTO bot_users(chat_id, user_id, is_blocked, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET is_blocked=excluded.is_blocked, last_seen_at=excluded.last_seen_at",
                    (chat_id, chat_id, 1 if blocked else 0, now, now),
                )
        except Exception:
            logger.debug("Could not update blocked state for %s", chat_id, exc_info=True)

    def user_count(self, blocked: bool | None = None) -> int:
        if not self.connection:
            if blocked is None:
                return len(self.memory_users)
            return sum(1 for user in self.memory_users.values() if bool(user.get("is_blocked")) == blocked)
        try:
            with self.lock:
                if blocked is None:
                    row = self.connection.execute("SELECT COUNT(*) FROM bot_users").fetchone()
                else:
                    row = self.connection.execute("SELECT COUNT(*) FROM bot_users WHERE is_blocked=?", (1 if blocked else 0,)).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0

    def list_users(self, limit: int = 30, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        if not self.connection:
            users = sorted(self.memory_users.values(), key=lambda item: item.get("last_seen_at", ""), reverse=True)
            return [dict(user) for user in users[offset:offset + limit]]
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT chat_id, user_id, username, first_name, last_name, is_blocked, created_at, last_seen_at "
                    "FROM bot_users ORDER BY last_seen_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            keys = ["chat_id", "user_id", "username", "first_name", "last_name", "is_blocked", "created_at", "last_seen_at"]
            return [dict(zip(keys, row)) for row in rows]
        except Exception:
            logger.exception("Could not list users")
            return []

    def new_users(self, hours: int = 24, limit: int = 30) -> list[dict]:
        hours = max(1, min(int(hours), 24 * 30))
        limit = max(1, min(int(limit), 100))
        if not self.connection:
            cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
            users = [
                user for user in self.memory_users.values()
                if datetime.fromisoformat(user["created_at"]).timestamp() >= cutoff
            ]
            users.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            return [dict(user) for user in users[:limit]]
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - hours * 3600, timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT chat_id, user_id, username, first_name, last_name, is_blocked, created_at, last_seen_at "
                    "FROM bot_users WHERE created_at>=? ORDER BY created_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            keys = ["chat_id", "user_id", "username", "first_name", "last_name", "is_blocked", "created_at", "last_seen_at"]
            return [dict(zip(keys, row)) for row in rows]
        except Exception:
            logger.exception("Could not list new users")
            return []

    def count_users_since(self, hours: int = 24, field: str = "last_seen_at") -> int:
        hours = max(1, min(int(hours), 24 * 30))
        if field not in {"created_at", "last_seen_at"}:
            raise ValueError("field must be created_at or last_seen_at")
        if not self.connection:
            cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
            return sum(
                1 for user in self.memory_users.values()
                if datetime.fromisoformat(user[field]).timestamp() >= cutoff
            )
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - hours * 3600, timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock:
                row = self.connection.execute(f"SELECT COUNT(*) FROM bot_users WHERE {field}>=?", (cutoff,)).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0

    def active_session_count(self) -> int:
        now_ms = cairo_now().timestamp() * 1000
        if not self.connection:
            return sum(
                1 for session in self.memory.values()
                if session.get("step") not in {"idle", "done"}
                or (session.get("step") == "done" and session.get("reminder_ends_at", 0) > now_ms)
            )
        try:
            with self.lock:
                row = self.connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE step NOT IN ('idle','done') OR (step='done' AND reminder_ends_at>?)",
                    (now_ms,),
                ).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            return 0

    def recent_events_for_chat(self, chat_id: int, limit: int = 10) -> list[dict]:
        limit = max(1, min(int(limit), 50))
        if not self.connection:
            return [dict(event) for event in reversed(self.memory_events) if event["chat_id"] == chat_id][:limit]
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT chat_id, event_type, details, created_at FROM bot_events WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                    (chat_id, limit),
                ).fetchall()
            keys = ["chat_id", "event_type", "details", "created_at"]
            return [dict(zip(keys, row)) for row in rows]
        except Exception:
            logger.exception("Could not read events for chat %s", chat_id)
            return []

    def user_profile(self, chat_id: int) -> dict | None:
        if not self.connection:
            user = self.memory_users.get(chat_id)
            if not user:
                return None
            result = dict(user)
            session = self.memory.get(chat_id, self.fresh())
            result.update({"current_step": session.get("step", "idle"), "total_sessions": session.get("total_sessions", 0), "total_minutes": session.get("total_minutes", 0), "history_count": 0, "event_count": 0})
            return result
        try:
            with self.lock:
                row = self.connection.execute(
                    "SELECT u.chat_id, u.user_id, u.username, u.first_name, u.last_name, u.is_blocked, u.created_at, u.last_seen_at, "
                    "COALESCE(s.step, 'idle'), COALESCE(s.total_sessions, 0), COALESCE(s.total_minutes, 0), "
                    "(SELECT COUNT(*) FROM study_history h WHERE h.chat_id=u.chat_id), "
                    "(SELECT COUNT(*) FROM bot_events e WHERE e.chat_id=u.chat_id) "
                    "FROM bot_users u LEFT JOIN sessions s ON s.chat_id=u.chat_id WHERE u.chat_id=?",
                    (chat_id,),
                ).fetchone()
            if not row:
                return None
            keys = ["chat_id", "user_id", "username", "first_name", "last_name", "is_blocked", "created_at", "last_seen_at", "current_step", "total_sessions", "total_minutes", "history_count", "event_count"]
            return dict(zip(keys, row))
        except Exception:
            logger.exception("Could not read user profile for %s", chat_id)
            return None

    def broadcast_targets(self) -> list[int]:
        if not self.connection:
            return [int(chat_id) for chat_id, user in self.memory_users.items() if not user.get("is_blocked")]
        try:
            with self.lock:
                rows = self.connection.execute("SELECT chat_id FROM bot_users WHERE is_blocked=0").fetchall()
            return [int(row[0]) for row in rows]
        except Exception:
            return []

    def log_event(self, chat_id: int, event_type: str, details: str = "") -> None:
        event = {
            "chat_id": int(chat_id),
            "event_type": event_type[:80],
            "details": details[:500],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if not self.connection:
            self.memory_events.append(event)
            self.memory_events = self.memory_events[-500:]
            return
        try:
            with self.lock:
                self.connection.execute(
                    "INSERT INTO bot_events(chat_id, event_type, details, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, event_type[:80], details[:500], datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
        except Exception:
            logger.debug("Could not log event", exc_info=True)

    def recent_events(self, minutes: int = 10, limit: int = 100) -> list[dict]:
        minutes = max(1, min(int(minutes), 1440))
        limit = max(1, min(int(limit), 200))
        if not self.connection:
            cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
            return [
                dict(event)
                for event in reversed(self.memory_events)
                if datetime.fromisoformat(event["created_at"]).timestamp() >= cutoff
            ][:limit]
        limit = max(1, min(int(limit), 200))
        cutoff = (datetime.now(timezone.utc).timestamp() - minutes * 60)
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT chat_id, event_type, details, created_at FROM bot_events WHERE created_at>=? ORDER BY id DESC LIMIT ?",
                    (cutoff_iso, limit),
                ).fetchall()
            keys = ["chat_id", "event_type", "details", "created_at"]
            return [dict(zip(keys, row)) for row in rows]
        except Exception:
            return []

    def bot_totals(self) -> dict:
        if not self.connection:
            return {
                "users": len(self.memory_users),
                "blocked": sum(1 for user in self.memory_users.values() if user.get("is_blocked")),
                "sessions": 0,
                "minutes": 0,
            }
        try:
            with self.lock:
                users = self.connection.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
                blocked = self.connection.execute("SELECT COUNT(*) FROM bot_users WHERE is_blocked=1").fetchone()[0]
                sessions = self.connection.execute("SELECT COUNT(*) FROM study_history").fetchone()[0]
                minutes = self.connection.execute("SELECT COALESCE(SUM(duration_minutes),0) FROM study_history").fetchone()[0]
            return {"users": int(users), "blocked": int(blocked), "sessions": int(sessions), "minutes": int(minutes)}
        except Exception:
            return {"users": 0, "blocked": 0, "sessions": 0, "minutes": 0}

    def get_setting(self, key: str, default: str = "") -> str:
        if not self.connection:
            return default
        try:
            with self.lock:
                row = self.connection.execute("SELECT setting_value FROM bot_settings WHERE setting_key=?", (key,)).fetchone()
            return row[0] if row else default
        except Exception:
            return default

    def set_setting(self, key: str, value: str) -> None:
        if not self.connection:
            return
        try:
            with self.lock:
                self.connection.execute(
                    "INSERT INTO bot_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at",
                    (key, value, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
        except Exception:
            logger.debug("Could not save setting %s", key, exc_info=True)

    def daily_session_count(self, chat_id: int, day=None) -> int:
        """Return completed sessions for a Cairo calendar day."""
        if not self.connection:
            return 0
        day = day or cairo_now().date()
        local_start = datetime.combine(day, datetime.min.time(), tzinfo=CAIRO)
        local_end = local_start + timedelta(days=1)
        start_utc = local_start.astimezone(timezone.utc).isoformat(timespec="seconds")
        end_utc = local_end.astimezone(timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock:
                row = self.connection.execute(
                    "SELECT COUNT(*) FROM study_history WHERE chat_id=? AND created_at>=? AND created_at<?",
                    (chat_id, start_utc, end_utc),
                ).fetchone()
            return int(row[0] if row else 0)
        except Exception:
            logger.exception("Could not count daily sessions for chat %s", chat_id)
            return 0

    def recent_history(self, chat_id: int, limit: int = 5) -> list[dict]:
        if not self.connection:
            return []
        try:
            limit = max(1, min(int(limit), 20))
            with self.lock:
                rows = self.connection.execute(
                    "SELECT template_id, duration_minutes, task_count, start_time, end_time, created_at FROM study_history WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                    (chat_id, limit),
                ).fetchall()
            keys = ["template_id", "duration_minutes", "task_count", "start_time", "end_time", "created_at"]
            return [dict(zip(keys, row)) for row in rows]
        except Exception:
            logger.exception("Could not read study history for chat %s", chat_id)
            return []

    def pending_reminders(self) -> list[tuple[int, dict, float]]:
        if not self.connection:
            return []
        # cairo_now() is timezone-aware, so .timestamp() always yields a correct
        # UTC epoch regardless of the server's local timezone. A naive
        # datetime.now().timestamp() silently assumes the server clock is UTC,
        # which produces multi-hour drift on reminder restoration if it isn't.
        now = cairo_now().timestamp() * 1000
        try:
            with self.lock:
                rows = self.connection.execute(
                    "SELECT chat_id, reminder_ends_at FROM sessions WHERE reminder_ends_at > ?",
                    (now,),
                ).fetchall()
            return [(chat_id, self.get(chat_id), ends) for chat_id, ends in rows]
        except Exception:
            logger.exception("Could not restore pending reminders")
            return []

    def close(self) -> None:
        if self.connection:
            with self.lock:
                try:
                    self.connection.close()
                except Exception:
                    logger.exception("Could not close SQLite connection")
                finally:
                    self.connection = None


def reset_flow(store: Store, chat_id: int, keep_stats: bool = True) -> dict:
    old = store.get(chat_id)
    fresh = store.fresh()
    if keep_stats:
        for key in ("total_sessions", "total_minutes", "streak_days", "last_study_date"):
            fresh[key] = old[key]
    store.save(chat_id, fresh)
    return fresh
