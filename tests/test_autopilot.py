from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from pipeline_videos import cli
from pipeline_videos.autopilot import (
    AutopilotResult,
    collect_external_waits,
    prepare_local_coordinator_job,
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


def test_local_coordinator_job_is_isolated_and_completion_first(tmp_path) -> None:
    layout = create_project(
        "coordenador-compacto",
        "Coordenador compacto",
        projects_dir=tmp_path,
        target_duration_seconds=18,
        duration_min_seconds=15,
        duration_max_seconds=20,
        video_format="short",
        project_date=date(2026, 7, 21),
    )

    job = prepare_local_coordinator_job(layout)

    assert job["orchestration"]["coordinator_role"] == "local_video_coordinator"
    assert job["orchestration"]["fork_turns"] == "none"
    assert job["external_roles"]["image_workers"]["maximum_parallel"] == 2
    assert job["external_roles"]["shorts_visual_planner"]["reasoning_effort"] == "medium"
    assert "shorts_script_specialist" not in job["external_roles"]
    assert "biblical_reviewer" not in job["external_roles"]
    assert job["policies"]["validation_policy"] == "completion_first_v1"
    assert job["policies"]["token_budget_behavior"] == "fallback_then_continue"
    assert layout.coordinator_job.is_file()


def test_noncompact_short_coordinator_uses_script_specialist(tmp_path) -> None:
    layout = create_project(
        "coordenador-padrao",
        "Coordenador padrão",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 21),
    )

    job = prepare_local_coordinator_job(layout)

    assert job["external_roles"]["shorts_script_specialist"]["reasoning_effort"] == "high"
    assert "biblical_reviewer" not in job["external_roles"]
    assert "shorts_visual_planner" not in job["external_roles"]


def test_noncompact_main_agent_short_does_not_declare_external_script_specialist(
    tmp_path,
) -> None:
    layout = create_project(
        "coordenador-agente-principal",
        "Coordenador no agente principal",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 21),
    )
    project = layout.load_manifest()
    project.config.shorts_script.use_specialist_subagent = False
    layout.save_manifest(project)

    job = prepare_local_coordinator_job(layout)

    assert "shorts_script_specialist" not in job["external_roles"]
    assert "shorts_visual_planner" not in job["external_roles"]
    assert "biblical_reviewer" not in job["external_roles"]


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


def test_autopilot_can_finish_validation_and_private_upload_in_one_explicit_command(
    tmp_path, monkeypatch
) -> None:
    layout = _waiting_layout(tmp_path)
    monkeypatch.setattr(cli, "_project", lambda project, projects_dir=None: layout)
    import pipeline_videos.autopilot as autopilot
    import pipeline_videos.publication as publication
    import pipeline_videos.token_audit as token_audit

    monkeypatch.setattr(
        autopilot,
        "run_autopilot_once",
        lambda *args, **kwargs: AutopilotResult(
            "completed",
            [],
            render_path=Path("render/final.mp4"),
            validation_ok=True,
        ),
    )
    monkeypatch.setattr(publication, "authenticated_client", lambda source=None: object())
    captured = {}

    def fake_upload(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(youtube_url="https://youtu.be/private-test")

    monkeypatch.setattr(publication, "upload_project", fake_upload)
    monkeypatch.setattr(
        token_audit,
        "write_local_token_audit",
        lambda layout: {
            "codex": {"usage": {"fresh_plus_output_tokens": 100}},
        },
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "autopilot",
            "autopilot-test",
            "--allow-external",
            "--upload-private",
            "--confirm-upload",
            layout.load_manifest().project_id,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["privacy_status"] == "private"
    assert captured["automated_review"] is True
    assert "https://youtu.be/private-test" in result.stdout
