from __future__ import annotations

import shutil
from pathlib import Path

from pipeline_videos.models import (
    APPLICATION_REQUIRED_CREATIVE_MODES,
    ApprovalStatus,
    BiblicalReview,
    Briefing,
    ScriptDocument,
)
from pipeline_videos.state import complete_stage, invalidate, set_approval
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value

LEGACY_REQUIRED_BRIEFING_FIELDS = (
    "main_passage",
    "central_theme",
    "practical_application",
    "audience",
    "emotional_intent",
)


def validate_briefing_for_approval(layout: ProjectLayout) -> Briefing:
    briefing = read_model(layout.briefing, Briefing)
    project = layout.load_manifest()
    creative_standard = (
        project.config.shorts_script.narrative_standard == "creative_retention_v3"
        if project.video_format == "short"
        else project.config.long_form_script.narrative_standard == "creative_retention_long_v2"
    )
    required_fields = (
        ("main_passage", "central_theme", "audience", "emotional_intent", "viewer_outcome")
        if creative_standard
        else LEGACY_REQUIRED_BRIEFING_FIELDS
    )
    missing = [name for name in required_fields if not getattr(briefing, name).strip()]
    if missing:
        raise ValueError(
            f"O briefing ainda não pode ser aprovado. Campos vazios: {', '.join(missing)}"
        )
    if creative_standard:
        if briefing.creative_mode == "auto":
            raise ValueError("Resolva creative_mode antes de aprovar o briefing.")
        if briefing.hook_policy == "auto" or briefing.hook_family == "auto":
            raise ValueError("Resolva política e família do hook antes de aprovar o briefing.")
        if project.video_format == "short" and briefing.hook_family == "none":
            raise ValueError("Shorts exigem um hook inicial.")
        if (
            briefing.creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES
            and not briefing.practical_application.strip()
        ):
            raise ValueError("O modo devocional exige practical_application no briefing.")
    if not briefing.interpretation_limits:
        raise ValueError("Registre ao menos um limite de interpretação no briefing.")
    if not briefing.claims_to_avoid:
        raise ValueError("Registre ao menos uma afirmação que o roteiro deve evitar.")
    return briefing


def import_biblical_review(layout: ProjectLayout, source: Path) -> BiblicalReview:
    source = source.expanduser().resolve()
    review = read_model(source, BiblicalReview)
    script = read_model(layout.script, ScriptDocument)
    if review.script_version != script.version:
        raise ValueError(
            f"A revisão é da versão {review.script_version}, mas o roteiro atual é "
            f"v{script.version}."
        )
    return save_biblical_review(layout, review, provider="human-or-codex-review")


def save_biblical_review(
    layout: ProjectLayout,
    review: BiblicalReview,
    *,
    provider: str,
) -> BiblicalReview:
    script = read_model(layout.script, ScriptDocument)
    if review.script_version != script.version:
        raise ValueError(
            f"A revisão é da versão {review.script_version}, mas o roteiro atual é "
            f"v{script.version}."
        )
    invalidate(
        layout,
        stages=(
            "scenes",
            "image_manifest",
            "images",
            "audio",
            "captions",
            "youtube_growth",
            "metadata",
            "thumbnail",
            "render",
            "validation",
        ),
        approvals=("biblical_review", "script", "images", "metadata", "final_render"),
        note="A revisão bíblica foi atualizada.",
    )
    if layout.biblical_review.exists():
        old = read_model(layout.biblical_review, BiblicalReview)
        archive = (
            layout.biblical_review.parent
            / "versions"
            / f"biblical-consistency-script-v{old.script_version:03d}.json"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            shutil.copy2(layout.biblical_review, archive)
    write_json(layout.biblical_review, review)
    digest = sha256_file(layout.biblical_review)
    complete_stage(
        layout,
        "biblical_review",
        digest,
        [relative_posix(layout.biblical_review, layout.root)],
        provider=provider,
    )
    return review


def approve_biblical_review(
    layout: ProjectLayout,
    *,
    decided_by: str,
    note: str | None = None,
) -> BiblicalReview:
    review = read_model(layout.biblical_review, BiblicalReview)
    script = read_model(layout.script, ScriptDocument)
    if review.script_version != script.version:
        raise ValueError("A revisão bíblica não corresponde à versão ativa do roteiro.")
    if review.has_open_blockers:
        blockers = [item.finding_id for item in review.findings if item.severity == "blocking"]
        raise ValueError(f"A revisão tem bloqueios abertos: {', '.join(blockers)}")
    review.approved = True
    review.approval_note = note or "Sem bloqueios abertos; incertezas permanecem registradas."
    write_json(layout.biblical_review, review)
    set_approval(
        layout,
        "biblical_review",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=review.approval_note,
    )
    return review


def approve_briefing(
    layout: ProjectLayout,
    *,
    decided_by: str,
    note: str | None = None,
) -> None:
    briefing = validate_briefing_for_approval(layout)
    input_hash = sha256_value(
        {
            "briefing": briefing,
            "sources": read_json(layout.sources) if layout.sources.exists() else {"entries": []},
        }
    )
    previous_hash = layout.load_manifest().stages["briefing"].input_hash
    if previous_hash is not None and previous_hash != input_hash:
        invalidate(
            layout,
            stages=(
                "shorts_script",
                "script",
                "biblical_review",
                "scenes",
                "image_manifest",
                "images",
                "audio",
                "captions",
                "youtube_growth",
                "metadata",
                "thumbnail",
                "render",
                "validation",
            ),
            approvals=("script", "biblical_review", "images", "metadata", "final_render"),
            note="O briefing ou suas fontes foram atualizados.",
        )
    complete_stage(
        layout,
        "briefing",
        input_hash,
        [relative_posix(layout.briefing, layout.root), relative_posix(layout.sources, layout.root)],
        provider="human-or-codex-editorial",
    )
    set_approval(
        layout,
        "briefing",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=note,
    )


def approve_script(
    layout: ProjectLayout,
    *,
    decided_by: str,
    note: str | None = None,
) -> None:
    review = read_model(layout.biblical_review, BiblicalReview)
    if not review.approved or review.has_open_blockers:
        raise ValueError("O roteiro só pode ser aprovado após uma revisão bíblica aprovada.")
    set_approval(
        layout,
        "script",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=note,
    )


def validate_draft_preflight(layout: ProjectLayout) -> None:
    """Allow a one-shot draft without human gates while preserving biblical blockers."""
    script = read_model(layout.script, ScriptDocument)
    review = read_model(layout.biblical_review, BiblicalReview)
    if review.script_version != script.version:
        raise RuntimeError("A revisão bíblica não corresponde à versão ativa do roteiro.")
    if review.has_open_blockers:
        raise RuntimeError("A revisão bíblica possui bloqueios abertos.")
