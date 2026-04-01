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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

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
    attempts = HEALTH_TIMEOUT_S // HEALTH_INTERVAL_S + 1  # +1 so final attempt is at exactly TIMEOUT_S
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
    logger.error(
        "llama.cpp server did not become healthy within %ds — exiting", HEALTH_TIMEOUT_S
    )
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

from app.health import router as health_router          # noqa: E402
from app.jobs_router import router as jobs_router        # noqa: E402
from app.hooks_router import router as hooks_router      # noqa: E402

app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(hooks_router)
app.mount("/mcp", mcp.http_app(transport="sse"))
