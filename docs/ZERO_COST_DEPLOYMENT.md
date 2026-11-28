# Zero-dollar cloud setup

Implemented path: **Render Free API + free static PWA + Neon Free Postgres + Supabase Free private storage + public GitHub Actions runner**. Video production and coding workspaces run on the scheduled runner, so the API can sleep. No GPU, E2B subscription, or paid video-generation API is required for this path.

Each installation uses its owner's cloud accounts, model credentials and Google channel consent.

## 1. Create the persistent services

- **Neon Free:** create a project and copy its pooled Postgres URL. The backend accepts ordinary `postgresql://...?...sslmode=require` URLs and converts them for asyncpg.
- **Supabase Free:** create a project and a **private** bucket named `video-previews`. In Storage → S3, create server-side access credentials. Copy the exact endpoint and region from the dashboard. A typical endpoint is `https://<project>.storage.supabase.co/storage/v1/s3`.
- **Render:** deploy the included `render.yaml` Blueprint. Keep the backend on **Free**, and the frontend as a free static site. Set the database and storage values below. Backend Docker includes fonts, FFmpeg through Python dependencies, and offline speech support.

Keep both database/storage projects on their free plans. The app limits preview storage to **750 MiB**, individual videos to **45 MiB**, and prunes completed preview files older than seven days. Pending/review/failed run media is retained; if it fills the limit, production stops rather than requiring an upgrade. YouTube originals remain on the channel after preview expiry.

## 2. API configuration

Set these in Render environment settings. Secrets stay in the backend, not the PWA build:

| Variable | Value |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `APP_TOKEN` | A generated persistent single-user login token |
| `CONNECTOR_SECRET` | A different generated persistent encryption secret |
| `DATABASE_URL` | Neon pooled connection URL |
| `CORS_ORIGINS` | Exact PWA origin |
| `PUBLIC_BASE_URL` | API origin |
| `AUTOMATION_FRONTEND_URL` | PWA origin, including any Pages subpath |
| `AUTOMATION_ENABLED` | `false` (runner handles video jobs) |
| `BACKGROUND_WORKER_ENABLED` | `false` (runner handles coding/chat jobs) |
| `KEEPALIVE_ENABLED` | `false` |
| `OPENCODE_REMOTE_WORKER` | `true` |
| `LOCAL_LLM_REMOTE_WORKER` | `true` |
| `AUTOMATION_S3_ENDPOINT` | Supabase S3 endpoint |
| `AUTOMATION_S3_ACCESS_KEY` / `AUTOMATION_S3_SECRET_KEY` | S3 server credentials |
| `AUTOMATION_S3_BUCKET` | `video-previews` |
| `AUTOMATION_S3_REGION` | Actual region from Supabase |

Generate tokens locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, once per secret. Preserve `CONNECTOR_SECRET`; changing it makes existing encrypted channel/model credentials unreadable.

For phone **Run now**, **Approve**, **Retry**, and coding task creation to dispatch a worker immediately, also set:

- `AUTOMATION_GITHUB_REPOSITORY=owner/repository`
- `AUTOMATION_GITHUB_TOKEN`: fine-grained token scoped to that repository with Actions write access
- `AUTOMATION_GITHUB_REF=main`

Dispatch failures leave the task queued for cron. An immediate dispatch still waits for GitHub to start the runner.

## 3. Google / YouTube connection

1. Create a Google Cloud project, enable **YouTube Data API v3**, and configure its OAuth consent screen.
2. Sign in to the PWA and open **Studio → Setup → YouTube**. Copy the displayed redirect URI into a Google **Web application** OAuth client. If Google shows the app in testing, add your channel's Google account as a test user.
3. Download that client's JSON from Google and choose **Upload Google client JSON** in Studio. The backend encrypts the credentials. Installations managed through Render environment variables can use `AUTOMATION_YOUTUBE_CLIENT_ID`, `AUTOMATION_YOUTUBE_CLIENT_SECRET`, and `AUTOMATION_YOUTUBE_REDIRECT_URI` instead.
4. Choose **Connect with Google**, select the account that manages the channel, and approve access. Studio displays the connected channel after Google redirects back.
5. Start with a series set to **Review** and **Private** visibility. Confirm a real upload, then select **Public** and **Automatic** for unattended publishing.

Production consent configuration, the API project's available quota, channel publishing eligibility, and any applicable verification requirements must be checked in Google. Testing-mode consent may require reconnection as refresh grants expire. The app reports failures instead of assuming permanent authorization.

## 4. Free scheduled runner

The workflow `.github/workflows/video-production.yml` runs on a **standard public-repository Ubuntu runner** at minute 17 each hour. It reserves due slots in the shared database, generates/renders/uploads up to three videos, and can run one queued coding/chat task. It uses database leases and stage checkpoints across restarts.

The job is deliberately disabled for private repositories so this path does not bill private runner minutes. It uses no Actions video artifacts. A bounded CPU-model cache is the only workflow cache besides package dependencies.

Repository **variables**:

| Variable | Value |
| --- | --- |
| `ENABLE_VIDEO_AUTOMATION` | `true` after setup |
| `FRONTEND_URL` | PWA origin |
| `YOUTUBE_REDIRECT_URI` | Same Google callback as the API |
| `S3_BUCKET` | `video-previews` |
| `S3_REGION` | Supabase region |

Repository **secrets**:

`APP_TOKEN`, `CONNECTOR_SECRET`, `DATABASE_URL`, `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`.

Use the **same values** as the API. Model keys can be saved once through the phone's **Connectors** page into the shared encrypted database; the scheduled worker reads them there.

Enable Actions on the default branch, then run **Daily video production → Run workflow**. Scheduled workflows can be delayed and may be disabled by GitHub after prolonged repository inactivity. This is a best-effort free cron service, not an exact-time availability guarantee. A queued job remains in Postgres until a worker can claim it.

## 5. Models

- **FreeGG GPT-6.1 Sol:** supported through `https://freegg.app/v1` (OpenAI Responses). A live local connection and complete story generation passed using the user's configured key.
- **AgentRouter GPT-6.1 Sol:** supported through `https://agentrouter.org/v1` with its separate Responses key. Existing Gemini environment keys remain separate from this route.
- **Xingchi GPT-6.1 Sol:** supported through `https://api.xingchiapi.com/v1`, with `xingchi` and `xingchi2` connectors.
- **OpenCode free models:** the runner invokes the real headless client. Live local checks returned free quota exhaustion; the app recognizes that promptly rather than hanging or silently buying access.
- **Free CPU fallback:** Ollama runs `qwen3:1.7b` locally on the cloud runner, with cloud inference disabled. Quality and speed are lower than the configured GPT route; it is a usable zero-inference-cost fallback.

Automatic video generation tries configured FreeGG, then OpenCode, then local CPU inference. Selecting a provider explicitly pins that route. AgentRouter/Xingchi cost and quota depend on your provider account; the workflow never enables auto-recharge or assumes they are free.

Import existing model settings locally with:

```powershell
# Dry run: prints only provider URLs and whether a key exists.
& .venv\Scripts\python.exe scripts/import_model_providers.py
# Save the requested provider keys encrypted, without printing them.
& .venv\Scripts\python.exe scripts/import_model_providers.py --apply
```

For the cloud database, save the keys using **Connectors** on the deployed PWA, or run the importer with the cloud `DATABASE_URL`, `APP_TOKEN`, and `CONNECTOR_SECRET` configured in your environment.

## 6. Phone experience and notifications

Set `VITE_API_BASE` to the backend origin and build the PWA. The app has Studio series settings, 1–3 production slots, status, previews, history, review/approve, cancellation, and retries. Add it to your phone's home screen for standalone use.

Optional free Web Push: generate VAPID keys, configure them on both API and runner, then enable notifications in Settings. The service worker displays draft/published/failure updates and opens the correct run. iOS requires an installed supported PWA and notification permission.

The coding cockpit also runs through free Docker workspaces on the Actions runner: chat, repository tasks, tool approvals, persisted stop/steer controls, and saved patch review. It remains an evolving Codex-style cockpit; broader features such as connected-computer control, voice, and a complete skills/plugin experience are not yet feature-parity complete.

## Current checks and limits

- **87 backend tests and 10 subtests passed**; the production TypeScript/PWA build passed.
- The full migration chain passes on a fresh database, and the legacy project upgrade was applied without removing existing records.
- A live FreeGG chat-agent task completed successfully with durable history and its provider route retained (`scripts/verify_agent.py`).
- Live FreeGG connection and an original script generated successfully.
- Generated **1080×1920, 24 FPS** MP4s passed full decode. The latest saved Studio draft, **The Star That Squeaked**, is **49.57 seconds**, with real installed-voice narration, original music, and timed captions.
- `scripts/verify_studio.py` passed the real authenticated Studio routes and signed MP4 byte-range seeking. `scripts/smoke_video.py --generate --full-size --offline --save-studio` creates a real commissioning draft inside Studio.
- Offline timing is approximated; Edge speech provides actual word timings when available.
- Renderer is original procedural illustrated animation with a supported set/action/prop vocabulary. It does not capture Roblox gameplay or synthesize photorealistic arbitrary footage.
- Free hosting/quotas can suspend or delay work. The database records queued, failed, cancelled, waiting-review, uploaded, and published states so the phone can show what happened.
- Docker's local engine was unavailable in this development environment. Container commands, per-task ownership, timeout cleanup, and durable controls have offline tests; verify the actual Docker build/run on the GitHub runner before relying on repository tasks.

Sources checked: [Render Free](https://render.com/docs/free), [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Neon pricing](https://neon.com/pricing), [Supabase S3 authentication](https://supabase.com/docs/guides/storage/s3/authentication), [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview), and [OpenCode models](https://opencode.ai/docs/zen/). Hugging Face's current docs require a paid plan to create Docker Spaces; its static hosting remains an optional frontend destination. Rentry is a document host rather than a production Python worker.

### Chart-selected background music

Open **Studio → Your series → Chart music**. Enter a YouTube chart playlist URL or ID and choose **Use playlist**. Connect YouTube in Studio so the worker can read the playlist with the existing read-only permission. Choose a playlist whose order represents your desired chart ranking.

Add up to three licensed MP3s of 4 MiB each. For each file enter the exact 11-character YouTube video ID of its entry in the chart. At render time the worker reads the first 50 chart entries and mixes the highest-ranked matching upload beneath the narration. If the chart is unavailable or has no matching upload, it uses your series background track or the generated score. The run activity reports the selection. Disabling the chart preserves your uploaded candidates.

Each chart lookup uses one YouTube Data API quota unit. [Google playlistItems.list reference](https://developers.google.com/youtube/v3/docs/playlistItems/list)
