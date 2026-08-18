import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiofiles

import config

_LOCK = asyncio.Lock()


def queue_path() -> Path:
    return Path(config.STATE_DIR) / "retry-queue.json"


async def read_queue() -> list[dict]:
    try:
        async with aiofiles.open(queue_path(), encoding="utf-8") as f:
            raw = await f.read()
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


async def _write_queue_unlocked(items: list[dict]) -> None:
    path = queue_path()
    tmp_path = path.with_name(f".{path.name}.tmp")
    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(items, indent=2) + "\n")
    os.replace(tmp_path, path)


async def write_queue(items: list[dict]) -> None:
    async with _LOCK:
        await _write_queue_unlocked(items)


async def upsert_queue_item(item: dict) -> None:
    async with _LOCK:
        items = await read_queue()
        idx = next((i for i, existing in enumerate(items) if existing.get("id") == item["id"]), None)
        if idx is not None:
            items[idx] = item
        else:
            items.append(item)
        await _write_queue_unlocked(items)


async def remove_queue_item(item_id: str) -> None:
    async with _LOCK:
        items = await read_queue()
        items = [i for i in items if i.get("id") != item_id]
        await _write_queue_unlocked(items)


def run_id_from_item_id(item_id: str) -> str:
    idx = item_id.find(":")
    return item_id if idx == -1 else item_id[:idx]


def _short_model_name(full_id: str) -> str:
    return full_id.split("/")[-1] if full_id else full_id


def build_subtasks(mode: str) -> list[dict]:
    if mode == "claude-ensemble":
        scorer_env = config.NVIDIA_ENSEMBLE_SCORERS or (
            "meta/llama-4-maverick-17b-128e-instruct,moonshotai/kimi-k2.6,"
            "nvidia/llama-3.3-nemotron-super-49b-v1.5"
        )
        synth_env = config.NVIDIA_ENSEMBLE_SYNTHESIZER or "nvidia/llama-3.3-nemotron-super-49b-v1.5"
        scorer_ids = ["scorer:maverick", "scorer:kimi", "scorer:nemotron"]
        models = [m.strip() for m in scorer_env.split(",") if m.strip()]
        scorer_subtasks = [
            {
                "id": scorer_ids[i] if i < len(scorer_ids) else f"scorer:{i}",
                "label": f"{_short_model_name(m)} (scorer)",
                "status": "todo",
            }
            for i, m in enumerate(models)
        ]
        return [
            *scorer_subtasks,
            {"id": "synthesis", "label": f"{_short_model_name(synth_env)} (synthesis)", "status": "todo"},
            {"id": "discord", "label": "Discord push", "status": "todo"},
        ]

    single_model = config.NVIDIA_MODEL
    return [
        {"id": "model:claude", "label": f"{_short_model_name(single_model)} (scorer)", "status": "todo"},
        {"id": "discord", "label": "Discord push", "status": "todo"},
    ]


def retry_backoff_seconds(attempt: int) -> int:
    return 30 * (2**attempt)


def next_retry_at(attempt: int) -> str:
    delay = timedelta(seconds=retry_backoff_seconds(attempt))
    return (datetime.now(UTC) + delay).isoformat().replace("+00:00", "Z")
