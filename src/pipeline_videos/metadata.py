from __future__ import annotations

import re

from pipeline_videos.creative_direction import CREATIVE_MODE_SPECS
from pipeline_videos.editorial import scripture_citations
from pipeline_videos.models import (
    ApprovalStatus,
    AudioManifest,
    Briefing,
    SceneManifest,
    ScriptDocument,
    SourceLibrary,
    YouTubeMetadata,
)
from pipeline_videos.state import complete_stage, invalidate, set_approval
from pipeline_videos.storage import ProjectLayout, read_model, write_json, write_text
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value

METADATA_ALGORITHM_VERSION = "2.0"


def _timestamp(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minute}:{second:02d}"


def _timeline_timestamp(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"


def _chapter_title(role: str) -> str:
    return {
        "opening": "Abertura",
        "biblical_narrative": "A história",
        "reflection": "O sentido",
        "application": "Aplicação",
        "closing": "Encerramento",
    }.get(role, "Narrativa")


def _role(scene: object) -> str:
    function = str(getattr(scene, "narrative_function", "")).casefold()
    if "curiosidade" in function or "abertura" in function:
        return "opening"
    if "passagem" in function or "narrativ" in function:
        return "biblical_narrative"
    if "reflex" in function:
        return "reflection"
    if "prática" in function or "aplica" in function:
        return "application"
    return "closing"


def _build_chapters(
    scenes: SceneManifest,
    audio: AudioManifest | None,
    script: ScriptDocument | None = None,
    *,
    scripture_timeline: bool = False,
) -> list[str]:
    starts = {segment.scene_id: segment.start for segment in audio.segments} if audio else {}
    if scripture_timeline and script is not None:
        section_starts: dict[str, float] = {}
        elapsed = 0.0
        for scene in scenes.scenes:
            section_starts.setdefault(scene.section_id, starts.get(scene.scene_id, elapsed))
            elapsed += scene.estimated_duration_seconds
        timeline = ["00:00 - Abertura"]
        cited_section_ids = {item.section_id for item in scripture_citations(script)}
        for section in script.sections:
            if section.section_id not in cited_section_ids or not section.biblical_basis:
                continue
            references = "; ".join(dict.fromkeys(section.biblical_basis))
            timeline.append(
                f"{_timeline_timestamp(section_starts.get(section.section_id, 0.0))} - {references}"
            )
        return timeline

    chapters: list[str] = []
    elapsed = 0.0
    seen_sections: set[str] = set()
    for scene in scenes.scenes:
        if scene.section_id not in seen_sections:
            seen_sections.add(scene.section_id)
            chapters.append(
                f"{_timestamp(starts.get(scene.scene_id, elapsed))} {_chapter_title(_role(scene))}"
            )
        elapsed += scene.estimated_duration_seconds
    if chapters and not chapters[0].startswith("0:00 "):
        chapters[0] = "0:00 " + chapters[0].split(" ", 1)[1]
    return chapters


def build_metadata(layout: ProjectLayout, *, force: bool = False) -> YouTubeMetadata:
    if layout.approved_script_lock.is_file():
        from pipeline_videos.approved_scripts import verify_project_approved_script_lock

        verify_project_approved_script_lock(layout)
        return read_model(layout.metadata, YouTubeMetadata)

    briefing = read_model(layout.briefing, Briefing)
    scenes = read_model(layout.scenes, SceneManifest)
    sources = read_model(layout.sources, SourceLibrary)
    project = layout.load_manifest()
    script = read_model(layout.script, ScriptDocument) if layout.script.is_file() else None
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    input_hash = sha256_value(
        {
            "algorithm": METADATA_ALGORITHM_VERSION,
            "briefing": briefing,
            "scenes": scenes,
            "sources": sources,
            "script": script,
        }
    )
    stage = project.stages["metadata"]
    if (
        not force
        and stage.input_hash == input_hash
        and stage.status.value == "completed"
        and layout.metadata.is_file()
    ):
        return read_model(layout.metadata, YouTubeMetadata)

    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Os metadados foram atualizados.",
    )
    subject = briefing.central_theme.strip().rstrip(".") or briefing.main_passage
    subject_title = subject[:1].upper() + subject[1:]
    mode_label = (
        CREATIVE_MODE_SPECS[briefing.creative_mode].label
        if briefing.creative_mode in CREATIVE_MODE_SPECS
        else "Conteúdo bíblico"
    )
    title = (
        script.title[:100]
        if script is not None
        else f"{subject_title} | {briefing.main_passage}"[:100]
    )
    source_lines = [
        f"- {entry.title}" + (f" — {entry.url}" if entry.url else "") for entry in sources.entries
    ]
    description = "\n\n".join(
        part
        for part in (
            (
                f"{subject_title} em {briefing.main_passage}: uma abordagem "
                f"{mode_label.casefold()} fiel à passagem."
            ),
            briefing.viewer_outcome or briefing.practical_application,
            "Referências consultadas:\n" + "\n".join(source_lines) if source_lines else "",
        )
        if part
    )
    book_name = re.sub(r"\s+\d.*$", "", briefing.main_passage).strip()
    topics = list(
        dict.fromkeys(
            item.strip()
            for item in (
                subject,
                briefing.main_passage,
                f"{subject} na Bíblia",
                f"{briefing.main_passage} explicado",
                f"livro de {book_name}" if book_name else "Bíblia",
                "Jesus",
                "estudo bíblico",
                "explicação bíblica",
                "Bíblia em português",
                "contexto bíblico",
            )
            if item.strip()
        )
    )
    metadata = YouTubeMetadata(
        title=title,
        alternative_titles=[],
        description=description,
        chapters=(
            []
            if project.video_format == "short"
            else _build_chapters(
                scenes,
                audio,
                script,
                scripture_timeline=True,
            )
        ),
        topics=topics,
        pinned_comment=(
            f"O que {briefing.main_passage} esclarece sobre {subject.casefold()} para você?"
        ),
    )
    write_json(layout.metadata, metadata)
    write_text(layout.description, metadata.description.rstrip() + "\n")
    complete_stage(
        layout,
        "metadata",
        input_hash,
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
        ],
        provider="deterministic-draft",
    )
    return metadata


def approve_metadata(layout: ProjectLayout, *, decided_by: str, note: str | None = None) -> None:
    metadata = read_model(layout.metadata, YouTubeMetadata)
    if not metadata.title.strip() or not metadata.description.strip():
        raise ValueError("Título e descrição precisam estar completos antes da aprovação.")
    set_approval(
        layout,
        "metadata",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=note,
        artifact_hash=sha256_value(
            {
                "metadata": sha256_file(layout.metadata),
                "description": sha256_file(layout.description),
            }
        ),
    )
