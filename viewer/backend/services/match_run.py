import json
import random
import string
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

import config

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_RUN_ID_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choices(_RUN_ID_SUFFIX_ALPHABET, k=6))
    return f"run_{timestamp}_{suffix}"


def match_run_dir(run_id: str) -> Path:
    return Path(config.MATCH_RUNS_DIR) / run_id


def match_run_manifest_path(run_id: str) -> Path:
    return match_run_dir(run_id) / "manifest.json"


def match_run_input_path(run_id: str) -> Path:
    return match_run_dir(run_id) / "jobs.jsonl"


def match_run_results_path(run_id: str) -> Path:
    return match_run_dir(run_id) / "results.jsonl"


def match_run_log_path(run_id: str) -> Path:
    return match_run_dir(run_id) / "matcher.log"


async def write_manifest(run_id: str, manifest: dict) -> None:
    match_run_dir(run_id).mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(match_run_manifest_path(run_id), "w", encoding="utf-8") as f:
        await f.write(json.dumps(manifest, indent=2) + "\n")


async def read_manifest(run_id: str) -> dict | None:
    try:
        async with aiofiles.open(match_run_manifest_path(run_id), "r", encoding="utf-8") as f:
            raw = await f.read()
        return json.loads(raw)
    except (OSError, ValueError):
        return None


async def _read_queue_file() -> list[dict]:
    path = Path(config.STATE_DIR) / "retry-queue.json"
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            raw = await f.read()
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


async def _write_queue_file(items: list[dict]) -> None:
    path = Path(config.STATE_DIR) / "retry-queue.json"
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(items, indent=2) + "\n")


async def _flip_orphaned_queue_items() -> None:
    items = await _read_queue_file()
    changed = False
    for item in items:
        if item.get("status") in ("running", "todo"):
            item["status"] = "permanent_error"
            item["error"] = "orphaned: server restarted"
            item["updated_at"] = _now_iso()
            for subtask in item.get("subtasks", []):
                if subtask.get("status") in ("running", "todo"):
                    subtask["status"] = "error"
                    subtask["error"] = "orphaned"
            changed = True
    if changed:
        await _write_queue_file(items)


async def mark_orphaned_runs_failed() -> None:
    runs_dir = Path(config.MATCH_RUNS_DIR)
    if runs_dir.is_dir():
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue
            manifest = await read_manifest(entry.name)
            if manifest is None or manifest.get("status") != STATUS_RUNNING:
                continue
            manifest["status"] = STATUS_FAILED
            manifest["error"] = "orphaned: server restarted"
            manifest["updated_at"] = _now_iso()
            await write_manifest(entry.name, manifest)

    await _flip_orphaned_queue_items()
