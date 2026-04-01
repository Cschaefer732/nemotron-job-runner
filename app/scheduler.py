"""
APScheduler integration. Cron/interval jobs enqueue entries into SQLite
rather than running directly — the worker thread handles execution.

To add a cron job, uncomment an entry in SCHEDULED_JOBS or call add_cron_job() at runtime.
"""
import logging
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# Cron job definitions. Each tuple: (job_type, payload, cron_kwargs).
# Example: trigger smart_connections_reembed daily at 02:00
SCHEDULED_JOBS: list = [
    # ("smart_connections_reembed", {}, {"hour": 2, "minute": 0}),
]


def setup_scheduler(conn: sqlite3.Connection, handlers: dict) -> BackgroundScheduler:
    """Create and configure the APScheduler. Call scheduler.start() after this."""
    scheduler = BackgroundScheduler()

    for job_type, payload, cron_kwargs in SCHEDULED_JOBS:
        _add_cron_job(scheduler, conn, job_type, payload, cron_kwargs)
        logger.info("Scheduled cron job: %s %s", job_type, cron_kwargs)

    return scheduler


def _add_cron_job(
    scheduler: BackgroundScheduler,
    conn: sqlite3.Connection,
    job_type: str,
    payload: dict,
    cron_kwargs: dict,
) -> None:
    from app.queue import create_job

    def enqueue():
        job_id = create_job(conn, job_type, payload, trigger="cron")
        logger.info("Cron enqueued job %s type=%s", job_id, job_type)

    scheduler.add_job(enqueue, "cron", **cron_kwargs, id=f"cron_{job_type}")


def add_cron_job(
    scheduler: BackgroundScheduler,
    conn: sqlite3.Connection,
    job_type: str,
    payload: dict,
    **cron_kwargs,
) -> None:
    """Dynamically add a cron job at runtime."""
    _add_cron_job(scheduler, conn, job_type, payload, cron_kwargs)
    logger.info("Dynamically added cron job: %s", job_type)
