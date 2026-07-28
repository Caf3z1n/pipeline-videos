from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pipeline_videos.models import StageStatus
from pipeline_videos.storage import ProjectLayout, read_json, write_json
from pipeline_videos.utils import relative_posix, sha256_value, utc_now


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
    return isinstance(package, str) and _is_nonempty_file(layout.root / package)


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

    if project.config.long_form_profile.enabled:
        if not layout.long_form_edit_plan.is_file() and layout.long_form_edit_plan_queue.is_file():
            waits.append(
                {
                    "kind": "long_form_edit_plan",
                    "ready": _is_nonempty_file(layout.long_form_edit_plan_staging_file),
                    "queue": relative_posix(
                        layout.long_form_edit_plan_queue,
                        layout.root,
                    ),
                }
            )
        elif layout.long_form_asset_queue.is_file():
            asset_queue = read_json(layout.long_form_asset_queue)
            pending_assets = [
                item
                for item in asset_queue.get("items", [])
                if isinstance(item, dict) and item.get("status") != "completed"
            ]
            if pending_assets:
                waits.append(
                    {
                        "kind": "cutout_assets",
                        "ready": _cutout_asset_staging_ready(layout),
                        "pending": len(pending_assets),
                        "jobs_directory": relative_posix(
                            layout.long_form_asset_jobs,
                            layout.root,
                        ),
                    }
                )
    else:
        pending_images = _pending_image_entries(layout)
        if pending_images:
            queue = read_json(layout.image_queue)
            waits.append(
                {
                    "kind": "images",
                    "ready": _all_staged_images_ready(layout),
                    "pending": len(pending_images),
                    "worker_job_files": queue.get("orchestration", {}).get(
                        "worker_job_files",
                        {},
                    ),
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
    external_roles: dict[str, Any] = {
        "image_workers": {
            "jobs_directory": relative_posix(
                (
                    layout.long_form_asset_jobs
                    if project.config.long_form_profile.enabled
                    else layout.image_jobs
                ),
                layout.root,
            ),
            "maximum_parallel": (
                project.config.long_form_profile.image_worker_limit
                if project.config.long_form_profile.enabled
                else min(2, project.config.production.image_workers)
            ),
            "reasoning_effort": "low",
            "fork_turns": "none",
            "when_present": True,
        },
    }
    if (
        project.config.youtube_growth.enabled
        and project.config.youtube_growth.use_specialist_subagent
    ):
        external_roles["youtube_growth_specialist"] = {
            "queue": relative_posix(layout.growth_queue, layout.root),
            "reasoning_effort": "medium",
            "when_present": True,
        }
    if (
        project.config.long_form_profile.enabled
        and project.config.long_form_profile.planner_model_passes > 0
    ):
        external_roles["long_form_visual_editor"] = {
            "queue": relative_posix(layout.long_form_edit_plan_queue, layout.root),
            "reasoning_effort": "medium",
            "fork_turns": "none",
            "when_ready_after_audio": True,
            "model_pass_limit": project.config.long_form_profile.planner_model_passes,
            "aggregate_repair_limit": (project.config.long_form_profile.aggregate_model_repairs),
        }
    if (
        project.video_format == "short"
        and project.stages["shorts_script"].status != StageStatus.COMPLETED
    ):
        visual_plan_only = (
            project.config.shorts_script.generation_mode == "deterministic_compact_visual_plan_v1"
        )
        if visual_plan_only or project.config.shorts_script.use_specialist_subagent:
            role = "shorts_visual_planner" if visual_plan_only else "shorts_script_specialist"
            external_roles[role] = {
                "queue": relative_posix(layout.shorts_script_queue, layout.root),
                "reasoning_effort": "medium" if visual_plan_only else "high",
                "when_present": True,
            }
        if (
            not visual_plan_only
            and project.config.shorts_script.require_independent_biblical_review
        ):
            external_roles["biblical_reviewer"] = {
                "queue": relative_posix(layout.shorts_biblical_review_queue, layout.root),
                "reasoning_effort": "high",
                "when_present": True,
            }
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
        "external_roles": external_roles,
        "policies": {
            "repair_policy": project.config.production.repair_policy,
            "max_model_repairs_per_stage": (project.config.production.max_model_repairs_per_stage),
            "token_budget_behavior": project.config.production.token_budget_behavior,
            "validation_policy": project.config.production.validation_policy,
            "publication_authorized": False,
            "one_final_render": True,
            "duration_policy": project.duration_policy,
            "duration_target_is_advisory": project.duration_policy
            in {"preferred_target_v1", "approved_script_locked_v1"},
            "duration_is_defined_by_approved_script": (
                project.duration_policy == "approved_script_locked_v1"
            ),
            "natural_voice_speed": project.config.audio.speed,
            "forbid_duration_based_speech_rate_changes": True,
            "forbid_duration_padding_or_time_stretch": True,
            "long_form_profile": project.config.long_form_profile.profile_id,
            "plan_before_assets": project.config.long_form_profile.enabled,
            "asset_generation_requires_valid_aggregate_preflight": (
                project.config.long_form_profile.enabled
            ),
            "forbid_text_asset_overlap": project.config.long_form_profile.enabled,
            "forbid_generic_asset_fallback": project.config.long_form_profile.enabled,
        },
    }
    write_json(layout.coordinator_job, payload)
    return payload


def _cutout_asset_staging_ready(layout: ProjectLayout) -> bool:
    if not layout.long_form_asset_queue.is_file():
        return False
    queue = read_json(layout.long_form_asset_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        return False
    pending = [
        item
        for item in queue["items"]
        if isinstance(item, dict) and item.get("status") != "completed"
    ]
    return bool(pending) and all(
        isinstance(item.get("staging_path"), str)
        and _is_nonempty_file(layout.root / item["staging_path"])
        for item in pending
    )


def _run_cutout_autopilot_once(
    layout: ProjectLayout,
    *,
    allow_external: bool,
    force: bool,
) -> AutopilotResult:
    """Run the opt-in plan-first profile without entering the legacy image/render path."""

    from pipeline_videos.audio import generate_audio
    from pipeline_videos.long_form_profile import (
        compile_project_edit_plan,
        import_profile_assets,
        prepare_profile_asset_queue,
    )
    from pipeline_videos.music import select_music
    from pipeline_videos.scenes import build_scenes
    from pipeline_videos.state import complete_stage

    if layout.approved_script_lock.is_file():
        from pipeline_videos.approved_scripts import verify_project_approved_script_lock

        verify_project_approved_script_lock(layout)
    else:
        from pipeline_videos.review import validate_draft_preflight

        validate_draft_preflight(layout)
    build_scenes(layout, force=force, skip_approvals=True)

    project = layout.load_manifest()
    if project.config.audio.provider == "local_manual":
        from pipeline_videos.manual_audio import prepare_manual_audio

        prepare_manual_audio(layout, force=force)
        if project.stages["audio"].status != StageStatus.COMPLETED:
            result = AutopilotResult(
                "waiting_external",
                [
                    {
                        "kind": "manual_audio",
                        "ready": False,
                        "queue": relative_posix(layout.manual_audio_queue, layout.root),
                    }
                ],
            )
            _write_state(layout, result)
            return result
    else:
        generate_audio(layout, force=force, allow_external=allow_external)
    select_music(layout, force=force)

    project = layout.load_manifest()
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
    ):
        from pipeline_videos.growth import prepare_growth_queue

        prepare_growth_queue(layout, force=force)

    _, report, _ = compile_project_edit_plan(layout, force=force)
    if not report.valid:
        raise RuntimeError("O plano visual cutout não preservou os invariantes compilados.")

    queue = prepare_profile_asset_queue(layout, force=force)
    if queue["items"]:
        if _cutout_asset_staging_ready(layout):
            inventory = import_profile_assets(layout)
            complete_stage(
                layout,
                "images",
                sha256_value(inventory),
                [
                    relative_posix(layout.long_form_asset_inventory, layout.root),
                    relative_posix(layout.long_form_asset_queue, layout.root),
                ],
                provider="cutout-profile-asset-batch",
            )
        else:
            result = AutopilotResult(
                "waiting_external",
                [
                    {
                        "kind": "cutout_assets",
                        "ready": False,
                        "pending": len(queue["items"]),
                        "queue": relative_posix(
                            layout.long_form_asset_queue,
                            layout.root,
                        ),
                        "jobs_directory": relative_posix(
                            layout.long_form_asset_jobs,
                            layout.root,
                        ),
                    }
                ],
            )
            _write_state(layout, result)
            return result
    else:
        inventory = read_json(layout.long_form_asset_inventory)
        complete_stage(
            layout,
            "images",
            sha256_value(inventory),
            [
                relative_posix(layout.long_form_asset_inventory, layout.root),
                relative_posix(layout.long_form_asset_queue, layout.root),
            ],
            provider="cutout-profile-reused-assets",
        )

    project = layout.load_manifest()
    if project.config.youtube_growth.generation_mode == "script_package_local_v1":
        from pipeline_videos.growth import materialize_cutout_growth_package
        from pipeline_videos.thumbnail import compose_cutout_thumbnail

        materialize_cutout_growth_package(layout, force=force)
        compose_cutout_thumbnail(layout, force=force)

    project = layout.load_manifest()
    project.stages["image_manifest"] = project.stages["image_manifest"].model_copy(
        update={
            "status": StageStatus.SKIPPED,
            "note": "O perfil cutout usa inventário deduplicado em vez do manifesto por cena.",
        }
    )
    project.stages["captions"] = project.stages["captions"].model_copy(
        update={
            "status": StageStatus.SKIPPED,
            "note": "O perfil cutout não usa legendas convencionais.",
        }
    )
    layout.save_manifest(project)

    from pipeline_videos.cutout_render import render_cutout_profile

    render_result = render_cutout_profile(layout, force=force)
    project = layout.load_manifest()
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
        and _growth_outputs_ready(layout)
    ):
        from pipeline_videos.growth import import_growth_package

        import_growth_package(layout, force=force)
    waits = [
        wait
        for wait in collect_external_waits(layout)
        if wait.get("kind") == "youtube_growth" and not wait.get("ready")
    ]
    if waits:
        result = AutopilotResult(
            "waiting_external",
            waits,
            render_path=layout.final_video,
            validation_ok=True,
        )
        _write_state(layout, result)
        return result

    from pipeline_videos.metadata import build_metadata

    project = layout.load_manifest()
    if not project.demo and project.config.youtube_growth.enabled:
        if project.stages["metadata"].status != StageStatus.COMPLETED:
            from pipeline_videos.growth import refresh_growth_metadata

            refresh_growth_metadata(layout)
    elif project.stages["metadata"].status != StageStatus.COMPLETED:
        build_metadata(layout, force=force)
    validation_path = layout.long_form_edit_preflight.parent / "cutout-render.json"
    complete_stage(
        layout,
        "validation",
        render_result["input_hash"],
        [relative_posix(validation_path, layout.root)],
        provider="cutout-profile-aggregate-validator",
    )
    result = AutopilotResult(
        "completed",
        [],
        render_path=layout.final_video,
        validation_ok=True,
    )
    _write_state(layout, result)
    return result


def run_autopilot_once(
    layout: ProjectLayout,
    *,
    allow_external: bool = False,
    force: bool = False,
) -> AutopilotResult:
    prepare_local_coordinator_job(layout)
    project = layout.load_manifest()
    if project.config.long_form_profile.enabled:
        return _run_cutout_autopilot_once(
            layout,
            allow_external=allow_external,
            force=force,
        )

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

    from pipeline_videos.scenes import build_scenes

    if layout.approved_script_lock.is_file():
        from pipeline_videos.approved_scripts import verify_project_approved_script_lock

        verify_project_approved_script_lock(layout)
    else:
        from pipeline_videos.review import validate_draft_preflight

        validate_draft_preflight(layout)
    build_scenes(layout, force=force, skip_approvals=True)

    from pipeline_videos.audio import generate_audio
    from pipeline_videos.captions import generate_captions
    from pipeline_videos.images import import_staged_images, prepare_images
    from pipeline_videos.music import select_music

    prepare_images(layout, force=force)
    project = layout.load_manifest()
    if (
        not project.demo
        and project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status != StageStatus.COMPLETED
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
    from pipeline_videos.metadata import build_metadata

    if not project.demo and project.config.youtube_growth.enabled:
        if project.stages["metadata"].status != StageStatus.COMPLETED:
            from pipeline_videos.growth import refresh_growth_metadata

            refresh_growth_metadata(layout)
    elif project.stages["metadata"].status != StageStatus.COMPLETED:
        build_metadata(layout, force=force)

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
