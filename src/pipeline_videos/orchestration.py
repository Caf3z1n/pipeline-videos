from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from pipeline_videos.models import ProjectManifest, StageStatus
from pipeline_videos.storage import ProjectLayout, read_json

ORCHESTRATION_PLAN_SCHEMA_VERSION = "1.0"
MAX_PARALLEL_SUBAGENTS = 3
_DONE_STATUSES = {StageStatus.COMPLETED.value, StageStatus.SKIPPED.value}
_READY_STATUSES = _DONE_STATUSES | {"ready", "ready_for_import"}


def _stage_status(manifest: ProjectManifest, stage: str) -> str:
    record = manifest.stages.get(stage)
    return record.status.value if record is not None else "missing"


def _read_optional_queue(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    queue = read_json(path)
    if not isinstance(queue, dict):
        raise ValueError(f"Fila inválida: {path} precisa conter um objeto JSON.")
    return queue


def _relative_path(layout: ProjectLayout, path: Path) -> str:
    return path.relative_to(layout.root).as_posix()


def _isolated_staging_path(
    layout: ProjectLayout,
    value: object,
    *,
    expected_prefix: tuple[str, ...],
    label: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} precisa declarar um staging_path relativo.")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or (path.parts and path.parts[0].endswith(":")):
        raise ValueError(f"{label} usa staging_path inseguro: {value}")
    if path.parts[: len(expected_prefix)] != expected_prefix:
        prefix = "/".join(expected_prefix)
        raise ValueError(f"{label} precisa permanecer isolado em {prefix}/: {value}")
    resolved = (layout.root / Path(*path.parts)).resolve()
    if not resolved.is_relative_to(layout.root.resolve()):
        raise ValueError(f"{label} escapa da raiz do projeto: {value}")
    return path.as_posix()


def _staging_exists(layout: ProjectLayout, relative_path: str) -> bool:
    return (layout.root / Path(*PurePosixPath(relative_path).parts)).is_file()


def _queue_output_paths(
    layout: ProjectLayout,
    queue: Mapping[str, Any] | None,
    *,
    expected_prefix: tuple[str, ...],
    label: str,
) -> list[str]:
    if queue is None:
        return []
    outputs = queue.get("outputs")
    if not isinstance(outputs, Mapping):
        return []
    candidates: list[object] = []
    if outputs.get("package") is not None:
        candidates.append(outputs["package"])
    if outputs.get("review") is not None:
        candidates.append(outputs["review"])
    thumbnails = outputs.get("thumbnails")
    if isinstance(thumbnails, list):
        candidates.extend(thumbnails)
    return [
        _isolated_staging_path(
            layout,
            candidate,
            expected_prefix=expected_prefix,
            label=f"{label}[{index}]",
        )
        for index, candidate in enumerate(candidates, start=1)
    ]


def _external_task_status(
    stage_status: str,
    queue: Mapping[str, Any] | None,
    staging_paths: list[str],
    layout: ProjectLayout,
    *,
    enabled: bool,
) -> str:
    if not enabled:
        return StageStatus.SKIPPED.value
    if stage_status in _DONE_STATUSES:
        return stage_status
    if staging_paths and all(_staging_exists(layout, path) for path in staging_paths):
        return "ready_for_import"
    if queue is not None:
        status = queue.get("status")
        if isinstance(status, str):
            return status
    return stage_status


def _ordered_image_jobs(
    layout: ProjectLayout,
    queue: Mapping[str, Any] | None,
    *,
    images_stage_status: str,
) -> list[dict[str, Any]]:
    raw_items = queue.get("items", []) if queue is not None else []
    if not isinstance(raw_items, list):
        raise ValueError("images/codex-queue.json precisa declarar items como lista.")
    jobs: list[dict[str, Any]] = []
    job_ids: set[str] = set()
    staging_paths: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("Cada item da fila de imagens precisa ser um objeto.")
        job_id = raw.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("Cada item da fila de imagens precisa declarar job_id.")
        if job_id in job_ids:
            raise ValueError(f"job_id duplicado na fila de imagens: {job_id}")
        staging_path = _isolated_staging_path(
            layout,
            raw.get("staging_path"),
            expected_prefix=("images", "staging"),
            label=job_id,
        )
        if staging_path in staging_paths:
            raise ValueError(f"staging_path compartilhado por mais de um job: {staging_path}")
        job_ids.add(job_id)
        staging_paths.add(staging_path)
        queue_status = raw.get("status", StageStatus.PENDING.value)
        if not isinstance(queue_status, str):
            queue_status = StageStatus.PENDING.value
        staging_ready = _staging_exists(layout, staging_path)
        effective_status = (
            StageStatus.COMPLETED.value
            if images_stage_status == StageStatus.COMPLETED.value
            else "ready_for_import"
            if staging_ready
            else queue_status
        )
        version = raw.get("version")
        jobs.append(
            {
                "job_id": job_id,
                "scene_id": raw.get("scene_id"),
                "image_id": raw.get("image_id"),
                "version": version if isinstance(version, int) else None,
                "queue_status": queue_status,
                "status": effective_status,
                "staging_path": staging_path,
                "staging_ready": staging_ready,
                "requested_worker_id": raw.get("worker_id"),
            }
        )
    jobs.sort(
        key=lambda item: (
            str(item["scene_id"] or ""),
            item["version"] if item["version"] is not None else 0,
            item["job_id"],
        )
    )
    for order, job in enumerate(jobs, start=1):
        job["order"] = order
    return jobs


def recommend_next_image_assignment(
    plan: Mapping[str, Any],
    *,
    busy_worker_ids: Iterable[str] = (),
    claimed_job_ids: Iterable[str] = (),
) -> dict[str, Any] | None:
    """Return the next deterministic pull assignment without mutating the plan."""
    assignment = plan.get("dynamic_image_assignment")
    if not isinstance(assignment, Mapping):
        return None
    busy = set(busy_worker_ids)
    claimed = set(claimed_job_ids)
    workers = assignment.get("worker_slots", [])
    jobs = assignment.get("jobs", [])
    if not isinstance(workers, list) or not isinstance(jobs, list):
        return None
    worker_id = next((worker for worker in workers if worker not in busy), None)
    if not isinstance(worker_id, str):
        return None
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        job_id = job.get("job_id")
        if (
            isinstance(job_id, str)
            and job_id not in claimed
            and not job.get("staging_ready", False)
            and job.get("status") != StageStatus.COMPLETED.value
        ):
            return {
                "worker_id": worker_id,
                "job_id": job_id,
                "order": job.get("order"),
                "scene_id": job.get("scene_id"),
                "staging_path": job.get("staging_path"),
            }
    return None


def _initial_image_assignments(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    assignment = plan["dynamic_image_assignment"]
    workers = assignment["worker_slots"]
    busy: set[str] = set()
    claimed: set[str] = set()
    recommendations: list[dict[str, Any]] = []
    for _ in workers:
        recommendation = recommend_next_image_assignment(
            plan,
            busy_worker_ids=busy,
            claimed_job_ids=claimed,
        )
        if recommendation is None:
            break
        recommendations.append(recommendation)
        busy.add(recommendation["worker_id"])
        claimed.add(recommendation["job_id"])
    return recommendations


def _task(
    task_id: str,
    *,
    owner: str,
    status: str,
    depends_on: Iterable[str] = (),
    writes_project_state: bool,
    staging_paths: Iterable[str] = (),
    command: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "owner": owner,
        "status": status,
        "depends_on": list(depends_on),
        "writes_project_state": writes_project_state,
        "staging_paths": list(staging_paths),
        "command": command,
    }


def _import_status(stage_status: str, external_status: str, *, enabled: bool) -> str:
    if not enabled:
        return StageStatus.SKIPPED.value
    if stage_status in _DONE_STATUSES:
        return stage_status
    if external_status == "ready_for_import":
        return "ready"
    return "blocked"


def _barrier_status(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if all(value in _DONE_STATUSES for value in values):
        return StageStatus.COMPLETED.value
    if all(value in _READY_STATUSES for value in values):
        return "ready"
    return "blocked"


def build_one_shot_plan(
    layout: ProjectLayout,
    *,
    allow_external: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, serializable and read-only one-shot execution plan."""
    manifest = layout.load_manifest()
    shorts_enabled = manifest.video_format == "short" and manifest.config.shorts_script.enabled
    review_required = (
        shorts_enabled and manifest.config.shorts_script.require_independent_biblical_review
    )
    growth_enabled = not manifest.demo and manifest.config.youtube_growth.enabled
    shorts_queue = _read_optional_queue(layout.shorts_script_queue)
    review_queue = _read_optional_queue(layout.shorts_biblical_review_queue)
    image_queue = _read_optional_queue(layout.image_queue)
    growth_queue = _read_optional_queue(layout.growth_queue)

    shorts_outputs = _queue_output_paths(
        layout,
        shorts_queue,
        expected_prefix=("script", "staging", "shorts-script-specialist"),
        label="shorts_script",
    )
    review_outputs = _queue_output_paths(
        layout,
        review_queue,
        expected_prefix=("reviews", "staging", "biblical-reviewer"),
        label="biblical_review",
    )
    growth_outputs = _queue_output_paths(
        layout,
        growth_queue,
        expected_prefix=("youtube", "staging", "youtube-growth-specialist"),
        label="youtube_growth",
    )
    shorts_stage = _stage_status(manifest, "shorts_script")
    images_stage = _stage_status(manifest, "images")
    growth_stage = _stage_status(manifest, "youtube_growth")
    shorts_external_status = _external_task_status(
        shorts_stage,
        shorts_queue,
        shorts_outputs,
        layout,
        enabled=shorts_enabled,
    )
    review_external_status = _external_task_status(
        shorts_stage,
        review_queue,
        review_outputs,
        layout,
        enabled=review_required,
    )
    growth_external_status = _external_task_status(
        growth_stage,
        growth_queue,
        growth_outputs,
        layout,
        enabled=growth_enabled,
    )
    image_jobs = _ordered_image_jobs(
        layout,
        image_queue,
        images_stage_status=images_stage,
    )
    reserved_growth_slots = 1 if growth_enabled else 0
    image_workers = (
        min(
            MAX_PARALLEL_SUBAGENTS - reserved_growth_slots, manifest.config.production.image_workers
        )
        if manifest.config.production.use_subagents
        else 1
    )
    worker_slots = (
        [f"image-worker-{index}" for index in range(1, image_workers + 1)]
        if manifest.config.production.use_subagents
        else ["main"]
    )
    image_external_status = images_stage
    if image_jobs and images_stage not in _DONE_STATUSES:
        image_external_status = (
            "ready_for_import"
            if all(job["status"] in _READY_STATUSES for job in image_jobs)
            else str(image_queue.get("status", images_stage))
            if image_queue is not None
            else images_stage
        )

    audio_status = _stage_status(manifest, "audio")
    if (
        manifest.config.audio.provider == "elevenlabs"
        and audio_status not in _DONE_STATUSES
        and not allow_external
    ):
        audio_status = "blocked_external_guard"
    elif manifest.config.audio.provider == "local_manual" and audio_status not in _DONE_STATUSES:
        audio_status = "waiting_manual_audio"

    plan: dict[str, Any] = {
        "schema_version": ORCHESTRATION_PLAN_SCHEMA_VERSION,
        "project_id": manifest.project_id,
        "mode": manifest.config.production.mode,
        "read_only": True,
        "single_writer": {
            "owner": "main",
            "shared_state": "project.json",
            "imports": [
                "script shorts-import",
                "images import-batch",
                "metadata growth-import",
            ],
            "rule": (
                "Subagentes escrevem somente em staging; o principal serializa "
                "importações e etapas locais."
            ),
        },
        "dynamic_image_assignment": {
            "policy": "ordered_pull_first_free_worker",
            "max_parallel_workers": image_workers,
            "worker_slots": worker_slots,
            "jobs": image_jobs,
            "initial_assignments": [],
            "next_after_initial": None,
            "staging_isolation": "images/staging/<job> salvo por um único worker",
            "import_owner": "main",
        },
        "waves": [],
        "current_pause": None,
    }

    initial_assignments = _initial_image_assignments(plan)
    plan["dynamic_image_assignment"]["initial_assignments"] = initial_assignments
    claimed = {item["job_id"] for item in initial_assignments}
    next_job = next(
        (
            job
            for job in image_jobs
            if job["job_id"] not in claimed
            and not job["staging_ready"]
            and job["status"] != StageStatus.COMPLETED.value
        ),
        None,
    )
    if next_job is not None:
        plan["dynamic_image_assignment"]["next_after_initial"] = {
            "job_id": next_job["job_id"],
            "order": next_job["order"],
            "staging_path": next_job["staging_path"],
            "worker_id": "first_free_worker",
        }

    shorts_ready_for_import = shorts_external_status == "ready_for_import" and (
        not review_required or review_external_status == "ready_for_import"
    )
    shorts_import_status = _import_status(
        shorts_stage,
        "ready_for_import" if shorts_ready_for_import else "blocked",
        enabled=shorts_enabled,
    )
    script_generation_task_id = "shorts_script_generation"
    visual_plan_only = (
        manifest.config.shorts_script.generation_mode == "deterministic_compact_visual_plan_v1"
    )
    script_tasks = [
        _task(
            script_generation_task_id,
            owner=(
                "shorts_visual_planner"
                if visual_plan_only
                else "shorts_script_specialist"
                if manifest.config.shorts_script.use_specialist_subagent
                else "main"
            ),
            status=shorts_external_status,
            depends_on=("briefing",),
            writes_project_state=False,
            staging_paths=shorts_outputs,
        ),
    ]
    script_import_dependency = script_generation_task_id
    if review_required:
        script_import_dependency = "independent_biblical_review"
        script_tasks.append(
            _task(
                script_import_dependency,
                owner="biblical_reviewer",
                status=review_external_status,
                depends_on=(script_generation_task_id,),
                writes_project_state=False,
                staging_paths=review_outputs,
                command="script shorts-review-prepare",
            )
        )
    script_tasks.extend(
        [
            _task(
                "shorts_script_import",
                owner="main",
                status=shorts_import_status,
                depends_on=(script_import_dependency,),
                writes_project_state=True,
                command="script shorts-import",
            ),
            _task(
                "script",
                owner="main",
                status=_stage_status(manifest, "script"),
                depends_on=("shorts_script_import" if shorts_enabled else "briefing",),
                writes_project_state=True,
            ),
            _task(
                "biblical_review",
                owner="main",
                status=_stage_status(manifest, "biblical_review"),
                depends_on=("script",),
                writes_project_state=True,
            ),
            _task(
                "scenes",
                owner="main",
                status=_stage_status(manifest, "scenes"),
                depends_on=("script", "biblical_review"),
                writes_project_state=True,
            ),
        ]
    )

    image_task_ids: list[str] = []
    parallel_tasks: list[dict[str, Any]] = []
    recommended_owners = {item["job_id"]: item["worker_id"] for item in initial_assignments}
    for job in image_jobs:
        task_id = f"image:{job['job_id']}"
        image_task_ids.append(task_id)
        parallel_tasks.append(
            _task(
                task_id,
                owner=(
                    recommended_owners.get(job["job_id"], "first_free_image_worker")
                    if manifest.config.production.use_subagents
                    else "main"
                ),
                status=job["status"],
                depends_on=("scenes",),
                writes_project_state=False,
                staging_paths=(job["staging_path"],),
            )
        )
    if not image_task_ids:
        image_task_ids.append("images_queue")
        parallel_tasks.append(
            _task(
                "images_queue",
                owner="image-worker-pool" if manifest.config.production.use_subagents else "main",
                status=images_stage,
                depends_on=("scenes",),
                writes_project_state=False,
            )
        )
    parallel_tasks.extend(
        [
            _task(
                "youtube_growth_specialist",
                owner=(
                    "youtube_growth_specialist"
                    if manifest.config.youtube_growth.use_specialist_subagent
                    else "main"
                ),
                status=growth_external_status,
                depends_on=("scenes",),
                writes_project_state=False,
                staging_paths=growth_outputs,
            ),
            _task(
                "audio",
                owner="main",
                status=audio_status,
                depends_on=("scenes",),
                writes_project_state=True,
            ),
            _task(
                "captions",
                owner="main",
                status=_stage_status(manifest, "captions"),
                depends_on=("audio",),
                writes_project_state=True,
            ),
            _task(
                "music",
                owner="main",
                status=_stage_status(manifest, "music"),
                depends_on=("scenes",),
                writes_project_state=True,
            ),
        ]
    )

    images_import_status = _import_status(
        images_stage,
        image_external_status,
        enabled=True,
    )
    growth_import_status = _import_status(
        growth_stage,
        growth_external_status,
        enabled=growth_enabled,
    )
    growth_import_dependencies = (
        ("youtube_growth_specialist", "images_import_batch")
        if manifest.video_format == "short"
        else ("youtube_growth_specialist",)
    )
    join_requirements = [
        images_import_status,
        growth_import_status,
        audio_status,
        _stage_status(manifest, "captions"),
        _stage_status(manifest, "music"),
        _stage_status(manifest, "metadata"),
        _stage_status(manifest, "thumbnail"),
    ]
    join_status = _barrier_status(join_requirements)
    join_tasks = [
        _task(
            "images_import_batch",
            owner="main",
            status=images_import_status,
            depends_on=image_task_ids,
            writes_project_state=True,
            command="images import-batch",
        ),
        _task(
            "growth_import",
            owner="main",
            status=growth_import_status,
            depends_on=growth_import_dependencies,
            writes_project_state=True,
            command="metadata growth-import",
        ),
        _task(
            "metadata",
            owner="main",
            status=_stage_status(manifest, "metadata"),
            depends_on=("growth_import", "audio"),
            writes_project_state=True,
        ),
        _task(
            "thumbnail",
            owner="main",
            status=_stage_status(manifest, "thumbnail"),
            depends_on=("growth_import",),
            writes_project_state=True,
        ),
        _task(
            "production_join",
            owner="main",
            status=join_status,
            depends_on=(
                "images_import_batch",
                "growth_import",
                "audio",
                "captions",
                "music",
                "metadata",
                "thumbnail",
            ),
            writes_project_state=False,
        ),
    ]

    plan["waves"] = [
        {
            "wave_id": "wave-01-script",
            "label": "Roteiro sequencial",
            "execution": "sequential",
            "depends_on": [],
            "lanes": [
                {
                    "lane_id": "script-production",
                    "tasks": [task["task_id"] for task in script_tasks],
                }
            ],
            "tasks": script_tasks,
        },
        {
            "wave_id": "wave-02-parallel-production",
            "label": "Produção paralela isolada",
            "execution": "parallel_lanes",
            "depends_on": ["wave-01-script"],
            "lanes": [
                {
                    "lane_id": "main",
                    "execution": "serialized_single_writer",
                    "recommended_order": ["audio", "captions", "music"],
                    "tasks": ["audio", "captions", "music"],
                },
                {
                    "lane_id": "youtube-growth-specialist",
                    "execution": "single_external_worker",
                    "tasks": ["youtube_growth_specialist"],
                },
                {
                    "lane_id": "image-worker-pool",
                    "execution": "dynamic_ordered_pull",
                    "max_workers": image_workers,
                    "tasks": image_task_ids,
                },
            ],
            "tasks": parallel_tasks,
        },
        {
            "wave_id": "wave-03-join-imports",
            "label": "Join e importações do principal",
            "execution": "sequential_single_writer",
            "depends_on": ["wave-02-parallel-production"],
            "lanes": [{"lane_id": "main", "tasks": [task["task_id"] for task in join_tasks]}],
            "tasks": join_tasks,
        },
        {
            "wave_id": "wave-04-render",
            "label": "Render",
            "execution": "sequential",
            "depends_on": ["wave-03-join-imports"],
            "lanes": [{"lane_id": "main", "tasks": ["render"]}],
            "tasks": [
                _task(
                    "render",
                    owner="main",
                    status=_stage_status(manifest, "render"),
                    depends_on=("production_join",),
                    writes_project_state=True,
                )
            ],
        },
        {
            "wave_id": "wave-05-validation",
            "label": "Validação",
            "execution": "sequential",
            "depends_on": ["wave-04-render"],
            "lanes": [{"lane_id": "main", "tasks": ["validation"]}],
            "tasks": [
                _task(
                    "validation",
                    owner="main",
                    status=_stage_status(manifest, "validation"),
                    depends_on=("render",),
                    writes_project_state=True,
                )
            ],
        },
    ]

    pending_image_jobs = [
        job
        for job in image_jobs
        if not job["staging_ready"] and job["status"] != StageStatus.COMPLETED.value
    ]
    if shorts_enabled and shorts_stage not in _DONE_STATUSES:
        if shorts_external_status != "ready_for_import":
            plan["current_pause"] = script_generation_task_id
        elif review_required and review_external_status != "ready_for_import":
            plan["current_pause"] = "independent_biblical_review"
        else:
            plan["current_pause"] = "shorts_script_import"
    elif pending_image_jobs or (
        growth_enabled and growth_external_status == StageStatus.WAITING_EXTERNAL.value
    ):
        plan["current_pause"] = "external_generation"
    elif images_import_status == "ready" or growth_import_status == "ready":
        plan["current_pause"] = "join_imports"
    elif audio_status == "blocked_external_guard":
        plan["current_pause"] = "audio_external_guard"
    elif audio_status == "waiting_manual_audio":
        plan["current_pause"] = "manual_audio_upload"
    return plan


def format_plan_lines(plan: Mapping[str, Any]) -> list[str]:
    """Render the orchestration plan as compact CLI-friendly text lines."""
    single_writer = plan["single_writer"]
    lines = [
        f"Projeto: {plan['project_id']} | modo: {plan['mode']} | read-only: {plan['read_only']}",
        f"Escritor único: {single_writer['owner']} -> {single_writer['shared_state']}",
    ]
    for wave in plan["waves"]:
        dependencies = ", ".join(wave["depends_on"]) or "início"
        lines.append(f"{wave['wave_id']} · {wave['label']} [{wave['execution']}] <- {dependencies}")
        for lane in wave["lanes"]:
            tasks = " -> ".join(lane["tasks"])
            suffix = f" (máx. {lane['max_workers']})" if lane.get("max_workers") is not None else ""
            lines.append(f"  lane {lane['lane_id']}{suffix}: {tasks}")
        for task in wave["tasks"]:
            dependencies = ", ".join(task["depends_on"]) or "—"
            lines.append(
                f"    {task['task_id']}: {task['status']} | "
                f"owner={task['owner']} | deps={dependencies}"
            )
    assignment = plan["dynamic_image_assignment"]
    lines.append(
        "Imagem dinâmica: "
        f"{assignment['policy']}; {assignment['max_parallel_workers']} worker(s); "
        f"{len(assignment['jobs'])} job(s) ordenado(s)."
    )
    for item in assignment["initial_assignments"]:
        lines.append(
            f"  pull inicial: {item['worker_id']} -> {item['job_id']} -> {item['staging_path']}"
        )
    if assignment["next_after_initial"] is not None:
        item = assignment["next_after_initial"]
        lines.append(
            f"  próximo livre: {item['worker_id']} -> {item['job_id']} -> {item['staging_path']}"
        )
    if plan.get("current_pause"):
        lines.append(f"Pausa atual: {plan['current_pause']}")
    return lines
