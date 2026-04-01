"""
MCP server exposing job queue tools to agents.
Mounted on the FastAPI app at /mcp — SSE endpoint: /mcp/sse

Claude Code MCP config:
  { "type": "sse", "url": "http://localhost:8082/mcp/sse" }
  (fastmcp 3.x: mount("/mcp", mcp.http_app(transport="sse")) → /mcp/sse)
"""
from fastmcp import FastMCP

mcp = FastMCP("NemotronJobRunner")

# Populated at app startup via set_mcp_context() in main.py lifespan.
# Both assigned atomically to avoid a partial-init window.
_conn = None
_handlers: dict = {}


def set_mcp_context(conn, handlers: dict) -> None:
    global _conn, _handlers
    _conn, _handlers = conn, handlers  # atomic tuple assignment under CPython GIL


def _require_conn():
    if _conn is None:
        raise RuntimeError("MCP context not initialized — server is still starting up")


@mcp.tool()
def queue_job(job_type: str, payload: dict, priority: int = 50) -> dict:
    """Submit a job to the queue. Returns the created job record.

    job_type must be one of the registered handler types (see list_job_types).
    priority: 0 = highest urgency, 100 = lowest. Default 50.
    """
    _require_conn()
    if job_type not in _handlers:
        return {"error": f"Unknown job type '{job_type}'. Known types: {list(_handlers.keys())}"}
    from app.queue import create_job, get_job
    job_id = create_job(_conn, job_type, payload, priority=priority, trigger="agent")
    return get_job(_conn, job_id)


@mcp.tool()
def list_jobs(
    status: str = None,
    job_type: str = None,
    trigger: str = None,
    limit: int = 20,
) -> list[dict]:
    """List jobs, most urgent first.

    status: pending | running | completed | failed | cancelled
    job_type: filter by handler type name
    trigger: manual | cron | agent | hook
    limit: max rows (default 20)
    Returns list of job objects. Use get_job for result details.
    """
    _require_conn()
    from app.queue import list_jobs as _list_jobs
    return _list_jobs(_conn, status=status, job_type=job_type, trigger=trigger, limit=limit)


@mcp.tool()
def get_job(job_id: str) -> dict:
    """Get full details and result for a job by ID.

    Returns job object including result field if completed,
    or {'error': '...'} if not found.
    """
    _require_conn()
    from app.queue import get_job as _get_job
    job = _get_job(_conn, job_id)
    if job is None:
        return {"error": f"Job {job_id} not found"}
    return job


@mcp.tool()
def update_job(
    job_id: str,
    priority: int = None,
    payload: dict = None,
    scheduled_at: str = None,
) -> dict:
    """Edit a pending job's priority, payload, or scheduled_at. Returns updated job.

    scheduled_at: ISO8601 UTC string, e.g. '2026-04-01T06:00:00+00:00'
    Only pending jobs can have fields edited. Returns {'error': '...'} if not found or not modifiable.
    """
    _require_conn()
    from app.queue import update_job as _update_job, get_job as _get_job
    result = _update_job(_conn, job_id, priority=priority, payload=payload, scheduled_at=scheduled_at)
    if result == "not_found":
        return {"error": f"Job {job_id} not found"}
    if result == "conflict":
        return {"error": f"Job {job_id} cannot be modified in its current state"}
    return _get_job(_conn, job_id)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """Cancel a pending or running job.

    Returns cancelled job record, or {'error': '...'} if not found or already in a terminal state.
    """
    _require_conn()
    from app.queue import update_job as _update_job, get_job as _get_job
    result = _update_job(_conn, job_id, status="cancelled")
    if result == "not_found":
        return {"error": f"Job {job_id} not found"}
    if result == "conflict":
        return {"error": f"Job {job_id} is already in a terminal state and cannot be cancelled"}
    return _get_job(_conn, job_id)


@mcp.tool()
def list_job_types() -> list[dict]:
    """List all registered handler job types."""
    _require_conn()
    return [
        {"job_type": key, "description": type(handler).__doc__ or ""}
        for key, handler in _handlers.items()
    ]
