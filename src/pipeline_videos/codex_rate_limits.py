from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from pipeline_videos.models import PipelineModel
from pipeline_videos.utils import utc_now

WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


class CodexRateLimitWindow(PipelineModel):
    used_percent: float = Field(ge=0)
    remaining_percent: float = Field(ge=0, le=100)
    window_duration_minutes: int = Field(gt=0)
    resets_at: str | None = None


class CodexRateLimitSnapshot(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    captured_at: str
    source: Literal["codex_app_server_account_rate_limits_read"] = (
        "codex_app_server_account_rate_limits_read"
    )
    primary: CodexRateLimitWindow | None = None
    secondary: CodexRateLimitWindow | None = None
    weekly: CodexRateLimitWindow | None = None
    rate_limit_reached_type: str | None = None


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


def _rate_limit_window(payload: Any) -> CodexRateLimitWindow | None:
    if not isinstance(payload, dict):
        return None
    used = payload.get("usedPercent")
    duration = payload.get("windowDurationMins")
    if used is None or duration is None:
        return None
    used_percent = max(0.0, float(used))
    return CodexRateLimitWindow(
        used_percent=used_percent,
        remaining_percent=round(max(0.0, 100.0 - used_percent), 4),
        window_duration_minutes=int(duration),
        resets_at=_iso_timestamp(payload.get("resetsAt")),
    )


def snapshot_from_rate_limits_result(payload: dict[str, Any]) -> CodexRateLimitSnapshot:
    rate_limits = payload.get("rateLimits")
    if not isinstance(rate_limits, dict):
        raise RuntimeError("O Codex não retornou as janelas de uso da conta.")
    primary = _rate_limit_window(rate_limits.get("primary"))
    secondary = _rate_limit_window(rate_limits.get("secondary"))
    weekly_candidates = [
        window
        for window in (primary, secondary)
        if window is not None and window.window_duration_minutes >= WEEKLY_WINDOW_MINUTES
    ]
    weekly = (
        min(
            weekly_candidates,
            key=lambda window: abs(window.window_duration_minutes - WEEKLY_WINDOW_MINUTES),
        )
        if weekly_candidates
        else None
    )
    return CodexRateLimitSnapshot(
        captured_at=utc_now(),
        primary=primary,
        secondary=secondary,
        weekly=weekly,
        rate_limit_reached_type=rate_limits.get("rateLimitReachedType"),
    )


def _response_reader(
    stream: Any,
    responses: queue.Queue[dict[str, Any] | None],
) -> None:
    try:
        for line in stream:
            try:
                payload = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                responses.put(payload)
    finally:
        responses.put(None)


def _wait_for_response(
    responses: queue.Queue[dict[str, Any] | None],
    request_id: int,
    *,
    deadline: float,
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("O Codex não respondeu à leitura de uso no prazo esperado.")
        try:
            payload = responses.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("O Codex não respondeu à leitura de uso no prazo esperado.") from exc
        if payload is None:
            raise RuntimeError("O Codex encerrou a leitura de uso antes de responder.")
        if payload.get("id") != request_id:
            continue
        if payload.get("error") is not None:
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"O Codex recusou a leitura de uso: {message}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("O Codex retornou uma resposta de uso inválida.")
        return result


def read_codex_rate_limit_snapshot(
    *,
    codex_binary: str | None = None,
    timeout_seconds: float = 20.0,
) -> CodexRateLimitSnapshot:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds precisa ser positivo.")
    binary = codex_binary or os.getenv("CODEX_BINARY") or shutil.which("codex")
    if not binary:
        raise FileNotFoundError("Executável do Codex não encontrado na VPS.")
    process = subprocess.Popen(
        [binary, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise RuntimeError("Não foi possível abrir o canal de telemetria do Codex.")
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
    reader = threading.Thread(
        target=_response_reader,
        args=(process.stdout, responses),
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + timeout_seconds

    def send(payload: dict[str, Any]) -> None:
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "pipeline_videos_remote_executor",
                        "title": "Pipeline Videos Remote Executor",
                        "version": "1.0.0",
                    }
                },
            }
        )
        _wait_for_response(responses, 1, deadline=deadline)
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 2})
        result = _wait_for_response(responses, 2, deadline=deadline)
        return snapshot_from_rate_limits_result(result)
    finally:
        with suppress(OSError):
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
