import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

import config
from db import run_migrations
from routers.jobs import router as jobs_router


logging.basicConfig(level=logging.INFO, format="[viewer] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _mark_orphaned_runs_failed() -> None:
    logger.info("mark_orphaned_runs_failed: not yet implemented (Story 3.1)")


async def _start_retry_scheduler() -> None:
    logger.info("start_retry_scheduler: not yet implemented (Story 5.1)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(config.MATCH_RUNS_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("match runs dir ready: %s", config.MATCH_RUNS_DIR)

    await run_migrations()
    logger.info("migrations complete")

    await _mark_orphaned_runs_failed()
    await _start_retry_scheduler()

    yield


app = FastAPI(title="viewer-service", lifespan=lifespan)
app.include_router(jobs_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok"}
