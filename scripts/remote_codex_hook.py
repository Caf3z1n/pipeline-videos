from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SAVED_PATH_PATTERN = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)[^\r\n\"']*?"
    r"generated_images[\\/][^\r\n\"']+[\\/]exec-[^\r\n\"']+\.png)"
)


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


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nested_value(value: Any, accepted_keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold().replace("_", "")
            if normalized in accepted_keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _nested_value(item, accepted_keys)
            if found:
                return found
    elif isinstance(value, list | tuple):
        for item in value:
            found = _nested_value(item, accepted_keys)
            if found:
                return found
    elif isinstance(value, str):
        with_json = None
        with suppress(json.JSONDecodeError):
            with_json = json.loads(value)
        if with_json is not None and with_json != value:
            found = _nested_value(with_json, accepted_keys)
            if found:
                return found
        if "savedpath" in accepted_keys:
            match = SAVED_PATH_PATTERN.search(value)
            if match:
                return match.group("path")
    return None


def _is_png(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _load_artifact_map(path_value: str | None) -> list[dict[str, str]]:
    if not path_value:
        return []
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list):
        return []
    return [
        {
            "job_id": str(item["job_id"]),
            "prompt_hash": str(item["prompt_hash"]),
            "staging_path": str(item["staging_path"]),
        }
        for item in artifacts
        if isinstance(item, dict)
        and all(
            isinstance(item.get(key), str) and item[key]
            for key in ("job_id", "prompt_hash", "staging_path")
        )
    ]


def _materialize_imagegen_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "image_job_id": None,
        "image_prompt_hash": None,
        "image_saved_path": None,
        "image_staging_path": None,
        "artifact_materialized": False,
        "materialization_error": None,
    }
    if payload.get("hook_event_name") != "PostToolUse":
        return result
    tool_name = str(payload.get("tool_name") or "").casefold()
    if "imagegen" not in tool_name and "image_generation" not in tool_name:
        return result
    prompt = _nested_value(payload.get("tool_input"), {"prompt"})
    response = payload.get("tool_response") or payload.get("tool_result")
    saved_path = _nested_value(response, {"savedpath"})
    if prompt is None or saved_path is None:
        result["materialization_error"] = "imagegen_prompt_or_saved_path_missing"
        return result
    prompt_hash = _canonical_hash(prompt)
    result["image_prompt_hash"] = prompt_hash
    result["image_saved_path"] = saved_path
    artifacts = _load_artifact_map(os.getenv("PIPELINE_VIDEOS_IMAGE_ARTIFACT_MAP"))
    matches = [item for item in artifacts if item["prompt_hash"] == prompt_hash]
    if len(matches) != 1:
        result["materialization_error"] = (
            "imagegen_prompt_hash_not_unique" if matches else "imagegen_prompt_hash_unknown"
        )
        return result
    artifact = matches[0]
    result["image_job_id"] = artifact["job_id"]
    result["image_staging_path"] = artifact["staging_path"]
    try:
        raw_source = Path(saved_path).expanduser()
        if not raw_source.is_absolute():
            raise ValueError("saved_path_not_absolute")
        source = raw_source.resolve(strict=True)
        codex_home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
        generated_root = (codex_home.expanduser().resolve() / "generated_images").resolve()
        source.relative_to(generated_root)
        if not source.name.startswith("exec-") or source.suffix.casefold() != ".png":
            raise ValueError("saved_path_not_imagegen_png")
        if not _is_png(source):
            raise ValueError("saved_path_invalid_png")
        destination = Path(artifact["staging_path"]).expanduser().resolve()
        if destination.suffix.casefold() != ".png":
            raise ValueError("staging_path_not_png")
        if _is_png(destination):
            result["artifact_materialized"] = True
            return result
        destination.parent.mkdir(parents=True, exist_ok=True)
        turn_suffix = hashlib.sha256(
            str(payload.get("turn_id") or "hook").encode("utf-8")
        ).hexdigest()[:8]
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{turn_suffix}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if not _is_png(temporary):
                raise ValueError("staging_copy_invalid_png")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        result["artifact_materialized"] = True
    except (OSError, ValueError) as exc:
        result["materialization_error"] = str(exc)
    return result


def main() -> int:
    destination = os.getenv("PIPELINE_VIDEOS_CODEX_HOOK_LOG")
    if not destination:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    artifact = _materialize_imagegen_artifact(payload)
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
            payload.get("tool_response") or payload.get("tool_result")
        ),
        **artifact,
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
