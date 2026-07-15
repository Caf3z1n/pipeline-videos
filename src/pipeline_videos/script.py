from __future__ import annotations

import json
import shutil
from pathlib import Path

from pipeline_videos.editorial import validate_script_editorial_policy
from pipeline_videos.models import ScriptDocument, ScriptSection, SourceLibrary
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json, write_text
from pipeline_videos.utils import relative_posix, sha256_file, tokenize_words

SECTION_ROLES = (
    "opening",
    "biblical_narrative",
    "reflection",
    "application",
    "closing",
)


def _archive_current_script(layout: ProjectLayout) -> ScriptDocument | None:
    if not layout.script.exists():
        return None
    previous = read_model(layout.script, ScriptDocument)
    archive = layout.script.parent / "versions" / f"script-v{previous.version:03d}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        shutil.copy2(layout.script, archive)
    return previous


def _from_plain_text(path: Path, title: str) -> ScriptDocument:
    paragraphs = [part.strip() for part in path.read_text(encoding="utf-8").split("\n\n")]
    paragraphs = [part for part in paragraphs if part]
    if not paragraphs:
        raise ValueError("O arquivo de roteiro está vazio.")
    sections: list[ScriptSection] = []
    for index, paragraph in enumerate(paragraphs):
        role_index = min(index, len(SECTION_ROLES) - 1)
        if index == len(paragraphs) - 1:
            role_index = len(SECTION_ROLES) - 1
        role = SECTION_ROLES[role_index]
        sections.append(
            ScriptSection(
                section_id=f"section-{index + 1:03d}",
                role=role,  # type: ignore[arg-type]
                title=role.replace("_", " ").title(),
                narration=paragraph,
                claim_classification=["interpretation"],
            )
        )
    return ScriptDocument(title=title, sections=sections)


def _load_source(path: Path, title: str) -> ScriptDocument:
    if path.suffix.lower() == ".json":
        return ScriptDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return _from_plain_text(path, title)


def import_script(layout: ProjectLayout, source: Path, *, force: bool = False) -> ScriptDocument:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Roteiro não encontrado: {source}")
    source_hash = sha256_file(source)
    if stage_is_current(layout, "script", source_hash, force=force):
        return read_model(layout.script, ScriptDocument)

    invalidate(
        layout,
        stages=(
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
        note="O roteiro foi importado novamente.",
    )
    start_stage(layout, "script", source_hash, provider="file-import")
    try:
        document = _load_source(source, layout.load_manifest().title)
        project = layout.load_manifest()
        sources = read_model(layout.sources, SourceLibrary) if layout.sources.is_file() else None
        validate_script_editorial_policy(
            document,
            duration_seconds=project.target_duration_seconds,
            policy=project.config.editorial_policy,
            sources=sources,
        )
        previous = _archive_current_script(layout)
        if previous is not None:
            document.version = previous.version + 1
        words = tokenize_words(document.narration_text())
        document.word_count = len(words)
        document.estimated_duration_seconds = round(len(words) / 145 * 60, 2)
        write_json(layout.script, document)
        write_text(layout.narration, document.narration_text())
        artifacts = [
            relative_posix(layout.script, layout.root),
            relative_posix(layout.narration, layout.root),
        ]
        complete_stage(layout, "script", source_hash, artifacts, provider="file-import")
        return document
    except Exception as exc:
        fail_stage(layout, "script", exc)
        raise


def save_script(
    layout: ProjectLayout, document: ScriptDocument, *, provider: str
) -> ScriptDocument:
    project = layout.load_manifest()
    sources = read_model(layout.sources, SourceLibrary) if layout.sources.is_file() else None
    validate_script_editorial_policy(
        document,
        duration_seconds=project.target_duration_seconds,
        policy=project.config.editorial_policy,
        sources=sources,
    )
    invalidate(
        layout,
        stages=(
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
        note="O roteiro ativo foi atualizado.",
    )
    previous = _archive_current_script(layout)
    if previous is not None:
        document.version = previous.version + 1
    words = tokenize_words(document.narration_text())
    document.word_count = len(words)
    document.estimated_duration_seconds = round(len(words) / 145 * 60, 2)
    payload_hash = document.model_dump(mode="json")
    from pipeline_videos.utils import sha256_value

    input_hash = sha256_value(payload_hash)
    write_json(layout.script, document)
    write_text(layout.narration, document.narration_text())
    complete_stage(
        layout,
        "script",
        input_hash,
        [relative_posix(layout.script, layout.root), relative_posix(layout.narration, layout.root)],
        provider=provider,
    )
    return document
