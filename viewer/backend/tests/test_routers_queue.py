import asyncio
import json
import time

import httpx
import pytest

import main
from services import matcher_client
from tests.conftest import insert_job


async def _fake_parse_batch(urls):
    return [{"url": u, "parsed": {"title": "Parsed"}, "parse_error": None} for u in urls]


async def _fake_analyze(mode, jobs, run_id):
    return [
        {
            "status": "ok",
            "provider": j["provider"],
            "source_key": j["source_key"],
            "job_id": j["job_id"],
            "analysis": {"score_5": 4.5, "pipeline": mode},
        }
        for j in jobs
    ]


async def _slow_analyze(mode, jobs, run_id):
    import asyncio

    await asyncio.sleep(5)
    return []


@pytest.fixture
def env_with_matcher(migrated_env, monkeypatch):
    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", _fake_analyze)
    return migrated_env


def _wait_for(predicate, timeout=10):
    deadline = time.time() + timeout
    result = None
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    return result


def test_get_queue_empty(app_client):
    r = app_client.get("/api/queue")
    assert r.status_code == 200
    assert r.json() == []


def test_retry_not_found(app_client):
    r = app_client.post("/api/queue/does-not-exist/retry")
    assert r.status_code == 404


def _seed_queue_file(state_dir, items):
    """Writes directly to the queue JSON file instead of going through
    queue_store's asyncio.Lock — that lock binds to whichever event loop
    first acquires it, and TestClient runs the app (and thus queue_store)
    on its own portal-thread loop, so awaiting queue_store from the test's
    own loop in the same test would crash with a cross-loop error."""
    import json

    (state_dir / "retry-queue.json").write_text(json.dumps(items))


def test_retry_discord_only_does_not_rerun_analysis(app_client, migrated_env):
    import json

    item_id = "discord_x"
    row = {"provider": "gh", "source_key": "acme", "job_id": "1"}
    _seed_queue_file(
        migrated_env["state_dir"],
        [
            {
                "id": item_id,
                "job_key": "gh|acme|1",
                "mode": "discord-only",
                "status": "retrying",
                "subtasks": [{"id": "discord", "status": "error"}],
                "attempt": 1,
                "max_attempts": 3,
                "created_at": "x",
                "updated_at": "x",
                "error": json.dumps(row),
                "next_retry_at": None,
            }
        ],
    )
    r = app_client.post(f"/api/queue/{item_id}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "retrying"
    assert body["next_retry_at"] is not None


def test_retry_normal_item_resets_attempt_and_reruns(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude"})
    run_id = r.json()["run_id"]
    item_id = f"{run_id}:gh|acme|1"

    _wait_for(
        lambda: app_client.get("/api/queue").json()
        and app_client.get("/api/queue").json()[0]["status"] == "done"
    )

    rr = app_client.post(f"/api/queue/{item_id}/retry")
    assert rr.status_code == 200
    assert rr.json()["attempt"] == 0

    _wait_for(lambda: app_client.get("/api/queue").json()[0]["status"] == "done")
    final = app_client.get("/api/queue").json()[0]
    assert final["status"] == "done"


def test_stop_cancels_matcher_run_and_flips_manifest(app_client, migrated_env, monkeypatch):
    cancelled = []

    async def fake_cancel_run(run_id):
        cancelled.append(run_id)
        return True

    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", _slow_analyze)
    monkeypatch.setattr(matcher_client, "cancel_run", fake_cancel_run)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude"})
    run_id = r.json()["run_id"]
    item_id = f"{run_id}:gh|acme|1"

    _wait_for(lambda: app_client.get("/api/queue").json())

    sr = app_client.post(f"/api/queue/{item_id}/stop")
    assert sr.status_code == 200
    assert sr.json()["status"] == "permanent_error"
    assert cancelled == [run_id]

    manifest = app_client.get(f"/api/match-runs/{run_id}").json()
    assert manifest["status"] in ("failed", "running", "completed")


def test_restart_resets_created_at(app_client, env_with_matcher):
    insert_job(env_with_matcher["catalog_db"], provider="gh", source_key="acme", job_id="1")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude"})
    run_id = r.json()["run_id"]
    item_id = f"{run_id}:gh|acme|1"

    _wait_for(
        lambda: app_client.get("/api/queue").json()
        and app_client.get("/api/queue").json()[0]["status"] == "done"
    )
    original = app_client.get("/api/queue").json()[0]

    rr = app_client.post(f"/api/queue/{item_id}/restart")
    assert rr.status_code == 200
    assert rr.json()["attempt"] == 1
    assert rr.json()["created_at"] != original["created_at"]

    _wait_for(lambda: app_client.get("/api/queue").json()[0]["status"] == "done")


def test_delete_removes_item(app_client, migrated_env):
    _seed_queue_file(migrated_env["state_dir"], [{"id": "x", "status": "done"}])
    r = app_client.delete("/api/queue/x")
    assert r.status_code == 200
    assert app_client.get("/api/queue").json() == []


def _read_error_log(state_dir):
    import json as json_module

    path = state_dir / "errors.jsonl"
    if not path.exists():
        return []
    return [json_module.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_failed_match_run_logs_one_error_line_per_job(app_client, monkeypatch, migrated_env):
    async def dead_matcher(mode, jobs, run_id):
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", dead_matcher)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="2")
    r = app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1", "gh|acme|2"], "mode": "claude"})
    run_id = r.json()["run_id"]

    assert _wait_for(
        lambda: app_client.get("/api/queue").json()
        and all(i["status"] == "permanent_error" for i in app_client.get("/api/queue").json())
    )
    entries = _read_error_log(migrated_env["state_dir"])
    assert len(entries) == 2
    for entry in entries:
        assert entry["level"] == "error"
        assert entry["stage"] == "quick scoring"
        assert entry["error_type"] == "ConnectError"
        assert "All connection attempts failed" in entry["message"]
        assert entry["run_id"] == run_id
        assert entry["pipeline"] == "quick"
    assert {e["job_key"] for e in entries} == {"gh|acme|1", "gh|acme|2"}


def test_match_run_row_errors_are_logged(app_client, monkeypatch, migrated_env):
    async def partial_analyze(mode, jobs, run_id):
        return [
            {
                "status": "ok",
                "provider": "gh",
                "source_key": "acme",
                "job_id": "1",
                "analysis": {"score_5": 4.0, "pipeline": mode},
            },
            {
                "status": "error",
                "provider": "gh",
                "source_key": "acme",
                "job_id": "2",
                "error": "model meta/x exploded",
                "error_type": "RetryableHttpError",
                "http_status": 429,
                "model": "meta/x",
            },
        ]

    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", partial_analyze)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="2")
    app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1", "gh|acme|2"], "mode": "claude"})

    assert _wait_for(
        lambda: app_client.get("/api/queue").json()
        and all(i["status"] == "done" for i in app_client.get("/api/queue").json())
    )
    entries = _read_error_log(migrated_env["state_dir"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "error"
    assert entry["job_key"] == "gh|acme|2"
    assert entry["stage"] == "quick scoring"
    assert entry["error_type"] == "RetryableHttpError"
    assert entry["http_status"] == 429
    assert entry["model"] == "meta/x"
    assert "exploded" in entry["message"]


def test_ensemble_scorer_skips_logged_as_warnings(app_client, monkeypatch, migrated_env):
    async def ensemble_with_skip(mode, jobs, run_id):
        return [
            {
                "status": "ok",
                "provider": "gh",
                "source_key": "acme",
                "job_id": "1",
                "analysis": {"score_5": 4.0, "pipeline": mode},
                "scorer_skips": ["moonshotai/kimi-k2.6"],
            }
        ]

    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", ensemble_with_skip)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude-ensemble"})

    assert _wait_for(
        lambda: app_client.get("/api/queue").json()
        and app_client.get("/api/queue").json()[0]["status"] == "done"
    )
    entries = _read_error_log(migrated_env["state_dir"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "warning"
    assert entry["stage"] == "moonshotai/kimi-k2.6"
    assert entry["model"] == "moonshotai/kimi-k2.6"
    assert entry["pipeline"] == "ensemble"


def test_parse_failure_logged_as_warning(app_client, monkeypatch, migrated_env):
    async def failing_parse(urls):
        return [{"url": u, "parsed": {}, "parse_error": "JD fetch timed out"} for u in urls]

    monkeypatch.setattr(matcher_client, "parse_batch", failing_parse)
    monkeypatch.setattr(matcher_client, "analyze", _fake_analyze)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude"})

    assert _wait_for(
        lambda: app_client.get("/api/queue").json()
        and app_client.get("/api/queue").json()[0]["status"] == "done"
    )
    entries = _read_error_log(migrated_env["state_dir"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "warning"
    assert entry["stage"] == "parse"
    assert "JD fetch timed out" in entry["message"]


def test_persistence_failure_is_logged(app_client, monkeypatch, migrated_env):
    async def broken_persist(results, run_id, notify_fn):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(matcher_client, "parse_batch", _fake_parse_batch)
    monkeypatch.setattr(matcher_client, "analyze", _fake_analyze)
    monkeypatch.setattr("services.match_run.persist_run_results", broken_persist)
    insert_job(migrated_env["catalog_db"], provider="gh", source_key="acme", job_id="1")
    app_client.post("/api/match-runs", json={"job_keys": ["gh|acme|1"], "mode": "claude"})

    assert _wait_for(
        lambda: app_client.get("/api/queue").json()
        and app_client.get("/api/queue").json()[0]["status"] == "permanent_error"
    )
    entries = _read_error_log(migrated_env["state_dir"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["level"] == "error"
    assert entry["stage"] == "persistence"
    assert entry["error_type"] == "RuntimeError"
    assert "database is locked" in entry["message"]


def _seed_statuses(state_dir):
    _seed_queue_file(
        state_dir,
        [
            {"id": "run_a:greenhouse|acme|1", "status": "done"},
            {"id": "run_a:greenhouse|acme|2", "status": "running"},
            {"id": "run_a:greenhouse|acme|3", "status": "todo"},
            {"id": "run_a:greenhouse|acme|4", "status": "retrying"},
            {"id": "run_a:greenhouse|acme|5", "status": "error"},
            {"id": "run_a:greenhouse|acme|6", "status": "permanent_error"},
        ],
    )


def test_clear_finished_removes_only_finished_items(app_client, migrated_env):
    _seed_statuses(migrated_env["state_dir"])
    r = app_client.post("/api/queue/clear", json={"scope": "finished"})
    assert r.status_code == 200
    assert r.json()["removed"] == 1
    statuses = {i["status"] for i in app_client.get("/api/queue").json()}
    assert statuses == {"running", "todo", "retrying", "error", "permanent_error"}


def test_clear_all_removes_everything_not_running(app_client, migrated_env):
    _seed_statuses(migrated_env["state_dir"])
    r = app_client.post("/api/queue/clear", json={"scope": "all"})
    assert r.status_code == 200
    assert r.json()["removed"] == 5
    assert [i["status"] for i in app_client.get("/api/queue").json()] == ["running"]


def test_clear_invalid_scope_is_rejected(app_client, migrated_env):
    r = app_client.post("/api/queue/clear", json={"scope": "everything"})
    assert r.status_code == 400


def test_cleared_items_do_not_reappear_after_backend_restart(migrated_env):
    from fastapi.testclient import TestClient

    _seed_statuses(migrated_env["state_dir"])
    with TestClient(main.app) as first_client:
        r = first_client.post("/api/queue/clear", json={"scope": "finished"})
        assert r.json()["removed"] == 1

    # A fresh TestClient re-runs the app lifespan (mark_orphaned_runs_failed
    # included) like a container restart would.
    with TestClient(main.app) as restarted_client:
        items = restarted_client.get("/api/queue").json()
    statuses = {i["status"] for i in items}
    assert "done" not in statuses
    # The running item may be flipped to permanent_error by the orphan sweep,
    # but no cleared (done) item may come back.
    assert statuses <= {"running", "todo", "retrying", "error", "permanent_error"}


def test_download_queue_errors_returns_valid_jsonl_attachment(app_client, migrated_env):
    from services import error_log

    asyncio.run(error_log.append_error({"job_key": "gh|acme|1", "pipeline": "quick", "stage": "s1"}))
    asyncio.run(error_log.append_error({"job_key": "gh|acme|1", "pipeline": "quick", "stage": "s2"}))

    r = app_client.get("/api/queue/errors")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith('attachment; filename="queue-errors-')
    assert r.headers["content-disposition"].endswith('.jsonl"')
    lines = [line for line in r.text.splitlines() if line.strip()]
    assert len(lines) == 2
    assert [json.loads(line)["stage"] for line in lines] == ["s1", "s2"]


def test_download_queue_errors_supports_since_and_limit(app_client, migrated_env):
    from services import error_log

    async def seed():
        await error_log.append_error({"job_key": "gh|acme|1", "stage": "a"})
        await error_log.append_error({"job_key": "gh|acme|1", "stage": "b"})

    asyncio.run(seed())
    entries = asyncio.run(error_log.read_errors())

    r = app_client.get("/api/queue/errors", params={"limit": 1})
    assert [json.loads(line)["stage"] for line in r.text.splitlines()] == ["b"]

    r = app_client.get("/api/queue/errors", params={"since": entries[1]["timestamp"]})
    assert [json.loads(line)["stage"] for line in r.text.splitlines()] == ["b"]


def test_download_queue_errors_rejects_bad_since(app_client, migrated_env):
    r = app_client.get("/api/queue/errors", params={"since": "not-a-timestamp"})
    assert r.status_code == 400


def test_clear_error_log_endpoint(app_client, migrated_env):
    from services import error_log

    asyncio.run(error_log.append_error({"job_key": "gh|acme|1", "stage": "x"}))
    r = app_client.post("/api/queue/errors/clear")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "removed": 1}
    assert app_client.get("/api/queue/errors").text == ""
