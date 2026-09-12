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
backend/   FastAPI + agent loop (deploy target: Hugging Face Space, Docker)
pwa/       React + Vite + Tailwind PWA (deploy target: Cloudflare Pages)
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
| `DATABASE_URL` | `sqlite+aiosqlite:///./app.db` (dev) or `postgresql+asyncpg://...` (prod) |
| `E2B_API_KEY` | E2B sandbox key |
| `GITHUB_PAT` | Injected into sandboxes for clone/push/PR. Never logged. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` | Web Push. Generate with `npx web-push generate-vapid-keys` |

## Deploying for ₹0

- **Backend** → Hugging Face Space (Docker SDK). The included `backend/Dockerfile` listens on
  `$PORT`. Add secrets in Space settings. Free CPU Spaces sleep after ~48h idle; keep it warm
  with a free cron ping (e.g. cron-job.org hitting `/api/health`). The agent loop resumes
  interrupted tasks from the database on wake.
- **PWA** → Cloudflare Pages: build command `npm run build`, output directory `dist`.
- **Database** → Neon free Postgres (`DATABASE_URL`).
- **Sandboxes** → E2B free tier (~100 hrs/month). If you exhaust it, GitHub Actions runners are
  the documented fallback substrate.

Point the PWA at the backend by proxying `/api` (Cloudflare Pages redirect/worker or same-origin
reverse proxy).

## How a task runs

1. Create a project (GitHub repo) and a task in natural language.
2. The worker boots an E2B sandbox, clones the repo, and creates branch `agent/<task-id>`.
3. The agent loops: plan → tool call → observe → fix. Tools include shell, file edit, grep,
   web fetch, headless browser (with screenshots), git commit/push, and `gh pr create`.
4. Risky commands hit the permission policy: `auto`, `ask` (pauses the task and pushes an
   approval to your phone), or `deny`.
5. On success the task opens a PR and reports back. Close the app at any time — the run
   continues, and the event feed resumes from the database when you reopen it.
