import asyncio
import logging
from datetime import datetime, timezone

from services import queue_store
from services.match_run import execute_match_run_from_input, read_input_lines

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 10
_task: asyncio.Task | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _retry_discord_only(item: dict) -> None:
    logger.info("retry_scheduler: discord-only retry for %s (notify stub, Story 6.2)", item.get("id"))


async def _retry_match_run(item: dict) -> None:
    run_id = queue_store.run_id_from_item_id(item["id"])
    input_lines = await read_input_lines(run_id)
    if not input_lines:
        logger.error("retry_scheduler: no input file for run %s, skipping", run_id)
        return
    await execute_match_run_from_input(run_id, input_lines, item.get("mode") or "claude")


async def _tick() -> None:
    now = _now_iso()
    items = await queue_store.read_queue()
    for item in items:
        if item.get("status") != "retrying":
            continue
        next_retry_at = item.get("next_retry_at")
        if not next_retry_at or next_retry_at > now:
            continue
        try:
            if item.get("mode") == "discord-only":
                await _retry_discord_only(item)
            else:
                await _retry_match_run(item)
        except Exception:
            logger.exception("retry_scheduler: error processing item %s", item.get("id"))


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("retry_scheduler: tick failed")
        await asyncio.sleep(_POLL_INTERVAL_S)


def start() -> asyncio.Task:
    global _task
    _task = asyncio.create_task(_loop())
    return _task


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
