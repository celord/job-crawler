import asyncio
import json
import logging
import random
import re
import string
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

import config
from db import execute
from services import queue_store
from services.analysis import persist_run_results
from services.company import company_name, is_real_compensation, sanitize_job
from services.notifications import notify_discord_for_score

logger = logging.getLogger(__name__)

PARSE_CONCURRENCY = 6

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

active_run_ids: set[str] = set()
active_run_processes: dict[str, "asyncio.subprocess.Process"] = {}

_RUN_ID_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def generate_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
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
        async with aiofiles.open(match_run_manifest_path(run_id), encoding="utf-8") as f:
            raw = await f.read()
        return json.loads(raw)
    except (OSError, ValueError):
        return None


async def _flip_orphaned_queue_items() -> None:
    items = await queue_store.read_queue()
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
        await queue_store.write_queue(items)


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
        (
            parsed.get("compensation")
            if is_real_compensation(parsed.get("compensation"))
            else sanitize_job(job).get("compensation")
        ),
    )
    add(
        "Posted datetime",
        parsed.get("posted_datetime")
        or job.get("posted_at")
        or job.get("updated_at")
        or job.get("first_seen_at"),
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
        "must_have_requirements": parsed.get("must_have_requirements")
        or parsed.get("requirements_summary")
        or [],
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


async def read_input_lines(run_id: str) -> list[str]:
    try:
        async with aiofiles.open(match_run_input_path(run_id), encoding="utf-8") as f:
            raw = await f.read()
    except OSError:
        return []
    return [line for line in raw.splitlines() if line.strip()]


async def read_results_jsonl(run_id: str) -> list[dict]:
    try:
        async with aiofiles.open(match_run_results_path(run_id), encoding="utf-8") as f:
            raw = await f.read()
    except OSError:
        return []
    results: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except ValueError:
            continue
    return results


def kill_run(run_id: str) -> bool:
    proc = active_run_processes.get(run_id)
    if proc is None:
        return False
    proc.terminate()
    active_run_processes.pop(run_id, None)
    return True


def _scorer_subtask_id(model_name: str) -> str | None:
    name = model_name.lower()
    if "maverick" in name:
        return "scorer:maverick"
    if "kimi" in name:
        return "scorer:kimi"
    if "nemotron" in name:
        return "scorer:nemotron"
    return None


_START_RE = re.compile(r"\|\s*start\b", re.IGNORECASE)
_SCORER_DONE_RE = re.compile(r"\|\s*scorer\s+([\w\-./]+)\s*\|\s*avg=", re.IGNORECASE)
_SCORER_ERR_RE = re.compile(r"\|\s*scorer\s+([\w\-./]+)\s*\|\s*error[:\s]", re.IGNORECASE)
_SYNTH_RUNNING_RE = re.compile(r"\|\s*synthesizing", re.IGNORECASE)
_SYNTH_DONE_RE = re.compile(r"\|\s*synthesis done", re.IGNORECASE)


def _job_label_pattern(label: str) -> re.Pattern:
    escaped = re.escape(label)
    return re.compile(rf"\[ensemble(?:-batch)?\]\s+(?:#\d+\s+)?{escaped}\s+\|", re.IGNORECASE)


def _apply_log_line_to_item(item: dict, label_re: re.Pattern, line: str) -> bool:
    """Mutates `item` in place (subtask dicts included) so the caller's own
    reference to `item` stays in sync — it is reused after the scorer phase
    completes to mark the discord subtask."""
    if not label_re.search(line):
        return False

    now = _now_iso()

    def _patch_subtasks(predicate, **patch) -> bool:
        changed = False
        for subtask in item["subtasks"]:
            if predicate(subtask):
                subtask.update(patch)
                changed = True
        return changed

    if _START_RE.search(line):
        changed = _patch_subtasks(
            lambda s: s["id"].startswith("scorer:") or s["id"] == "model:claude",
            status="running",
            started_at=now,
        )
        if changed:
            item["status"] = "running"
            item["updated_at"] = now
        return changed

    match = _SCORER_DONE_RE.search(line)
    if match:
        subtask_id = _scorer_subtask_id(match.group(1))
        if subtask_id is None:
            return False
        changed = _patch_subtasks(lambda s: s["id"] == subtask_id, status="done", finished_at=now)
        if changed:
            item["updated_at"] = now
        return changed

    match = _SCORER_ERR_RE.search(line)
    if match:
        subtask_id = _scorer_subtask_id(match.group(1))
        if subtask_id is None:
            return False
        changed = _patch_subtasks(lambda s: s["id"] == subtask_id, status="error", finished_at=now)
        if changed:
            item["updated_at"] = now
        return changed

    if _SYNTH_RUNNING_RE.search(line):
        changed = _patch_subtasks(lambda s: s["id"] == "synthesis", status="running", started_at=now)
        if changed:
            item["updated_at"] = now
        return changed

    if _SYNTH_DONE_RE.search(line):
        changed = _patch_subtasks(lambda s: s["id"] == "synthesis", status="done", finished_at=now)
        if changed:
            item["updated_at"] = now
        return changed

    return False


async def run_scorer_phase(run_id: str, mode: str, queue_items: list[dict]) -> list[dict]:
    is_ensemble = mode == "claude-ensemble"
    script = "ensemble_runner.py" if is_ensemble else "job_fit_analyzer.py"
    pipeline_tag = "claude-ensemble" if is_ensemble else "claude"

    labeled_items = [
        (item, _job_label_pattern(f"{item.get('company', '')} | {item.get('title', '')}"))
        for item in queue_items
    ]

    proc = await asyncio.create_subprocess_exec(
        config.PYTHON_BIN,
        str(Path(config.MATCHER_DIR) / script),
        "--jobs-jsonl",
        str(match_run_input_path(run_id)),
        "--results-jsonl",
        str(match_run_results_path(run_id)),
        "--profile-dir",
        str(Path(config.MATCHER_DIR) / config.CAREER_OPS_DIR),
        "--pipeline",
        pipeline_tag,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    active_run_processes[run_id] = proc

    async def _drain_stdout() -> None:
        assert proc.stdout is not None
        async for _ in proc.stdout:
            pass

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        async for raw_line in proc.stderr:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            await append_match_run_log(run_id, line, stream="stderr")
            for item, label_re in labeled_items:
                if _apply_log_line_to_item(item, label_re, line):
                    await queue_store.upsert_queue_item(item)

    try:
        await asyncio.gather(_drain_stdout(), _drain_stderr())
        returncode = await proc.wait()
    finally:
        active_run_processes.pop(run_id, None)

    if returncode != 0:
        raise RuntimeError(f"{script} exited with code {returncode}")

    results = await read_results_jsonl(run_id)
    if results and all(r.get("status") == "error" for r in results):
        first_error = results[0].get("error")
        raise RuntimeError(first_error or "All jobs failed in Python pipeline")

    return results


def _new_queue_item(run_id: str, job: dict, mode: str, existing: dict | None, now: str) -> dict:
    job_key = f"{job.get('provider', '')}|{job.get('source_key', '')}|{job.get('job_id', '')}"
    return {
        "id": f"{run_id}:{job_key}",
        "job_key": job_key,
        "title": job.get("title") or "Unknown job",
        "company": job.get("company") or company_name(job),
        "mode": mode,
        "status": "running",
        "subtasks": queue_store.build_subtasks(mode),
        "attempt": existing["attempt"] if existing else 1,
        "max_attempts": existing["max_attempts"] if existing else 3,
        "next_retry_at": None,
        "created_at": existing["created_at"] if existing else now,
        "updated_at": now,
        "error": None,
        "score": existing.get("score") if existing else None,
    }


async def _run_scored_pipeline(run_id: str, manifest: dict, jobs_for_queue: list[dict], mode: str) -> None:
    now = _now_iso()
    existing_by_id = {i["id"]: i for i in await queue_store.read_queue()}
    queue_items: list[dict] = []
    for job in jobs_for_queue or [
        {"provider": "", "source_key": run_id, "job_id": run_id, "title": "Unknown job"}
    ]:
        job_key = f"{job.get('provider', '')}|{job.get('source_key', '')}|{job.get('job_id', '')}"
        item = _new_queue_item(run_id, job, mode, existing_by_id.get(f"{run_id}:{job_key}"), now)
        queue_items.append(item)

    await asyncio.gather(*(queue_store.upsert_queue_item(i) for i in queue_items))

    try:
        results = await run_scorer_phase(run_id, mode, queue_items)
        matched_count = len([r for r in results if r.get("status") == "ok"])

        discord_started = _now_iso()
        for item in queue_items:
            item["subtasks"] = [
                {**s, "status": "running", "started_at": discord_started} if s["id"] == "discord" else s
                for s in item["subtasks"]
            ]
            item["updated_at"] = discord_started
            await queue_store.upsert_queue_item(item)

        await persist_run_results(results, run_id, notify_discord_for_score)

        score_by_job_key: dict[str, float] = {}
        for row in results:
            if row.get("status") == "ok":
                key = f"{row.get('provider')}|{row.get('source_key')}|{row.get('job_id')}"
                score5 = (row.get("analysis") or {}).get("score_5")
                if score5 is not None:
                    score_by_job_key[key] = score5

        finished_at = _now_iso()
        for item in queue_items:
            item["status"] = "done"
            score = score_by_job_key.get(item["job_key"])
            if score is not None:
                item["score"] = score
            item["subtasks"] = [
                {**s, "status": "done", "finished_at": finished_at} if s["id"] == "discord" else s
                for s in item["subtasks"]
            ]
            item["updated_at"] = finished_at
            await queue_store.upsert_queue_item(item)

        manifest = {
            **manifest,
            "status": STATUS_COMPLETED,
            "matched_count": matched_count,
            "error": None,
            "updated_at": _now_iso(),
        }
        await write_manifest(run_id, manifest)
    except Exception as exc:
        err_msg = str(exc)
        for item in queue_items:
            next_attempt = item["attempt"] + 1
            is_permanent = next_attempt >= item["max_attempts"]
            item["attempt"] = next_attempt
            item["status"] = "permanent_error" if is_permanent else "retrying"
            item["next_retry_at"] = None if is_permanent else queue_store.next_retry_at(next_attempt)
            item["error"] = err_msg
            item["subtasks"] = [
                {**s, "status": "error"} if s.get("status") in ("todo", "running") else s
                for s in item["subtasks"]
            ]
            item["updated_at"] = _now_iso()
            await queue_store.upsert_queue_item(item)

        manifest = {**manifest, "status": STATUS_FAILED, "error": err_msg, "updated_at": _now_iso()}
        await write_manifest(run_id, manifest)


async def execute_match_run(run_id: str, jobs: list[dict], mode: str) -> None:
    manifest = await read_manifest(run_id)
    if manifest is None:
        return

    active_run_ids.add(run_id)
    try:
        manifest = {**manifest, "status": STATUS_RUNNING, "updated_at": _now_iso()}
        await write_manifest(run_id, manifest)

        manifest = await write_batch_input(run_id, jobs, manifest)
        await write_manifest(run_id, manifest)

        await _run_scored_pipeline(run_id, manifest, jobs, mode)
    finally:
        active_run_ids.discard(run_id)


async def execute_match_run_from_input(run_id: str, input_lines: list[str], mode: str) -> None:
    manifest = await read_manifest(run_id)
    if manifest is None:
        return

    active_run_ids.add(run_id)
    try:
        async with aiofiles.open(match_run_input_path(run_id), "w", encoding="utf-8") as f:
            await f.write("\n".join(input_lines) + "\n")

        jobs_for_queue: list[dict] = []
        for line in input_lines:
            try:
                jobs_for_queue.append(json.loads(line))
            except ValueError:
                continue

        manifest = {**manifest, "status": STATUS_RUNNING, "updated_at": _now_iso()}
        await write_manifest(run_id, manifest)

        await _run_scored_pipeline(run_id, manifest, jobs_for_queue, mode)
    finally:
        active_run_ids.discard(run_id)
