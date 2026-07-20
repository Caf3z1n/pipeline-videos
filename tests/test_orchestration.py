from __future__ import annotations

import json
from datetime import date

import pytest
from typer.testing import CliRunner

from pipeline_videos import cli
from pipeline_videos.models import Briefing, StageStatus, elevenlabs_voice_id_for_variant
from pipeline_videos.orchestration import (
    build_one_shot_plan,
    format_plan_lines,
    recommend_next_image_assignment,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import read_model, write_json


def _short_layout(tmp_path):
    return create_project(
        "davi-e-golias",
        "Davi e Golias",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )


def _set_stage(layout, stage: str, status: StageStatus) -> None:
    manifest = layout.load_manifest()
    manifest.stages[stage] = manifest.stages[stage].model_copy(update={"status": status})
    layout.save_manifest(manifest)


def _write_parallel_queues(layout) -> None:
    write_json(
        layout.image_queue,
        {
            "status": "waiting_external",
            "items": [
                {
                    "job_id": "scene-003-v001",
                    "scene_id": "scene-003",
                    "image_id": "image-003",
                    "version": 1,
                    "staging_path": "images/staging/scene-003-v001.png",
                    "status": "waiting_external",
                    "worker_id": "image-worker-1",
                },
                {
                    "job_id": "scene-001-v001",
                    "scene_id": "scene-001",
                    "image_id": "image-001",
                    "version": 1,
                    "staging_path": "images/staging/scene-001-v001.png",
                    "status": "waiting_external",
                    "worker_id": "image-worker-2",
                },
                {
                    "job_id": "scene-002-v001",
                    "scene_id": "scene-002",
                    "image_id": "image-002",
                    "version": 1,
                    "staging_path": "images/staging/scene-002-v001.png",
                    "status": "waiting_external",
                    "worker_id": "image-worker-1",
                },
            ],
        },
    )
    write_json(
        layout.growth_queue,
        {
            "status": "waiting_external",
            "outputs": {
                "package": "youtube/staging/youtube-growth-specialist/package.json",
                "thumbnails": ["youtube/staging/youtube-growth-specialist/thumbnail-001.png"],
            },
        },
    )
    for stage in ("shorts_script", "script", "biblical_review", "scenes"):
        _set_stage(layout, stage, StageStatus.COMPLETED)
    _set_stage(layout, "images", StageStatus.WAITING_EXTERNAL)
    _set_stage(layout, "youtube_growth", StageStatus.WAITING_EXTERNAL)


def _task(plan, task_id: str):
    return next(
        task for wave in plan["waves"] for task in wave["tasks"] if task["task_id"] == task_id
    )


def test_plan_is_read_only_serializable_and_exposes_dependency_waves(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    before = layout.manifest.read_bytes()

    first = build_one_shot_plan(layout)
    second = build_one_shot_plan(layout)

    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert layout.manifest.read_bytes() == before
    assert first["read_only"] is True
    assert first["single_writer"]["owner"] == "main"
    assert first["single_writer"]["shared_state"] == "project.json"
    assert [wave["wave_id"] for wave in first["waves"]] == [
        "wave-01-script",
        "wave-02-parallel-production",
        "wave-03-join-imports",
        "wave-04-render",
        "wave-05-validation",
    ]
    assert first["waves"][0]["execution"] == "sequential"
    assert first["waves"][1]["execution"] == "parallel_lanes"
    assert first["waves"][2]["execution"] == "sequential_single_writer"
    assert _task(first, "captions")["depends_on"] == ["audio"]
    assert _task(first, "independent_biblical_review")["owner"] == "biblical_reviewer"
    assert _task(first, "shorts_script_import")["depends_on"] == ["independent_biblical_review"]
    assert _task(first, "growth_import")["depends_on"] == [
        "youtube_growth_specialist",
        "images_import_batch",
    ]
    assert _task(first, "render")["depends_on"] == ["production_join"]
    assert _task(first, "validation")["depends_on"] == ["render"]
    assert first["dynamic_image_assignment"]["max_parallel_workers"] == 2
    assert all(line.encode("cp1252") for line in format_plan_lines(first))


def test_plan_pauses_for_independent_review_before_the_single_script_import(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    package = layout.shorts_script_package_staging
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text("{}", encoding="utf-8")
    write_json(
        layout.shorts_script_queue,
        {"outputs": {"package": "script/staging/shorts-script-specialist/package.json"}},
    )
    write_json(
        layout.shorts_biblical_review_queue,
        {"outputs": {"review": "reviews/staging/biblical-reviewer/review.json"}},
    )

    waiting = build_one_shot_plan(layout)

    assert waiting["current_pause"] == "independent_biblical_review"
    assert _task(waiting, "shorts_script_import")["status"] == "blocked"

    layout.shorts_biblical_review_staging_file.write_text("{}", encoding="utf-8")
    ready = build_one_shot_plan(layout)

    assert ready["current_pause"] == "shorts_script_import"
    assert _task(ready, "shorts_script_import")["status"] == "ready"


def test_dynamic_pool_orders_jobs_and_recommends_next_free_worker(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    _write_parallel_queues(layout)
    files_before = {
        path: path.read_bytes()
        for path in (layout.manifest, layout.image_queue, layout.growth_queue)
    }

    plan = build_one_shot_plan(layout)
    assignment = plan["dynamic_image_assignment"]

    assert [job["job_id"] for job in assignment["jobs"]] == [
        "scene-001-v001",
        "scene-002-v001",
        "scene-003-v001",
    ]
    assert assignment["worker_slots"] == ["image-worker-1", "image-worker-2"]
    assert assignment["initial_assignments"] == [
        {
            "worker_id": "image-worker-1",
            "job_id": "scene-001-v001",
            "order": 1,
            "scene_id": "scene-001",
            "staging_path": "images/staging/scene-001-v001.png",
        },
        {
            "worker_id": "image-worker-2",
            "job_id": "scene-002-v001",
            "order": 2,
            "scene_id": "scene-002",
            "staging_path": "images/staging/scene-002-v001.png",
        },
    ]
    assert assignment["next_after_initial"] == {
        "job_id": "scene-003-v001",
        "order": 3,
        "staging_path": "images/staging/scene-003-v001.png",
        "worker_id": "first_free_worker",
    }
    assert recommend_next_image_assignment(
        plan,
        busy_worker_ids={"image-worker-1"},
        claimed_job_ids={"scene-001-v001"},
    ) == {
        "worker_id": "image-worker-2",
        "job_id": "scene-002-v001",
        "order": 2,
        "scene_id": "scene-002",
        "staging_path": "images/staging/scene-002-v001.png",
    }
    assert plan["current_pause"] == "external_generation"
    assert all(
        _task(plan, f"image:{job['job_id']}")["writes_project_state"] is False
        for job in assignment["jobs"]
    )
    assert _task(plan, "images_import_batch")["owner"] == "main"
    assert _task(plan, "growth_import")["owner"] == "main"
    assert all(path.read_bytes() == content for path, content in files_before.items())


def test_single_agent_plan_assigns_script_images_and_growth_to_main(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.production.use_subagents = False
    manifest.config.production.image_workers = 1
    manifest.config.shorts_script.use_specialist_subagent = False
    manifest.config.youtube_growth.enabled = True
    manifest.config.youtube_growth.use_specialist_subagent = False
    manifest.stages["youtube_growth"] = manifest.stages["youtube_growth"].model_copy(
        update={"status": StageStatus.WAITING_EXTERNAL}
    )
    layout.save_manifest(manifest)
    for stage in ("shorts_script", "script", "biblical_review", "scenes"):
        _set_stage(layout, stage, StageStatus.COMPLETED)
    _set_stage(layout, "images", StageStatus.WAITING_EXTERNAL)
    write_json(
        layout.image_queue,
        {
            "provider": "codex-imagegen",
            "status": "waiting_external",
            "items": [
                {
                    "job_id": "scene-001-v001",
                    "scene_id": "scene-001",
                    "image_id": "image-001",
                    "version": 1,
                    "order": 1,
                    "staging_path": "images/staging/scene-001-v001.png",
                    "status": "waiting_external",
                    "worker_id": "main",
                }
            ],
        },
    )
    write_json(
        layout.growth_queue,
        {
            "status": "waiting_external",
            "outputs": {
                "package": "youtube/staging/youtube-growth-specialist/package.json",
                "thumbnails": [],
            },
        },
    )

    plan = build_one_shot_plan(layout)

    assert plan["dynamic_image_assignment"]["max_parallel_workers"] == 1
    assert plan["dynamic_image_assignment"]["worker_slots"] == ["main"]
    assert _task(plan, "shorts_script_generation")["owner"] == "main"
    assert _task(plan, "image:scene-001-v001")["owner"] == "main"
    assert _task(plan, "youtube_growth_specialist")["status"] == "waiting_external"
    assert _task(plan, "youtube_growth_specialist")["owner"] == "main"
    assert _task(plan, "images_import_batch")["command"] == "images import-batch"
    assert plan["current_pause"] == "external_generation"


def test_plan_marks_local_manual_audio_without_external_guard(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.audio.provider = "local_manual"
    manifest.stages["shorts_script"] = manifest.stages["shorts_script"].model_copy(
        update={"status": StageStatus.COMPLETED}
    )
    manifest.stages["images"] = manifest.stages["images"].model_copy(
        update={"status": StageStatus.COMPLETED}
    )
    manifest.stages["youtube_growth"] = manifest.stages["youtube_growth"].model_copy(
        update={"status": StageStatus.COMPLETED}
    )
    layout.save_manifest(manifest)

    plan = build_one_shot_plan(layout)

    assert _task(plan, "audio")["status"] == "waiting_manual_audio"
    assert plan["current_pause"] == "manual_audio_upload"


def test_manual_audio_short_uses_current_production_mode(tmp_path) -> None:
    runner = CliRunner()

    manual_audio = runner.invoke(
        cli.app,
        ["new-short-audio-manual", "--projects-dir", str(tmp_path)],
    )

    assert manual_audio.exit_code == 0, manual_audio.output
    manifests = sorted((tmp_path / "shorts").glob("*/project.json"))
    assert len(manifests) == 1
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in manifests]
    assert payloads[0]["config"]["production"]["mode"] == "final_candidate_one_shot"
    assert payloads[0]["config"]["audio"]["provider"] == "local_manual"
    started = runner.invoke(cli.app, ["run", str(manifests[0].parent)])
    assert started.exit_code == 0, started.output
    script_queue = json.loads(
        (manifests[0].parent / "script/shorts-queue.json").read_text(encoding="utf-8")
    )
    assert script_queue["orchestration"]["mode"] == "subagent"
    assert script_queue["orchestration"]["reasoning_effort"] == "high"


def test_manual_short_bootstrap_applies_mode_voice_hook_and_music_in_one_command(tmp_path) -> None:
    runner = CliRunner()
    music = tmp_path / "Leaning.mp3"
    music.write_bytes(b"leaning")

    result = runner.invoke(
        cli.app,
        [
            "new",
            "judas-retrato",
            "--title",
            "Judas em retrato",
            "--duration",
            "50",
            "--format",
            "short",
            "--creative-mode",
            "character_portrait",
            "--voice-variant",
            "jesus_2",
            "--music-file",
            str(music),
            "--projects-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    project = next((tmp_path / "shorts").iterdir())
    briefing = read_model(project / "editorial/briefing.json", Briefing)
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))

    assert briefing.creative_mode == "character_portrait"
    assert briefing.hook_contract == "user_six_v1"
    assert briefing.hook_family == "nobody_talks_but"
    assert manifest["config"]["audio"]["voice_id"] == elevenlabs_voice_id_for_variant("jesus_2")
    assert manifest["config"]["render"]["background_music_path"] == "audio/music/Leaning.mp3"
    assert (project / "audio/music/Leaning.mp3").read_bytes() == b"leaning"


def test_manual_short_cli_accepts_fifteen_second_duration(tmp_path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "new",
            "micro-quinze",
            "--title",
            "Micro Short",
            "--duration",
            "15",
            "--format",
            "short",
            "--creative-mode",
            "character_portrait",
            "--projects-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    project = next((tmp_path / "shorts").iterdir())
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert manifest["target_duration_seconds"] == 15
    assert manifest["config"]["shorts_script"]["duration_profile"] == "adaptive_compact_v1"


def test_image_pool_uses_third_slot_when_growth_specialist_is_disabled(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.youtube_growth.enabled = False
    manifest.config.production.image_workers = 3
    layout.save_manifest(manifest)

    plan = build_one_shot_plan(layout)

    assert plan["dynamic_image_assignment"]["max_parallel_workers"] == 3
    assert plan["dynamic_image_assignment"]["worker_slots"] == [
        "image-worker-1",
        "image-worker-2",
        "image-worker-3",
    ]


def test_staged_jobs_leave_the_pool_and_join_becomes_ready_for_import(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    _write_parallel_queues(layout)
    image_queue = json.loads(layout.image_queue.read_text(encoding="utf-8"))
    image_queue["items"][1]["status"] = "completed"
    write_json(layout.image_queue, image_queue)
    for path in (
        layout.root / "images/staging/scene-002-v001.png",
        layout.root / "images/staging/scene-003-v001.png",
        layout.root / "youtube/staging/youtube-growth-specialist/package.json",
        layout.root / "youtube/staging/youtube-growth-specialist/thumbnail-001.png",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"staged")

    plan = build_one_shot_plan(layout)

    assert plan["dynamic_image_assignment"]["initial_assignments"] == []
    assert recommend_next_image_assignment(plan) is None
    assert _task(plan, "images_import_batch")["status"] == "ready"
    assert _task(plan, "growth_import")["status"] == "ready"
    assert plan["current_pause"] == "join_imports"


def test_planner_rejects_staging_paths_outside_worker_namespace(tmp_path) -> None:
    layout = _short_layout(tmp_path)
    write_json(
        layout.image_queue,
        {
            "status": "waiting_external",
            "items": [
                {
                    "job_id": "unsafe",
                    "scene_id": "scene-001",
                    "staging_path": "../project.json",
                    "status": "waiting_external",
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="staging_path inseguro"):
        build_one_shot_plan(layout)


def test_run_dry_run_prints_waves_without_mutating_project(tmp_path, monkeypatch) -> None:
    layout = _short_layout(tmp_path)
    before = layout.manifest.read_bytes()
    monkeypatch.setattr(cli, "_project", lambda project, projects_dir=None: layout)

    result = CliRunner().invoke(cli.app, ["run", "davi-e-golias", "--dry-run"])

    assert result.exit_code == 0
    assert "wave-01-script" in result.stdout
    assert "wave-02-parallel-production" in result.stdout
    assert "wave-03-join-imports" in result.stdout
    assert "wave-04-render" in result.stdout
    assert "wave-05-validation" in result.stdout
    assert "Escritor único: main" in result.stdout
    assert layout.manifest.read_bytes() == before
