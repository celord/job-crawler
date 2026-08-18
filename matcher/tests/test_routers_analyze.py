import dataclasses

import pytest
from fastapi.testclient import TestClient

import main
from routers import analyze


@pytest.fixture
def client(monkeypatch, profile_dir):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, career_ops_dir=str(profile_dir)))
    with TestClient(main.app) as c:
        yield c


def test_analyze_quick_success(client, monkeypatch):
    async def fake_score(job, profile_data, model):
        return {"score": 80, "pipeline": "maverick"}

    monkeypatch.setattr(analyze, "score_job_quick", fake_score)

    resp = client.post(
        "/analyze/quick",
        json={"jobs": [{"provider": "greenhouse", "source_key": "acme", "job_id": "1", "title": "TPM"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "ok"
    assert body["results"][0]["analysis"]["score"] == 80
    assert body["run_id"].startswith("run_")


def test_analyze_quick_partial_failure(client, monkeypatch):
    async def fake_score(job, profile_data, model):
        raise RuntimeError("LLM timeout")

    monkeypatch.setattr(analyze, "score_job_quick", fake_score)

    resp = client.post(
        "/analyze/quick", json={"jobs": [{"provider": "greenhouse", "source_key": "acme", "job_id": "1"}]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["status"] == "error"
    assert "LLM timeout" in body["results"][0]["error"]


def test_analyze_ensemble_sequential(client, monkeypatch):
    async def fake_score(job, profile_data):
        return {"score": 90, "pipeline": "ensemble"}

    monkeypatch.setattr(analyze, "score_job_ensemble", fake_score)
    monkeypatch.setattr(
        analyze, "settings", dataclasses.replace(analyze.settings, ensemble_job_concurrency=1)
    )

    resp = client.post(
        "/analyze/ensemble", json={"jobs": [{"provider": "lever", "source_key": "acme", "job_id": "1"}]}
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["analysis"]["score"] == 90


def test_analyze_ensemble_bounded_concurrency(client, monkeypatch):
    async def fake_score(job, profile_data):
        return {"score": 90, "pipeline": "ensemble"}

    monkeypatch.setattr(analyze, "score_job_ensemble", fake_score)
    monkeypatch.setattr(
        analyze, "settings", dataclasses.replace(analyze.settings, ensemble_job_concurrency=3)
    )

    jobs = [{"provider": "lever", "source_key": "acme", "job_id": str(i)} for i in range(3)]
    resp = client.post("/analyze/ensemble", json={"jobs": jobs})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 3
    assert [r["job_id"] for r in body["results"]] == ["0", "1", "2"]
