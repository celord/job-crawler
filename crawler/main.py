"""Entrypoint. Absorbs docker-entrypoint.sh's lock-file `trap cleanup EXIT
INT TERM` responsibility and the post-crawl.sh handoff directly into
Python -- there is no separate shell wrapper or compiled-JS CLI step
anymore.

Lock-file and progress-file cleanup run on both success and failure
(a deliberate improvement over the pre-migration cli.ts, which only
unlinked the progress file on success -- leaving a stale "still running"
progress file behind after a crash was undesirable). post_crawl.py and
trend_log.py only run after a successful crawl, matching the shell
version's `&& post-crawl.sh` short-circuit.
"""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import post_crawl
from config import parse_args
from runner import run_crawler
from trend_log import append_trend_entry

LOCK_HEARTBEAT_S = 60
# A lock this old was left behind by a crawl that died without cleaning up.
LOCK_STALE_S = 10 * 60


def _lock_path() -> str:
    return os.environ.get("CRAWLER_ACTIVE_LOCK_PATH", "/app/state/crawler-active.lock")


def _write_lock(lock_path: Path) -> None:
    lock_path.write_text(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")


def _held_by_another_crawl(lock_path: Path) -> bool:
    try:
        return time.time() - lock_path.stat().st_mtime < LOCK_STALE_S
    except FileNotFoundError:
        return False


async def _keep_lock_fresh(lock_path: Path) -> None:
    while True:
        await asyncio.sleep(LOCK_HEARTBEAT_S)
        _write_lock(lock_path)


async def main() -> int:
    options = parse_args(sys.argv[1:])
    lock_path = Path(_lock_path())
    # Two crawls writing catalog.sqlite at once have corrupted it before.
    if _held_by_another_crawl(lock_path):
        print(f"Another crawl is running ({lock_path} is fresh); not starting.", file=sys.stderr)
        return 1
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _write_lock(lock_path)
    heartbeat = asyncio.create_task(_keep_lock_fresh(lock_path))

    report = None
    try:
        report = await run_crawler(options)
    except Exception as error:  # noqa: BLE001 -- top-level crawl failure, reported then exits non-zero
        print(str(error), file=sys.stderr)
    finally:
        heartbeat.cancel()
        lock_path.unlink(missing_ok=True)
        Path(options.progress_file).unlink(missing_ok=True)

    if report is None:
        return 1

    exclude_path = str(Path(options.catalog_db).parent / "exclude.jsonl")
    try:
        post_crawl.run(options.report, exclude_path)
    except Exception as error:  # noqa: BLE001
        print(f"[post-crawl] failed: {error}", file=sys.stderr)
        return 1

    try:
        append_trend_entry(options.catalog_db, str(Path(options.catalog_db).parent))
    except Exception as error:  # noqa: BLE001 -- non-fatal, matches cli.ts's swallowed catch
        print(f"[trend-log] failed: {error}", file=sys.stderr)

    print(
        json.dumps(
            {
                "started_at": report.started_at,
                "ended_at": report.ended_at,
                "total_jobs": report.total_jobs,
                "failures": len(report.failures),
                "out": options.out,
                "report": options.report,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
