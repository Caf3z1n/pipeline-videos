from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from pipeline_videos.editorial import validate_script_editorial_policy
from pipeline_videos.models import (
    Briefing,
    CreativeBeat,
    CreativeContract,
    CreativePacingProfile,
    ScriptDocument,
    ScriptSection,
    ShortsScriptStrategy,
    SourceLibrary,
)
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json, write_text
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value, tokenize_words

SECTION_ROLES = (
    "opening",
    "biblical_narrative",
    "reflection",
    "application",
    "closing",
)

CREATIVE_CONTRACT_ALGORITHM = "creative-contract-v1"
SHORT_BEAT_FUNCTIONS = {
    "hook": "hook",
    "open_loop": "progression",
    "stakes": "progression",
    "pattern_interrupt": "reengagement",
    "payoff": "payoff",
    "application": "application",
    "cta": "cta",
}


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def _fallback_functions(role: str, *, first: bool, last: bool) -> list[str]:
    if first and last:
        return ["hook", "payoff"]
    if first:
        return ["hook"]
    if last:
        if role == "reflection":
            return ["contemplation", "payoff"]
        if role == "application":
            return ["application", "payoff"]
        if role == "closing":
            return ["payoff", "cta"]
        return ["progression", "payoff"]
    if role == "reflection":
        return ["contemplation"]
    if role == "application":
        return ["application"]
    if role == "closing":
        return ["payoff", "cta"]
    return ["progression"]


def _section_timings(
    document: ScriptDocument, *, words_per_minute: int
) -> list[tuple[float, float]]:
    timings: list[tuple[float, float]] = []
    cursor = 0.0
    for section in document.sections:
        duration = max(0.1, len(tokenize_words(section.narration)) / words_per_minute * 60)
        timings.append((round(cursor, 2), round(cursor + duration, 2)))
        cursor += duration
    return timings


def build_creative_contract(
    layout: ProjectLayout,
    document: ScriptDocument,
    *,
    strategy: ShortsScriptStrategy | None = None,
) -> CreativeContract:
    """Derive the shared semantic contract without another model call or pipeline stage."""
    project = layout.load_manifest()
    briefing = read_model(layout.briefing, Briefing)
    is_short = project.video_format == "short"
    if is_short and strategy is None and layout.shorts_script_strategy.is_file():
        strategy = read_model(layout.shorts_script_strategy, ShortsScriptStrategy)

    input_hash = sha256_value(
        {
            "algorithm": CREATIVE_CONTRACT_ALGORITHM,
            "format": project.video_format,
            "briefing": briefing,
            "script": document,
            "shorts_strategy": strategy,
        }
    )
    section_ids = [section.section_id for section in document.sections]
    if not section_ids:
        raise ValueError("O contrato criativo exige ao menos uma seção de roteiro.")

    wpm = project.config.shorts_script.estimated_narration_wpm if is_short else 145
    timings = _section_timings(document, words_per_minute=wpm)
    strategy_beats: dict[str, list] = {}
    if strategy is not None:
        for beat in strategy.retention_beats:
            strategy_beats.setdefault(beat.section_id, []).append(beat)

    reengagement_indexes: set[int] = set()
    if not is_short and len(document.sections) >= 5:
        reengagement_indexes = {
            max(1, min(len(document.sections) - 2, round((len(document.sections) - 1) * share)))
            for share in (1 / 3, 2 / 3)
        }

    beats: list[CreativeBeat] = []
    for index, (section, (start, end)) in enumerate(zip(document.sections, timings, strict=True)):
        linked = strategy_beats.get(section.section_id, [])
        functions = _fallback_functions(
            section.role,
            first=index == 0,
            last=index == len(document.sections) - 1,
        )
        if linked:
            functions = list(dict.fromkeys(SHORT_BEAT_FUNCTIONS[beat.purpose] for beat in linked))
            start = min(beat.target_start_seconds for beat in linked)
            end = max(beat.target_end_seconds for beat in linked)
        if index in reengagement_indexes and "reengagement" not in functions:
            functions.append("reengagement")
        viewer_value = (
            linked[0].viewer_value
            if linked
            else section.retention_goal or section.title or section.role.replace("_", " ")
        )
        critical = bool({"hook", "reengagement", "payoff", "application"}.intersection(functions))
        beats.append(
            CreativeBeat(
                section_id=section.section_id,
                functions=functions,
                viewer_value=viewer_value.strip(),
                target_start_seconds=start,
                target_end_seconds=end,
                critical=critical,
            )
        )

    first_section = document.sections[0]
    closing = document.sections[-1]
    content_promise = (
        strategy.content_promise
        if strategy is not None
        else briefing.central_theme.strip() or document.title.strip()
    )
    opening_hook = (
        strategy.hook if strategy is not None else _first_sentence(first_section.narration)
    )
    payoff = (
        strategy.payoff
        if strategy is not None
        else briefing.practical_application.strip()
        or closing.retention_goal
        or _first_sentence(closing.narration)
    )
    practical_application = (
        strategy.practical_value
        if strategy is not None
        else briefing.practical_application.strip() or payoff
    )
    target_viewer = (
        strategy.target_viewer
        if strategy is not None
        else briefing.audience.strip() or "pessoas interessadas nesta mensagem bíblica"
    )
    pacing = (
        CreativePacingProfile(
            profile_id="short_v1",
            default_scene_seconds=8.0,
            opening_scene_seconds=project.config.shorts_script.verse_visual_change_seconds,
            reengagement_scene_seconds=8.0,
            contemplative_scene_seconds=8.0,
            payoff_scene_seconds=8.0,
        )
        if is_short
        else CreativePacingProfile(
            profile_id="balanced_long_v1",
            default_scene_seconds=12.0,
            opening_scene_seconds=10.0,
            reengagement_scene_seconds=10.0,
            contemplative_scene_seconds=16.0,
            payoff_scene_seconds=13.0,
        )
    )
    return CreativeContract(
        input_hash=input_hash,
        format="short" if is_short else "long_form",
        target_viewer=target_viewer,
        content_promise=content_promise,
        opening_hook=opening_hook,
        payoff=payoff,
        practical_application=practical_application,
        main_passage=briefing.main_passage,
        source_ids=list(
            dict.fromkeys(strategy.source_ids if strategy is not None else document.source_ids)
        ),
        section_ids=section_ids,
        claims_to_avoid=briefing.claims_to_avoid,
        beats=beats,
        critical_component_ids=[beat.section_id for beat in beats if beat.critical],
        pacing=pacing,
    )


def ensure_creative_contract(
    layout: ProjectLayout,
    document: ScriptDocument | None = None,
    *,
    strategy: ShortsScriptStrategy | None = None,
) -> CreativeContract:
    document = document or read_model(layout.script, ScriptDocument)
    contract = build_creative_contract(layout, document, strategy=strategy)
    if layout.creative_contract.is_file():
        current = read_model(layout.creative_contract, CreativeContract)
        if current.input_hash == contract.input_hash:
            return current
    write_json(layout.creative_contract, contract)
    return contract


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
        document = read_model(layout.script, ScriptDocument)
        ensure_creative_contract(layout, document)
        return document

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
        ensure_creative_contract(layout, document)
        artifacts = [
            relative_posix(layout.script, layout.root),
            relative_posix(layout.narration, layout.root),
            relative_posix(layout.creative_contract, layout.root),
        ]
        complete_stage(layout, "script", source_hash, artifacts, provider="file-import")
        from pipeline_videos.verse_selection import sync_shared_usage

        sync_shared_usage(layout.root.parent.parent)
        return document
    except Exception as exc:
        fail_stage(layout, "script", exc)
        raise


def save_script(
    layout: ProjectLayout,
    document: ScriptDocument,
    *,
    provider: str,
    creative_strategy: ShortsScriptStrategy | None = None,
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
    input_hash = sha256_value(document.model_dump(mode="json"))
    write_json(layout.script, document)
    write_text(layout.narration, document.narration_text())
    ensure_creative_contract(layout, document, strategy=creative_strategy)
    complete_stage(
        layout,
        "script",
        input_hash,
        [
            relative_posix(layout.script, layout.root),
            relative_posix(layout.narration, layout.root),
            relative_posix(layout.creative_contract, layout.root),
        ],
        provider=provider,
    )
    from pipeline_videos.verse_selection import sync_shared_usage

    sync_shared_usage(layout.root.parent.parent)
    return document
