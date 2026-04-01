# Nemotron Job Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Nemotron 8B inference service + extensible job queue with REST and MCP interfaces, running as two NSSM Windows services.

**Architecture:** Two NSSM services — `NemotronServer` (llama.cpp serving Nemotron Q5_K_M on GPU 1, port 8081) and `NemotronJobRunner` (FastAPI + SQLite queue + worker thread, port 8082). The runner mounts fastmcp at `/mcp/sse` for agent access and exposes a REST API for job submission, priority edits, and hook registration. A background worker serializes GPU jobs one at a time.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, fastmcp 2.x, APScheduler 3.x, SQLite (stdlib), pydantic 2.x, httpx, pytest, NSSM, llama.cpp (llama-server.exe CUDA 12 build)

---

## Parallel Execution Map

```
Task 1 (scaffold) ──┬── Task 2 (queue.py)       ──┐
                    ├── Task 3 (models.py)          │
                    ├── Task 4 (health.py)           ├── Task 7 (jobs router)    ──┐
                    ├── Task 5 (handlers/__init__)   ├── Task 8 (hooks router)     ├── Task 11 (main.py)
                    └── Task 6 (smart_connections)  ├── Task 9 (mcp_server.py)    │   ── Task 14 (NSSM)
                                                    └── Task 10 (scheduler.py) ──┘
Task 12 (llama binary)  ─── can start immediately, parallel to all code tasks
Task 13 (model download) ── can start immediately, takes longest (~5.5 GB)
Task 15 (smart_env.json) ── independent, any time
```

---

## File Map

| File | Responsibility |
|------|----------------|
| `app/__init__.py` | Package marker |
| `app/main.py` | FastAPI app factory, lifespan (startup health check, worker start) |
| `app/queue.py` | SQLite init, CRUD helpers, Worker thread |
| `app/health.py` | `GET /health` router + nvidia-smi VRAM query |
| `app/models.py` | Pydantic request/response models |
| `app/jobs_router.py` | `POST/GET/PATCH/DELETE /jobs` endpoints |
| `app/hooks_router.py` | `GET/PUT/POST /hooks` endpoints |
| `app/mcp_server.py` | fastmcp MCP server, all 6 tools |
| `app/scheduler.py` | APScheduler setup + cron job registration |
| `app/handlers/__init__.py` | `JobHandler` base class + auto-discovery from `handlers/*.py` |
| `app/handlers/smart_connections.py` | `SmartConnectionsReembed` handler |
| `tests/conftest.py` | Shared fixtures: in-memory DB, TestClient |
| `tests/test_queue.py` | Queue CRUD + worker unit tests |
| `tests/test_api.py` | REST endpoint integration tests |
| `tests/test_handlers.py` | Handler discovery + SmartConnections handler |
| `requirements.txt` | Pinned dependencies |
| `start.bat` | Manual launch for testing |
| `.gitignore` | Excludes venv, data, logs, model |

---

## Task 1: Scaffold — venv, requirements, directory structure

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `start.bat`
- Create: `app/__init__.py`
- Create: `app/handlers/__init__.py` (empty, filled in Task 5)
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `data/.gitkeep`
- Create: `logs/.gitkeep`

- [ ] **Step 1: Create Python venv**

Run from `E:\Development\nemotron`:
```bash
python -m venv venv
```
Expected: `venv\Scripts\python.exe` exists.

- [ ] **Step 2: Write requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.34.0
fastmcp>=2.3.0
apscheduler>=3.10.0
pydantic>=2.0
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Install dependencies**

```bash
venv/Scripts/pip install -r requirements.txt
```
Expected: All packages install without error.

- [ ] **Step 4: Write .gitignore**

```
venv/
data/jobs.db
logs/*.log
models/*.gguf
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 5: Write start.bat**

```bat
@echo off
cd /d E:\Development\nemotron
call venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8082 --reload
```

- [ ] **Step 6: Create package markers and conftest**

Create `app/__init__.py` (empty).
Create `app/handlers/__init__.py` (empty for now — filled in Task 5).
Create `tests/__init__.py` (empty).

Create `tests/conftest.py`:
```python
import os
import pytest
import sqlite3
from fastapi.testclient import TestClient

# Point to in-memory DB for tests
os.environ["NEMOTRON_DB_PATH"] = ":memory:"
os.environ["NEMOTRON_SKIP_LLAMA_HEALTH"] = "1"

from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def db():
    from app.queue import init_db
    conn = init_db(":memory:")
    yield conn
    conn.close()
```

- [ ] **Step 7: Create data and logs placeholder files**

```bash
mkdir -p data logs
touch data/.gitkeep logs/.gitkeep
```

- [ ] **Step 8: Commit scaffold**

```bash
git add requirements.txt .gitignore start.bat app/__init__.py app/handlers/__init__.py tests/__init__.py tests/conftest.py data/.gitkeep logs/.gitkeep
git commit -m "feat: scaffold nemotron job runner project"
```

---

## Task 2: SQLite schema + CRUD + Worker (queue.py)

**Files:**
- Create: `app/queue.py`
- Create: `tests/test_queue.py`

- [ ] **Step 1: Write failing test for DB init**

Create `tests/test_queue.py`:
```python
import sqlite3
import pytest
from app.queue import init_db, create_job, get_job, list_jobs, update_job

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

def test_worker_runs_handler(db):
    import threading, time
    results = []

    class FakeHandler:
        job_type = "fake"
        def run(self, job_id, payload):
            results.append(payload)
            return {"done": True}

    from app.queue import Worker
    worker = Worker(db, {"fake": FakeHandler()})
    worker.start()
    job_id = create_job(db, "fake", {"x": 1})
    time.sleep(0.5)  # let worker poll
    worker.stop()

    job = get_job(db, job_id)
    assert job["status"] == "completed"
    assert results == [{"x": 1}]

def test_worker_marks_failed_on_handler_error(db):
    import time

    class FailHandler:
        job_type = "fail"
        def run(self, job_id, payload):
            raise ValueError("deliberate failure")

    from app.queue import Worker
    worker = Worker(db, {"fail": FailHandler()})
    worker.start()
    job_id = create_job(db, "fail", {})
    time.sleep(0.5)
    worker.stop()

    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "deliberate failure" in job["error"]

def test_worker_skips_unknown_handler(db):
    import time
    from app.queue import Worker
    worker = Worker(db, {})
    worker.start()
    job_id = create_job(db, "ghost", {})
    time.sleep(0.5)
    worker.stop()
    job = get_job(db, job_id)
    assert job["status"] == "failed"
    assert "No handler" in job["error"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/Scripts/pytest tests/test_queue.py -v
```
Expected: ImportError or AttributeError — `app.queue` doesn't exist yet.

- [ ] **Step 3: Write queue.py**

Create `app/queue.py`:
```python
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
) -> list[dict]:
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
    """Returns False if job not found or update not allowed."""
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return False
    current_status = row["status"]
    # Can only edit pending jobs (except cancel which allows pending/running)
    if status == "cancelled" and current_status not in ("pending", "running"):
        return False
    if status not in (None, "cancelled") and current_status != "pending":
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
    conn.execute("DELETE FROM job_results WHERE job_id=?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()
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
        while not self._stop.wait(2):
            try:
                self._process_next()
            except Exception:
                logger.exception("Worker loop error")

    def _process_next(self) -> None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE status='pending' "
            "AND (scheduled_at IS NULL OR scheduled_at <= datetime('now')) "
            "ORDER BY priority ASC, created_at ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return

        job_id = row["id"]
        job_type = row["type"]
        payload = json.loads(row["payload"])

        self._conn.execute(
            "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=?",
            (_now(), _now(), job_id),
        )
        self._conn.commit()
        logger.info("Running job %s type=%s", job_id, job_type)

        handler = self._registry.get(job_type)
        if handler is None:
            self._conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?",
                (f"No handler for job type: {job_type}", _now(), _now(), job_id),
            )
            self._conn.commit()
            return

        try:
            result = handler.run(job_id, payload)
            result_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO job_results (id, job_id, result, created_at) VALUES (?, ?, ?, ?)",
                (result_id, job_id, json.dumps(result), _now()),
            )
            self._conn.execute(
                "UPDATE jobs SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), job_id),
            )
            self._conn.commit()
            logger.info("Job %s completed", job_id)
        except Exception as exc:
            self._conn.execute(
                "UPDATE jobs SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?",
                (str(exc), _now(), _now(), job_id),
            )
            self._conn.commit()
            logger.exception("Job %s failed", job_id)
```

- [ ] **Step 4: Run tests**

```bash
venv/Scripts/pytest tests/test_queue.py -v
```
Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/queue.py tests/test_queue.py
git commit -m "feat: SQLite schema, job CRUD, and background worker"
```

---

## Task 3: Pydantic models (models.py)

**Files:**
- Create: `app/models.py`

No dedicated test file — models are validated indirectly via API tests in Task 7.

- [ ] **Step 1: Write models.py**

```python
from pydantic import BaseModel, field_validator
from typing import Optional


class JobSubmit(BaseModel):
    type: str
    payload: dict = {}
    priority: int = 50
    trigger: str = "manual"
    scheduled_at: Optional[str] = None


class JobPatch(BaseModel):
    priority: Optional[int] = None
    payload: Optional[dict] = None
    scheduled_at: Optional[str] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_cancelled(cls, v):
        if v is not None and v != "cancelled":
            raise ValueError("status can only be set to 'cancelled' via PATCH")
        return v


class HookRegister(BaseModel):
    job_type: str
    payload: dict = {}
```

- [ ] **Step 2: Commit**

```bash
git add app/models.py
git commit -m "feat: Pydantic request/response models"
```

---

## Task 4: Health endpoint (health.py)

**Files:**
- Create: `app/health.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_health.py`:
```python
import pytest
from unittest.mock import patch


def test_health_returns_200(client):
    with patch("app.health.get_vram_stats", return_value={"used_mb": 7900, "free_mb": 300, "total_mb": 12288, "gpu": "RTX 4070 Ti"}):
        r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "vram" in data
    assert data["vram"]["used_mb"] == 7900


def test_get_vram_stats_parses_nvidia_smi():
    from unittest.mock import patch, MagicMock
    from app.health import get_vram_stats
    mock_result = MagicMock()
    mock_result.stdout = "7900, 300, 12288\n"
    with patch("subprocess.run", return_value=mock_result):
        stats = get_vram_stats()
    assert stats["used_mb"] == 7900
    assert stats["free_mb"] == 300
    assert stats["total_mb"] == 12288
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/Scripts/pytest tests/test_health.py -v
```
Expected: ImportError — `app.health` doesn't exist.

- [ ] **Step 3: Write health.py**

```python
import subprocess
import logging
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)


def get_vram_stats() -> dict:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
                "--id=1",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        used, free, total = [int(x.strip()) for x in result.stdout.strip().split(",")]
        return {"used_mb": used, "free_mb": free, "total_mb": total, "gpu": "RTX 4070 Ti"}
    except Exception as exc:
        logger.warning("nvidia-smi failed: %s", exc)
        return {"error": str(exc)}


@router.get("/health")
def health():
    return {
        "status": "ok",
        "vram": get_vram_stats(),
    }
```

- [ ] **Step 4: Run tests**

```bash
venv/Scripts/pytest tests/test_health.py -v
```
Expected: Both tests pass.

Note: `/health` test requires the full app (TestClient) — it will pass once `main.py` is wired in Task 11. The `get_vram_stats` unit test passes now.

- [ ] **Step 5: Commit**

```bash
git add app/health.py tests/test_health.py
git commit -m "feat: /health endpoint with RTX 4070 Ti VRAM query"
```

---

## Task 5: Handler registry + auto-discovery (handlers/__init__.py)

**Files:**
- Modify: `app/handlers/__init__.py`
- Create: `tests/test_handlers.py` (partial — full test after Task 6)

- [ ] **Step 1: Write failing tests**

Create `tests/test_handlers.py`:
```python
from app.handlers import JobHandler, load_handlers


def test_job_handler_base_requires_job_type():
    class NoType(JobHandler):
        def run(self, job_id, payload):
            return {}
    # Should raise at class definition time (or at load time)
    # We just verify the interface exists
    assert hasattr(JobHandler, "job_type")
    assert hasattr(JobHandler, "run")


def test_load_handlers_returns_dict():
    handlers = load_handlers()
    assert isinstance(handlers, dict)


def test_load_handlers_includes_smart_connections():
    handlers = load_handlers()
    assert "smart_connections_reembed" in handlers


def test_handler_registry_keyed_by_job_type():
    handlers = load_handlers()
    for key, handler in handlers.items():
        assert handler.job_type == key
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/Scripts/pytest tests/test_handlers.py -v
```
Expected: ImportError.

- [ ] **Step 3: Write handlers/__init__.py**

```python
import importlib
import inspect
import pkgutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class JobHandler:
    """Base class for all job handlers. Subclasses must set job_type and implement run()."""
    job_type: str = ""

    def run(self, job_id: str, payload: dict) -> dict:
        raise NotImplementedError


def load_handlers() -> dict[str, JobHandler]:
    """
    Scan all modules in app/handlers/ for JobHandler subclasses.
    Returns a dict mapping job_type -> handler instance.
    Skips the base JobHandler class itself.
    """
    handlers: dict[str, JobHandler] = {}
    package_dir = Path(__file__).parent
    package_name = __name__  # "app.handlers"

    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        full_name = f"{package_name}.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception:
            logger.exception("Failed to import handler module %s", full_name)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, JobHandler)
                and obj is not JobHandler
                and obj.job_type
            ):
                instance = obj()
                handlers[instance.job_type] = instance
                logger.debug("Registered handler: %s", instance.job_type)

    return handlers
```

- [ ] **Step 4: Run tests** (test_load_handlers_includes_smart_connections will fail until Task 6)

```bash
venv/Scripts/pytest tests/test_handlers.py::test_load_handlers_returns_dict tests/test_handlers.py::test_job_handler_base_requires_job_type -v
```
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/__init__.py tests/test_handlers.py
git commit -m "feat: JobHandler base class + auto-discovery registry"
```

---

## Task 6: SmartConnectionsReembed handler

**Files:**
- Create: `app/handlers/smart_connections.py`

- [ ] **Step 1: Write handler**

Create `app/handlers/smart_connections.py`:
```python
import logging
from pathlib import Path
from app.handlers import JobHandler

logger = logging.getLogger(__name__)

# Sentinel file watched by Smart Connections plugin (candidate mechanism)
VAULT_PATH = Path(r"E:\ObsidianVault\AdminAssistantMemory")
SENTINEL_FILE = VAULT_PATH / ".smart-env" / "reembed_trigger"


class SmartConnectionsReembed(JobHandler):
    job_type = "smart_connections_reembed"

    def run(self, job_id: str, payload: dict) -> dict:
        """
        Trigger Smart Connections re-embedding by writing a sentinel file.
        The Smart Connections plugin must be configured to watch for this file.
        If the sentinel approach is not supported, this is the extension point
        to swap in an Obsidian URI call or alternative mechanism.
        """
        logger.info("Triggering Smart Connections re-embed (job %s)", job_id)
        SENTINEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        SENTINEL_FILE.touch()
        logger.info("Wrote sentinel file: %s", SENTINEL_FILE)
        return {"sentinel_file": str(SENTINEL_FILE), "triggered": True}
```

- [ ] **Step 2: Run handler tests**

```bash
venv/Scripts/pytest tests/test_handlers.py -v
```
Expected: All 4 tests pass.

- [ ] **Step 3: Verify handler loads**

```bash
venv/Scripts/python -c "from app.handlers import load_handlers; h = load_handlers(); print(list(h.keys()))"
```
Expected: `['smart_connections_reembed']`

- [ ] **Step 4: Commit**

```bash
git add app/handlers/smart_connections.py
git commit -m "feat: SmartConnectionsReembed handler (sentinel file trigger)"
```

---

## Task 7: REST API — jobs router (jobs_router.py)

**Files:**
- Create: `app/jobs_router.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_api.py`:
```python
import pytest


def test_submit_job(client):
    r = client.post("/jobs", json={"type": "test_job", "payload": {"x": 1}})
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["status"] == "pending"


def test_list_jobs_empty(client):
    r = client.get("/jobs")
    assert r.status_code == 200
    assert r.json() == []


def test_get_job_not_found(client):
    r = client.get("/jobs/nonexistent-id")
    assert r.status_code == 404


def test_get_job_after_submit(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    r2 = client.get(f"/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job_id


def test_patch_job_priority(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    r2 = client.patch(f"/jobs/{job_id}", json={"priority": 10})
    assert r2.status_code == 200
    r3 = client.get(f"/jobs/{job_id}")
    assert r3.json()["priority"] == 10


def test_patch_job_cancel(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    r2 = client.patch(f"/jobs/{job_id}", json={"status": "cancelled"})
    assert r2.status_code == 200
    r3 = client.get(f"/jobs/{job_id}")
    assert r3.json()["status"] == "cancelled"


def test_delete_completed_job(client):
    # Submit and manually force to completed via DB
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    # Force status via direct DB update (test only)
    from app.main import app
    conn = app.state.conn
    conn.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    conn.commit()
    r2 = client.delete(f"/jobs/{job_id}")
    assert r2.status_code == 204


def test_delete_pending_job_rejected(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    r2 = client.delete(f"/jobs/{job_id}")
    assert r2.status_code == 409


def test_list_jobs_filter_status(client):
    client.post("/jobs", json={"type": "t1", "payload": {}})
    client.post("/jobs", json={"type": "t2", "payload": {}})
    r = client.get("/jobs?status=pending")
    assert r.status_code == 200
    jobs = r.json()
    assert all(j["status"] == "pending" for j in jobs)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/Scripts/pytest tests/test_api.py -v
```
Expected: Import errors or 404s because router not registered yet.

- [ ] **Step 3: Write jobs_router.py**

Create `app/jobs_router.py`:
```python
from fastapi import APIRouter, HTTPException, Request
from app.models import JobSubmit, JobPatch
from app import queue as q

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", status_code=201)
def submit_job(body: JobSubmit, request: Request):
    conn = request.app.state.conn
    job_id = q.create_job(
        conn,
        body.type,
        body.payload,
        priority=body.priority,
        trigger=body.trigger,
        scheduled_at=body.scheduled_at,
    )
    return q.get_job(conn, job_id)


@router.get("")
def list_jobs(
    request: Request,
    status: str = None,
    type: str = None,
    trigger: str = None,
    limit: int = 50,
    offset: int = 0,
):
    conn = request.app.state.conn
    return q.list_jobs(conn, status=status, job_type=type, trigger=trigger, limit=limit, offset=offset)


@router.get("/{job_id}")
def get_job(job_id: str, request: Request):
    conn = request.app.state.conn
    job = q.get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/{job_id}")
def patch_job(job_id: str, body: JobPatch, request: Request):
    conn = request.app.state.conn
    updated = q.update_job(
        conn,
        job_id,
        priority=body.priority,
        payload=body.payload,
        scheduled_at=body.scheduled_at,
        status=body.status,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found or cannot be modified")
    return q.get_job(conn, job_id)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, request: Request):
    conn = request.app.state.conn
    deleted = q.delete_job(conn, job_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="Job not found or not in a terminal state")
```

- [ ] **Step 4: Wire into main.py stub**

This task creates the router. Full wiring happens in Task 11. For now, create a minimal `app/main.py` to make the tests pass:
```python
# app/main.py — stub, replaced fully in Task 11
import os
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.queue import init_db

DB_PATH = os.environ.get("NEMOTRON_DB_PATH", "data/jobs.db")
SKIP_HEALTH = os.environ.get("NEMOTRON_SKIP_LLAMA_HEALTH", "") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db(DB_PATH)
    app.state.conn = conn
    app.state.handlers = {}
    yield
    conn.close()


app = FastAPI(lifespan=lifespan)

from app.health import router as health_router
from app.jobs_router import router as jobs_router

app.include_router(health_router)
app.include_router(jobs_router)
```

- [ ] **Step 5: Run tests**

```bash
venv/Scripts/pytest tests/test_api.py -v
```
Expected: All 9 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/jobs_router.py app/main.py tests/test_api.py
git commit -m "feat: REST API jobs endpoints (CRUD + cancel + delete)"
```

---

## Task 8: REST API — hooks router (hooks_router.py)

**Files:**
- Create: `app/hooks_router.py`
- Extend: `tests/test_api.py`

- [ ] **Step 1: Add hook tests to test_api.py**

Append to `tests/test_api.py`:
```python
def test_register_and_list_hook(client):
    r = client.put("/hooks/vault_updated", json={"job_type": "smart_connections_reembed", "payload": {}})
    assert r.status_code == 200
    r2 = client.get("/hooks")
    hooks = r2.json()
    names = [h["name"] for h in hooks]
    assert "vault_updated" in names


def test_trigger_hook_submits_job(client):
    client.put("/hooks/vault_updated", json={"job_type": "smart_connections_reembed", "payload": {}})
    r = client.post("/hooks/vault_updated")
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["type"] == "smart_connections_reembed"


def test_trigger_unknown_hook_returns_404(client):
    r = client.post("/hooks/nonexistent_hook")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify new ones fail**

```bash
venv/Scripts/pytest tests/test_api.py::test_register_and_list_hook -v
```
Expected: 404 — route not registered.

- [ ] **Step 3: Write hooks_router.py**

Create `app/hooks_router.py`:
```python
import json
import logging
from fastapi import APIRouter, HTTPException, Request
from app.models import HookRegister
from app import queue as q

router = APIRouter(prefix="/hooks", tags=["hooks"])
logger = logging.getLogger(__name__)

HOOKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hooks (
    name        TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
"""


@router.get("")
def list_hooks(request: Request):
    conn = request.app.state.conn
    rows = conn.execute("SELECT name, job_type, payload, created_at FROM hooks").fetchall()
    return [{"name": r["name"], "job_type": r["job_type"],
             "payload": json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows]


@router.put("/{hook_name}")
def register_hook(hook_name: str, body: HookRegister, request: Request):
    conn = request.app.state.conn
    from app.queue import _now
    conn.execute(
        "INSERT INTO hooks (name, job_type, payload, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET job_type=excluded.job_type, payload=excluded.payload",
        (hook_name, body.job_type, json.dumps(body.payload), _now()),
    )
    conn.commit()
    return {"name": hook_name, "job_type": body.job_type, "payload": body.payload}


@router.post("/{hook_name}", status_code=201)
def trigger_hook(hook_name: str, request: Request):
    conn = request.app.state.conn
    row = conn.execute("SELECT * FROM hooks WHERE name=?", (hook_name,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Hook '{hook_name}' not registered")
    hook_payload = json.loads(row["payload"])
    job_id = q.create_job(conn, row["job_type"], hook_payload, trigger="hook")
    return q.get_job(conn, job_id)
```

- [ ] **Step 4: Add router to main.py stub**

Edit `app/main.py` — add after jobs_router import:
```python
from app.hooks_router import router as hooks_router
app.include_router(hooks_router)
```

- [ ] **Step 5: Run tests**

```bash
venv/Scripts/pytest tests/test_api.py -v
```
Expected: All 12 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/hooks_router.py tests/test_api.py app/main.py
git commit -m "feat: hooks REST endpoints (register, list, trigger)"
```

---

## Task 9: MCP server (mcp_server.py)

**Files:**
- Create: `app/mcp_server.py`

MCP tools are tested manually (Claude Code MCP client) rather than via pytest, so no automated test file for this task. The tools call the same queue functions as the REST API.

- [ ] **Step 1: Write mcp_server.py**

Create `app/mcp_server.py`:
```python
"""
MCP server exposing job queue tools to agents.
Mounted on the FastAPI app at /mcp — SSE endpoint: /mcp/sse
Claude Code config: { "type": "sse", "url": "http://localhost:8082/mcp/sse" }
"""
from fastmcp import FastMCP

mcp = FastMCP("NemotronJobRunner")

# These functions are set at app startup so mcp tools can access the live DB and registry.
# Populated in main.py lifespan via set_mcp_context().
_conn = None
_handlers: dict = {}


def set_mcp_context(conn, handlers: dict) -> None:
    global _conn, _handlers
    _conn = conn
    _handlers = handlers


@mcp.tool()
def queue_job(job_type: str, payload: dict, priority: int = 50) -> dict:
    """Submit a job to the queue. Returns the created job record."""
    from app.queue import create_job, get_job
    job_id = create_job(_conn, job_type, payload, priority=priority, trigger="agent")
    return get_job(_conn, job_id)


@mcp.tool()
def list_jobs(status: str = None, job_type: str = None, limit: int = 20) -> list:
    """List jobs in the queue. Filter by status (pending/running/completed/failed/cancelled) or job_type."""
    from app.queue import list_jobs as _list_jobs
    return _list_jobs(_conn, status=status, job_type=job_type, limit=limit)


@mcp.tool()
def get_job(job_id: str) -> dict:
    """Get full details and result for a job by ID."""
    from app.queue import get_job as _get_job
    job = _get_job(_conn, job_id)
    if job is None:
        return {"error": f"Job {job_id} not found"}
    return job


@mcp.tool()
def update_job(job_id: str, priority: int = None, scheduled_at: str = None) -> dict:
    """Edit a pending job's priority or scheduled_at. Returns updated job."""
    from app.queue import update_job as _update_job, get_job as _get_job
    _update_job(_conn, job_id, priority=priority, scheduled_at=scheduled_at)
    return _get_job(_conn, job_id)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """Cancel a pending job."""
    from app.queue import update_job as _update_job, get_job as _get_job
    updated = _update_job(_conn, job_id, status="cancelled")
    if not updated:
        return {"error": f"Job {job_id} not found or cannot be cancelled"}
    return _get_job(_conn, job_id)


@mcp.tool()
def list_job_types() -> list:
    """List all registered handler job types."""
    return [
        {"job_type": key, "description": type(handler).__doc__ or ""}
        for key, handler in _handlers.items()
    ]
```

- [ ] **Step 2: Commit**

```bash
git add app/mcp_server.py
git commit -m "feat: MCP server with queue_job, list_jobs, get_job, update_job, cancel_job, list_job_types"
```

---

## Task 10: APScheduler (scheduler.py)

**Files:**
- Create: `app/scheduler.py`

- [ ] **Step 1: Write scheduler.py**

```python
"""
APScheduler integration. Cron/interval jobs submit entries to the SQLite queue
rather than running directly — the worker thread handles execution.
Add cron jobs here by calling add_cron_job() or by modifying SCHEDULED_JOBS.
"""
import logging
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# Cron job definitions. Each entry: (job_type, payload, cron_kwargs)
# Example: trigger smart_connections_reembed every day at 02:00
SCHEDULED_JOBS: list[tuple[str, dict, dict]] = [
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
```

- [ ] **Step 2: Commit**

```bash
git add app/scheduler.py
git commit -m "feat: APScheduler setup with cron job enqueue support"
```

---

## Task 11: App factory — main.py (final wiring)

**Files:**
- Modify: `app/main.py` (replace stub with full implementation)

- [ ] **Step 1: Replace main.py with full implementation**

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.queue import init_db, Worker
from app.handlers import load_handlers
from app.scheduler import setup_scheduler
from app.mcp_server import mcp, set_mcp_context

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DB_PATH = os.environ.get("NEMOTRON_DB_PATH", "data/jobs.db")
SKIP_HEALTH = os.environ.get("NEMOTRON_SKIP_LLAMA_HEALTH", "") == "1"
LLAMA_HEALTH_URL = "http://localhost:8081/health"
HEALTH_TIMEOUT_S = 120
HEALTH_INTERVAL_S = 5


async def _wait_for_llama() -> None:
    if SKIP_HEALTH:
        logger.info("Skipping llama.cpp health check (NEMOTRON_SKIP_LLAMA_HEALTH=1)")
        return
    logger.info("Waiting for llama.cpp server at %s ...", LLAMA_HEALTH_URL)
    attempts = HEALTH_TIMEOUT_S // HEALTH_INTERVAL_S
    async with httpx.AsyncClient() as client:
        for i in range(attempts):
            try:
                r = await client.get(LLAMA_HEALTH_URL, timeout=5)
                if r.status_code == 200:
                    logger.info("llama.cpp server healthy after %ds", i * HEALTH_INTERVAL_S)
                    return
            except Exception:
                pass
            await asyncio.sleep(HEALTH_INTERVAL_S)
    logger.error("llama.cpp server did not become healthy within %ds — exiting", HEALTH_TIMEOUT_S)
    raise SystemExit(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _wait_for_llama()

    conn = init_db(DB_PATH)
    handlers = load_handlers()
    set_mcp_context(conn, handlers)

    worker = Worker(conn, handlers)
    worker.start()
    logger.info("Worker started. Registered handlers: %s", list(handlers.keys()))

    scheduler = setup_scheduler(conn, handlers)
    scheduler.start()

    app.state.conn = conn
    app.state.handlers = handlers
    app.state.worker = worker
    app.state.scheduler = scheduler

    yield

    logger.info("Shutting down...")
    worker.stop()
    scheduler.shutdown(wait=False)
    conn.close()


app = FastAPI(title="NemotronJobRunner", version="1.0.0", lifespan=lifespan)

from app.health import router as health_router
from app.jobs_router import router as jobs_router
from app.hooks_router import router as hooks_router

app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(hooks_router)
app.mount("/mcp", mcp.sse_app())
```

- [ ] **Step 2: Run full test suite**

```bash
venv/Scripts/pytest tests/ -v
```
Expected: All tests pass. (The `/health` test in test_health.py that uses `client` will now also pass.)

- [ ] **Step 3: Smoke test — start the app manually**

Ensure NemotronServer is either running or you have `NEMOTRON_SKIP_LLAMA_HEALTH=1` set.

```bash
set NEMOTRON_SKIP_LLAMA_HEALTH=1 && venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8082
```

In another terminal:
```bash
curl http://localhost:8082/health
curl -X POST http://localhost:8082/jobs -H "Content-Type: application/json" -d "{\"type\":\"smart_connections_reembed\",\"payload\":{}}"
curl http://localhost:8082/jobs
```

Expected: `{"status":"ok","vram":{...}}`, job created, job listed.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: full app factory with lifespan, worker, scheduler, MCP mount"
```

---

## Task 12: Download llama-server.exe binary

**Run this task in parallel with code tasks — it takes a few minutes.**

- [ ] **Step 1: Check CUDA version**

```bash
nvidia-smi
```
Look for "CUDA Version: 12.x" in the top-right. Confirm it's CUDA 12.

- [ ] **Step 2: Download llama-server.exe**

Go to: https://github.com/ggml-org/llama.cpp/releases/latest

Download the asset matching: `llama-b*-bin-win-cuda-cu12.x-x64.zip`

Extract `llama-server.exe` from the zip into `E:\Development\nemotron\`.

Verify:
```bash
E:\Development\nemotron\llama-server.exe --version
```
Expected: Prints build info without errors.

- [ ] **Step 3: Commit**

```bash
# llama-server.exe is gitignored if you add *.exe to .gitignore
# If you want to track the binary version, add a VERSION file instead:
echo "llama.cpp build bXXXX CUDA 12" > llama-server-version.txt
git add llama-server-version.txt
git commit -m "chore: record llama-server.exe version"
```

---

## Task 13: Download Nemotron GGUF model

**Start immediately — 5.5 GB download, runs in parallel with everything.**

- [ ] **Step 1: Create models directory**

```bash
mkdir E:\Development\nemotron\models
```

- [ ] **Step 2: Download Q5_K_M GGUF**

Option A — huggingface-cli (recommended):
```bash
pip install huggingface-hub
huggingface-cli download bartowski/Llama-3.1-Nemotron-8B-Instruct-GGUF \
  Llama-3.1-Nemotron-8B-Instruct-Q5_K_M.gguf \
  --local-dir E:\Development\nemotron\models \
  --local-dir-use-symlinks False
```

Option B — direct URL (paste in browser or PowerShell):
```powershell
Invoke-WebRequest -Uri "https://huggingface.co/bartowski/Llama-3.1-Nemotron-8B-Instruct-GGUF/resolve/main/Llama-3.1-Nemotron-8B-Instruct-Q5_K_M.gguf" -OutFile "E:\Development\nemotron\models\Llama-3.1-Nemotron-8B-Instruct-Q5_K_M.gguf"
```

- [ ] **Step 3: Rename to match NSSM config**

```bash
mv "E:\Development\nemotron\models\Llama-3.1-Nemotron-8B-Instruct-Q5_K_M.gguf" \
   "E:\Development\nemotron\models\nemotron-8b-q5_k_m.gguf"
```

- [ ] **Step 4: Verify model loads**

```bash
E:\Development\nemotron\llama-server.exe \
  -m models\nemotron-8b-q5_k_m.gguf \
  --main-gpu 1 -ngl 999 -c 16384 \
  --port 8081 --host 127.0.0.1 \
  --log-disable
```

Wait ~30s for "server is listening" message. Then in another terminal:
```bash
curl http://localhost:8081/health
curl http://localhost:8081/v1/models
```
Expected: `{"status":"ok"}` and model list. Press Ctrl+C to stop once verified.

Also verify VRAM — in another terminal while model is loaded:
```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits --id=1
```
Expected: ~7900 MB used, 12288 MB total.

---

## Task 14: NSSM service registration

**Depends on Tasks 12 + 13 being complete. Run as Administrator.**

- [ ] **Step 1: Install NemotronServer service**

Open an elevated (Administrator) PowerShell, then:

```powershell
nssm install NemotronServer "E:\Development\nemotron\llama-server.exe"
nssm set NemotronServer AppParameters "-m models\nemotron-8b-q5_k_m.gguf --main-gpu 1 -ngl 999 -c 16384 --port 8081 --host 127.0.0.1 --log-disable"
nssm set NemotronServer AppDirectory "E:\Development\nemotron"
nssm set NemotronServer Start SERVICE_DELAYED_AUTO_START
nssm set NemotronServer AppStdout "E:\Development\nemotron\logs\nemotron-server.log"
nssm set NemotronServer AppStderr "E:\Development\nemotron\logs\nemotron-server-err.log"
nssm set NemotronServer AppStdoutCreationDisposition 4
nssm set NemotronServer AppStderrCreationDisposition 4
```

- [ ] **Step 2: Install NemotronJobRunner service**

```powershell
nssm install NemotronJobRunner "E:\Development\nemotron\venv\Scripts\python.exe"
nssm set NemotronJobRunner AppParameters "-m uvicorn app.main:app --host 127.0.0.1 --port 8082"
nssm set NemotronJobRunner AppDirectory "E:\Development\nemotron"
nssm set NemotronJobRunner Start SERVICE_DELAYED_AUTO_START
nssm set NemotronJobRunner DependOnService NemotronServer
nssm set NemotronJobRunner AppStdout "E:\Development\nemotron\logs\runner.log"
nssm set NemotronJobRunner AppStderr "E:\Development\nemotron\logs\runner-err.log"
nssm set NemotronJobRunner AppStdoutCreationDisposition 4
nssm set NemotronJobRunner AppStderrCreationDisposition 4
```

- [ ] **Step 3: Start services and verify**

```powershell
nssm start NemotronServer
Start-Sleep 60  # wait for model load
nssm start NemotronJobRunner
Start-Sleep 10
```

Check status:
```powershell
nssm status NemotronServer   # Expected: SERVICE_RUNNING
nssm status NemotronJobRunner  # Expected: SERVICE_RUNNING
```

Check endpoints:
```bash
curl http://localhost:8081/health
curl http://localhost:8082/health
```

Check logs if either fails:
```bash
cat E:\Development\nemotron\logs\nemotron-server-err.log
cat E:\Development\nemotron\logs\runner-err.log
```

- [ ] **Step 4: Test restart behavior**

```powershell
nssm restart NemotronJobRunner
Start-Sleep 15
nssm status NemotronJobRunner   # Expected: SERVICE_RUNNING
curl http://localhost:8082/health
```

---

## Task 15: Update Smart Connections smart_env.json

**Independent — can run any time after the spec is finalized.**

- [ ] **Step 1: Read current smart_env.json**

```bash
cat "E:\ObsidianVault\AdminAssistantMemory\.smart-env\smart_env.json"
```

Find the `smart_chat_threads` section, specifically the `chat_model` block.

- [ ] **Step 2: Update chat_model adapter**

In `E:\ObsidianVault\AdminAssistantMemory\.smart-env\smart_env.json`, change the `smart_chat_threads.chat_model` section to:

```json
"smart_chat_threads": {
  "chat_model": {
    "adapter": "openai",
    "openai": {
      "base_url": "http://localhost:8081/v1",
      "model_key": "nemotron",
      "api_key": "local"
    }
  }
}
```

The key change: `"adapter"` from `"ollama"` (or whatever it currently is) → `"openai"`.

- [ ] **Step 3: Test in Obsidian**

1. Ensure NemotronServer is running (`curl http://localhost:8081/health`)
2. Open Obsidian → Smart Connections panel → open a chat thread
3. Send a test message: "What is 2 + 2?"
4. Expected: Response from Nemotron model appears in the Smart Chat panel

If it doesn't work, check: Smart Connections plugin settings → Chat Model → verify it shows the openai adapter pointing to localhost:8081.

---

## Task 16: Register Claude Code MCP client

**After Task 11 and Task 14 — connects Claude Code to the job queue.**

- [ ] **Step 1: Add MCP server to Claude Code config**

In your Claude Code MCP configuration (typically `%APPDATA%\Claude\claude_desktop_config.json` or in `.claude/settings.json`), add:

```json
{
  "mcpServers": {
    "nemotron-jobs": {
      "type": "sse",
      "url": "http://localhost:8082/mcp/sse"
    }
  }
}
```

- [ ] **Step 2: Restart Claude Code and verify tools**

After restart, in a Claude Code session:
```
/mcp
```
Expected: `nemotron-jobs` appears with tools: `queue_job`, `list_jobs`, `get_job`, `update_job`, `cancel_job`, `list_job_types`.

- [ ] **Step 3: End-to-end MCP test**

Ask Claude Code to:
> "Use the nemotron-jobs MCP to queue a smart_connections_reembed job and then check its status."

Expected: Job created, status starts as `pending`, worker picks it up, sentinel file written to `.smart-env/reembed_trigger`.

---

## Self-Review Against Spec

Spec requirements vs tasks:

| Requirement | Task |
|---|---|
| NemotronServer NSSM service, port 8081 | Task 12, 13, 14 |
| VRAM ≤ 8.25 GB on 4070 Ti | Task 13 (verify during model test) |
| NemotronJobRunner NSSM service, waits for health check | Task 11 (lifespan), Task 14 |
| `GET /health` with VRAM stats | Task 4 |
| `POST /jobs` | Task 7 |
| Worker picks up pending jobs in priority order | Task 2 |
| `PATCH /jobs/{id}` | Task 7 |
| APScheduler cron triggers | Task 10 |
| `POST /hooks/{hook_name}` | Task 8 |
| MCP tools: queue_job, list_jobs, get_job, update_job, cancel_job, list_job_types | Task 9 |
| Smart Chat via Nemotron | Task 15 |
| `smart_connections_reembed` handler | Task 6 |
| New handler = drop file in handlers/ + restart | Task 5 (auto-discovery) |
| `--main-gpu 1` — GPU 0 untouched | Task 13 (llama-server args) |

All requirements covered. No gaps.
