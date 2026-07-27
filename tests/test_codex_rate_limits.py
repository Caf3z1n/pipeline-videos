from __future__ import annotations

import json

from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.codex_rate_limits import (
    CodexRateLimitSnapshot,
    read_codex_rate_limit_snapshot,
    snapshot_from_rate_limits_result,
)


def _rate_limit_result(*, weekly_used: float = 12.5) -> dict:
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": 8,
                "windowDurationMins": 300,
                "resetsAt": 1785200000,
            },
            "secondary": {
                "usedPercent": weekly_used,
                "windowDurationMins": 10080,
                "resetsAt": 1785800000,
            },
            "rateLimitReachedType": None,
        }
    }


def test_snapshot_selects_weekly_window_and_derives_remaining_percent() -> None:
    snapshot = snapshot_from_rate_limits_result(_rate_limit_result(weekly_used=12.5))

    assert snapshot.primary is not None
    assert snapshot.primary.window_duration_minutes == 300
    assert snapshot.weekly is not None
    assert snapshot.weekly.window_duration_minutes == 10080
    assert snapshot.weekly.used_percent == 12.5
    assert snapshot.weekly.remaining_percent == 87.5
    assert snapshot.weekly.resets_at is not None


class _InputRecorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, value: str) -> int:
        self.lines.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _InputRecorder()
        self.stdout = iter(
            [
                json.dumps({"id": 1, "result": {"userAgent": "test"}}) + "\n",
                json.dumps({"id": 2, "result": _rate_limit_result()}) + "\n",
            ]
        )
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


def test_app_server_reader_performs_initialize_handshake(monkeypatch) -> None:
    process = _FakeProcess()
    monkeypatch.setattr(
        "pipeline_videos.codex_rate_limits.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    snapshot = read_codex_rate_limit_snapshot(codex_binary="codex-test")

    sent = [json.loads(line) for line in process.stdin.lines]
    assert [payload["method"] for payload in sent] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    assert isinstance(snapshot, CodexRateLimitSnapshot)
    assert snapshot.weekly is not None
    assert snapshot.weekly.used_percent == 12.5


def test_remote_account_usage_command_returns_snapshot(monkeypatch) -> None:
    snapshot = snapshot_from_rate_limits_result(_rate_limit_result(weekly_used=7.25))
    monkeypatch.setattr(
        "pipeline_videos.codex_rate_limits.read_codex_rate_limit_snapshot",
        lambda: snapshot,
    )

    result = CliRunner().invoke(app, ["remote", "account-usage"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["weekly"]["used_percent"] == 7.25
    assert payload["weekly"]["remaining_percent"] == 92.75
