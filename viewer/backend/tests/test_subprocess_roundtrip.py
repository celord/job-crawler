"""Story 9.2 — Subprocess Round-Trip Test.

Verifies parse -> score -> persist end-to-end through the real
POST /api/match-runs router, against a real catalog_jobs row, using fake
(but structurally faithful) job_post_parser.py/job_fit_analyzer.py
subprocess scripts in place of live LLM calls -- this test is about proving
the pipeline's wiring is correct, not about LLM output quality, matching
the story's own goal ("Verify parse -> score -> persist works
end-to-end").
"""

import json
import time

import pytest

from tests.conftest import insert_job

PARSER_SCRIPT = """
import argparse, json
p = argparse.ArgumentParser()
p.add_argument("--url", required=True)
args = p.parse_args()
print(json.dumps({"title": "Parsed Title", "location": "Remote", "provider": "greenhouse"}))
"""

SCORER_SCRIPT = """
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
    results.append({"status": "ok", "provider": job["provider"], "source_key": job["source_key"],
                     "job_id": job["job_id"], "analysis": {"score_5": 4.5, "pipeline": "claude"}})
with open(args.results_jsonl, "w") as f:
    for r in results:
        f.write(json.dumps(r) + "\\n")
"""


@pytest.fixture
def env(migrated_env):
    (migrated_env["matcher_dir"] / "job_post_parser.py").write_text(PARSER_SCRIPT)
    (migrated_env["matcher_dir"] / "job_fit_analyzer.py").write_text(SCORER_SCRIPT)
    return migrated_env


def _wait_for_completion(client, run_id, timeout_s=120):
    deadline = time.time() + timeout_s
    manifest = None
    while time.time() < deadline:
        manifest = client.get(f"/api/match-runs/{run_id}").json()
        if manifest["status"] in ("completed", "failed"):
            return manifest
        time.sleep(0.05)
    return manifest


def test_full_round_trip_parse_score_persist(app_client, env):
    insert_job(
        env["catalog_db"],
        provider="greenhouse",
        source_key="acme",
        job_id="1",
        title="Backend Engineer",
        job_url="https://example.com/job/1",
    )

    r = app_client.post("/api/match-runs", json={"job_keys": ["greenhouse|acme|1"], "mode": "claude"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    started = time.time()
    manifest = _wait_for_completion(app_client, run_id, timeout_s=120)
    elapsed = time.time() - started

    assert elapsed < 120, f"round trip took {elapsed:.1f}s"
    assert manifest["status"] == "completed", manifest

    # analysis_score is written to catalog_jobs after completion
    row = fetchone_sync(env["catalog_db"], "greenhouse", "acme", "1")
    assert row["analysis_score"] == 4.5

    # parsed_jd is populated after the parse phase
    assert row["parsed_jd"] is not None
    parsed = json.loads(row["parsed_jd"])
    assert parsed["title"] == "Parsed Title"

    # results.jsonl exists with at least one status: "ok" row
    from services.match_run import match_run_results_path

    results_text = match_run_results_path(run_id).read_text()
    results = [json.loads(line) for line in results_text.splitlines() if line.strip()]
    assert any(r["status"] == "ok" for r in results)

    # GET /api/job reflects the same analysis
    job = app_client.get(
        "/api/job", params={"provider": "greenhouse", "source_key": "acme", "job_id": "1"}
    ).json()
    assert job["analysis"]["score_5"] == 4.5


def fetchone_sync(catalog_db, provider, source_key, job_id):
    import sqlite3

    conn = sqlite3.connect(str(catalog_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT analysis_score, parsed_jd FROM catalog_jobs "
        "WHERE provider = ? AND source_key = ? AND job_id = ?",
        (provider, source_key, job_id),
    ).fetchone()
    conn.close()
    return row
