# Architecture (V1)

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Mobile client | PWA (React + TS + Vite + vite-plugin-pwa) | Cloudflare Pages later; `npm run dev` locally |
| Backend + agent loop | Python 3.12+ / FastAPI, single process | Deployed to Hugging Face Space (Docker, port from `PORT` env) |
| State | SQLAlchemy 2.0 async; SQLite (dev default) / Neon Postgres (prod) via `DATABASE_URL` | Event log = source of truth |
| Workspaces | E2B sandboxes (one per running task) | Reconnect by sandbox ID; PAT injected via env vars, never logged |
| LLM | agentrouter proxy, OpenAI-compatible (`/v1/chat/completions`, `/v1/models`) | `AGENTROUTER_BASE_URL`, `AGENTROUTER_API_KEY` |
| Artifacts | Screenshots stored in DB (bytea), served by backend | R2 is V2 |
| Push | Web Push (VAPID) via pywebpush | |
| Git/PRs | git + gh CLI inside sandbox | Task endpoint = open PR |

## Process model

- One FastAPI process. The agent loop runs as an asyncio background worker (concurrency = 1 task at a time in V1).
- Every agent iteration is persisted (messages + counters + sandbox_id) before the next LLM call → backend can crash/restart and resume.
- On startup: any task in `running`/`awaiting_approval` is re-enqueued for resume (reconnect sandbox by ID; if dead, restart task with a summary of prior progress injected into the conversation).
- SSE endpoints tail the append-only event log from the DB (poll by seq), so streams work across restarts and multiple clients.

## Data model

- `Project(id, name, repo_url, default_branch, settings JSON, created_at)`
- `Task(id, project_id, prompt, status, model, iterations, tokens_used, sandbox_id, error, result_summary, created_at, updated_at)`
- `Event(id autoinc, task_id, seq, type, payload JSON, created_at)` — unique(task_id, seq)
- `Message(id autoinc, task_id, seq, role, content, tool_calls JSON, tool_call_id, created_at)` — agent conversation (compactable)
- `Approval(id, task_id, kind, description, payload JSON, status, decided_at, created_at)`
- `Artifact(id, task_id, kind, filename, mime, size, data BLOB, created_at)`
- `PushSubscription(endpoint unique, p256dh, auth, created_at)`
- `Setting(single row, JSON)` — default_model, max_iterations, token_budget, command_timeout_s, permissions policy

## Permission model

Tool actions map to a level: `auto` | `ask` | `deny`.
- File ops, grep/glob, read-only git, web_fetch: auto
- bash: command matched against glob rules (e.g. `sudo *` deny, `git push --force*` deny, `gh pr create*` ask by default)
- Rules live in settings; per-task override not needed in V1.
- `ask` → create Approval row, task → `awaiting_approval`, web push sent, worker blocks on an in-process asyncio.Event (decision endpoint sets it). Approval timeout (30 min default) → treated as denied; agent is told and must adapt.

## Event types (wire format)

`{ "seq": int, "task_id": str, "type": str, "payload": object, "created_at": iso8601 }`

`task_started, agent_message, tool_call, tool_result, terminal, screenshot, approval_request, approval_decision, task_completed, task_failed, task_stopped, checkpoint, error`

## Task statuses

`queued, running, awaiting_approval, done, failed, stopped`
