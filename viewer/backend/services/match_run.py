import asyncio
import json
import logging
import random
import re
import string
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

import config
from db import execute
from services.company import company_name, is_real_compensation, sanitize_job

logger = logging.getLogger(__name__)

PARSE_CONCURRENCY = 6

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


async def append_match_run_log(run_id: str, message: str, stream: str = "stderr") -> None:
    rendered = f"[match-run {run_id}] {stream}: {message}"
    if stream == "stderr":
        logger.error(rendered)
    else:
        logger.info(rendered)
    async with aiofiles.open(match_run_log_path(run_id), "a", encoding="utf-8") as f:
        await f.write(rendered + "\n")


async def parse_job_post(url: str) -> dict:
    script = str(Path(config.MATCHER_DIR) / "job_post_parser.py")
    proc = await asyncio.create_subprocess_exec(
        config.PYTHON_BIN,
        script,
        "--url",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"job_post_parser.py exited with code {proc.returncode}")
    return json.loads(stdout.decode("utf-8"))


_WHITESPACE_RE = re.compile(r"\s+")


def clean_parsed_text(value: object) -> str | None:
    text = str(value if value is not None else "").strip()
    text = _WHITESPACE_RE.sub(" ", text)
    return text or None


async def persist_parsed_metadata(job: dict, parsed: dict) -> None:
    location = clean_parsed_text(parsed.get("location"))
    parsed_compensation = clean_parsed_text(parsed.get("compensation"))
    compensation = parsed_compensation if is_real_compensation(parsed_compensation) else None
    has_data = len(parsed) > 0
    if not location and not compensation and not has_data:
        return

    parsed_jd = json.dumps(parsed) if has_data else None

    try:
        await execute(
            "UPDATE catalog_jobs SET "
            "location = CASE WHEN ? IS NOT NULL AND TRIM(?) <> '' "
            "AND (location IS NULL OR TRIM(location) = '') THEN ? ELSE location END, "
            "compensation = CASE WHEN ? IS NOT NULL AND TRIM(?) <> '' THEN ? ELSE compensation END, "
            "parsed_jd = CASE WHEN parsed_jd IS NULL AND ? IS NOT NULL THEN ? ELSE parsed_jd END "
            "WHERE provider = ? AND source_key = ? AND job_id = ?",
            (
                location,
                location,
                location,
                compensation,
                compensation,
                compensation,
                parsed_jd,
                parsed_jd,
                job["provider"],
                job["source_key"],
                job["job_id"],
            ),
        )
    except Exception:
        logger.exception("persist_parsed_metadata error")


def build_jd_text(parsed: dict, job: dict) -> str:
    def as_list(value: object) -> list:
        return value if isinstance(value, list) else []

    responsibilities = as_list(parsed.get("responsibilities"))
    requirements = as_list(parsed.get("requirements_summary"))
    must_have = as_list(parsed.get("must_have_requirements")) or requirements
    nice_to_have = as_list(parsed.get("nice_to_have_requirements"))
    technical_tools = as_list(parsed.get("technical_tools_mentioned"))
    concepts = as_list(parsed.get("jd_concepts"))

    sections: list[str] = []

    def add(label: str, value: object) -> None:
        rendered = str(value if value is not None else "").strip()
        if rendered:
            sections.append(f"{label}: {rendered}")

    add("Title", parsed.get("title") or job.get("title"))
    add("Company", company_name(job))
    add("Provider", job.get("provider"))
    add("Location", parsed.get("location") or job.get("location"))
    add("Employment type", parsed.get("employment_type") or job.get("employment_type"))
    add("Workplace type", parsed.get("workplace_type"))
    add(
        "Compensation",
        parsed.get("compensation")
        if is_real_compensation(parsed.get("compensation"))
        else sanitize_job(job).get("compensation"),
    )
    add(
        "Posted datetime",
        parsed.get("posted_datetime") or job.get("posted_at") or job.get("updated_at") or job.get("first_seen_at"),
    )
    add("JD concepts", ", ".join(concepts))
    add("Technical tools mentioned", ", ".join(technical_tools))

    if responsibilities:
        sections.append("Responsibilities:\n" + "\n".join(f"- {item}" for item in responsibilities))
    if must_have:
        sections.append("Requirements:\n" + "\n".join(f"- {item}" for item in must_have))
    if nice_to_have:
        sections.append("Nice-to-have:\n" + "\n".join(f"- {item}" for item in nice_to_have))

    return "\n\n".join(sections)


async def _parse_one(sem: asyncio.Semaphore, run_id: str, job: dict) -> tuple[dict, str | None]:
    job_label = f"{company_name(job)} | {job.get('title') or job.get('job_id')}"

    cached_parsed = None
    parsed_jd_raw = job.get("parsed_jd")
    if parsed_jd_raw:
        try:
            cached_parsed = json.loads(parsed_jd_raw)
        except (TypeError, ValueError):
            cached_parsed = None

    async with sem:
        if cached_parsed is not None:
            await append_match_run_log(run_id, f"[parse] {job_label} | cached")
            return cached_parsed, None

        job_url = job.get("job_url")
        if not job_url:
            await append_match_run_log(run_id, f"[parse] {job_label} | failed | Missing job URL")
            return {}, "Missing job URL"

        await append_match_run_log(run_id, f"[parse] {job_label} | start | url={job_url}")
        try:
            parsed = await parse_job_post(job_url)
        except Exception as exc:
            parse_error = str(exc)
            await append_match_run_log(run_id, f"[parse] {job_label} | failed | {parse_error}")
            return {}, parse_error

        await append_match_run_log(
            run_id,
            f"[parse] {job_label} | success | provider={parsed.get('provider', job.get('provider'))} "
            f"title={parsed.get('title') or job.get('title') or 'n/a'}",
        )
        await persist_parsed_metadata(job, parsed)
        return parsed, None


def _build_input_line(job: dict, parsed: dict, parse_error: str | None) -> str:
    line = {
        **sanitize_job(job),
        "title": parsed.get("title") or job.get("title"),
        "company": company_name(job),
        "location": parsed.get("location") or job.get("location"),
        "employment_type": parsed.get("employment_type") or job.get("employment_type"),
        "compensation": (
            parsed.get("compensation")
            if is_real_compensation(parsed.get("compensation"))
            else sanitize_job(job).get("compensation")
        ),
        "workplace_type": parsed.get("workplace_type"),
        "posted_datetime": (
            parsed.get("posted_datetime")
            or job.get("posted_at")
            or job.get("updated_at")
            or job.get("first_seen_at")
        ),
        "responsibilities": parsed.get("responsibilities") or [],
        "requirements_summary": parsed.get("requirements_summary") or [],
        "must_have_requirements": parsed.get("must_have_requirements") or parsed.get("requirements_summary") or [],
        "nice_to_have_requirements": parsed.get("nice_to_have_requirements") or [],
        "technical_tools_mentioned": parsed.get("technical_tools_mentioned") or [],
        "jd_concepts": parsed.get("jd_concepts") or [],
        "job_url": job.get("job_url"),
        "url": job.get("job_url"),
        "jd_text": build_jd_text(parsed, job),
        "parse_error": parse_error,
    }
    return json.dumps(line)


async def write_batch_input(run_id: str, jobs: list[dict], manifest: dict) -> dict:
    async with aiofiles.open(match_run_log_path(run_id), "w", encoding="utf-8"):
        pass  # reset the log file for this run

    sem = asyncio.Semaphore(PARSE_CONCURRENCY)
    results = await asyncio.gather(*(_parse_one(sem, run_id, job) for job in jobs))

    parsed_count = 0
    lines: list[str] = []
    for job, (parsed, parse_error) in zip(jobs, results):
        if not parse_error:
            parsed_count += 1
        lines.append(_build_input_line(job, parsed, parse_error))

    async with aiofiles.open(match_run_input_path(run_id), "w", encoding="utf-8") as f:
        await f.write("\n".join(lines) + "\n")

    return {**manifest, "parsed_count": parsed_count}
