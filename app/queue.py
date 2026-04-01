import sqlite3
import threading
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    priority     INTEGER NOT NULL DEFAULT 50,
    trigger      TEXT NOT NULL DEFAULT 'manual',
    scheduled_at TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS job_results (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    result     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hooks (
    name        TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    conn: sqlite3.Connection,
    job_type: str,
    payload: dict,
    priority: int = 50,
    trigger: str = "manual",
    scheduled_at: Optional[str] = None,
) -> str:
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, type, payload, priority, trigger, scheduled_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, job_type, json.dumps(payload), priority, trigger, scheduled_at, _now(), _now()),
    )
    conn.commit()
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["payload"] = json.loads(job["payload"])
    result_row = conn.execute(
        "SELECT result FROM job_results WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    job["result"] = json.loads(result_row["result"]) if result_row else None
    return job


def list_jobs(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    trigger: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    query = "SELECT * FROM jobs WHERE 1=1"
    params: list = []
    if status:
        query += " AND status=?"
        params.append(status)
    if job_type:
        query += " AND type=?"
        params.append(job_type)
    if trigger:
        query += " AND trigger=?"
        params.append(trigger)
    query += " ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        result.append(d)
    return result


def update_job(
    conn: sqlite3.Connection,
    job_id: str,
    priority: Optional[int] = None,
    payload: Optional[dict] = None,
    scheduled_at: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """Returns False if job not found or update not allowed.

    Rules:
    - status: only 'cancelled' may be set externally; pending and running jobs may be cancelled.
    - priority/payload/scheduled_at: only editable while job is pending.
    """
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return False
    current_status = row["status"]

    # Only 'cancelled' is an externally-settable status
    if status is not None and status != "cancelled":
        return False
    if status == "cancelled" and current_status not in ("pending", "running"):
        return False

    # Field edits (priority/payload/scheduled_at) only allowed on pending jobs
    if any(v is not None for v in (priority, payload, scheduled_at)):
        if current_status != "pending":
            return False

    sets = ["updated_at=?"]
    params: list = [_now()]
    if priority is not None:
        sets.append("priority=?")
        params.append(priority)
    if payload is not None:
        sets.append("payload=?")
        params.append(json.dumps(payload))
    if scheduled_at is not None:
        sets.append("scheduled_at=?")
        params.append(scheduled_at)
    if status is not None:
        sets.append("status=?")
        params.append(status)
    params.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    return True


def delete_job(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return False
    if row["status"] not in ("completed", "failed", "cancelled"):
        return False
    with conn:
        conn.execute("DELETE FROM job_results WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return True


class Worker:
    def __init__(self, conn: sqlite3.Connection, handler_registry: dict):
        self._conn = conn
        self._registry = handler_registry
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-worker")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._process_next()
            except Exception:
                logger.exception("Worker loop error")
            self._stop.wait(2)

    def _process_next(self) -> None:
        # Use datetime(scheduled_at) to handle ISO 8601 strings with timezone offsets,
        # which don't compare correctly against datetime('now') as raw strings.
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE status='pending' "
            "AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime('now')) "
            "ORDER BY priority ASC, created_at ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return

        job_id = row["id"]
        job_type = row["type"]
        payload = json.loads(row["payload"])

        # Add AND status='pending' guard: if the job was cancelled between the SELECT
        # and this UPDATE, rowcount will be 0 and we skip execution.
        now = _now()
        cur = self._conn.execute(
            "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=? AND status='pending'",
            (now, now, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return  # job was cancelled or picked up by another process between SELECT and UPDATE
        logger.info("Running job %s type=%s", job_id, job_type)

        handler = self._registry.get(job_type)
        if handler is None:
            now = _now()
            self._conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?",
                (f"No handler for job type: {job_type}", now, now, job_id),
            )
            self._conn.commit()
            return

        try:
            result = handler.run(job_id, payload)
            result_id = str(uuid.uuid4())
            now = _now()
            self._conn.execute(
                "INSERT INTO job_results (id, job_id, result, created_at) VALUES (?, ?, ?, ?)",
                (result_id, job_id, json.dumps(result), now),
            )
            self._conn.execute(
                "UPDATE jobs SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                (now, now, job_id),
            )
            self._conn.commit()
            logger.info("Job %s completed", job_id)
        except Exception as exc:
            now = _now()
            self._conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?",
                (str(exc), now, now, job_id),
            )
            self._conn.commit()
            logger.exception("Job %s failed", job_id)
