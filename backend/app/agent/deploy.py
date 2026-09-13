"""deploy_site: publish a static site/page and return public URLs.

Two strategies, best-effort:
  1. ``live``      — start a static server inside the E2B sandbox and expose it
                     via ``sandbox.get_host(port)``. Instant, works for any port
                     (also used for fullstack apps when the agent supplies one).
  2. ``persistent``— commit the files to a GitHub Pages repo with the configured
                     PAT. Survives sandbox shutdown, works on any device.
  3. fallback      — store the entry HTML as an artifact served by the backend
                     at ``/s/{id}`` when GitHub Pages is unavailable.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import posixpath
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import settings
from ..db import SessionLocal
from ..models import Artifact, new_id
from .. import sandbox as sbx_mod

log = logging.getLogger("deploy")

PAGES_REPO = "agent-sites"
LIVE_PORT = 8000
MAX_FILES = 150
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
SKIP_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".cache")


def _public_base() -> str:
    base = settings.PUBLIC_BASE_URL or os.environ.get("RENDER_EXTERNAL_URL") or ""
    return base.rstrip("/")


# ── live sandbox URL ─────────────────────────────────────


async def _expose_live(
    ctx, sbx, root: str, port: Optional[int]
) -> Optional[str]:
    from .tools import _emit

    if port is None:
        port = LIVE_PORT
        await sbx_mod.run_command(
            sbx,
            f"cd {sbx_mod._sh(root)} && nohup python3 -m http.server {port} "
            f"--bind 0.0.0.0 >/tmp/deploy_http.log 2>&1 & sleep 1.5; "
            f"curl -sS -o /dev/null -w '%{{http_code}}' http://localhost:{port}/ || true",
            timeout=60,
        )
    try:
        host = sbx.get_host(port)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not expose sandbox port %s: %s", port, exc)
        return None
    if not host:
        return None
    if not host.startswith("http"):
        host = f"https://{host}"
    await _emit(
        ctx.task_id,
        "terminal",
        {
            "command": f"serve {root} on port {port}",
            "exit_code": 0,
            "output": f"live preview available at {host}",
            "truncated": False,
        },
    )
    return host


# ── file collection ──────────────────────────────────────


async def _stat(sbx, path: str) -> Optional[str]:
    res = await sbx_mod.run_command(
        sbx, f"if [ -d {sbx_mod._sh(path)} ]; then echo dir; elif [ -f {sbx_mod._sh(path)} ]; then echo file; fi", timeout=30
    )
    kind = (res.get("output") or "").strip()
    return kind or None


async def _collect_files(sbx, root: str) -> Tuple[List[str], Optional[str]]:
    """Return (relative paths, error). Capped to keep deploys small and fast."""
    excludes = " -o ".join(f"-name {d}" for d in SKIP_DIRS)
    find_cmd = (
        f"find {sbx_mod._sh(root)} -type d \\( {excludes} \\) -prune -o -type f -print 2>/dev/null | head -{MAX_FILES + 1}"
    )
    res = await sbx_mod.run_command(sbx, find_cmd, timeout=60)
    paths = [p for p in (res.get("output") or "").splitlines() if p.strip()]
    if len(paths) > MAX_FILES:
        return [], f"too many files to deploy (>{MAX_FILES}); deploy a smaller folder"
    rels: List[str] = []
    for p in paths:
        rel = p[len(root):].lstrip("/") if p.startswith(root) else posixpath.basename(p)
        if rel and rel not in rels:
            rels.append(rel)
    if not rels:
        return [], "no files found to deploy"
    return rels, None


async def _read_bytes(sbx, path: str) -> Optional[bytes]:
    try:
        data = await sbx.files.read(path, format="bytes")
    except Exception:
        return None
    if isinstance(data, str):
        return data.encode("utf-8", "replace")
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    return None


# ── GitHub Pages ─────────────────────────────────────────


def _gh_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-cockpit",
    }


async def _gh_login(client: httpx.AsyncClient) -> Optional[str]:
    resp = await client.get("https://api.github.com/user")
    if resp.status_code != 200:
        return None
    return (resp.json() or {}).get("login")


async def _gh_ensure_repo(client: httpx.AsyncClient, login: str) -> str:
    repo = f"{login}/{PAGES_REPO}"
    resp = await client.get(f"https://api.github.com/repos/{repo}")
    if resp.status_code == 200:
        return repo
    resp = await client.post(
        "https://api.github.com/user/repos",
        json={"name": PAGES_REPO, "private": False, "auto_init": True,
              "description": "Sites published by the AI agent"},
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"repo create failed: {resp.status_code} {resp.text[:200]}")
    await asyncio.sleep(1.5)
    return repo


async def _gh_put(client: httpx.AsyncClient, repo: str, path: str, content: bytes, message: str) -> None:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    sha = None
    existing = await client.get(url)
    if existing.status_code == 200:
        sha = (existing.json() or {}).get("sha")
    body: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    resp = await client.put(url, json=body)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"upload failed for {path}: {resp.status_code} {resp.text[:200]}")


async def _gh_enable_pages(client: httpx.AsyncClient, repo: str) -> Optional[str]:
    """Ensure Pages is enabled. Returns None on success, else an error string.

    Fine-grained PATs often lack the Pages scope (403); in that case we accept
    it only if Pages is already enabled, otherwise the caller falls back.
    """
    resp = await client.post(
        f"https://api.github.com/repos/{repo}/pages",
        json={"source": {"branch": "main", "path": "/"}},
    )
    if resp.status_code in (200, 201, 204, 409):
        return None
    check = await client.get(f"https://api.github.com/repos/{repo}/pages")
    if check.status_code == 200:
        return None
    return f"pages not enabled ({resp.status_code}); enable it once in repo settings"


async def _deploy_github_pages(
    token: str, task_id: str, files: Dict[str, bytes]
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (index_url, error)."""
    try:
        async with httpx.AsyncClient(timeout=30, headers=_gh_headers(token)) as client:
            login = await _gh_login(client)
            if not login:
                return None, "GitHub token rejected (check PAT scopes)"
            repo = await _gh_ensure_repo(client, login)
            prefix = f"sites/{task_id}"
            for rel, content in files.items():
                await _gh_put(client, repo, f"{prefix}/{rel}", content, f"deploy {task_id}: {rel}")
            pages_err = await _gh_enable_pages(client, repo)
        entry = "index.html" if "index.html" in files else next(iter(files))
        url = f"https://{login}.github.io/{PAGES_REPO}/{prefix}/{entry}"
        if pages_err:
            return None, f"files uploaded to {repo} but {pages_err}"
        return url, None
    except Exception as exc:  # noqa: BLE001
        log.warning("github pages deploy failed: %s", exc)
        return None, str(exc)


async def _store_artifact(ctx, filename: str, data: bytes) -> Optional[str]:
    from .tools import _emit, guess_mime

    try:
        artifact_id = new_id()
        async with SessionLocal() as session:
            session.add(
                Artifact(
                    id=artifact_id,
                    task_id=ctx.task_id,
                    kind="site",
                    filename=filename,
                    mime=guess_mime(filename),
                    size=len(data),
                    data=data,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return artifact_id
    except Exception as exc:  # noqa: BLE001
        log.warning("artifact fallback failed: %s", exc)
        return None


# ── entrypoint ───────────────────────────────────────────


async def deploy_site(ctx, args: dict) -> Tuple[bool, str, Optional[dict]]:
    from .tools import _emit, _repo_path

    sbx = await ctx.ensure_sandbox()
    raw = str(args.get("path") or "").strip() or "index.html"
    path = _repo_path(raw, ctx)
    title = str(args.get("title") or "").strip()

    kind = await _stat(sbx, path)
    if kind is None and not raw.startswith("/"):
        # Model may have written files under a different base than it deploys
        # from (e.g. /home/user/repo vs /home/user/work on chat tasks).
        for base in (sbx_mod.WORK_DIR, sbx_mod.REPO_DIR):
            alt = posixpath.join(base, raw)
            if alt == path:
                continue
            alt_kind = await _stat(sbx, alt)
            if alt_kind:
                path, kind = alt, alt_kind
                break
    if kind is None:
        return False, f"path not found in sandbox: {path}", None
    if kind == "dir":
        root = path
    else:
        root = posixpath.dirname(path.rstrip("/")) or "."

    port: Optional[int] = None
    raw_port = args.get("port")
    if raw_port:
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            port = None

    live_url = await _expose_live(ctx, sbx, root, port)

    rels, err = await _collect_files(sbx, root)
    if err:
        if live_url:
            return True, f"live preview: {live_url} ({err})", {"url": live_url}
        return False, err, None

    files: Dict[str, bytes] = {}
    total = 0
    for rel in rels:
        data = await _read_bytes(sbx, posixpath.join(root, rel))
        if data is None or len(data) > MAX_FILE_BYTES:
            continue
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            break
        files[rel] = data
    if not files:
        if live_url:
            return True, f"live preview: {live_url} (no static files collected)", {"url": live_url}
        return False, "could not read any deployable files", None

    persistent_url: Optional[str] = None
    pages_err: Optional[str] = None
    if settings.GITHUB_PAT:
        persistent_url, pages_err = await _deploy_github_pages(settings.GITHUB_PAT, ctx.task_id, files)

    artifact_url: Optional[str] = None
    if not persistent_url:
        entry = files.get("index.html") or next(iter(files.values()))
        entry_name = "index.html" if "index.html" in files else next(iter(files))
        artifact_id = await _store_artifact(ctx, entry_name, entry)
        base = _public_base()
        if artifact_id and base:
            artifact_url = f"{base}/s/{artifact_id}"

    url = live_url or persistent_url or artifact_url
    data = {
        "url": live_url,
        "persistent_url": persistent_url,
        "artifact_url": artifact_url,
        "files": len(files),
        "title": title or None,
    }
    if not url:
        return False, f"deploy failed: {pages_err or 'no public URL available'}", data

    await _emit(
        ctx.task_id,
        "deployed",
        {
            "url": live_url,
            "persistent_url": persistent_url,
            "artifact_url": artifact_url,
            "title": title,
            "files": len(files),
        },
    )
    parts = []
    if live_url:
        parts.append(f"live: {live_url}")
    if persistent_url:
        parts.append(f"persistent: {persistent_url}")
    if artifact_url:
        parts.append(f"backup: {artifact_url}")
    if pages_err:
        parts.append(f"(github pages unavailable: {pages_err})")
    return True, "site deployed — " + "; ".join(parts), data
