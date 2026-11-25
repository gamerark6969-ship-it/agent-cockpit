"""Wake the free scheduled runner for a phone action; cron remains the fallback."""

import logging
import re

import httpx

from .config import automation_settings as cfg

log = logging.getLogger("automation.dispatch")


async def dispatch_worker() -> bool:
    if not cfg.github_repository or not cfg.github_token:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", cfg.github_repository):
        log.warning("Worker repository is not configured correctly")
        return False
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"https://api.github.com/repos/{cfg.github_repository}/actions/workflows/{cfg.github_workflow}/dispatches",
                headers={"Authorization":f"Bearer {cfg.github_token}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"},
                json={"ref":cfg.github_ref},
            )
        if response.status_code != 204:
            log.warning("Worker dispatch failed (HTTP %s); job remains queued", response.status_code)
            return False
        return True
    except httpx.HTTPError:
        log.warning("Worker dispatch unavailable; job remains queued")
        return False
