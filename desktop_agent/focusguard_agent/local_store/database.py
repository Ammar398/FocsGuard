"""Local SQLite store for sessions, events and scores (spec sections 37, 45).

WHY A LOCAL DATABASE AT ALL
---------------------------
The agent must keep working with no network (section 37). Everything is written
here **first** and uploaded afterwards, so a dropped connection costs nothing and
a crash mid-session loses at most the last few seconds.

IDEMPOTENCY
-----------
Every event carries a `uuid` generated **on this machine, before the write**, with
a UNIQUE constraint behind it. That is the whole of the section 37 retry design:
if an upload times out ambiguously the agent can simply send again, and the
duplicate is rejected by the database rather than by hopeful application logic.

WAL MODE
--------
The agent writes continuously while a dashboard may be reading. Without
write-ahead logging those readers would block the writer and the capture thread
would stall behind a UI query.

TIME
----
Durations are measured on a **monotonic** clock, because a wall clock can jump
backwards when the system syncs time and would produce negative durations.
Storage needs wall-clock times, so `SessionClock` converts once, at the session
boundary, using the offset captured when the session started.

RETENTION (SECTION 45)
----------------------
Raw events are kept for 30 days by default, then rolled into `daily_summaries`
and deleted. Summaries are tiny and kept indefinitely. On a 128 GB SSD this is
the difference between a database that grows forever and one that does not.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid as uuid_module
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from focusguard_shared.constants import SyncStatus
from focusguard_shared.utilities import get_logger
from focusguard_shared.utilities.paths import app_data_dir

logger = get_logger("local_store.database")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    task_name     TEXT,
    task_category TEXT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    agent_version TEXT NOT NULL,
    sync_status   TEXT NOT NULL DEFAULT 'PENDING'
);

CREATE TABLE IF NOT EXISTS vision_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid             TEXT NOT NULL UNIQUE,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL,
    start_time       TEXT NOT NULL,
    end_time         TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    confidence       REAL NOT NULL,
    metadata         TEXT,
    sync_status      TEXT NOT NULL DEFAULT 'PENDING',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid             TEXT NOT NULL UNIQUE,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    process_name     TEXT,
    application_name TEXT NOT NULL,
    window_title     TEXT,
    domain           TEXT,
    path_prefix      TEXT,
    category         TEXT NOT NULL,
    classification   TEXT NOT NULL,
    rule_source      TEXT NOT NULL,
    start_time       TEXT NOT NULL,
    end_time         TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    sync_status      TEXT NOT NULL DEFAULT 'PENDING',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_scores (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                 INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    recorded_at                TEXT NOT NULL,
    visual_attention_score     REAL NOT NULL,
    digital_productivity_score REAL,
    combined_focus_score       REAL NOT NULL,
    active_events              TEXT,
    sync_status                TEXT NOT NULL DEFAULT 'PENDING'
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    day                 TEXT NOT NULL UNIQUE,
    sessions_count      INTEGER NOT NULL DEFAULT 0,
    monitored_seconds   REAL NOT NULL DEFAULT 0,
    focused_seconds     REAL NOT NULL DEFAULT 0,
    distracted_seconds  REAL NOT NULL DEFAULT 0,
    average_focus_score REAL NOT NULL DEFAULT 0,
    event_seconds       TEXT,
    application_seconds TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_vision_session   ON vision_events(session_id, start_time);
CREATE INDEX IF NOT EXISTS ix_vision_sync      ON vision_events(sync_status);
CREATE INDEX IF NOT EXISTS ix_activity_session ON activity_events(session_id, start_time);
CREATE INDEX IF NOT EXISTS ix_activity_sync    ON activity_events(sync_status);
CREATE INDEX IF NOT EXISTS ix_scores_session   ON focus_scores(session_id, recorded_at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SessionClock:
    """Converts monotonic timestamps to wall-clock times.

    Durations must be measured monotonically - a wall clock can step backwards
    when the system syncs time, which would produce negative durations. But
    storage and reports need real times, so the offset is captured once at the
    session start and applied on the way out.
    """

    started_wall: datetime
    started_monotonic: float

    def to_wall(self, monotonic_time: float) -> datetime:
        return self.started_wall + timedelta(
            seconds=monotonic_time - self.started_monotonic
        )


class LocalStore:
    """Thread-safe SQLite store for the agent."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "focusguard.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=10.0
        )
        self._connection.row_factory = sqlite3.Row

        # WAL lets a dashboard read while the agent writes; without it a UI query
        # would block the capture pipeline.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=NORMAL")

        self._migrate()
        logger.info("Local store ready at %s", self.path)

    # -- schema ------------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._connection.commit()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                yield cursor
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            finally:
                cursor.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> LocalStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- sessions ----------------------------------------------------------

    def start_session(
        self,
        name: str,
        agent_version: str,
        task_name: str | None = None,
        task_category: str | None = None,
        started_at: datetime | None = None,
    ) -> int:
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO sessions(uuid, name, task_name, task_category,
                                        started_at, agent_version)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid_module.uuid4()),
                    name,
                    task_name,
                    task_category,
                    (started_at or datetime.now(UTC)).isoformat(),
                    agent_version,
                ),
            )
            return int(cursor.lastrowid)

    def end_session(self, session_id: int, ended_at: datetime | None = None) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                ((ended_at or datetime.now(UTC)).isoformat(), session_id),
            )

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            return cursor.fetchone()

    def recent_sessions(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            )
            return cursor.fetchall()

    # -- events ------------------------------------------------------------

    def record_vision_event(
        self,
        session_id: int,
        event_type: str,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: float,
        confidence: float,
        metadata: dict | None = None,
        event_uuid: str | None = None,
    ) -> bool:
        """Store one vision event. Returns False if it was already stored.

        A duplicate is not an error: it means a retry arrived, which is exactly
        what the UUID exists to make safe.
        """
        return self._insert_event(
            """INSERT INTO vision_events(uuid, session_id, event_type, start_time,
                                         end_time, duration_seconds, confidence,
                                         metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_uuid or str(uuid_module.uuid4()),
                session_id,
                str(event_type),
                start_time.isoformat(),
                end_time.isoformat(),
                duration_seconds,
                confidence,
                json.dumps(metadata) if metadata else None,
                _now_iso(),
            ),
        )

    def record_activity_event(
        self,
        session_id: int,
        application_name: str,
        category: str,
        classification: str,
        rule_source: str,
        start_time: datetime,
        end_time: datetime,
        duration_seconds: float,
        process_name: str | None = None,
        window_title: str | None = None,
        domain: str | None = None,
        path_prefix: str | None = None,
        event_uuid: str | None = None,
    ) -> bool:
        return self._insert_event(
            """INSERT INTO activity_events(uuid, session_id, process_name,
                                           application_name, window_title, domain,
                                           path_prefix, category, classification,
                                           rule_source, start_time, end_time,
                                           duration_seconds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_uuid or str(uuid_module.uuid4()),
                session_id,
                process_name,
                application_name,
                window_title,
                domain,
                path_prefix,
                str(category),
                str(classification),
                str(rule_source),
                start_time.isoformat(),
                end_time.isoformat(),
                duration_seconds,
                _now_iso(),
            ),
        )

    def _insert_event(self, statement: str, values: tuple) -> bool:
        try:
            with self._cursor() as cursor:
                cursor.execute(statement, values)
            return True
        except sqlite3.IntegrityError:
            # The UNIQUE constraint on uuid did its job.
            logger.debug("Duplicate event rejected by the uuid constraint")
            return False

    def record_score(
        self,
        session_id: int,
        recorded_at: datetime,
        visual_attention_score: float,
        combined_focus_score: float,
        digital_productivity_score: float | None = None,
        active_events: list[str] | None = None,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """INSERT INTO focus_scores(session_id, recorded_at,
                                            visual_attention_score,
                                            digital_productivity_score,
                                            combined_focus_score, active_events)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    recorded_at.isoformat(),
                    visual_attention_score,
                    digital_productivity_score,
                    combined_focus_score,
                    json.dumps(active_events) if active_events else None,
                ),
            )

    # -- reading -----------------------------------------------------------

    def vision_events(self, session_id: int) -> list[sqlite3.Row]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM vision_events WHERE session_id = ? ORDER BY start_time",
                (session_id,),
            )
            return cursor.fetchall()

    def activity_events(self, session_id: int) -> list[sqlite3.Row]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM activity_events WHERE session_id = ? ORDER BY start_time",
                (session_id,),
            )
            return cursor.fetchall()

    def scores(self, session_id: int) -> list[sqlite3.Row]:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM focus_scores WHERE session_id = ? ORDER BY recorded_at",
                (session_id,),
            )
            return cursor.fetchall()

    # -- sync queue (spec section 37) --------------------------------------

    def pending_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._cursor() as cursor:
            for table in ("vision_events", "activity_events", "focus_scores"):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE sync_status = ?",  # noqa: S608
                    (SyncStatus.PENDING.value,),
                )
                counts[table] = int(cursor.fetchone()[0])
        return counts

    def take_pending(self, table: str, limit: int = 50) -> list[sqlite3.Row]:
        """Return a batch awaiting upload, oldest first."""
        if table not in ("vision_events", "activity_events", "focus_scores"):
            raise ValueError(f"Unknown table {table!r}")

        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE sync_status = ? ORDER BY id LIMIT ?",  # noqa: S608
                (SyncStatus.PENDING.value, limit),
            )
            return cursor.fetchall()

    def mark_synced(self, table: str, ids: list[int]) -> None:
        if not ids or table not in ("vision_events", "activity_events", "focus_scores"):
            return

        placeholders = ",".join("?" * len(ids))
        with self._cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET sync_status = ? WHERE id IN ({placeholders})",  # noqa: S608
                (SyncStatus.SYNCED.value, *ids),
            )

    # -- retention (spec section 45) ---------------------------------------

    def roll_up_and_prune(self, retention_days: int = 30, now: datetime | None = None) -> int:
        """Summarise days older than the retention window, then delete their rows.

        Returns the number of raw rows removed. Summaries are kept indefinitely -
        they are a few hundred bytes a day, against tens of thousands of rows.
        """
        cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
        cutoff_iso = cutoff.isoformat()
        removed = 0

        with self._cursor() as cursor:
            cursor.execute(
                """SELECT substr(start_time, 1, 10) AS day,
                          COUNT(DISTINCT session_id)  AS sessions,
                          SUM(duration_seconds)       AS distracted
                     FROM vision_events
                    WHERE start_time < ?
                 GROUP BY day""",
                (cutoff_iso,),
            )
            days = cursor.fetchall()

            for row in days:
                cursor.execute(
                    """SELECT event_type, SUM(duration_seconds) AS seconds
                         FROM vision_events
                        WHERE substr(start_time, 1, 10) = ?
                     GROUP BY event_type""",
                    (row["day"],),
                )
                event_seconds = {r["event_type"]: r["seconds"] for r in cursor.fetchall()}

                cursor.execute(
                    """SELECT application_name, SUM(duration_seconds) AS seconds
                         FROM activity_events
                        WHERE substr(start_time, 1, 10) = ?
                     GROUP BY application_name""",
                    (row["day"],),
                )
                app_seconds = {r["application_name"]: r["seconds"] for r in cursor.fetchall()}

                cursor.execute(
                    """INSERT INTO daily_summaries(day, sessions_count, distracted_seconds,
                                                   event_seconds, application_seconds,
                                                   created_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(day) DO UPDATE SET
                           sessions_count      = excluded.sessions_count,
                           distracted_seconds  = excluded.distracted_seconds,
                           event_seconds       = excluded.event_seconds,
                           application_seconds = excluded.application_seconds""",
                    (
                        row["day"],
                        row["sessions"],
                        row["distracted"] or 0.0,
                        json.dumps(event_seconds),
                        json.dumps(app_seconds),
                        _now_iso(),
                    ),
                )

            for table in ("vision_events", "activity_events"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE start_time < ?",  # noqa: S608
                    (cutoff_iso,),
                )
                removed += cursor.rowcount

            cursor.execute("DELETE FROM focus_scores WHERE recorded_at < ?", (cutoff_iso,))
            removed += cursor.rowcount

        if removed:
            logger.info("Retention: rolled up %d day(s), removed %d rows", len(days), removed)
        return removed

    def vacuum(self) -> None:
        """Reclaim space after a prune. Kept separate: it rewrites the file."""
        with self._lock:
            self._connection.execute("VACUUM")

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
