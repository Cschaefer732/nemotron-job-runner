"""
MCP server exposing job queue tools to agents.
Mounted on the FastAPI app at /mcp — SSE endpoint: /mcp/sse

Claude Code MCP config:
  { "type": "sse", "url": "http://localhost:8082/mcp/sse" }
  (fastmcp 3.x: mount("/mcp", mcp.http_app(transport="sse")) → /mcp/sse)
"""
from fastmcp import FastMCP

mcp = FastMCP("NemotronJobRunner")

# Populated at app startup via set_mcp_context() in main.py lifespan
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
    """List jobs. Filter by status (pending/running/completed/failed/cancelled) or job_type."""
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
