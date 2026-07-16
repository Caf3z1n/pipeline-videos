from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _content_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(key)) + _content_size(item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return sum(_content_size(item) for item in value)
    return len(str(value))


def main() -> int:
    destination = os.getenv("PIPELINE_VIDEOS_CODEX_HOOK_LOG")
    if not destination:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    record = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": os.getenv("PIPELINE_VIDEOS_REMOTE_PHASE"),
        "event": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "model": payload.get("model"),
        "tool_name": payload.get("tool_name"),
        "tool_input_chars": _content_size(payload.get("tool_input")),
        "tool_response_chars": _content_size(
            payload.get("tool_response", payload.get("tool_result"))
        ),
    }
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
