from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline_videos.models import (
    ApprovalRecord,
    ApprovalStatus,
    ProjectManifest,
    StageRecord,
    StageStatus,
)
from pipeline_videos.storage import ProjectLayout
from pipeline_videos.utils import utc_now

STAGE_ORDER = (
    "briefing",
    "shorts_script",
    "script",
    "biblical_review",
    "scenes",
    "image_manifest",
    "images",
    "audio",
    "music",
    "captions",
    "youtube_growth",
    "metadata",
    "render",
    "validation",
    "publication",
)

APPROVAL_KEYS = (
    "briefing",
    "script",
    "biblical_review",
    "images",
    "metadata",
    "final_render",
)


def initial_stage_records() -> dict[str, StageRecord]:
    return {name: StageRecord(updated_at=utc_now()) for name in STAGE_ORDER}


def initial_approvals() -> dict[str, ApprovalRecord]:
    return {name: ApprovalRecord() for name in APPROVAL_KEYS}


def artifacts_exist(layout: ProjectLayout, artifacts: list[str]) -> bool:
    return bool(artifacts) and all((layout.root / Path(item)).is_file() for item in artifacts)


def stage_is_current(
    layout: ProjectLayout,
    stage: str,
    input_hash: str,
    *,
    force: bool = False,
) -> bool:
    if force:
        return False
    record = layout.load_manifest().stages[stage]
    return (
        record.status == StageStatus.COMPLETED
        and record.input_hash == input_hash
        and artifacts_exist(layout, record.artifacts)
    )


def start_stage(
    layout: ProjectLayout,
    stage: str,
    input_hash: str,
    provider: str | None = None,
) -> ProjectManifest:
    manifest = layout.load_manifest()
    current = manifest.stages[stage]
    manifest.stages[stage] = StageRecord(
        status=StageStatus.RUNNING,
        input_hash=input_hash,
        provider=provider,
        attempts=current.attempts + 1,
        updated_at=utc_now(),
    )
    layout.save_manifest(manifest)
    layout.append_event(
        {"event": "stage_started", "stage": stage, "input_hash": input_hash, "provider": provider}
    )
    return manifest


def complete_stage(
    layout: ProjectLayout,
    stage: str,
    input_hash: str,
    artifacts: list[str],
    *,
    provider: str | None = None,
    usage: dict[str, int | float | str | None] | None = None,
    note: str | None = None,
) -> None:
    manifest = layout.load_manifest()
    previous = manifest.stages[stage]
    manifest.stages[stage] = StageRecord(
        status=StageStatus.COMPLETED,
        input_hash=input_hash,
        provider=provider or previous.provider,
        attempts=previous.attempts,
        artifacts=artifacts,
        updated_at=utc_now(),
        usage=usage or {},
        note=note,
    )
    layout.save_manifest(manifest)
    layout.append_event(
        {"event": "stage_completed", "stage": stage, "artifacts": artifacts, "usage": usage or {}}
    )


def wait_stage(
    layout: ProjectLayout,
    stage: str,
    input_hash: str,
    note: str,
    artifacts: list[str] | None = None,
) -> None:
    manifest = layout.load_manifest()
    previous = manifest.stages[stage]
    manifest.stages[stage] = StageRecord(
        status=StageStatus.WAITING_EXTERNAL,
        input_hash=input_hash,
        provider=previous.provider,
        attempts=previous.attempts,
        artifacts=artifacts or [],
        updated_at=utc_now(),
        note=note,
    )
    layout.save_manifest(manifest)
    layout.append_event({"event": "stage_waiting", "stage": stage, "note": note})


def fail_stage(
    layout: ProjectLayout,
    stage: str,
    error: Exception | str,
    artifacts: list[str] | None = None,
) -> None:
    manifest = layout.load_manifest()
    previous = manifest.stages[stage]
    message = str(error)
    manifest.stages[stage] = previous.model_copy(
        update={
            "status": StageStatus.FAILED,
            "error": message,
            "artifacts": artifacts if artifacts is not None else previous.artifacts,
            "updated_at": utc_now(),
        }
    )
    layout.save_manifest(manifest)
    layout.append_event({"event": "stage_failed", "stage": stage, "error": message})


def set_approval(
    layout: ProjectLayout,
    key: str,
    status: ApprovalStatus,
    *,
    decided_by: str,
    note: str | None = None,
    artifact_hash: str | None = None,
) -> None:
    if key not in APPROVAL_KEYS:
        allowed = ", ".join(APPROVAL_KEYS)
        raise ValueError(f"Aprovação desconhecida: {key}. Valores aceitos: {allowed}")
    manifest = layout.load_manifest()
    manifest.approvals[key] = ApprovalRecord(
        status=status,
        decided_at=utc_now(),
        decided_by=decided_by,
        note=note,
        artifact_hash=artifact_hash,
    )
    layout.save_manifest(manifest)
    layout.append_decision(
        {"type": "approval", "subject": key, "status": status.value, "by": decided_by, "note": note}
    )


def invalidate(
    layout: ProjectLayout,
    *,
    stages: tuple[str, ...] = (),
    approvals: tuple[str, ...] = (),
    note: str,
) -> None:
    manifest = layout.load_manifest()
    for stage in stages:
        if stage not in manifest.stages:
            raise ValueError(f"Etapa desconhecida para invalidação: {stage}")
        record = manifest.stages[stage]
        manifest.stages[stage] = record.model_copy(
            update={"status": StageStatus.STALE, "note": note, "error": None}
        )
    for approval in approvals:
        if approval not in manifest.approvals:
            raise ValueError(f"Aprovação desconhecida para invalidação: {approval}")
        manifest.approvals[approval] = ApprovalRecord(note=note)
    layout.save_manifest(manifest)
    layout.append_event(
        {
            "event": "dependencies_invalidated",
            "stages": list(stages),
            "approvals": list(approvals),
            "note": note,
        }
    )


def require_approval(layout: ProjectLayout, key: str) -> None:
    record = layout.load_manifest().approvals[key]
    if record.status != ApprovalStatus.APPROVED:
        raise RuntimeError(
            f"A etapa exige aprovação explícita de '{key}'. Execute o comando approve primeiro."
        )


def stage_summary(layout: ProjectLayout) -> list[dict[str, Any]]:
    manifest = layout.load_manifest()
    rows = []
    for name in STAGE_ORDER:
        record = manifest.stages[name]
        rows.append(
            {
                "stage": name,
                "status": record.status.value,
                "attempts": record.attempts,
                "note": record.note or record.error or "",
            }
        )
    return rows
