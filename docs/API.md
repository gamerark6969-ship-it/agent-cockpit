# API contract (V1)

Base URL: `/api`. Auth for every endpoint except `/api/health`:
`Authorization: Bearer <APP_TOKEN>`

All IDs are UUID strings. Timestamps ISO 8601 UTC.

## Health
- `GET /api/health` → `{ "status": "ok", "version": "1.0.0" }`

## Public
- `GET /s/{artifact_id}` → HTML (no auth). Serves a single-file site published by
  the `deploy_site` tool when live/GitHub Pages hosting is unavailable. Used as a
  fallback persistent URL for generated pages.

## Projects
- `GET /api/projects` → `[{ "id", "name", "repo_url", "default_branch", "settings": {}, "created_at" }]`
- `POST /api/projects` body `{ "repo_url": "https://github.com/o/r", "name"?, "default_branch"? }` → 201 project. `name` defaults to repo name.
- `GET /api/projects/{id}` → project
- `DELETE /api/projects/{id}` → 204 (also stops running tasks)

## Tasks
- `POST /api/projects/{id}/tasks` body `{ "prompt": str, "model"?: str }` → 201 task (status `queued`)
- `GET /api/projects/{id}/tasks` → `[task]`
- `GET /api/tasks/{id}` → `{ "id", "project_id", "prompt", "status", "model", "iterations", "tokens_used", "error", "result_summary", "created_at", "updated_at" }`
- `POST /api/tasks/{id}/stop` → 202 (worker stops sandbox; status `stopped`)
- `POST /api/tasks/{id}/steer` body `{ "message": str }` → 202 (message injected as user turn; only valid while running)
- `GET /api/tasks/{id}/events?after=<seq>&limit=<n>` → `[event]` (polling fallback)
- `GET /api/tasks/{id}/stream?after=<seq>` → SSE. Frames:
  - `event: event` + `id: <seq>` + `data: <event json>` for each new event
  - `: ping` heartbeat every 15s
- `GET /api/tasks/{id}/diff` → `{ "summary": str, "files": [{ "path", "status", "additions", "deletions" }] }` (from sandbox git; empty if no sandbox)
- `GET /api/tasks/{id}/screenshots` → `[{ "id", "filename", "created_at" }]`
- `GET /api/artifacts/{id}` → binary (Content-Type: image/png etc.; requires auth header)

## Approvals
- `GET /api/approvals?pending=true` → `[{ "id", "task_id", "kind", "description", "payload", "created_at" }]`
- `POST /api/approvals/{id}/decision` body `{ "decision": "approve" | "deny" }` → 200 approval (sets task back to `running` if approved)

## Push
- `GET /api/push/vapid-public` → `{ "public_key": str }` (empty string when VAPID not configured)
- `POST /api/push/subscribe` body: standard Web Push subscription JSON `{ "endpoint", "keys": { "p256dh", "auth" } }` → 201
- `DELETE /api/push/subscribe` body `{ "endpoint" }` → 204

## Settings
- `GET /api/settings` →
```json
{
  "default_model": "str",
  "max_iterations": 120,
  "token_budget": 2000000,
  "command_timeout_s": 600,
  "approval_timeout_s": 1800,
  "compaction_threshold_tokens": 24000,
  "permissions": {
    "bash_rules": [
      { "match": "sudo *", "level": "deny" },
      { "match": "git push --force*", "level": "deny" },
      { "match": "rm -rf /*", "level": "deny" },
      { "match": "gh pr create*", "level": "ask" },
      { "match": "*curl*|*sh", "level": "ask" }
    ],
    "tool_levels": { "browser_*": "auto", "web_fetch": "auto", "git_push": "auto" }
  }
}
```
- `PUT /api/settings` body = same shape → 200 (validated)
- `GET /api/models` → `{ "models": ["id", ...] }` (proxied from `GET {AGENTROUTER_BASE_URL}/v1/models`)

## Error shape
`{ "detail": "message" }` with 4xx/5xx codes. 401 invalid token, 404 unknown id, 409 invalid state transition.

## Event payload shapes (per type)
- `task_started`: `{ "model": str }`
- `agent_message`: `{ "content": str }` (assistant text)
- `tool_call`: `{ "tool": str, "args": object }`
- `tool_result`: `{ "tool": str, "ok": bool, "summary": str, "truncated": bool }`
- `terminal`: `{ "command": str, "exit_code": int, "output": str, "truncated": bool }` (head+tail capped ~7500 chars)
- `screenshot`: `{ "artifact_id": str, "filename": str, "url": "/api/artifacts/{id}" }`
- `approval_request`: `{ "approval_id": str, "kind": str, "description": str, "payload": object }`
- `approval_decision`: `{ "approval_id": str, "decision": str }`
- `steered`: `{ "message": str }` (a mid-run `steer` message was injected)
- `context_compacted`: `{ "before_messages": int, "after_messages": int, "estimated_tokens_before": int }`
- `deployed`: `{ "url": str|null, "persistent_url": str|null, "artifact_url": str|null, "title": str, "files": int }` (`url` = live E2B, `persistent_url` = GitHub Pages, `artifact_url` = `/s/{id}` fallback)
- `task_completed`: `{ "result_summary": str, "pr_url": str|null, "pr_number": int|null, "branch": str|null, "iterations": int, "tokens_used": int }`
- `task_failed`: `{ "error": str }`
- `task_stopped`: `{ "reason": str }`
- `checkpoint`: `{ "iteration": int, "sandbox_alive": bool }`
- `error`: `{ "message": str }`
