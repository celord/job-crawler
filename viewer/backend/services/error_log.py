"""Persistent JSONL error log for failed analyses.

One line per failed analysis (level "error") plus "warning" lines for
non-fatal degradations (JD parse failures, ensemble scorer skips). Entries
are redacted before they hit disk: Authorization headers, NVIDIA_API_KEY
(from the environment or key=value form), and Discord webhook URLs are
masked; request bodies (which embed the candidate profile) are never
logged — entries only ever contain the whitelisted fields below.

The file is capped at MAX_LOG_BYTES (5 MB): when an append would push it
past the cap the current file is rotated to <name>.1 (replacing any
previous rotation). Read paths only see the active file.
"""

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

import config

_LOCK = asyncio.Lock()

MAX_MESSAGE_CHARS = 2048
MAX_LOG_BYTES = 5 * 1024 * 1024

_AUTH_HEADER_RE = re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s\"',]+")
_WEBHOOK_URL_RE = re.compile(r"https?://(?:discord\.com|discordapp\.com)/api/webhooks/\S+")
_KEY_VALUE_RE = re.compile(r"(?i)\b(nvidia_api_key|api[_-]?key)(\s*[=:]\s*)[^\s\"',]+")


def error_log_path() -> Path:
    return Path(config.ERROR_LOG_PATH)


def redact_text(text: str) -> str:
    """Masks secrets that could leak into an error message: the value of
    NVIDIA_API_KEY (raw or in key=value form), Authorization headers, and
    Discord webhook URLs (both the configured one and any that appear
    inline)."""
    masked = str(text)
    api_key = os.environ.get("NVIDIA_API_KEY")
    if api_key:
        masked = masked.replace(api_key, "***")
    webhook = getattr(config, "DISCORD_WEBHOOK_URL", None)
    if webhook:
        masked = masked.replace(webhook, "***")
    masked = _WEBHOOK_URL_RE.sub("***", masked)
    masked = _AUTH_HEADER_RE.sub(r"\1***", masked)
    masked = _KEY_VALUE_RE.sub(r"\1\2***", masked)
    return masked


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalized_entry(entry: dict) -> dict:
    record = {
        "timestamp": entry.get("timestamp") or _now_iso(),
        "level": entry.get("level") or "error",
        "job_key": entry.get("job_key") or "",
        "pipeline": entry.get("pipeline") or "",
        "run_id": entry.get("run_id") or "",
        "stage": entry.get("stage") or "",
        "error_type": entry.get("error_type") or "",
        "message": redact_text(str(entry.get("message") or ""))[:MAX_MESSAGE_CHARS],
    }
    for key in ("http_status", "model"):
        if entry.get(key) is not None:
            record[key] = entry[key]
    return record


async def append_error(entry: dict) -> None:
    record = _normalized_entry(entry)
    line = json.dumps(record, ensure_ascii=False) + "\n"

    async with _LOCK:
        path = error_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > MAX_LOG_BYTES:
            os.replace(path, path.with_name(f"{path.name}.1"))
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(line)


async def read_errors(since: str | None = None, limit: int | None = None) -> list[dict]:
    """Reads the active log file, skipping corrupt lines. `since` filters
    to entries with timestamp >= since (ISO strings compare correctly);
    `limit` keeps the most recent N entries."""
    try:
        async with aiofiles.open(error_log_path(), encoding="utf-8") as f:
            raw = await f.read()
    except OSError:
        return []

    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)

    if since is not None:
        since_time = _parse_timestamp(since)
        filtered = []
        for entry in entries:
            try:
                timestamp = _parse_timestamp(str(entry.get("timestamp") or ""))
            except ValueError:
                continue
            if timestamp >= since_time:
                filtered.append(entry)
        entries = filtered
    if limit is not None:
        entries = entries[-limit:]
    return entries


async def clear_errors() -> int:
    async with _LOCK:
        path = error_log_path()
        removed = 0
        for candidate in (path, path.with_name(f"{path.name}.1")):
            try:
                async with aiofiles.open(candidate, encoding="utf-8") as f:
                    raw = await f.read()
                removed += len([line for line in raw.splitlines() if line.strip()])
            except OSError:
                pass
            candidate.unlink(missing_ok=True)
        return removed


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)
