"""In-memory sliding-window rate limiting shared by middleware and routes.

Counters are per process, which matches the single-process deployment model of
this backend. For multi-process deployments the effective limit becomes
limit x process_count, which is still a useful bound.
"""

import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


class SlidingWindowLimiter:
    """Sliding-window counter with an injectable clock for deterministic tests."""

    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, float]:
        """Record a hit for key; return (allowed, retry_after_seconds)."""
        now = self._clock()
        cutoff = now - self.window_s
        hits = self._hits[key]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, max(0.0, hits[0] + self.window_s - now)
        hits.append(now)
        return True, 0.0

    def reset(self) -> None:
        self._hits.clear()


def enforce_rate_limit(limiter: SlidingWindowLimiter, request: Request) -> None:
    allowed, retry_after = limiter.check(client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded; retry later",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
