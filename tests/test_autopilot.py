from __future__ import annotations

from datetime import date

from typer.testing import CliRunner

from pipeline_videos import cli
from pipeline_videos.autopilot import (
    AutopilotResult,
    collect_external_waits,
    wait_until_external_ready,
)
from pipeline_videos.models import StageStatus
from pipeline_videos.project import create_project
from pipeline_videos.storage import write_json


def _waiting_layout(tmp_path):
    layout = create_project(
        "autopilot-test",
        "Autopilot",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        project_date=date(2026, 7, 15),
    )
    project = layout.load_manifest()
    project.config.youtube_growth.enabled = False
    project.config.youtube_growth.use_specialist_subagent = False
    project.stages["images"] = project.stages["images"].model_copy(
        update={"status": StageStatus.WAITING_EXTERNAL}
    )
    layout.save_manifest(project)
    write_json(
        layout.image_queue,
        {
            "provider": "codex-built-in-imagegen",
            "status": StageStatus.WAITING_EXTERNAL.value,
            "orchestration": {
                "worker_job_files": {
                    "image-worker-1": "images/jobs/image-worker-1.json",
                }
            },
            "items": [
                {
                    "scene_id": "scene-001",
                    "status": StageStatus.WAITING_EXTERNAL.value,
                    "staging_path": "images/staging/scene-001-v001.png",
                }
            ],
        },
    )
    return layout


def test_external_waits_become_ready_without_model_rounds(tmp_path) -> None:
    layout = _waiting_layout(tmp_path)

    waits = collect_external_waits(layout)

    assert waits == [
        {
            "kind": "images",
            "ready": False,
            "pending": 1,
            "worker_job_files": {
                "image-worker-1": "images/jobs/image-worker-1.json",
            },
        }
    ]

    staged = layout.root / "images/staging/scene-001-v001.png"
    staged.write_bytes(b"ready")

    ready = wait_until_external_ready(layout, timeout_seconds=1, poll_seconds=0.01)
    assert ready[0]["ready"] is True


def test_autopilot_cli_reports_compact_external_pause(tmp_path, monkeypatch) -> None:
    layout = _waiting_layout(tmp_path)
    monkeypatch.setattr(cli, "_project", lambda project, projects_dir=None: layout)

    import pipeline_videos.autopilot as autopilot

    monkeypatch.setattr(
        autopilot,
        "run_autopilot_once",
        lambda *args, **kwargs: AutopilotResult(
            "waiting_external",
            collect_external_waits(layout),
        ),
    )

    result = CliRunner().invoke(cli.app, ["autopilot", "autopilot-test"])

    assert result.exit_code == 0
    assert "Autopilot aguardando" in result.stdout
    assert "image-worker-1.json" in result.stdout
    assert "autopilot-state.json" in result.stdout
