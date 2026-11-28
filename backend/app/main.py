import asyncio
import logging
from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .api.routes import public_router, router
from .connectors import seed_from_env as seed_connectors_from_env
from .config import settings
from .db import engine, seed_settings
from .reminders import reminder_loop, router as reminders_router
from .keepalive import keepalive_loop
from .rate_limit import SlidingWindowLimiter
from .worker import Worker
from .automation.api import router as automation_router, public_router as automation_public_router, service as automation_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

worker = Worker()


def run_migrations() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")


async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


EXEMPT_PATHS = {"/api/health", "/api/health/"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding-window limit on every non-exempt HTTP path.

    Runs after CORS in the stack (CORS is added last so it executes first for
    browser preflights) and before auth, so brute-force token guessing is
    throttled instead of getting unlimited 401s.
    """

    def __init__(self, app):
        super().__init__(app)
        self._limiter = SlidingWindowLimiter(
            settings.API_RATE_LIMIT, settings.API_RATE_WINDOW_S
        )

    async def dispatch(self, request, call_next):
        if not settings.API_RATE_LIMIT_ENABLED or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        allowed, retry_after = self._limiter.check(
            request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        if not allowed:
            from fastapi import HTTPException

            response = JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded; retry later"},
            )
            response.headers["Retry-After"] = str(int(retry_after) + 1)
            return response
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_production_secrets()
    await asyncio.to_thread(run_migrations)
    from .agent.provider_store import refresh_provider_routes
    await refresh_provider_routes()
    await seed_settings()
    await seed_connectors_from_env()
    if settings.BACKGROUND_WORKER_ENABLED:
        await worker.resume_interrupted_tasks()
        await worker.start()
    await automation_service.start()
    reminders_task = asyncio.create_task(reminder_loop()) if settings.BACKGROUND_WORKER_ENABLED else None
    keepalive_task = asyncio.create_task(keepalive_loop())
    log.info("backend started (worker running)")
    try:
        yield
    finally:
        if reminders_task:
            reminders_task.cancel()
            await asyncio.gather(reminders_task, return_exceptions=True)
        keepalive_task.cancel()
        await automation_service.stop()
        await worker.stop()
        await engine.dispose()
        log.info("backend stopped")


app = FastAPI(title="AI Software Engineer Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(reminders_router)
app.include_router(public_router)
app.include_router(router)
app.include_router(automation_public_router)
app.include_router(automation_router)
