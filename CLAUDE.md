# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A from-scratch reimplementation of AWS Lambda's runtime behavior on local Docker. Handlers run inside containers that poll a Lambda-style Runtime API for events, run the handler, POST back the result. Educational — mirrors real Lambda's ephemeral-container model (hard timeout kill, idle eviction, cold/warm starts).

## Commands

```bash
# 1. Build the runtime image (REQUIRED before first invoke — scheduler runs `docker run local-lambda-runtime`)
cd runtime && docker build -t local-lambda-runtime .

# 2. Start server (from server/, so `from scheduler import ...` and `from runtime_api import ...` resolve)
cd server && pip install -r requirements.txt && python main.py

# 3. Invoke
curl -X POST http://localhost:9000/invoke/hello -H "Content-Type: application/json" -d '{"name":"World"}'
```

No tests, linter, or build system. Verify by invoking and reading logs.

Add a function: `functions/<name>/handler.py` with `def handler(event): ...` + `functions/<name>/event.json` (default payload when POST body is empty/invalid JSON).

## Architecture

Two servers in one process + N Docker containers:

- **`server/main.py`** — FastAPI on **:9000**. Public `/invoke/{fn}`. Loads payload (body, else `event.json`), calls `get_env(fn).invoke(payload)`.
- **`server/scheduler.py`** — Container lifecycle. Holds ONE global warm `ENV` (single concurrency). `get_env` reuses ENV if same function + alive, else kills old + `create_env` (cold start via `docker run -d`). Daemon `reap_idle` thread kills ENV after `IDLE_TIMEOUT`s idle.
- **`server/runtime_api.py`** — FastAPI on **:5001** (started in a thread from scheduler's `RuntimeAPI()`). This is the Lambda Runtime API containers poll. Per-container `queue.Queue` for event isolation. `invoke()` = enqueue event → wait for pickup (cold-start phase) → wait for `/response` (execution phase), each with its own timeout slice; on timeout `docker kill`s the container.
- **`runtime/runtime.py`** — Runs INSIDE the container. Infinite loop: GET `/{hostname}/next` (blocks), run `handler.handler(event)`, POST result to `/{request_id}/response`.

### Request flow

`curl → :9000/invoke → scheduler.get_env → RuntimeAPI.invoke enqueues → container GET /next picks up → handler runs → container POST /response → invoke returns → :9000 responds`

## Critical implementation details

- **Container ID duality**: `docker run` returns 64-char ID; container `hostname` (used for API routing / queue keys) is the first 12 chars. `Environment.container_id` = short (routing), `full_container_id` = full (`docker kill`). Keep both in sync when touching lifecycle.
- **Networking**: containers reach the host API via `host.docker.internal:5001` (`--add-host=host.docker.internal:host-gateway`). `RUNTIME_API` env var carries the addr into the container.
- **Function code mount**: `-v {function_path}:/function`, `LAMBDA_TASK_ROOT=/function`; runtime does `sys.path.insert(0, ...)` then `import handler`.
- **Two-phase timeout** (`runtime_api.invoke`): total budget (15s) splits into pickup wait + execution wait. Both kill the container on expiry — Lambda never trusts user code to exit.

## Concurrency model

- **`Pool`** (`scheduler.py`): `pools: {function_name: [Environment]}`, up to `MAX_PER_FN` (default 3) warm containers per function. `acquire` reuses a free env, cold-starts a new one if under cap, else blocks on a `threading.Condition` until `release`. `create_env` runs outside the lock so a slow `docker run` doesn't stall other functions.
- **`main.py`** wraps the blocking `get_env`/`env.invoke` in `run_in_threadpool` (endpoint is `async`, invoke is a busy-wait) and always `release`s in a `finally`. Without the threadpool the pool would exist but requests would still serialize on the event loop.
- **Errors**: handler exceptions POST to `/{rid}/error` → stored as `{"__error__": ...}` → `invoke()` raises → `main.py` maps to `{errorMessage, errorType}`. Fast-fail, no timeout wait.

## Known gaps (verify before relying on)

- `test_pool.py` covers pool logic with a fake `create_env` (no Docker). End-to-end Docker path is only manually verified — see README verification steps.
- No per-function global cap across all functions; each function independently scales to `MAX_PER_FN`.
