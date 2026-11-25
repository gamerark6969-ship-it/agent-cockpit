---
title: Agent Cockpit Backend
emoji: 🤖
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Agent Cockpit — backend

FastAPI + agent loop for the Agent Cockpit PWA. See the project docs for the API contract
and architecture.

Required Space secrets (Settings → Variables and secrets):

| Secret | Purpose |
|---|---|
| `APP_TOKEN` | Single-user bearer token for all API calls |
| `CONNECTOR_SECRET` | Persistent connector encryption secret; required and distinct from `APP_TOKEN` in production |
| `ENVIRONMENT` | Set to `production` to enable production configuration validation |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API |
| `AGENTROUTER_BASE_URL` | OpenAI-compatible LLM base URL |
| `AGENTROUTER_API_KEY` | LLM API key |
| `AGENTROUTER_DEFAULT_MODEL` | Default model id (e.g. `gemini-2.5-flash`) |
| `DATABASE_URL` | `postgresql+asyncpg://...` (Neon) — SQLite is ephemeral on Spaces |
| `MAX_ACTIVE_TASKS` | Maximum queued, running, or approval-waiting tasks across the application; defaults to `1` and must be a positive integer. New task creation returns HTTP 429 at the limit; terminal tasks do not count. |
| `MAX_TASK_RUNTIME_S` | Maximum wall-clock runtime for the complete task execution lifecycle; defaults to `1800` seconds and must be a positive integer. On expiry the task is persisted as failed with a runtime-timeout event, then the agent is cancelled so its existing terminal sandbox cleanup runs. |
| `MAX_EVENTS_PER_TASK` | Maximum persisted events retained per task; defaults to `1000` and must be a positive integer. The oldest events are pruned after each new event is persisted. Reconnecting API and SSE clients can replay only retained history; retained sequence numbers are not renumbered. |
| `SANDBOX_IDLE_TIMEOUT_S` | Seconds an inactive task sandbox may remain idle before cleanup; defaults to `900` and must be positive. Active and queued tasks are never cleaned by this process. |
| `SANDBOX_CLEANUP_INTERVAL_S` | Seconds between idle-sandbox scans; defaults to `60` and must be positive. A sandbox identifier is cleared only after successful termination. |
| `E2B_API_KEY` | E2B sandbox key |
| `GITHUB_PAT` | Injected into sandboxes for clone/push/PR (never logged) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push |

Health check: `GET /api/health`.
