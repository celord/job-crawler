import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

import config
from db import run_migrations
from routers.jobs import router as jobs_router
from routers.match_runs import router as match_runs_router
from routers.queue import router as queue_router
from services import retry_scheduler
from services.match_run import mark_orphaned_runs_failed


logging.basicConfig(level=logging.INFO, format="[viewer] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(config.MATCH_RUNS_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("match runs dir ready: %s", config.MATCH_RUNS_DIR)

    await run_migrations()
    logger.info("migrations complete")

    await mark_orphaned_runs_failed()
    retry_scheduler.start()

    yield

    await retry_scheduler.stop()


app = FastAPI(title="viewer-service", lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(match_runs_router)
app.include_router(queue_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok"}
