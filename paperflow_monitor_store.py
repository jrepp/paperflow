"""SQLite monitor adapter. Run completion and research content commit together."""
from __future__ import annotations

import random
import sqlite3
import time
from contextlib import contextmanager
from uuid import UUID, uuid4

from paperflow_ingest_models import ChannelConfig, IngestBatch
from paperflow_ingestion import persist_batch_in_repository
from paperflow_monitor_models import MonitoredChannel, MonitorRun
from paperflow_settings import MonitorSettings, StorageSettings
from paperflow_storage_sqlite import SQLiteResearchRepository, SQLiteResearchStorage

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS monitor_channels (
 name TEXT PRIMARY KEY, config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 paused INTEGER NOT NULL DEFAULT 0, interval_seconds INTEGER NOT NULL,
 next_due_at REAL NOT NULL, last_success_at REAL, consecutive_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS monitor_runs (
 id TEXT PRIMARY KEY, channel TEXT NOT NULL REFERENCES monitor_channels(name),
 config TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
 queued_at REAL NOT NULL, started_at REAL, finished_at REAL, heartbeat_at REAL,
 completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
 item_count INTEGER NOT NULL DEFAULT 0, warning_count INTEGER NOT NULL DEFAULT 0,
 duration_seconds REAL, error TEXT, trace_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS monitor_one_active_channel
 ON monitor_runs(channel) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS monitor_runs_history ON monitor_runs(queued_at);
"""


class MonitorStore:
    def __init__(self, storage: StorageSettings, settings: MonitorSettings):
        self.storage = storage
        self.settings = settings

    def _jitter(self, base_seconds):
        """Positive-only spread never shortens an interval or upstream cooldown."""
        ceiling = min(base_seconds * self.settings.jitter_fraction, self.settings.jitter_max_seconds)
        return random.uniform(0, ceiling) if ceiling else 0

    @contextmanager
    def _db(self, write=False):
        conn = sqlite3.connect(self.storage.path, timeout=self.storage.busy_timeout_seconds)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self, channels: dict[str, ChannelConfig]):
        SQLiteResearchStorage(self.storage).initialize()
        with self._db(True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS monitor_schema_version (version INTEGER PRIMARY KEY)")
            versions = [row[0] for row in conn.execute("SELECT version FROM monitor_schema_version")]
            if versions and versions != [1]:
                raise ValueError("unsupported monitor schema version")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT OR IGNORE INTO monitor_schema_version VALUES (1)")
            conn.execute("UPDATE monitor_channels SET enabled=0")
            timestamp = time.time()
            for name, config in channels.items():
                conn.execute("""INSERT INTO monitor_channels(name,config,interval_seconds,next_due_at)
                    VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                    config=excluded.config, interval_seconds=excluded.interval_seconds, enabled=1,
                    next_due_at=CASE WHEN monitor_channels.next_due_at<=? THEN excluded.next_due_at
                        ELSE monitor_channels.next_due_at END""",
                    (name, config.model_dump_json(), config.interval_seconds,
                     timestamp + self._jitter(config.interval_seconds), timestamp))
            conn.execute("""UPDATE monitor_runs SET status='interrupted', stage='finished', finished_at=?,
                error='Channel removed from configuration' WHERE status='queued'
                AND channel IN (SELECT name FROM monitor_channels WHERE enabled=0)""", (time.time(),))

    def recover(self):
        """Called only after obtaining the exclusive process lock."""
        with self._db(True) as conn:
            timestamp = time.time()
            for row in conn.execute("""SELECT name,interval_seconds FROM monitor_channels WHERE name IN
                (SELECT channel FROM monitor_runs WHERE status='running')""").fetchall():
                conn.execute("UPDATE monitor_channels SET next_due_at=? WHERE name=?",
                    (timestamp+self._jitter(row['interval_seconds']),row['name']))
            conn.execute("""UPDATE monitor_runs SET status='interrupted', stage='finished', finished_at=?,
                error='Monitor stopped before completion' WHERE status='running'""", (time.time(),))

    def _enqueue(self, conn, channel, timestamp):
        existing = conn.execute("SELECT id FROM monitor_runs WHERE channel=? AND status IN ('queued','running')", (channel,)).fetchone()
        if existing:
            return existing[0]
        row = conn.execute("SELECT config,paused FROM monitor_channels WHERE name=? AND enabled=1", (channel,)).fetchone()
        if not row:
            raise KeyError(channel)
        if row["paused"]:
            raise ValueError("channel is paused")
        run_id = str(uuid4())
        conn.execute("""INSERT INTO monitor_runs(id,channel,config,status,stage,queued_at)
            VALUES (?,?,?,'queued','queued',?)""", (run_id, channel, row["config"], timestamp))
        return run_id

    def enqueue(self, channel: str) -> MonitorRun:
        with self._db(True) as conn:
            run_id = self._enqueue(conn, channel, time.time())
            return MonitorRun.model_validate(dict(conn.execute("SELECT * FROM monitor_runs WHERE id=?", (run_id,)).fetchone()))

    def schedule_due(self):
        with self._db(True) as conn:
            timestamp = time.time()
            rows = conn.execute("SELECT name FROM monitor_channels WHERE enabled=1 AND paused=0 AND next_due_at<=?", (timestamp,)).fetchall()
            for row in rows:
                self._enqueue(conn, row["name"], timestamp)

    def claim(self) -> tuple[MonitorRun, ChannelConfig] | None:
        with self._db(True) as conn:
            row = conn.execute("""SELECT r.* FROM monitor_runs r JOIN monitor_channels c ON r.channel=c.name
                WHERE r.status='queued' AND c.enabled=1 AND c.paused=0 ORDER BY r.queued_at LIMIT 1""").fetchone()
            if not row:
                return None
            timestamp = time.time()
            conn.execute("UPDATE monitor_runs SET status='running', stage='starting', started_at=?,heartbeat_at=? WHERE id=?",
                         (timestamp, timestamp, row["id"]))
            data = dict(row)
            data.update(status="running", stage="starting", started_at=timestamp, heartbeat_at=timestamp)
            return MonitorRun.model_validate(data), ChannelConfig.model_validate_json(row["config"])

    def progress(self, run_id: UUID, stage: str, completed: int, total: int, trace_id: str | None = None):
        with self._db(True) as conn:
            conn.execute("""UPDATE monitor_runs SET stage=?,completed=?,total=?,heartbeat_at=?,trace_id=COALESCE(?,trace_id)
                WHERE id=? AND status='running'""", (stage, completed, total, time.time(), trace_id, str(run_id)))

    def finish(self, run_id: UUID, *, batch: IngestBatch | None, error: str | None, duration: float,
               retry_after_seconds: float = 0):
        if (batch is None) == (error is None):
            raise ValueError("completion requires either a successful batch or an error")
        with self._db(True) as conn:
            row = conn.execute("SELECT channel,config FROM monitor_runs WHERE id=? AND status='running'", (str(run_id),)).fetchone()
            if not row:
                raise ValueError("run no longer owns completion")
            if batch is not None and (batch.channel != row["channel"] or batch.config != ChannelConfig.model_validate_json(row["config"])):
                raise ValueError("batch does not belong to the claimed channel configuration")
            timestamp = time.time()
            count = 0
            warnings = 0
            if batch is not None:
                count = persist_batch_in_repository(SQLiteResearchRepository(conn), batch)
                warnings = len(batch.warnings) + sum(e.comments_status == "unavailable" for e in batch.entries)
            status = "interrupted" if error == "InterruptedError" else "failed" if error else "degraded" if warnings else "succeeded"
            conn.execute("""UPDATE monitor_runs SET status=?,stage='finished',finished_at=?,heartbeat_at=?,
                item_count=?,warning_count=?,duration_seconds=?,error=? WHERE id=?""",
                (status, timestamp, timestamp, count, warnings, duration, error, str(run_id)))
            channel = conn.execute("SELECT * FROM monitor_channels WHERE name=?", (row["channel"],)).fetchone()
            failures = channel["consecutive_failures"] + 1 if error else 0
            delay = min(self.settings.max_retry_seconds, self.settings.retry_seconds * 2 ** min(failures - 1, 20)) if error else channel["interval_seconds"]
            if status == "interrupted":
                delay = 0
                failures = channel["consecutive_failures"]
            delay = max(delay, retry_after_seconds)
            delay += self._jitter(delay or channel["interval_seconds"])
            conn.execute("""UPDATE monitor_channels SET next_due_at=?,consecutive_failures=?,
                last_success_at=CASE WHEN ? THEN last_success_at ELSE ? END WHERE name=?""",
                (timestamp + delay, failures, bool(error), timestamp, row["channel"]))

    def pause(self, channel: str, paused: bool):
        with self._db(True) as conn:
            cursor = conn.execute("UPDATE monitor_channels SET paused=? WHERE name=? AND enabled=1", (paused, channel))
            if not cursor.rowcount:
                raise KeyError(channel)
            if not paused:
                row = conn.execute("SELECT interval_seconds,next_due_at FROM monitor_channels WHERE name=?", (channel,)).fetchone()
                timestamp = time.time()
                if row['next_due_at']<=timestamp:
                    conn.execute("UPDATE monitor_channels SET next_due_at=? WHERE name=?",
                        (timestamp+self._jitter(row['interval_seconds']),channel))

    def get_run(self, run_id: UUID) -> MonitorRun | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM monitor_runs WHERE id=?", (str(run_id),)).fetchone()
            return MonitorRun.model_validate(dict(row)) if row else None

    def runs(self, limit: int = 50) -> list[MonitorRun]:
        with self._db() as conn:
            return [MonitorRun.model_validate(dict(row)) for row in conn.execute(
                "SELECT * FROM monitor_runs ORDER BY queued_at DESC,rowid DESC LIMIT ?", (limit,))]

    def running(self) -> list[MonitorRun]:
        with self._db() as conn:
            return [MonitorRun.model_validate(dict(row)) for row in conn.execute(
                "SELECT * FROM monitor_runs WHERE status='running'")]

    def channels(self) -> list[MonitoredChannel]:
        with self._db() as conn:
            result = []
            for row in conn.execute("SELECT * FROM monitor_channels WHERE enabled=1 ORDER BY name").fetchall():
                last = conn.execute("SELECT * FROM monitor_runs WHERE channel=? ORDER BY queued_at DESC,rowid DESC LIMIT 1", (row["name"],)).fetchone()
                active = conn.execute("SELECT id FROM monitor_runs WHERE channel=? AND status IN ('queued','running')", (row["name"],)).fetchone()
                result.append(MonitoredChannel(**{key: row[key] for key in (
                    "name", "paused", "interval_seconds", "next_due_at", "last_success_at", "consecutive_failures")},
                    active_run_id=active[0] if active else None,
                    last_run=MonitorRun.model_validate(dict(last)) if last else None,
                    stale=row["last_success_at"] is None or time.time() - row["last_success_at"] > 2 * row["interval_seconds"]))
            return result
