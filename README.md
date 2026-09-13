# Agent Cockpit — mobile-first autonomous AI software engineer

Delegate software-engineering tasks to an autonomous agent from your phone. The phone is the
cockpit; a cloud sandbox is the computer; the LLM is the brain.

```
📱 PWA  ──SSE/REST──▶  🧠 FastAPI backend (agent loop, permissions, resume)
                          ├─ Postgres/SQLite   (event log = source of truth)
                          ├─ Web Push          (task done / needs approval)
                          ├─ LLM gateway       (OpenAI-compatible, model-agnostic)
                          └─ ☁️ E2B sandbox    (terminal, git, gh, headless browser)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/API.md`](docs/API.md).
V1 endpoint for a task is **an opened pull request**. Multi-user, deployment, and third-party
connectors are intentionally out of scope.

## Layout

```
backend/   FastAPI + agent loop (deploy target: Render/Fly/HF Space, Docker)
pwa/       React + Vite + Tailwind PWA (deploy target: Render static / GitHub Pages)
docs/      architecture + API contract
```

## Local development

### 1. Backend

```powershell
cd backend
python -m venv .venv
& .venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item ..\.env.example .env   # then fill in values
& .venv\Scripts\python.exe run.py
```

The API boots even without `E2B_API_KEY` / `AGENTROUTER_API_KEY` / VAPID keys — tasks simply
fail fast with a clear error until the relevant key is configured.

Health check: `GET http://localhost:8000/api/health`

### 2. PWA

```powershell
cd pwa
npm install
npm run dev        # http://localhost:5173, proxies /api to :8000
```

Open the app, paste your `APP_TOKEN`, and connect.

## Environment variables

Copy [`.env.example`](.env.example) to `backend/.env`:

| Key | Purpose |
|---|---|
| `APP_TOKEN` | Single-user bearer token. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `AGENTROUTER_BASE_URL` | OpenAI-compatible base URL, e.g. `https://.../v1` |
| `AGENTROUTER_API_KEY` | Your agentrouter proxy key |
| `AGENTROUTER_DEFAULT_MODEL` | Default model id |
| `BAI_BASE_URL` / `BAI_API_KEY` | Optional secondary OpenAI-compatible provider, routed by model-id prefix |
| `DATABASE_URL` | `sqlite+aiosqlite:///./app.db` (dev) or `postgresql+asyncpg://...` (prod) |
| `E2B_API_KEY` | E2B sandbox key |
| `GITHUB_PAT` | Injected into sandboxes for clone/push/PR and `deploy_site`. Never logged. Add `Pages: write` to publish sites. |
| `PUBLIC_BASE_URL` | Public origin of the backend (Render auto-sets `RENDER_EXTERNAL_URL`); used for `deploy_site` fallback links |
| `WORKER_CONCURRENCY` | How many tasks run at once (default 3) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push. Generate with `npx web-push generate-vapid-keys` |

## Deploying for ₹0

- **Backend** → Render free web service (Docker or the included `render.yaml`). Reads `$PORT` and
  `RENDER_EXTERNAL_URL`. Free instances sleep after ~15 min idle and cold-start in ~1 min; keep it
  warm with the external cron in [`.github/workflows/keepalive.yml`](.github/workflows/keepalive.yml)
  (GitHub Actions) or a free pinger like cron-job.org hitting `/api/health`. Point it at your own
  backend URL before enabling. Interrupted tasks resume from the database on wake.
- **PWA** → Render static site (`npm run build`, `dist`), or GitHub Pages. Set build env
  `VITE_API_BASE=https://<backend>` and `VITE_BASE=/<repo>/` when serving from a Pages subpath.
- **Database** → Neon free Postgres (`DATABASE_URL`).
- **Sandboxes** → E2B free tier (~100 hrs/month).
- **Published sites** → `deploy_site` exposes the agent's local server through E2B for a live URL,
  and (optionally) pushes a static copy to GitHub Pages for a persistent URL, with an `/s/{id}`
  fallback served by the backend.

Point the PWA at the backend with `VITE_API_BASE` (the app sends `Authorization` via fetch, so
cross-origin works with permissive CORS).

## How a task runs

1. Create a project (GitHub repo) and a task in natural language.
2. The worker boots an E2B sandbox, clones the repo, and creates branch `agent/<task-id>`.
3. The agent loops: plan → tool call → observe → fix. Tools include shell, file edit, grep,
   web fetch, headless browser (with screenshots), `repo_map` (fast repo orientation), git
   commit/push, `gh pr create`, and `deploy_site` (publish a page or app and return a URL).
4. Risky commands hit the permission policy: `auto`, `ask` (pauses the task and pushes an
   approval to your phone), or `deny`.
5. Long runs stay cheap: when the model context grows past the compaction threshold it is
   summarized automatically, and tool output is head+tail truncated. You can `steer` a running
   task with a new instruction at any time.
6. On success the task opens a PR (and/or deploys a site) and reports back. Close the app at any
   time — the run continues, and the event feed resumes from the database when you reopen it.
