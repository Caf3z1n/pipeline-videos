from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

from pipeline_videos.utils import sha256_value


def _load_hook_module():
    path = Path(__file__).parents[1] / "scripts" / "remote_codex_hook.py"
    spec = importlib.util.spec_from_file_location("remote_codex_hook_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_tool_hook_materializes_imagegen_saved_path(tmp_path, monkeypatch) -> None:
    hook = _load_hook_module()
    prompt = "Uma cena bíblica cinematográfica"
    codex_home = tmp_path / ".codex"
    source = codex_home / "generated_images" / "worker-thread" / "call_image.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage bytes")
    destination = tmp_path / "project" / "images" / "staging" / "scene.png"
    artifact_map = tmp_path / "artifacts.json"
    artifact_map.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "job_id": "scene-001-v001",
                        "prompt_hash": sha256_value(prompt),
                        "staging_path": str(destination),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    hook_log = tmp_path / "hook.jsonl"
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "image_gen__imagegen",
        "tool_input": {"prompt": prompt},
        "tool_response": {"savedPath": str(source)},
        "turn_id": "../../unsafe",
    }
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("PIPELINE_VIDEOS_IMAGE_ARTIFACT_MAP", str(artifact_map))
    monkeypatch.setenv("PIPELINE_VIDEOS_CODEX_HOOK_LOG", str(hook_log))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert hook.main() == 0

    assert destination.read_bytes() == source.read_bytes()
    receipt = json.loads(hook_log.read_text(encoding="utf-8"))
    assert receipt["artifact_materialized"] is True
    assert receipt["image_job_id"] == "scene-001-v001"
    assert receipt["image_prompt_hash"] == sha256_value(prompt)
    assert list(destination.parent.glob("*.tmp")) == []


def test_post_tool_hook_rejects_unknown_prompt_without_copy(tmp_path, monkeypatch) -> None:
    hook = _load_hook_module()
    codex_home = tmp_path / ".codex"
    source = codex_home / "generated_images" / "worker-thread" / "exec-image.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage bytes")
    destination = tmp_path / "project" / "images" / "staging" / "scene.png"
    artifact_map = tmp_path / "artifacts.json"
    artifact_map.write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "job_id": "scene-001-v001",
                        "prompt_hash": sha256_value("prompt autorizado"),
                        "staging_path": str(destination),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    hook_log = tmp_path / "hook.jsonl"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("PIPELINE_VIDEOS_IMAGE_ARTIFACT_MAP", str(artifact_map))
    monkeypatch.setenv("PIPELINE_VIDEOS_CODEX_HOOK_LOG", str(hook_log))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "image_gen__imagegen",
                    "tool_input": {"prompt": "prompt diferente"},
                    "tool_response": {"savedPath": str(source)},
                }
            )
        ),
    )

    assert hook.main() == 0

    assert not destination.exists()
    receipt = json.loads(hook_log.read_text(encoding="utf-8"))
    assert receipt["artifact_materialized"] is False
    assert receipt["materialization_error"] == "imagegen_prompt_hash_unknown"
