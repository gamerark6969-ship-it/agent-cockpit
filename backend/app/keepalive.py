import asyncio
import logging
import os

import httpx

log = logging.getLogger("keepalive")

INTERVAL_S = int(os.environ.get("KEEPALIVE_INTERVAL_S", "600"))
GRACE_S = int(os.environ.get("KEEPALIVE_GRACE_S", "120"))


def _target_url() -> str | None:
    base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_BASE_URL")
    if not base:
        return None
    return base.rstrip("/") + "/api/health"


async def keepalive_loop() -> None:
    url = _target_url()
    if not url:
        log.info("keepalive disabled (no RENDER_EXTERNAL_URL/PUBLIC_BASE_URL set)")
        return

    await asyncio.sleep(GRACE_S)
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.get(url)
                log.info("keepalive ping %s -> %s", url, resp.status_code)
            except Exception as exc:  # noqa: BLE001
                log.warning("keepalive ping failed: %s", exc)
            await asyncio.sleep(INTERVAL_S)
