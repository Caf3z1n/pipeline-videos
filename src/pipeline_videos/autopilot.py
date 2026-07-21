from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pipeline_videos.models import StageStatus
from pipeline_videos.storage import ProjectLayout, read_json, write_json
from pipeline_videos.utils import relative_posix, utc_now


@dataclass(frozen=True)
class AutopilotResult:
    status: Literal["waiting_external", "completed"]
    waits: list[dict[str, Any]]
    render_path: Path | None = None
    validation_ok: bool | None = None


def _is_nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _pending_image_entries(layout: ProjectLayout) -> list[dict[str, Any]]:
    if not layout.image_queue.is_file():
        return []
    queue = read_json(layout.image_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        return []
    return [
        entry
        for entry in queue["items"]
        if isinstance(entry, dict) and entry.get("status") == StageStatus.WAITING_EXTERNAL.value
    ]


def _all_staged_images_ready(layout: ProjectLayout) -> bool:
    pending = _pending_image_entries(layout)
    return bool(pending) and all(
        isinstance(entry.get("staging_path"), str)
        and _is_nonempty_file(layout.root / entry["staging_path"])
        for entry in pending
    )


def _growth_outputs_ready(layout: ProjectLayout) -> bool:
    if not layout.growth_queue.is_file():
        return False
    queue = read_json(layout.growth_queue)
    if not isinstance(queue, dict):
        return False
    outputs = queue.get("outputs", {})
    package = outputs.get("package")
    if not isinstance(package, str) or not _is_nonempty_file(layout.root / package):
        return False
    if outputs.get("external_image_generation_required"):
        thumbnails = outputs.get("thumbnails", [])
        return bool(thumbnails) and all(
            isinstance(path, str) and _is_nonempty_file(layout.root / path) for path in thumbnails
        )
    return True


def collect_external_waits(layout: ProjectLayout) -> list[dict[str, Any]]:
    project = layout.load_manifest()
    waits: list[dict[str, Any]] = []
    if (
        project.video_format == "short"
        and project.config.shorts_script.enabled
        and project.stages["shorts_script"].status != StageStatus.COMPLETED
    ):
        if project.config.shorts_script.require_independent_biblical_review and _is_nonempty_file(
            layout.shorts_script_package_staging
        ):
            waits.append(
                {
                    "kind": "biblical_review",
                    "ready": _is_nonempty_file(layout.shorts_biblical_review_staging_file),
                    "queue": relative_posix(layout.shorts_biblical_review_queue, layout.root),
                }
            )
            return waits
        waits.append(
            {
                "kind": "shorts_script",
                "ready": _is_nonempty_file(layout.shorts_script_package_staging),
                "queue": relative_posix(layout.shorts_script_queue, layout.root),
            }
        )
        return waits

    pending_images = _pending_image_entries(layout)
    if pending_images:
        queue = read_json(layout.image_queue)
        waits.append(
            {
                "kind": "images",
                "ready": _all_staged_images_ready(layout),
                "pending": len(pending_images),
                "worker_job_files": queue.get("orchestration", {}).get("worker_job_files", {}),
            }
        )

    if (
        project.config.audio.provider == "local_manual"
        and project.stages["audio"].status != StageStatus.COMPLETED
    ):
        waits.append(
            {
                "kind": "manual_audio",
                "ready": False,
                "queue": relative_posix(layout.manual_audio_queue, layout.root),
                "transcript": relative_posix(layout.narration, layout.root),
            }
        )

    if (
        project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
        and layout.growth_queue.is_file()
    ):
        waits.append(
            {
                "kind": "youtube_growth",
                "ready": _growth_outputs_ready(layout),
                "queue": relative_posix(layout.growth_queue, layout.root),
            }
        )
    return waits


def _write_state(layout: ProjectLayout, result: AutopilotResult) -> None:
    write_json(
        layout.autopilot_state,
        {
            "schema_version": "1.0",
            "updated_at": utc_now(),
            "status": result.status,
            "waits": result.waits,
            "render_path": (
                relative_posix(result.render_path, layout.root) if result.render_path else None
            ),
            "validation_ok": result.validation_ok,
        },
    )


def prepare_local_coordinator_job(layout: ProjectLayout) -> dict[str, Any]:
    """Persist the compact hand-off used by an isolated local coordinator task."""

    project = layout.load_manifest()
    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "project_root": str(layout.root),
        "objective": "Produzir um único candidato final local e validado, sem publicar.",
        "orchestration": {
            "coordinator_role": "local_video_coordinator",
            "recommended_model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "fork_turns": "none",
            "sole_shared_state_writer": True,
            "context_policy": "read_this_job_then_declared_queues_only",
        },
        "command": (
            f'uv run pipeline-videos autopilot "{project.project_id}" '
            + (
                "--wait-external"
                if project.config.audio.provider == "local_manual"
                else "--allow-external --wait-external"
            )
        ),
        "external_roles": {
            "shorts_visual_planner": {
                "queue": relative_posix(layout.shorts_script_queue, layout.root),
                "reasoning_effort": "medium",
                "when_present": True,
            },
            "image_workers": {
                "jobs_directory": relative_posix(layout.image_jobs, layout.root),
                "maximum_parallel": min(2, project.config.production.image_workers),
                "reasoning_effort": "low",
            },
            "youtube_growth_specialist": {
                "queue": relative_posix(layout.growth_queue, layout.root),
                "reasoning_effort": "medium",
                "when_present": True,
            },
        },
        "policies": {
            "repair_policy": project.config.production.repair_policy,
            "max_model_repairs_per_stage": (project.config.production.max_model_repairs_per_stage),
            "token_budget_behavior": project.config.production.token_budget_behavior,
            "validation_policy": project.config.production.validation_policy,
            "publication_authorized": False,
            "one_final_render": True,
        },
    }
    write_json(layout.coordinator_job, payload)
    return payload


def run_autopilot_once(
    layout: ProjectLayout,
    *,
    allow_external: bool = False,
    force: bool = False,
) -> AutopilotResult:
    prepare_local_coordinator_job(layout)
    project = layout.load_manifest()

    if (
        project.video_format == "short"
        and project.config.shorts_script.enabled
        and project.stages["shorts_script"].status != StageStatus.COMPLETED
    ):
        from pipeline_videos.shorts_script import (
            check_shorts_script_package_staging,
            import_shorts_script_package,
            materialize_compact_visual_fallback,
            prepare_shorts_biblical_review_queue,
            prepare_shorts_script_queue,
        )

        prepare_shorts_script_queue(layout, force=force)
        if _is_nonempty_file(layout.shorts_script_package_staging):
            if project.config.shorts_script.require_independent_biblical_review:
                prepare_shorts_biblical_review_queue(layout, force=force)
                if _is_nonempty_file(layout.shorts_biblical_review_staging_file):
                    import_shorts_script_package(layout, force=force)
                else:
                    result = AutopilotResult("waiting_external", collect_external_waits(layout))
                    _write_state(layout, result)
                    return result
            else:
                check = check_shorts_script_package_staging(layout)
                if (
                    not check.valid
                    and project.config.shorts_script.generation_mode
                    == "deterministic_compact_visual_plan_v1"
                    and project.config.production.repair_policy == "bounded_then_fallback_v1"
                ):
                    materialize_compact_visual_fallback(layout)
                import_shorts_script_package(layout, force=force)
        else:
            result = AutopilotResult("waiting_external", collect_external_waits(layout))
            _write_state(layout, result)
            return result

    from pipeline_videos.review import validate_draft_preflight
    from pipeline_videos.scenes import build_scenes

    validate_draft_preflight(layout)
    build_scenes(layout, force=force, skip_approvals=True)

    from pipeline_videos.audio import generate_audio
    from pipeline_videos.captions import generate_captions
    from pipeline_videos.images import import_staged_images, prepare_images
    from pipeline_videos.music import select_music

    prepare_images(layout, force=force)
    project = layout.load_manifest()
    growth_without_images = (
        project.video_format == "short"
        and project.config.youtube_growth.thumbnail_generation_mode == "youtube_auto_frame_v1"
        and project.config.youtube_growth.thumbnail_reference_mode == "none_v1"
    )
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
        and growth_without_images
    ):
        from pipeline_videos.growth import prepare_growth_queue

        prepare_growth_queue(layout, force=force)
    project = layout.load_manifest()
    if project.config.audio.provider == "local_manual":
        from pipeline_videos.manual_audio import prepare_manual_audio

        prepare_manual_audio(layout, force=force)
        if project.stages["audio"].status != StageStatus.COMPLETED:
            result = AutopilotResult("waiting_external", collect_external_waits(layout))
            _write_state(layout, result)
            return result
    else:
        generate_audio(layout, force=force, allow_external=allow_external)
    generate_captions(layout, force=force)
    select_music(layout, force=force)

    if _all_staged_images_ready(layout):
        import_staged_images(layout, force=force)

    project = layout.load_manifest()
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
        and project.stages["images"].status == StageStatus.COMPLETED
    ):
        from pipeline_videos.growth import prepare_growth_queue

        prepare_growth_queue(layout, force=force)

    project = layout.load_manifest()
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
        and _growth_outputs_ready(layout)
    ):
        from pipeline_videos.growth import (
            check_growth_package_staging,
            import_growth_package,
            materialize_compact_growth_fallback,
        )

        check = check_growth_package_staging(layout)
        if (
            not check.valid
            and project.config.youtube_growth.generation_mode == "compact_metadata_draft_v1"
            and project.config.production.repair_policy == "bounded_then_fallback_v1"
        ):
            materialize_compact_growth_fallback(layout)
        import_growth_package(layout, force=force)

    waits = [wait for wait in collect_external_waits(layout) if not wait.get("ready")]
    if waits:
        result = AutopilotResult("waiting_external", waits)
        _write_state(layout, result)
        return result

    project = layout.load_manifest()
    from pipeline_videos.metadata import (
        build_metadata,
        generate_fixture_thumbnail,
        queue_thumbnail_for_codex,
    )

    if not project.demo and project.config.youtube_growth.enabled:
        if project.stages["metadata"].status != StageStatus.COMPLETED:
            from pipeline_videos.growth import refresh_growth_metadata

            refresh_growth_metadata(layout)
    else:
        build_metadata(layout, force=force)

    if project.demo or project.config.images.provider == "fixture":
        generate_fixture_thumbnail(layout, force=force)
    elif not project.config.youtube_growth.enabled and not layout.thumbnail.exists():
        queue_thumbnail_for_codex(layout)

    from pipeline_videos.render import render_review_candidate
    from pipeline_videos.validation import validate_project

    render = render_review_candidate(layout, force=force)
    report = validate_project(layout, require_render=True)
    result = AutopilotResult(
        "completed",
        [],
        render_path=render.final_video,
        validation_ok=report.valid,
    )
    _write_state(layout, result)
    return result


def wait_until_external_ready(
    layout: ProjectLayout,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        waits = collect_external_waits(layout)
        blocking = [wait for wait in waits if not wait.get("ready")]
        if not blocking:
            return waits
        if time.monotonic() >= deadline:
            labels = ", ".join(str(wait.get("kind")) for wait in blocking)
            raise TimeoutError(f"Tempo esgotado aguardando saídas externas: {labels}")
        time.sleep(poll_seconds)
