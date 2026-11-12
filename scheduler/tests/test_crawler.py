import subprocess

import config
import crawler
import state


def test_run_crawler_success_records_run_and_does_not_notify(monkeypatch, runs_file):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        stdout = "" if cmd[:2] == ["docker", "ps"] else "ok"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    notified = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: notified.append(msg))

    crawler.run_crawler()

    assert captured["cmd"] == [
        "docker",
        "compose",
        "-f",
        f"{config.PROJECT_DIR}/docker-compose.yml",
        "--project-directory",
        config.PROJECT_DIR,
        "run",
        "--rm",
        config.CRAWLER_SERVICE_NAME,
    ]
    assert notified == []
    assert len(state.read_runs()) == 1


def test_run_crawler_sets_pwd_to_project_dir_for_compose_interpolation(monkeypatch, runs_file):
    # docker-compose.yml interpolates ${PWD}; exec-form containers have no PWD.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setenv("SOME_VAR", "kept")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: None)

    crawler.run_crawler()

    assert captured["env"]["PWD"] == config.PROJECT_DIR
    assert captured["env"]["SOME_VAR"] == "kept"


def test_run_crawler_failure_logs_and_notifies_but_still_records_run(monkeypatch, runs_file):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="boom: stack trace")

    notified = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: notified.append(msg))

    crawler.run_crawler()

    assert notified == ["boom: stack trace"]
    assert len(state.read_runs()) == 1


def test_run_crawler_timeout_is_caught_and_notifies(monkeypatch, runs_file):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=3600)

    notified = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: notified.append(msg))

    crawler.run_crawler()  # must not raise

    assert len(notified) == 1
    assert "timed out" in notified[0]
    assert len(state.read_runs()) == 1


def test_run_crawler_missing_docker_binary_is_caught_and_notifies(monkeypatch, runs_file):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")

    notified = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: notified.append(msg))

    crawler.run_crawler()  # must not raise — FileNotFoundError is an OSError

    assert len(notified) == 1
    assert "Could not invoke docker" in notified[0]
    assert len(state.read_runs()) == 1


def test_run_crawler_skips_while_a_crawler_container_is_already_running(monkeypatch, runs_file):
    # A manual `docker compose run crawler` writing the catalog at the same time
    # as a scheduled one corrupted catalog.sqlite; never start a second crawl.
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="3f2a9c1b\n", stderr="")

    notified = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(crawler, "send_failure_notification", lambda msg: notified.append(msg))

    crawler.run_crawler()

    assert len(calls) == 1
    assert calls[0][:2] == ["docker", "ps"]
    assert "label=com.docker.compose.service=crawler" in calls[0]
    assert "label=com.docker.compose.project=project" in calls[0]
    assert notified == []
    # Skipped, not run: the next tick should try again rather than be debounced.
    assert state.read_runs() == []
