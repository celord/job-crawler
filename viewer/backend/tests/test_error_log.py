import json

import pytest

import config
from services import error_log


def _entry(**overrides):
    entry = {
        "job_key": "greenhouse|acme|1",
        "pipeline": "quick",
        "run_id": "run_1",
        "stage": "quick scoring",
        "error_type": "RuntimeError",
        "message": "matcher exploded",
    }
    entry.update(overrides)
    return entry


def test_redact_text_masks_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-FAKEKEY-123")
    redacted = error_log.redact_text("upstream rejected nvapi-FAKEKEY-123")
    assert "nvapi-FAKEKEY-123" not in redacted
    assert "***" in redacted


def test_redact_text_masks_authorization_header():
    redacted = error_log.redact_text("POST failed | Authorization: Bearer abc123secret")
    assert "abc123secret" not in redacted
    assert "Authorization" in redacted


def test_redact_text_masks_webhook_url(monkeypatch):
    webhook = "https://discord.com/api/webhooks/123456789/ABCDEF-token"
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", webhook)
    redacted = error_log.redact_text(f"discord push failed for {webhook}")
    assert "ABCDEF-token" not in redacted
    assert "***" in redacted


def test_redact_text_masks_webhook_even_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "")
    redacted = error_log.redact_text("hook https://discordapp.com/api/webhooks/1/xyz-token rejected")
    assert "xyz-token" not in redacted


def test_redact_text_masks_key_value_form_of_api_key():
    redacted = error_log.redact_text("request headers: NVIDIA_API_KEY=nvapi-LEAKED value")
    assert "nvapi-LEAKED" not in redacted


@pytest.mark.asyncio
async def test_append_error_never_writes_secrets(isolated_env, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-FAKEKEY-999")
    monkeypatch.setattr(
        config,
        "DISCORD_WEBHOOK_URL",
        "https://discord.com/api/webhooks/42/SECRET-WEBHOOK",
    )
    message = (
        "boom Authorization: Bearer header-secret-token nvapi-FAKEKEY-999 "
        "https://discord.com/api/webhooks/42/SECRET-WEBHOOK"
    )
    await error_log.append_error(_entry(message=message))

    raw = (isolated_env["state_dir"] / "errors.jsonl").read_text(encoding="utf-8")
    assert "nvapi-FAKEKEY-999" not in raw
    assert "header-secret-token" not in raw
    assert "SECRET-WEBHOOK" not in raw
    assert json.loads(raw.splitlines()[0])["message"].startswith("boom")


@pytest.mark.asyncio
async def test_append_error_truncates_message_to_about_2kb(isolated_env):
    await error_log.append_error(_entry(message="x" * 10_000))
    entry = (await error_log.read_errors())[0]
    assert len(entry["message"]) == error_log.MAX_MESSAGE_CHARS
    assert error_log.MAX_MESSAGE_CHARS <= 2 * 1024


@pytest.mark.asyncio
async def test_append_error_writes_expected_fields(isolated_env):
    await error_log.append_error(
        _entry(level="error", http_status=502, model="meta/llama-4-maverick-17b-128e-instruct")
    )
    await error_log.append_error(_entry(level="warning", stage="moonshotai/kimi-k2.6"))

    entries = await error_log.read_errors()
    assert len(entries) == 2
    first = entries[0]
    assert set(first) == {
        "timestamp",
        "level",
        "job_key",
        "pipeline",
        "run_id",
        "stage",
        "error_type",
        "message",
        "http_status",
        "model",
    }
    assert first["timestamp"].endswith("Z")
    assert first["level"] == "error"
    assert first["http_status"] == 502
    second = entries[1]
    assert second["level"] == "warning"
    assert second["stage"] == "moonshotai/kimi-k2.6"
    assert "http_status" not in second
    assert "model" not in second


@pytest.mark.asyncio
async def test_read_errors_filters_by_since_and_limits(isolated_env):
    await error_log.append_error(_entry(message="one"))
    await error_log.append_error(_entry(message="two"))
    await error_log.append_error(_entry(message="three"))
    entries = await error_log.read_errors()

    assert [e["message"] for e in await error_log.read_errors(since=entries[1]["timestamp"])] == [
        "two",
        "three",
    ]
    assert [e["message"] for e in await error_log.read_errors(limit=2)] == ["two", "three"]


@pytest.mark.asyncio
async def test_read_errors_since_compares_timestamp_instants(isolated_env):
    path = isolated_env["state_dir"] / "errors.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"timestamp": timestamp, "message": message})
            for timestamp, message in [
                ("2026-11-09T10:00:00.500000Z", "same second"),
                ("2026-11-09T09:59:59Z", "before"),
            ]
        ),
        encoding="utf-8",
    )

    z_results = await error_log.read_errors(since="2026-11-09T10:00:00Z")
    offset_results = await error_log.read_errors(since="2026-11-09T10:00:00+00:00")

    assert (
        z_results
        == offset_results
        == [{"timestamp": "2026-11-09T10:00:00.500000Z", "message": "same second"}]
    )


@pytest.mark.asyncio
async def test_read_errors_skips_corrupt_lines(isolated_env):
    path = isolated_env["state_dir"] / "errors.jsonl"
    path.write_text('{"message": "ok"}\nnot json\n', encoding="utf-8")
    entries = await error_log.read_errors()
    assert entries == [{"message": "ok"}]


@pytest.mark.asyncio
async def test_log_rotates_to_dot_one_at_cap(isolated_env, monkeypatch):
    monkeypatch.setattr(error_log, "MAX_LOG_BYTES", 200)
    await error_log.append_error(_entry(message="a" * 120))
    await error_log.append_error(_entry(message="b" * 120))

    state_dir = isolated_env["state_dir"]
    rotated = state_dir / "errors.jsonl.1"
    assert rotated.exists()
    assert "a" * 120 in rotated.read_text(encoding="utf-8")
    current = (state_dir / "errors.jsonl").read_text(encoding="utf-8")
    assert "b" * 120 in current
    assert "a" * 120 not in current


@pytest.mark.asyncio
async def test_clear_errors_removes_current_and_rotated_files(isolated_env):
    await error_log.append_error(_entry())
    rotated = isolated_env["state_dir"] / "errors.jsonl.1"
    rotated.write_text("old\nolder\n", encoding="utf-8")

    removed = await error_log.clear_errors()

    assert removed == 3
    assert await error_log.read_errors() == []
    assert not rotated.exists()
