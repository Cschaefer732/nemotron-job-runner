# Nemotron Job Runner — Design Spec
DATE: 2026-04-01
SESSION: Local Nemotron LLM server + extensible job queue with MCP/REST interface

---

## Intent

Run a local Nemotron 8B model on the RTX 4070 Ti as an always-on inference service.
Expose it as the Smart Chat backend for Obsidian Smart Connections, and build an
extensible job queue on top of it — accepting jobs via time triggers, agents (MCP),
and hooks (REST) — so future workloads can be queued and processed without blocking
interactive use.

---

## In Scope

- llama.cpp server binary running Nemotron-8B Q5_K_M on GPU 1 (4070 Ti)
- FastAPI job runner: REST API (port 8082) + MCP server (port 8083) + SQLite queue + worker
- APScheduler for cron/time-based job triggers
- Hook endpoint (`POST /hooks/{hook_name}`) for event-driven job submission
- `smart_connections_reembed` as the first registered job handler
- Smart Connections Obsidian plugin wired to Nemotron for Smart Chat
- Both services registered as NSSM Windows services, auto-start on login

---

## Out of Scope

- Embeddings — Smart Connections already uses TaylorAI/bge-micro-v2 locally (not replaced)
- Multi-GPU inference / tensor parallelism
- Authentication on the local APIs (localhost-only, trusted network)
- Job result persistence beyond SQLite (no S3, no external store)
- Web UI for queue management (agents and REST are the interface)

---

## Done Looks Like

- [ ] `NemotronServer` NSSM service starts on login, serves `/v1/chat/completions` on port 8081
- [ ] VRAM usage on 4070 Ti stays at or below 8.25 GB under load
- [ ] `NemotronJobRunner` NSSM service starts after NemotronServer, waits for health check
- [ ] `GET /health` returns 200 with current 4070 Ti VRAM stats
- [ ] `POST /jobs` accepts a job, returns job ID, job appears in SQLite
- [ ] Worker picks up pending jobs in priority + created_at order, runs one at a time
- [ ] `PATCH /jobs/{id}` can reorder priority, edit payload, or cancel a pending job
- [ ] APScheduler fires cron-triggered jobs on schedule
- [ ] `POST /hooks/{hook_name}` submits a job tied to that hook name
- [ ] MCP server exposes queue tools; Claude Code can call `queue_job`, `list_jobs`, etc.
- [ ] Obsidian Smart Chat routes through Nemotron (chat completions work in-plugin)
- [ ] `smart_connections_reembed` handler registered and runnable
- [ ] New handler added by dropping a file in `handlers/` and restarting the runner only

---

## Constraints

- MUST target GPU 1 (`--main-gpu 1`) — GPU 0 (RTX 5080) must not be touched
- MUST NOT exceed 8.25 GB VRAM total on 4070 Ti (voice pipeline shares the card)
- MUST use NSSM for both services (matches existing GPUaAudioProcessing pattern)
- PREFER delayed auto-start to avoid boot contention
- MUST NOT require Ollama or any other model serving layer

---

## Architecture

Two NSSM services in `E:\Development\nemotron\`:

```
┌──────────────────────────────────────────────────┐
│  NSSM: NemotronServer                            │
│  llama-server.exe                                │
│  Model: Llama-3.1-Nemotron-8B-Instruct Q5_K_M   │
│  Port: 8081  /v1/chat/completions (OpenAI compat)│
│  --main-gpu 1  -ngl 999  -c 16384               │
│  VRAM: ~7.9 GB (5.5 weights + 2.0 KV + 0.4 misc)│
└──────────────────────┬───────────────────────────┘
                       │ HTTP  localhost:8081
┌──────────────────────▼───────────────────────────┐
│  NSSM: NemotronJobRunner                         │
│  FastAPI (Python, uvicorn)                       │
│  Port 8082 — REST API + MCP server (/mcp/sse)    │
│  SQLite   — job queue + results  (data/jobs.db)  │
│  APScheduler — cron/time triggers                │
│  Worker thread — polls queue, serializes GPU jobs│
│  Handlers registry — pluggable job types         │
└──────────────────────────────────────────────────┘
         ▲                        ▲
   Obsidian Smart Chat      Claude Code / agents
   (direct → :8081)         (MCP :8083 + REST :8082)
   Scripts / hooks
   (REST :8082)
```

### Startup sequence

1. `NemotronServer` starts (delayed auto-start)
2. `NemotronJobRunner` starts, polls `GET http://localhost:8081/health` every 5s
3. After health check passes (or 120s timeout → log + exit), runner begins accepting jobs

---

## VRAM Budget

| Component | VRAM |
|-----------|------|
| Nemotron Q5_K_M weights | ~5.5 GB |
| KV cache (ctx 16384) | ~2.0 GB |
| CUDA overhead | ~0.4 GB |
| **Nemotron total** | **~7.9 GB** |
| Voice pipeline (Whisper + Kokoro + DTLN + VAD) | ~3.2 GB |
| **Combined peak** | **~11.1 GB / 12 GB** |

Buffer: ~0.9 GB. Kokoro VRAM leak (Issue #262) mitigated by nightly NSSM restart at 03:00.
`GET /health` reports live VRAM so agents can check before submitting heavy jobs.

---

## Job Queue

### Schema

```sql
CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,   -- UUID v4
    type         TEXT NOT NULL,      -- registered handler name
    payload      TEXT NOT NULL,      -- JSON, handler-specific
    status       TEXT NOT NULL DEFAULT 'pending',
                                     -- pending|running|completed|failed|cancelled
    priority     INTEGER NOT NULL DEFAULT 50,  -- 0=highest, 100=lowest
    trigger      TEXT NOT NULL DEFAULT 'manual',
                                     -- manual|cron|agent|hook
    scheduled_at TEXT,               -- ISO8601, NULL = run ASAP
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    error        TEXT
);

CREATE TABLE job_results (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    result     TEXT,                 -- JSON blob
    created_at TEXT NOT NULL
);
```

### Worker

Single background thread. Poll interval: 2s.

Query: `SELECT * FROM jobs WHERE status='pending' AND (scheduled_at IS NULL OR scheduled_at <= datetime('now')) ORDER BY priority ASC, created_at ASC LIMIT 1`

One job runs at a time — prevents concurrent GPU saturation. Job marked `running` before handler call, `completed`/`failed` after.

### Hook Registry

Hooks are stored in SQLite:

```sql
CREATE TABLE hooks (
    name        TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',  -- JSON default payload (merged with POST body)
    created_at  TEXT NOT NULL
);
```

`POST /hooks/vault_updated` → looks up hook `vault_updated` → submits job with registered `job_type` + `payload`. Hook body can override payload fields. Hooks registered via `PUT /hooks/{name}` by agents or at startup from a `hooks.json` seed file.

### Handler interface

```python
class JobHandler:
    job_type: str                              # unique string key

    def run(self, job_id: str, payload: dict) -> dict:
        ...                                    # raises on failure, returns result dict
```

Handlers registered at startup by scanning `handlers/*.py` for `JobHandler` subclasses.
Adding a new job type = new file in `handlers/` + runner restart (model stays hot).

---

## REST API  (port 8082)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit job — body: `{type, payload, priority?, scheduled_at?, trigger?}` |
| `GET` | `/jobs` | List jobs — query: `status`, `type`, `trigger`, `limit`, `offset` |
| `GET` | `/jobs/{id}` | Get job + result |
| `PATCH` | `/jobs/{id}` | Edit `priority`, `payload`, `scheduled_at`, or set `status=cancelled` |
| `DELETE` | `/jobs/{id}` | Hard delete (completed/failed only) |
| `POST` | `/hooks/{hook_name}` | Submit job(s) registered to that hook name (see Hook Registry) |
| `GET` | `/hooks` | List registered hook → job_type mappings |
| `PUT` | `/hooks/{hook_name}` | Register or update a hook → `{job_type, default_payload}` |
| `GET` | `/health` | Service status + 4070 Ti VRAM (used/free/total) |

---

## MCP Server  (mounted on port 8082 at `/mcp/sse`)

Served by fastmcp mounted on the same FastAPI app — no second port or process needed.
Claude Code MCP config points to `http://localhost:8082/mcp/sse`.

Tools exposed to agents:

| Tool | Description |
|------|-------------|
| `queue_job` | Submit a job by type + payload |
| `list_jobs` | List jobs with optional status/type filter |
| `get_job` | Get full job details + result |
| `update_job` | Edit priority, payload, or cancel |
| `cancel_job` | Cancel a pending job |
| `list_job_types` | List all registered handler types + their payload schemas |

---

## Smart Connections Integration

Update `E:\ObsidianVault\AdminAssistantMemory\.smart-env\smart_env.json` chat model section:

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

Smart Chat calls Nemotron directly — not queued. The queue is for background/batch jobs only.

### smart_connections_reembed handler

Payload: `{}` (no parameters for now)
Action: Triggers re-embedding of the Obsidian vault. Exact mechanism TBD during implementation — two candidates:
1. Write a sentinel file that the Smart Connections plugin watches
2. Call Obsidian URI scheme (`obsidian://smart-connections/reembed`) if supported

---

## Project Layout

```
E:\Development\nemotron\
  llama-server.exe              ← llama.cpp release binary
  models\
    nemotron-8b-q5_k_m.gguf    ← downloaded from HuggingFace
  app\
    main.py                     ← FastAPI app factory, mounts REST + MCP
    queue.py                    ← SQLite init, worker thread, polling loop
    mcp_server.py               ← MCP HTTP/SSE server (fastmcp or manual)
    scheduler.py                ← APScheduler setup, cron job registration
    health.py                   ← /health endpoint, nvidia-smi VRAM query
    handlers\
      __init__.py               ← handler registry + auto-discovery
      smart_connections.py      ← SmartConnectionsReembed
  data\
    jobs.db                     ← SQLite (gitignored)
  logs\                         ← NSSM stdout/stderr logs
  docs\
    superpowers\specs\
      2026-04-01-nemotron-job-runner-design.md
  start.bat                     ← manual launch for testing
  requirements.txt
  .gitignore
```

---

## NSSM Configuration

```
NemotronServer
  Application:   E:\Development\nemotron\llama-server.exe
  Arguments:     -m models\nemotron-8b-q5_k_m.gguf
                 --main-gpu 1 -ngl 999 -c 16384
                 --port 8081 --host 127.0.0.1
                 --log-disable
  AppDirectory:  E:\Development\nemotron
  Start:         Automatic (delayed)
  Stdout log:    E:\Development\nemotron\logs\nemotron-server.log
  Stderr log:    E:\Development\nemotron\logs\nemotron-server-err.log

NemotronJobRunner
  Application:   E:\Development\nemotron\venv\Scripts\python.exe
  Arguments:     -m uvicorn app.main:app --host 127.0.0.1 --port 8082
  AppDirectory:  E:\Development\nemotron
  Start:         Automatic (delayed)
  DependOnService: NemotronServer
  Stdout log:    E:\Development\nemotron\logs\runner.log
  Stderr log:    E:\Development\nemotron\logs\runner-err.log
```

---

## Risks / Unknowns

- **smart_connections_reembed mechanism** — Obsidian URI support for triggering re-embed TBD; may need a different approach
- **MCP server library** — `fastmcp` vs manual SSE implementation; fastmcp preferred if it supports HTTP/SSE transport cleanly on Windows
- **Nemotron GGUF availability** — `bartowski/Llama-3.1-Nemotron-8B-Instruct-GGUF` is the likely source; verify Q5_K_M file exists before download step
- **llama.cpp binary** — fetch from official GitHub releases (CUDA 12 build); verify CUDA version matches system

---

## ADR: Two services vs one

**Decision:** Two NSSM services (NemotronServer + NemotronJobRunner)

**Context:** Could run everything in one process via `llama-cpp-python`.

**Decision:** Separate because: (1) model server has a 30–60s cold start — restarting it to deploy a new job handler wastes time; (2) a handler bug crashing the runner should not unload the model; (3) separation of concerns makes each service independently debuggable.

**Consequence:** Runner startup must wait for model server health check. Minor added complexity.
