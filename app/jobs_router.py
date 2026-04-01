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
    result = q.update_job(
        conn,
        job_id,
        priority=body.priority,
        payload=body.payload,
        scheduled_at=body.scheduled_at,
        status=body.status,
    )
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    if result == "conflict":
        raise HTTPException(status_code=409, detail="Job cannot be modified in its current state")
    return q.get_job(conn, job_id)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, request: Request):
    conn = request.app.state.conn
    deleted = q.delete_job(conn, job_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="Job not found or not in a terminal state")
