import time
from app.queue import init_db, create_job, get_job, list_jobs, update_job, delete_job, Worker


def test_init_db_creates_tables(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "jobs" in tables
    assert "job_results" in tables
    assert "hooks" in tables


def test_create_and_get_job(db):
    job_id = create_job(db, "test_type", {"key": "val"})
    job = get_job(db, job_id)
    assert job is not None
    assert job["type"] == "test_type"
    assert job["status"] == "pending"
    assert job["priority"] == 50
    assert job["payload"] == {"key": "val"}


def test_list_jobs_filter_by_status(db):
    create_job(db, "t1", {})
    create_job(db, "t2", {})
    pending = list_jobs(db, status="pending")
    assert len(pending) == 2


def test_update_job_priority(db):
    job_id = create_job(db, "t", {})
    update_job(db, job_id, priority=10)
    job = get_job(db, job_id)
    assert job["priority"] == 10


def test_cancel_job(db):
    job_id = create_job(db, "t", {})
    update_job(db, job_id, status="cancelled")
    job = get_job(db, job_id)
    assert job["status"] == "cancelled"


def test_cancel_completed_job_fails(db):
    job_id = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    db.commit()
    assert update_job(db, job_id, status="cancelled") != "ok"


def test_update_arbitrary_status_rejected(db):
    """External callers must not be able to set status to running/completed/failed."""
    job_id = create_job(db, "t", {})
    assert update_job(db, job_id, status="running") != "ok"
    assert update_job(db, job_id, status="completed") != "ok"
    assert update_job(db, job_id, status="failed") != "ok"


def test_update_fields_on_running_job_rejected(db):
    """priority/payload/scheduled_at can only be changed while job is pending."""
    job_id = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    db.commit()
    assert update_job(db, job_id, priority=1) != "ok"
    assert update_job(db, job_id, payload={"x": 1}) != "ok"


def test_delete_failed_and_cancelled_jobs(db):
    job_id_failed = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id_failed,))
    job_id_cancelled = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id_cancelled,))
    db.commit()
    assert delete_job(db, job_id_failed) is True
    assert delete_job(db, job_id_cancelled) is True


def test_worker_picks_up_past_scheduled_job(db):
    """Jobs with scheduled_at in the past should be picked up by the worker."""
    results = []

    class FakeHandler:
        job_type = "sched"
        def run(self, job_id, payload):
            results.append(True)
            return {}

    # Create job before starting worker
    create_job(db, "sched", {}, scheduled_at="2000-01-01T00:00:00+00:00")
    worker = Worker(db, {"sched": FakeHandler()})
    worker.start()
    time.sleep(0.5)
    worker.stop()
    assert len(results) == 1, "Worker should have run the scheduled job"


def test_worker_skips_future_scheduled_job(db):
    """Jobs with scheduled_at in the future should not be picked up."""
    results = []

    class FakeHandler:
        job_type = "future"
        def run(self, job_id, payload):
            results.append(True)
            return {}

    create_job(db, "future", {}, scheduled_at="2099-01-01T00:00:00+00:00")
    worker = Worker(db, {"future": FakeHandler()})
    worker.start()
    time.sleep(0.5)
    worker.stop()
    assert len(results) == 0, "Worker must not run future-scheduled jobs early"


def test_delete_completed_job(db):
    job_id = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    db.commit()
    assert delete_job(db, job_id) is True
    assert get_job(db, job_id) is None


def test_delete_pending_job_fails(db):
    job_id = create_job(db, "t", {})
    assert delete_job(db, job_id) != "ok"


def test_worker_runs_handler(db):
    results = []

    class FakeHandler:
        job_type = "fake"

        def run(self, job_id, payload):
            results.append(payload)
            return {"done": True}

    # Create job before starting worker so the first scan is guaranteed to find it
    job_id = create_job(db, "fake", {"x": 1})
    worker = Worker(db, {"fake": FakeHandler()})
    worker.start()
    time.sleep(0.5)
    worker.stop()

    job = get_job(db, job_id)
    assert job["status"] == "completed"
    assert results == [{"x": 1}]


def test_worker_marks_failed_on_handler_error(db):
    class FailHandler:
        job_type = "fail"

        def run(self, job_id, payload):
            raise ValueError("deliberate failure")

    job_id = create_job(db, "fail", {})
    worker = Worker(db, {"fail": FailHandler()})
    worker.start()
    time.sleep(0.5)
    worker.stop()

    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "deliberate failure" in job["error"]


def test_worker_skips_unknown_handler(db):
    job_id = create_job(db, "ghost", {})
    worker = Worker(db, {})
    worker.start()
    time.sleep(0.5)
    worker.stop()
    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "No handler" in job["error"]
