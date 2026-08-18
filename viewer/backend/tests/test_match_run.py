import json
import re

import pytest

from services import match_run as mr
from services import queue_store
from tests.conftest import insert_job

pytestmark = pytest.mark.usefixtures("isolated_env")


def test_generate_run_id_matches_expected_format():
    run_id = mr.generate_run_id()
    assert re.fullmatch(r"run_\d{14}_[a-z0-9]{6}", run_id)


def test_match_run_paths(isolated_env):
    run_id = "run_x"
    d = mr.match_run_dir(run_id)
    assert mr.match_run_manifest_path(run_id) == d / "manifest.json"
    assert mr.match_run_input_path(run_id) == d / "jobs.jsonl"
    assert mr.match_run_results_path(run_id) == d / "results.jsonl"
    assert mr.match_run_log_path(run_id) == d / "matcher.log"


async def test_write_and_read_manifest_round_trip():
    manifest = {"id": "run_1", "status": "pending"}
    await mr.write_manifest("run_1", manifest)
    assert await mr.read_manifest("run_1") == manifest

    raw = mr.match_run_manifest_path("run_1").read_text()
    assert raw.endswith("\n")
    assert raw.startswith("{\n")  # indent=2 pretty-printed


async def test_read_manifest_missing_returns_none():
    assert await mr.read_manifest("does-not-exist") is None


async def test_mark_orphaned_runs_failed_flips_running_manifest():
    await mr.write_manifest("run_a", {"id": "run_a", "status": "running"})
    await mr.write_manifest("run_b", {"id": "run_b", "status": "completed"})
    await mr.mark_orphaned_runs_failed()
    a = await mr.read_manifest("run_a")
    b = await mr.read_manifest("run_b")
    assert a["status"] == "failed"
    assert a["error"] == "orphaned: server restarted"
    assert b["status"] == "completed"  # untouched


async def test_mark_orphaned_runs_failed_flips_stuck_queue_items():
    await queue_store.upsert_queue_item(
        {
            "id": "run_a:x|y|1",
            "status": "running",
            "subtasks": [{"id": "discord", "status": "running"}],
        }
    )
    await queue_store.upsert_queue_item(
        {
            "id": "run_a:x|y|2",
            "status": "todo",
            "subtasks": [{"id": "discord", "status": "todo"}],
        }
    )
    await queue_store.upsert_queue_item(
        {
            "id": "run_a:x|y|3",
            "status": "done",
            "subtasks": [{"id": "discord", "status": "done"}],
        }
    )
    await mr.mark_orphaned_runs_failed()
    items = {i["id"]: i for i in await queue_store.read_queue()}
    assert items["run_a:x|y|1"]["status"] == "permanent_error"
    assert items["run_a:x|y|1"]["subtasks"][0]["status"] == "error"
    assert items["run_a:x|y|2"]["status"] == "permanent_error"
    assert items["run_a:x|y|3"]["status"] == "done"  # untouched


async def test_append_match_run_log_writes_prefixed_line():
    mr.match_run_dir("run_1").mkdir(parents=True, exist_ok=True)
    await mr.append_match_run_log("run_1", "hello", stream="stdout")
    text = mr.match_run_log_path("run_1").read_text()
    assert text == "[match-run run_1] stdout: hello\n"


def test_clean_parsed_text_collapses_whitespace_and_strips():
    assert mr.clean_parsed_text("  a   b\n c ") == "a b c"


def test_clean_parsed_text_empty_is_none():
    assert mr.clean_parsed_text("   ") is None
    assert mr.clean_parsed_text(None) is None


async def test_persist_parsed_metadata_fills_empty_location_only(isolated_env):
    insert_job(isolated_env["catalog_db"], provider="gh", source_key="acme", job_id="1", location="")
    job = {"provider": "gh", "source_key": "acme", "job_id": "1"}
    await mr.persist_parsed_metadata(job, {"location": "Miami, FL"})

    from db import fetchall

    row = (await fetchall("SELECT location FROM catalog_jobs WHERE job_id = '1'"))[0]
    assert row["location"] == "Miami, FL"


async def test_persist_parsed_metadata_does_not_overwrite_existing_location(isolated_env):
    insert_job(isolated_env["catalog_db"], provider="gh", source_key="acme", job_id="1", location="Existing")
    job = {"provider": "gh", "source_key": "acme", "job_id": "1"}
    await mr.persist_parsed_metadata(job, {"location": "Miami, FL"})

    from db import fetchall

    row = (await fetchall("SELECT location FROM catalog_jobs WHERE job_id = '1'"))[0]
    assert row["location"] == "Existing"


async def test_persist_parsed_metadata_always_overwrites_compensation(isolated_env):
    insert_job(
        isolated_env["catalog_db"], provider="gh", source_key="acme", job_id="1", compensation="$50k-$60k"
    )
    job = {"provider": "gh", "source_key": "acme", "job_id": "1"}
    await mr.persist_parsed_metadata(job, {"compensation": "$100k-$120k"})

    from db import fetchall

    row = (await fetchall("SELECT compensation FROM catalog_jobs WHERE job_id = '1'"))[0]
    assert row["compensation"] == "$100k-$120k"


def test_build_jd_text_includes_sections_and_omits_empty():
    parsed = {
        "title": "Backend Engineer",
        "location": "Remote",
        "responsibilities": ["Ship code"],
        "must_have_requirements": ["5+ years Python"],
    }
    job = {"provider": "greenhouse", "source_key": "acme", "title": "Backend Engineer"}
    text = mr.build_jd_text(parsed, job)
    assert "Title: Backend Engineer" in text
    assert "Responsibilities:\n- Ship code" in text
    assert "Requirements:\n- 5+ years Python" in text
    assert "Nice-to-have" not in text


async def test_read_input_lines_missing_file_returns_empty():
    assert await mr.read_input_lines("nope") == []


async def test_read_results_jsonl_missing_file_returns_empty():
    assert await mr.read_results_jsonl("nope") == []


async def test_read_results_jsonl_skips_malformed_lines(isolated_env):
    path = mr.match_run_results_path("run_1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"a": 1}\nnot json\n{"b": 2}\n')
    results = await mr.read_results_jsonl("run_1")
    assert results == [{"a": 1}, {"b": 2}]


def test_kill_run_no_active_process_returns_false():
    assert mr.kill_run("nope") is False


def test_scorer_subtask_id_matching():
    assert mr._scorer_subtask_id("llama-4-maverick-17b") == "scorer:maverick"
    assert mr._scorer_subtask_id("kimi-k2.6") == "scorer:kimi"
    assert mr._scorer_subtask_id("nemotron-super-49b") == "scorer:nemotron"
    assert mr._scorer_subtask_id("unknown-model") is None


def test_apply_log_line_to_item_start_marks_scorers_running():
    item = {
        "id": "x",
        "status": "running",
        "subtasks": queue_store.build_subtasks("claude-ensemble"),
    }
    label_re = mr._job_label_pattern("acme | Backend Engineer")
    changed = mr._apply_log_line_to_item(item, label_re, "[ensemble] acme | Backend Engineer | start")
    assert changed is True
    scorer = next(s for s in item["subtasks"] if s["id"] == "scorer:maverick")
    assert scorer["status"] == "running"


def test_apply_log_line_to_item_ignores_other_jobs():
    item = {"id": "x", "status": "running", "subtasks": queue_store.build_subtasks("claude-ensemble")}
    label_re = mr._job_label_pattern("acme | Backend Engineer")
    changed = mr._apply_log_line_to_item(item, label_re, "[ensemble] other | Other Job | start")
    assert changed is False


def test_apply_log_line_to_item_scorer_done_and_error():
    item = {"id": "x", "status": "running", "subtasks": queue_store.build_subtasks("claude-ensemble")}
    label_re = mr._job_label_pattern("acme | Backend Engineer")
    mr._apply_log_line_to_item(
        item, label_re, "[ensemble] acme | Backend Engineer | scorer maverick | avg=4.2"
    )
    maverick = next(s for s in item["subtasks"] if s["id"] == "scorer:maverick")
    assert maverick["status"] == "done"

    mr._apply_log_line_to_item(
        item, label_re, "[ensemble] acme | Backend Engineer | scorer kimi | error: boom"
    )
    kimi = next(s for s in item["subtasks"] if s["id"] == "scorer:kimi")
    assert kimi["status"] == "error"


def test_apply_log_line_to_item_synthesis_running_and_done():
    item = {"id": "x", "status": "running", "subtasks": queue_store.build_subtasks("claude-ensemble")}
    label_re = mr._job_label_pattern("acme | Backend Engineer")
    mr._apply_log_line_to_item(item, label_re, "[ensemble] acme | Backend Engineer | synthesizing")
    assert next(s for s in item["subtasks"] if s["id"] == "synthesis")["status"] == "running"
    mr._apply_log_line_to_item(item, label_re, "[ensemble] acme | Backend Engineer | synthesis done")
    assert next(s for s in item["subtasks"] if s["id"] == "synthesis")["status"] == "done"


# --- Subprocess-backed integration tests -----------------------------------

PARSER_SCRIPT = """
import argparse, json, sys
p = argparse.ArgumentParser()
p.add_argument("--url", required=True)
args = p.parse_args()
if "fail" in args.url:
    print("boom", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"title": "Parsed Title", "location": "Remote", "provider": "greenhouse"}))
"""

ENSEMBLE_SCRIPT = """
import argparse, json, sys
p = argparse.ArgumentParser()
p.add_argument("--jobs-jsonl", required=True)
p.add_argument("--results-jsonl", required=True)
p.add_argument("--profile-dir", required=True)
p.add_argument("--pipeline", required=True)
args = p.parse_args()
with open(args.jobs_jsonl) as f:
    jobs = [json.loads(l) for l in f.read().splitlines() if l.strip()]
results = []
for job in jobs:
    label = f"{job['company']} | {job['title']}"
    print(f"[ensemble] {label} | start", file=sys.stderr, flush=True)
    print(f"[ensemble] {label} | scorer maverick | avg=4.5", file=sys.stderr, flush=True)
    print(f"[ensemble] {label} | scorer kimi | avg=4.3", file=sys.stderr, flush=True)
    print(f"[ensemble] {label} | scorer nemotron | avg=4.6", file=sys.stderr, flush=True)
    print(f"[ensemble] {label} | synthesizing", file=sys.stderr, flush=True)
    print(f"[ensemble] {label} | synthesis done", file=sys.stderr, flush=True)
    if job.get("_force_error"):
        results.append({
            "status": "error", "provider": job["provider"], "source_key": job["source_key"],
            "job_id": job["job_id"], "error": "forced failure",
        })
    else:
        results.append({
            "status": "ok", "provider": job["provider"], "source_key": job["source_key"],
            "job_id": job["job_id"], "analysis": {"score_5": 4.5, "pipeline": "claude-ensemble"},
        })
with open(args.results_jsonl, "w") as f:
    for r in results:
        f.write(json.dumps(r) + "\\n")
"""


@pytest.fixture
def fake_matcher_scripts(migrated_env):
    matcher_dir = migrated_env["matcher_dir"]
    (matcher_dir / "job_post_parser.py").write_text(PARSER_SCRIPT)
    (matcher_dir / "ensemble_runner.py").write_text(ENSEMBLE_SCRIPT)
    (matcher_dir / "job_fit_analyzer.py").write_text(ENSEMBLE_SCRIPT)
    return migrated_env


async def test_parse_job_post_success(fake_matcher_scripts):
    parsed = await mr.parse_job_post("https://example.com/ok")
    assert parsed["title"] == "Parsed Title"


async def test_parse_job_post_failure_raises(fake_matcher_scripts):
    with pytest.raises(RuntimeError, match="boom"):
        await mr.parse_job_post("https://example.com/fail")


async def test_write_batch_input_caps_concurrency_and_skips_cached(fake_matcher_scripts, isolated_env):
    jobs = [
        {
            "provider": "gh",
            "source_key": "acme",
            "job_id": str(i),
            "title": f"Job {i}",
            "job_url": "https://example.com/ok",
        }
        for i in range(3)
    ]
    jobs.append(
        {
            "provider": "gh",
            "source_key": "acme",
            "job_id": "cached",
            "title": "Cached",
            "parsed_jd": json.dumps({"title": "Already parsed"}),
        }
    )
    jobs.append({"provider": "gh", "source_key": "acme", "job_id": "nourl", "title": "No URL"})

    mr.match_run_dir("run_1").mkdir(parents=True, exist_ok=True)
    manifest = await mr.write_batch_input("run_1", jobs, {"id": "run_1"})
    assert manifest["parsed_count"] == 4  # 3 parsed + 1 cached; nourl fails

    lines = mr.match_run_input_path("run_1").read_text().splitlines()
    assert len(lines) == 5
    parsed_lines = [json.loads(line) for line in lines]
    nourl = next(line for line in parsed_lines if line["job_id"] == "nourl")
    assert nourl["parse_error"] == "Missing job URL"
    cached = next(line for line in parsed_lines if line["job_id"] == "cached")
    assert cached["parse_error"] is None
    assert cached["title"] == "Already parsed"


async def test_run_scorer_phase_success(fake_matcher_scripts, isolated_env):
    mr.match_run_input_path("run_1").parent.mkdir(parents=True, exist_ok=True)
    job_line = json.dumps(
        {"provider": "gh", "source_key": "acme", "job_id": "1", "title": "Backend", "company": "acme"}
    )
    mr.match_run_input_path("run_1").write_text(job_line + "\n")

    queue_items = [
        {
            "id": "run_1:gh|acme|1",
            "title": "Backend",
            "company": "acme",
            "subtasks": queue_store.build_subtasks("claude-ensemble"),
        }
    ]
    results = await mr.run_scorer_phase("run_1", "claude-ensemble", queue_items)
    assert results[0]["status"] == "ok"
    assert "run_1" not in mr.active_run_processes
    log_text = mr.match_run_log_path("run_1").read_text()
    assert "[match-run run_1] stderr:" in log_text


async def test_run_scorer_phase_all_error_raises(fake_matcher_scripts, isolated_env):
    mr.match_run_input_path("run_1").parent.mkdir(parents=True, exist_ok=True)
    job_line = json.dumps(
        {
            "provider": "gh",
            "source_key": "acme",
            "job_id": "1",
            "title": "Backend",
            "company": "acme",
            "_force_error": True,
        }
    )
    mr.match_run_input_path("run_1").write_text(job_line + "\n")

    queue_items = [
        {
            "id": "run_1:gh|acme|1",
            "title": "Backend",
            "company": "acme",
            "subtasks": queue_store.build_subtasks("claude-ensemble"),
        }
    ]
    with pytest.raises(RuntimeError, match="forced failure"):
        await mr.run_scorer_phase("run_1", "claude-ensemble", queue_items)


async def test_execute_match_run_from_input_end_to_end(fake_matcher_scripts, isolated_env):
    run_id = "run_e2e"
    await mr.write_manifest(
        run_id,
        {
            "id": run_id,
            "status": "pending",
            "mode": "claude-ensemble",
            "job_count": 1,
            "parsed_count": 0,
            "matched_count": 0,
            "created_at": "x",
            "updated_at": "x",
            "error": None,
        },
    )
    line = json.dumps(
        {"provider": "gh", "source_key": "acme", "job_id": "1", "title": "Backend", "company": "acme"}
    )

    await mr.execute_match_run_from_input(run_id, [line], "claude-ensemble")

    manifest = await mr.read_manifest(run_id)
    assert manifest["status"] == "completed"
    assert manifest["matched_count"] == 1
    assert run_id not in mr.active_run_ids

    items = await queue_store.read_queue()
    assert items[0]["status"] == "done"
    assert all(s["status"] == "done" for s in items[0]["subtasks"])


async def test_execute_match_run_partial_failure_does_not_raise(fake_matcher_scripts, isolated_env):
    run_id = "run_partial"
    await mr.write_manifest(
        run_id,
        {
            "id": run_id,
            "status": "pending",
            "mode": "claude-ensemble",
            "job_count": 2,
            "parsed_count": 0,
            "matched_count": 0,
            "created_at": "x",
            "updated_at": "x",
            "error": None,
        },
    )
    ok_line = json.dumps(
        {"provider": "gh", "source_key": "acme", "job_id": "1", "title": "OK", "company": "acme"}
    )
    err_line = json.dumps(
        {
            "provider": "gh",
            "source_key": "acme",
            "job_id": "2",
            "title": "Err",
            "company": "acme",
            "_force_error": True,
        }
    )

    await mr.execute_match_run_from_input(run_id, [ok_line, err_line], "claude-ensemble")
    manifest = await mr.read_manifest(run_id)
    assert manifest["status"] == "completed"
    assert manifest["matched_count"] == 1


async def test_execute_match_run_all_error_flips_manifest_failed(fake_matcher_scripts, isolated_env):
    run_id = "run_allerr"
    await mr.write_manifest(
        run_id,
        {
            "id": run_id,
            "status": "pending",
            "mode": "claude-ensemble",
            "job_count": 1,
            "parsed_count": 0,
            "matched_count": 0,
            "created_at": "x",
            "updated_at": "x",
            "error": None,
        },
    )
    err_line = json.dumps(
        {
            "provider": "gh",
            "source_key": "acme",
            "job_id": "1",
            "title": "Err",
            "company": "acme",
            "_force_error": True,
        }
    )

    await mr.execute_match_run_from_input(run_id, [err_line], "claude-ensemble")
    manifest = await mr.read_manifest(run_id)
    assert manifest["status"] == "failed"

    items = await queue_store.read_queue()
    assert items[0]["status"] == "retrying"
    assert items[0]["attempt"] == 2


async def test_execute_match_run_missing_manifest_is_noop(fake_matcher_scripts):
    await mr.execute_match_run_from_input("does-not-exist", ["{}"], "claude")
    assert await mr.read_manifest("does-not-exist") is None
