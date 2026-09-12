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
| `AGENTROUTER_BASE_URL` | OpenAI-compatible LLM base URL |
| `AGENTROUTER_API_KEY` | LLM API key |
| `AGENTROUTER_DEFAULT_MODEL` | Default model id (e.g. `gemini-2.5-flash`) |
| `DATABASE_URL` | `postgresql+asyncpg://...` (Neon) — SQLite is ephemeral on Spaces |
| `E2B_API_KEY` | E2B sandbox key |
| `GITHUB_PAT` | Injected into sandboxes for clone/push/PR (never logged) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push |

Health check: `GET /api/health`.
