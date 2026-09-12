import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import public_router, router
from .db import engine, seed_settings
from .keepalive import keepalive_loop
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

worker = Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_settings()
    await worker.resume_interrupted_tasks()
    await worker.start()
    keepalive_task = asyncio.create_task(keepalive_loop())
    log.info("backend started (worker running)")
    try:
        yield
    finally:
        keepalive_task.cancel()
        await worker.stop()
        await engine.dispose()
        log.info("backend stopped")


app = FastAPI(title="AI Software Engineer Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public_router)
app.include_router(router)
