def test_submit_job(client):
    r = client.post("/jobs", json={"type": "test_job", "payload": {"x": 1}})
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert data["type"] == "test_job"


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


def test_patch_invalid_status_rejected(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
    r2 = client.patch(f"/jobs/{job_id}", json={"status": "completed"})
    assert r2.status_code == 422


def test_delete_completed_job(client):
    r = client.post("/jobs", json={"type": "t", "payload": {}})
    job_id = r.json()["id"]
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


def test_register_and_list_hook(client):
    r = client.put(
        "/hooks/vault_updated",
        json={"job_type": "smart_connections_reembed", "payload": {}},
    )
    assert r.status_code == 200
    r2 = client.get("/hooks")
    names = [h["name"] for h in r2.json()]
    assert "vault_updated" in names


def test_trigger_hook_submits_job(client):
    client.put(
        "/hooks/vault_updated",
        json={"job_type": "smart_connections_reembed", "payload": {}},
    )
    r = client.post("/hooks/vault_updated")
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    assert data["type"] == "smart_connections_reembed"


def test_trigger_unknown_hook_returns_404(client):
    r = client.post("/hooks/nonexistent_hook")
    assert r.status_code == 404


def test_health_endpoint(client):
    from unittest.mock import patch
    with patch(
        "app.health.get_vram_stats",
        return_value={"used_mb": 7900, "free_mb": 300, "total_mb": 12288, "gpu": "RTX 4070 Ti"},
    ):
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["vram"]["used_mb"] == 7900
