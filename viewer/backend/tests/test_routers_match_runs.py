import time

import pytest

from tests.conftest import insert_job

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
for j in jobs:
    print(f"[ensemble] {j['company']} | {j['title']} | start", file=sys.stderr, flush=True)
    results.append({
        "status": "ok", "provider": j["provider"], "source_key": j["source_key"], "job_id": j["job_id"],
        "analysis": {"score_5": 4.5, "pipeline": "claude-ensemble"},
    })
with open(args.results_jsonl, "w") as f:
    for r in results:
        f.write(json.dumps(r) + "\\n")
"""


@pytest.fixture
def env_with_matcher(migrated_env):
    (migrated_env["matcher_dir"] / "ensemble_runner.py").write_text(ENSEMBLE_SCRIPT)
    (migrated_env["matcher_dir"] / "job_fit_analyzer.py").write_text(ENSEMBLE_SCRIPT)
    return migrated_env


def _wait_for_status(client, run_id, timeout=10):
    deadline = time.time() + timeout
    manifest = None
    while time.time() < deadline:
        manifest = client.get(f"/api/match-runs/{run_id}").json()
        if manifest["status"] in ("completed", "failed"):
            return manifest
        time.sleep(0.05)
    return manifest


def test_create_match_run_returns_202_and_dedups(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post(
        "/api/match-runs",
        json={
            "job_keys": ["gh|acme|1", "gh|acme|1"],
            "mode": "claude-ensemble",
        },
    )
    assert r.status_code == 202
    body = r.json()
    assert body["manifest"]["job_count"] == 1

    manifest = _wait_for_status(app_client, body["run_id"])
    assert manifest["status"] == "completed"
    assert manifest["matched_count"] == 1


def test_create_match_run_unknown_job_key_yields_zero_jobs(app_client, env_with_matcher):
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|nope"], "mode": "claude"})
    assert r.status_code == 202
    assert r.json()["manifest"]["job_count"] == 0


def test_get_match_run_not_found(app_client):
    r = app_client.get("/api/match-runs/does-not-exist")
    assert r.status_code == 404


def test_get_match_run_is_active_flag(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude-ensemble"})
    run_id = r.json()["run_id"]
    manifest = _wait_for_status(app_client, run_id)
    assert manifest["is_active"] is False


def test_get_match_run_results_empty_before_completion(app_client):
    r = app_client.get("/api/match-runs/nonexistent/results")
    assert r.status_code == 200
    assert r.json() == []


def test_get_match_run_results_after_completion(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude-ensemble"})
    run_id = r.json()["run_id"]
    _wait_for_status(app_client, run_id)
    results = app_client.get(f"/api/match-runs/{run_id}/results").json()
    assert len(results) == 1
    assert results[0]["status"] == "ok"


def test_create_match_run_with_jd_skips_parse_phase(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post(
        "/api/match-runs-with-jd",
        json={
            "provider": "gh",
            "source_key": "acme",
            "job_id": "1",
            "jd_text": "custom jd text",
            "mode": "claude-ensemble",
        },
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    manifest = _wait_for_status(app_client, run_id)
    assert manifest["status"] == "completed"

    from services.match_run import match_run_log_path

    log_text = match_run_log_path(run_id).read_text()
    assert "[parse]" not in log_text


def test_create_match_run_with_jd_unknown_job_still_runs(app_client, env_with_matcher):
    r = app_client.post(
        "/api/match-runs-with-jd",
        json={
            "provider": "gh",
            "source_key": "acme",
            "job_id": "999",
            "jd_text": "custom jd text",
            "mode": "claude",
        },
    )
    assert r.status_code == 202
