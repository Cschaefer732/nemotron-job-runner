import os
import pytest

# Point to in-memory DB for tests; skip llama.cpp health check
os.environ["NEMOTRON_DB_PATH"] = ":memory:"
os.environ["NEMOTRON_SKIP_LLAMA_HEALTH"] = "1"

from app.main import app  # noqa: E402 — env vars must be set before import


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    from app.queue import init_db
    conn = init_db(":memory:")
    yield conn
    conn.close()
