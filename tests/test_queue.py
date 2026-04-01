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


def test_delete_completed_job(db):
    job_id = create_job(db, "t", {})
    db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    db.commit()
    assert delete_job(db, job_id) is True
    assert get_job(db, job_id) is None


def test_delete_pending_job_fails(db):
    job_id = create_job(db, "t", {})
    assert delete_job(db, job_id) is False


def test_worker_runs_handler(db):
    results = []

    class FakeHandler:
        job_type = "fake"

        def run(self, job_id, payload):
            results.append(payload)
            return {"done": True}

    worker = Worker(db, {"fake": FakeHandler()})
    worker.start()
    job_id = create_job(db, "fake", {"x": 1})
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

    worker = Worker(db, {"fail": FailHandler()})
    worker.start()
    job_id = create_job(db, "fail", {})
    time.sleep(0.5)
    worker.stop()

    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "deliberate failure" in job["error"]


def test_worker_skips_unknown_handler(db):
    worker = Worker(db, {})
    worker.start()
    job_id = create_job(db, "ghost", {})
    time.sleep(0.5)
    worker.stop()
    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "No handler" in job["error"]
