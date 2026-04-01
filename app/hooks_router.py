import json
import logging
from fastapi import APIRouter, HTTPException, Request
from app.models import HookRegister
from app import queue as q
from app.queue import _now

router = APIRouter(prefix="/hooks", tags=["hooks"])
logger = logging.getLogger(__name__)


@router.get("")
def list_hooks(request: Request):
    conn = request.app.state.conn
    rows = conn.execute("SELECT name, job_type, payload, created_at FROM hooks").fetchall()
    return [
        {
            "name": r["name"],
            "job_type": r["job_type"],
            "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.put("/{hook_name}")
def register_hook(hook_name: str, body: HookRegister, request: Request):
    conn = request.app.state.conn
    conn.execute(
        "INSERT INTO hooks (name, job_type, payload, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET job_type=excluded.job_type, payload=excluded.payload",
        (hook_name, body.job_type, json.dumps(body.payload), _now()),
    )
    conn.commit()
    return {"name": hook_name, "job_type": body.job_type, "payload": body.payload}


@router.post("/{hook_name}", status_code=201)
async def trigger_hook(hook_name: str, request: Request):
    conn = request.app.state.conn
    row = conn.execute("SELECT * FROM hooks WHERE name=?", (hook_name,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Hook '{hook_name}' not registered")
    hook_payload = json.loads(row["payload"])
    # Merge request body over the stored default payload (body fields take precedence)
    try:
        body = await request.json()
        if isinstance(body, dict):
            hook_payload.update(body)
    except Exception:
        pass  # empty or non-JSON body is fine
    job_id = q.create_job(conn, row["job_type"], hook_payload, trigger="hook")
    return q.get_job(conn, job_id)
