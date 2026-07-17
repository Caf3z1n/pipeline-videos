from __future__ import annotations

import os
import re
import socket
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from pipeline_videos.git_registry import (
    read_shared_registry,
    shared_registry_enabled,
    update_shared_registry,
)
from pipeline_videos.models import (
    Briefing,
    ProductionMode,
    SourceEntry,
    SourceLibrary,
    VerseCandidate,
    VerseCatalog,
    VerseUsageIndex,
    VerseUsageRecord,
)
from pipeline_videos.project import create_project, project_directory_name
from pipeline_videos.state import complete_stage
from pipeline_videos.storage import (
    DEFAULT_PROJECTS_DIR,
    REPO_ROOT,
    ProjectLayout,
    read_json,
    read_model,
    write_json,
)
from pipeline_videos.utils import relative_posix, sha256_value, tokenize_words, utc_now

DEFAULT_VERSE_CATALOG = REPO_ROOT / "config" / "bible" / "shorts-verse-catalog.json"
VERSE_USAGE_FILENAME = "verse-usage.json"
REGISTRY_ENVIRONMENT_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_ENVIRONMENT"
RETENTION_ARCHETYPES = (
    "conflict_first",
    "counterintuitive_reveal",
    "stair_step_progression",
)

BOOK_ALIASES = {
    "genesis": "genesis",
    "exodo": "exodo",
    "deuteronomio": "deuteronomio",
    "josue": "josue",
    "1 samuel": "1-samuel",
    "i samuel": "1-samuel",
    "salmo": "salmos",
    "salmos": "salmos",
    "proverbios": "proverbios",
    "isaias": "isaias",
    "lamentacoes": "lamentacoes",
    "miqueias": "miqueias",
    "mateus": "mateus",
    "lucas": "lucas",
    "joao": "joao",
    "romanos": "romanos",
    "2 corintios": "2-corintios",
    "ii corintios": "2-corintios",
    "galatas": "galatas",
    "efesios": "efesios",
    "filipenses": "filipenses",
    "colossenses": "colossenses",
    "2 timoteo": "2-timoteo",
    "ii timoteo": "2-timoteo",
    "hebreus": "hebreus",
    "tiago": "tiago",
    "1 pedro": "1-pedro",
    "i pedro": "1-pedro",
    "1 joao": "1-joao",
    "i joao": "1-joao",
}
BOOK_PATTERN = "|".join(sorted((re.escape(item) for item in BOOK_ALIASES), key=len, reverse=True))
REFERENCE_RE = re.compile(
    rf"(?P<book>{BOOK_PATTERN})\s+(?P<chapter>\d{{1,3}})(?::(?P<verses>[\d,;\-–—\s]+))?",
    re.IGNORECASE,
)


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii").casefold().strip()


def registry_environment() -> str:
    configured = os.getenv(REGISTRY_ENVIRONMENT_ENV, "").strip()
    return _plain(configured or socket.gethostname()) or "unknown"


def reference_tokens(reference: str) -> set[str]:
    """Return canonical book|chapter|verse tokens; a whole chapter uses `*`."""
    text = _plain(reference).replace("º", "").replace("ª", "")
    tokens: set[str] = set()
    for match in REFERENCE_RE.finditer(text):
        book = BOOK_ALIASES[_plain(match.group("book"))]
        chapter = int(match.group("chapter"))
        verses = match.group("verses")
        if not verses or not verses.strip():
            tokens.add(f"{book}|{chapter}|*")
            continue
        for part in re.split(r"[,;]", verses):
            part = part.strip().replace("–", "-").replace("—", "-")
            if not part:
                continue
            if "-" in part:
                start_text, end_text = (item.strip() for item in part.split("-", 1))
                if start_text.isdigit() and end_text.isdigit():
                    start, end = int(start_text), int(end_text)
                    if 0 < start <= end <= 200:
                        tokens.update(
                            f"{book}|{chapter}|{verse}" for verse in range(start, end + 1)
                        )
                continue
            if part.isdigit():
                tokens.add(f"{book}|{chapter}|{int(part)}")
    return tokens


def references_overlap(first: str, second: str) -> bool:
    left, right = reference_tokens(first), reference_tokens(second)
    if left & right:
        return True
    for token in left:
        book, chapter, verse = token.split("|")
        if verse == "*" and any(item.startswith(f"{book}|{chapter}|") for item in right):
            return True
    for token in right:
        book, chapter, verse = token.split("|")
        if verse == "*" and any(item.startswith(f"{book}|{chapter}|") for item in left):
            return True
    return False


def load_verse_catalog(path: Path = DEFAULT_VERSE_CATALOG) -> VerseCatalog:
    return read_model(path, VerseCatalog)


def _json_object(path: Path) -> dict[str, object]:
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path} precisa conter um objeto JSON.")
    return value


def _json_list(value: object, *, field: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} precisa conter uma lista.")
    return value


def _project_references(layout: ProjectLayout) -> list[str]:
    references: list[str] = []
    if layout.briefing.is_file():
        data = _json_object(layout.briefing)
        references.extend(
            [
                str(data.get("main_passage", "")),
                *(
                    str(item)
                    for item in _json_list(
                        data.get("complementary_references"),
                        field=f"{layout.briefing}: complementary_references",
                    )
                ),
            ]
        )
    if layout.sources.is_file():
        data = _json_object(layout.sources)
        for item in _json_list(data.get("entries"), field=f"{layout.sources}: entries"):
            if not isinstance(item, dict):
                raise ValueError(f"{layout.sources}: cada fonte precisa ser um objeto.")
            references.append(str(item.get("passage", "")))
    if layout.script.is_file():
        data = _json_object(layout.script)
        for section in _json_list(data.get("sections"), field=f"{layout.script}: sections"):
            if not isinstance(section, dict):
                raise ValueError(f"{layout.script}: cada seção precisa ser um objeto.")
            references.extend(
                str(item)
                for item in _json_list(
                    section.get("biblical_basis"),
                    field=f"{layout.script}: biblical_basis",
                )
            )
    return list(dict.fromkeys(item.strip() for item in references if str(item).strip()))


def build_usage_index(
    projects_dir: Path | None = None,
    *,
    catalog: VerseCatalog | None = None,
) -> VerseUsageIndex:
    root = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    catalog = catalog or load_verse_catalog()
    records: list[VerseUsageRecord] = []
    for manifest_path in sorted(root.glob("*/*/project.json")):
        layout = ProjectLayout(manifest_path.parent)
        try:
            manifest = _json_object(manifest_path)
            project_id = str(manifest.get("project_id") or manifest_path.parent.name).strip()
            video_format = str(manifest.get("video_format", "")).strip()
            created_at = str(manifest.get("created_at", "")).strip()
            title = str(manifest.get("title") or project_id).strip()
            if not project_id or video_format not in {"landscape", "short"} or not created_at:
                raise ValueError("project_id, video_format e created_at são obrigatórios.")
            references = _project_references(layout)
            briefing = _json_object(layout.briefing) if layout.briefing.is_file() else {}
        except (ValueError, FileNotFoundError, TypeError) as exc:
            raise RuntimeError(
                f"Não foi possível indexar {manifest_path.parent}. "
                "A seleção foi bloqueada para evitar repetição."
            ) from exc
        main_passage = str(briefing.get("main_passage", "")).strip() or None
        topic = str(briefing.get("central_theme") or title).strip()
        retention_archetype = str(briefing.get("retention_archetype") or "").strip() or None
        if retention_archetype not in RETENTION_ARCHETYPES:
            retention_archetype = None
        hook_family = retention_archetype
        engagement_mode = None
        if layout.shorts_script_strategy.is_file():
            strategy = _json_object(layout.shorts_script_strategy)
            retention_story = strategy.get("retention_story")
            if isinstance(retention_story, dict):
                retention_archetype = (
                    str(retention_story.get("archetype") or "").strip() or retention_archetype
                )
                if retention_archetype not in RETENTION_ARCHETYPES:
                    retention_archetype = None
                hook_family = retention_archetype
                engagement = retention_story.get("engagement")
                if isinstance(engagement, dict):
                    engagement_mode = str(engagement.get("mode") or "").strip() or None
                    if engagement_mode not in {"comment", "subscribe", "share"}:
                        engagement_mode = None
        tokens = sorted(
            {token for reference in references for token in reference_tokens(reference)}
        )
        matched = [
            candidate
            for candidate in catalog.candidates
            if any(references_overlap(candidate.reference, reference) for reference in references)
        ]
        records.append(
            VerseUsageRecord(
                registry_id=f"{registry_environment()}:{project_id}",
                environment=registry_environment(),
                project_id=project_id,
                project_path=manifest_path.parent.relative_to(root).as_posix(),
                video_format=video_format,
                status="used" if layout.script.is_file() else "reserved",
                references=references,
                normalized_references=tokens,
                main_passage=main_passage,
                topic=topic,
                normalized_topic=_plain(topic),
                themes=list(
                    dict.fromkeys(
                        [
                            topic,
                            *(theme for item in matched for theme in item.themes),
                        ]
                    )
                ),
                characters=list(
                    dict.fromkeys(name for item in matched for name in item.characters)
                ),
                retention_archetype=retention_archetype,
                hook_family=hook_family,
                engagement_mode=engagement_mode,
                created_at=created_at,
            )
        )
    return VerseUsageIndex(generated_at=utc_now(), records=records)


def merge_usage_indexes(*indexes: VerseUsageIndex) -> VerseUsageIndex:
    merged: dict[str, VerseUsageRecord] = {}
    for index in indexes:
        for record in index.records:
            key = record.registry_id or f"legacy:{record.project_id}"
            current = merged.get(key)
            if current is None:
                merged[key] = record
                continue
            references = list(dict.fromkeys([*current.references, *record.references]))
            merged[key] = record.model_copy(
                update={
                    "status": "used" if "used" in {current.status, record.status} else "reserved",
                    "references": references,
                    "normalized_references": sorted(
                        {token for reference in references for token in reference_tokens(reference)}
                    ),
                    "themes": list(dict.fromkeys([*current.themes, *record.themes])),
                    "characters": list(dict.fromkeys([*current.characters, *record.characters])),
                    "created_at": min(current.created_at, record.created_at),
                    "updated_at": record.updated_at or current.updated_at,
                }
            )
    return VerseUsageIndex(
        generated_at=utc_now(),
        records=sorted(merged.values(), key=lambda item: (item.created_at, item.project_id)),
    )


def combined_usage_index(
    projects_dir: Path,
    *,
    catalog: VerseCatalog | None = None,
) -> VerseUsageIndex:
    local = build_usage_index(projects_dir, catalog=catalog)
    if not shared_registry_enabled():
        return local
    shared = VerseUsageIndex.model_validate(read_shared_registry())
    return merge_usage_indexes(shared, local)


def sync_shared_usage(
    projects_dir: Path,
    *,
    catalog: VerseCatalog | None = None,
) -> VerseUsageIndex:
    local = build_usage_index(projects_dir, catalog=catalog)
    if not shared_registry_enabled():
        write_json(projects_dir / VERSE_USAGE_FILENAME, local)
        return local

    def mutate(raw: dict[str, object]) -> tuple[dict[str, object], VerseUsageIndex]:
        shared = VerseUsageIndex.model_validate(raw)
        merged = merge_usage_indexes(shared, local)
        if [item.model_dump(mode="json") for item in merged.records] == [
            item.model_dump(mode="json") for item in shared.records
        ]:
            return raw, shared
        return merged.model_dump(mode="json"), merged

    merged = update_shared_registry(mutate, message="registry: sync project usage")
    write_json(projects_dir / VERSE_USAGE_FILENAME, merged)
    return merged


def _reservation_record(
    candidate: VerseCandidate,
    *,
    project_id: str,
    video_format: Literal["landscape", "short"],
    retention_archetype: str | None = None,
) -> VerseUsageRecord:
    category = "shorts" if video_format == "short" else "videos-longos"
    now = utc_now()
    references = list(dict.fromkeys([candidate.reference, candidate.context_reference]))
    environment = registry_environment()
    return VerseUsageRecord(
        registry_id=f"{environment}:{project_id}",
        environment=environment,
        project_id=project_id,
        project_path=f"{category}/{project_id}",
        video_format=video_format,
        status="reserved",
        references=references,
        normalized_references=sorted(
            {token for reference in references for token in reference_tokens(reference)}
        ),
        main_passage=candidate.reference,
        topic=candidate.central_theme,
        normalized_topic=_plain(candidate.central_theme),
        themes=list(dict.fromkeys([candidate.central_theme, *candidate.themes])),
        characters=candidate.characters,
        retention_archetype=retention_archetype,
        hook_family=retention_archetype,
        created_at=now,
        updated_at=now,
    )


def reserve_project_candidates(
    projects_dir: Path,
    *,
    project_id: str,
    video_format: Literal["landscape", "short"],
    candidates: list[VerseCandidate],
    catalog: VerseCatalog | None = None,
    retention_archetype: str | None = None,
) -> VerseUsageIndex:
    if not candidates:
        raise ValueError("Informe ao menos uma passagem para reservar.")
    root = projects_dir.expanduser().resolve()
    local = build_usage_index(root, catalog=catalog)
    environment = registry_environment()
    registry_id = f"{environment}:{project_id}"

    def reserve(usage: VerseUsageIndex) -> VerseUsageIndex:
        other_references = [
            reference
            for record in usage.records
            if record.registry_id != registry_id
            for reference in record.references
        ]
        repeated = next(
            (
                candidate.reference
                for candidate in candidates
                if any(
                    references_overlap(candidate.reference, reference)
                    for reference in other_references
                )
            ),
            None,
        )
        if repeated:
            raise RuntimeError(
                f"A passagem {repeated} foi reservada por outro projeto durante a seleção."
            )
        main = candidates[0]
        reservation = _reservation_record(
            main,
            project_id=project_id,
            video_format=video_format,
            retention_archetype=retention_archetype,
        )
        references = list(
            dict.fromkeys(
                reference
                for candidate in candidates
                for reference in (candidate.reference, candidate.context_reference)
            )
        )
        reservation = reservation.model_copy(
            update={
                "references": references,
                "normalized_references": sorted(
                    {token for reference in references for token in reference_tokens(reference)}
                ),
                "themes": list(
                    dict.fromkeys(
                        theme
                        for candidate in candidates
                        for theme in (candidate.central_theme, *candidate.themes)
                    )
                ),
                "characters": list(
                    dict.fromkeys(name for candidate in candidates for name in candidate.characters)
                ),
            }
        )
        return merge_usage_indexes(
            usage,
            VerseUsageIndex(generated_at=utc_now(), records=[reservation]),
        )

    with _selection_lock(root):
        if shared_registry_enabled():

            def mutate(raw: dict[str, object]) -> tuple[dict[str, object], VerseUsageIndex]:
                shared = VerseUsageIndex.model_validate(raw)
                updated = reserve(merge_usage_indexes(shared, local))
                return updated.model_dump(mode="json"), updated

            merged = update_shared_registry(
                mutate,
                message=f"registry: reserve passages for {project_id}",
            )
        else:
            merged = reserve(local)
        write_json(root / VERSE_USAGE_FILENAME, merged)
        return merged


def select_retention_archetype(
    candidate: VerseCandidate,
    usage: VerseUsageIndex,
) -> str:
    """Choose a preferred format without repeating either of the last two Shorts."""
    preferred = [
        archetype
        for archetype in candidate.retention_archetypes
        if archetype in RETENTION_ARCHETYPES
    ]
    if not preferred:
        preferred = list(RETENTION_ARCHETYPES)
    recent = sorted(
        (record for record in usage.records if record.video_format == "short"),
        key=lambda item: item.created_at,
        reverse=True,
    )
    last_two = {record.retention_archetype for record in recent[:2] if record.retention_archetype}
    fresh = [archetype for archetype in preferred if archetype not in last_two]
    if fresh:
        return fresh[0]
    global_fresh = [archetype for archetype in RETENTION_ARCHETYPES if archetype not in last_two]
    if global_fresh:
        return global_fresh[0]
    frequencies = Counter(
        record.retention_archetype for record in recent if record.retention_archetype in preferred
    )
    return min(
        preferred,
        key=lambda archetype: (frequencies[archetype], preferred.index(archetype)),
    )


def select_unused_verse(
    catalog: VerseCatalog, usage: VerseUsageIndex
) -> tuple[VerseCandidate, float]:
    used_references = [reference for record in usage.records for reference in record.references]
    used_topics = {
        record.normalized_topic or _plain(record.topic or "")
        for record in usage.records
        if record.normalized_topic or record.topic
    }
    available = [
        candidate
        for candidate in catalog.candidates
        if _plain(candidate.central_theme) not in used_topics
        and not any(
            references_overlap(candidate.reference, reference) for reference in used_references
        )
    ]
    if not available:
        raise RuntimeError("O catálogo de versículos para Shorts foi esgotado.")

    recent = sorted(usage.records, key=lambda item: item.created_at, reverse=True)[:8]
    recent_books = Counter(
        token.split("|", 1)[0] for record in recent for token in record.normalized_references
    )
    recent_themes = Counter(theme for record in recent for theme in record.themes)
    testament_counts = Counter()
    for record in usage.records:
        for candidate in catalog.candidates:
            if record.main_passage and references_overlap(candidate.reference, record.main_passage):
                testament_counts[candidate.testament] += 1
                break

    scored: list[tuple[float, int, VerseCandidate]] = []
    for order, candidate in enumerate(available):
        book_token = next(iter(reference_tokens(candidate.reference))).split("|", 1)[0]
        score = candidate.selection_weight * 100
        verse_word_count = len(tokenize_words(candidate.verse_text))
        if verse_word_count <= 18:
            brevity_bonus = 16
        elif verse_word_count <= 25:
            brevity_bonus = 8
        elif verse_word_count <= 32:
            brevity_bonus = 0
        else:
            brevity_bonus = -12
        score += candidate.familiarity_score * 20
        score += candidate.clarity_score * 16
        score += candidate.retention_potential_score * 20
        if candidate.human_conflict.strip() and candidate.central_question.strip():
            score += 6
        if len(candidate.visual_progression) >= 2:
            score += 6
        if candidate.possible_reveal.strip():
            score += 4
        score += brevity_bonus
        score -= recent_books[book_token] * 16
        score -= sum(recent_themes[theme] for theme in candidate.themes) * 7
        score -= testament_counts[candidate.testament] * 2
        scored.append((score, -order, candidate))
    score, _, selected = max(scored, key=lambda item: (item[0], item[1]))
    return selected, round(score, 2)


@contextmanager
def _selection_lock(projects_dir: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    lock = projects_dir / "shorts" / ".verse-selection.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()} at={utc_now()}".encode())
            os.close(descriptor)
            break
        except FileExistsError:
            if lock.exists() and time.time() - lock.stat().st_mtime > 300:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("Outra criação de Short está reservando um versículo.") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def create_auto_video(
    *,
    projects_dir: Path | None = None,
    target_duration_seconds: int,
    video_format: Literal["landscape", "short"],
    project_date: date | None = None,
    catalog_path: Path = DEFAULT_VERSE_CATALOG,
    production_mode: ProductionMode = "draft_one_shot",
) -> tuple[ProjectLayout, VerseCandidate, float]:
    root = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    catalog = load_verse_catalog(catalog_path)
    with _selection_lock(root):
        local = build_usage_index(root, catalog=catalog)

        def select_and_reserve(
            usage: VerseUsageIndex,
        ) -> tuple[
            VerseUsageIndex,
            tuple[VerseCandidate, float, VerseUsageIndex, str | None],
        ]:
            candidate, score = select_unused_verse(catalog, usage)
            retention_archetype = (
                select_retention_archetype(candidate, usage) if video_format == "short" else None
            )
            predicted_id = project_directory_name(candidate.slug, project_date=project_date)
            reservation = _reservation_record(
                candidate,
                project_id=predicted_id,
                video_format=video_format,
                retention_archetype=retention_archetype,
            )
            updated = merge_usage_indexes(
                usage,
                VerseUsageIndex(generated_at=utc_now(), records=[reservation]),
            )
            return updated, (candidate, score, usage, retention_archetype)

        if shared_registry_enabled():

            def mutate(
                raw: dict[str, object],
            ) -> tuple[
                dict[str, object],
                tuple[VerseCandidate, float, VerseUsageIndex, str | None],
            ]:
                shared = VerseUsageIndex.model_validate(raw)
                updated, result = select_and_reserve(merge_usage_indexes(shared, local))
                return updated.model_dump(mode="json"), result

            candidate, score, before, retention_archetype = update_shared_registry(
                mutate,
                message="registry: reserve automatic passage",
            )
        else:
            reserved, (candidate, score, before, retention_archetype) = select_and_reserve(local)
            write_json(root / VERSE_USAGE_FILENAME, reserved)
        project_title = (
            candidate.title_seed.strip() or candidate.title
            if video_format == "short"
            else candidate.title
        )
        layout = create_project(
            candidate.slug,
            project_title,
            projects_dir=root,
            target_duration_seconds=target_duration_seconds,
            video_format=video_format,
            project_date=project_date,
            production_mode=production_mode,
        )
        briefing = Briefing(
            title=project_title,
            main_passage=candidate.reference,
            central_theme=candidate.central_theme,
            practical_application=candidate.practical_application,
            audience=candidate.audience,
            emotional_intent=candidate.emotional_intent,
            narration_tone=candidate.narration_tone,
            target_duration_seconds=target_duration_seconds,
            bible_translation=candidate.translation,
            literal_quote_policy=(
                "Citar no roteiro o texto literal verificado desta fonte em domínio público; "
                "qualquer outra citação literal exige fonte, tradução e direitos registrados."
            ),
            interpretation_limits=candidate.interpretation_limits,
            claims_to_avoid=candidate.claims_to_avoid,
            visual_notes=candidate.visual_notes,
            call_to_action=(
                (
                    "Escolher exatamente uma ação natural — comentar, inscrever-se ou "
                    "compartilhar — integrada ao payoff, em até doze palavras e quatro segundos."
                )
                if video_format == "short"
                else (
                    "Concluir com uma aplicação prática fiel à passagem e um convite comunitário "
                    "natural, sem promessas espirituais ou pressão para engajamento."
                )
            ),
            retention_archetype=retention_archetype,
            human_conflict=candidate.human_conflict,
            central_question=candidate.central_question,
            visual_progression=candidate.visual_progression,
            possible_reveal=candidate.possible_reveal,
            title_seed=candidate.title_seed,
        )
        sources = SourceLibrary(
            entries=[
                SourceEntry(
                    source_id=candidate.source_id,
                    kind="biblical_text",
                    title=f"{candidate.reference} — {candidate.translation}",
                    passage=candidate.context_reference,
                    translation=candidate.translation,
                    author_or_publisher=catalog.publisher,
                    url=candidate.source_url,
                    accessed_at=candidate.accessed_at,
                    excerpt=candidate.verse_text,
                    use_in_video=(
                        "Citação literal obrigatória e base da reflexão do Short."
                        if video_format == "short"
                        else "Citação literal obrigatória e base do desenvolvimento do vídeo."
                    ),
                    claim_scope="Texto bíblico; contexto e aplicações devem ser distinguidos.",
                    rights_note=candidate.rights_note,
                    verified=True,
                )
            ]
        )
        write_json(layout.briefing, briefing)
        write_json(layout.sources, sources)
        digest = sha256_value({"briefing": briefing, "sources": sources})
        complete_stage(
            layout,
            "briefing",
            digest,
            [
                relative_posix(layout.briefing, layout.root),
                relative_posix(layout.sources, layout.root),
            ],
            provider="automatic-verse-selector",
            note="Briefing preenchido automaticamente; aprovação humana permanece pendente.",
        )
        layout.append_decision(
            {
                "type": "automatic_base_verse_selection",
                "candidate_id": candidate.candidate_id,
                "reference": candidate.reference,
                "score": score,
                "verse_word_count": len(tokenize_words(candidate.verse_text)),
                "familiarity_score": candidate.familiarity_score,
                "clarity_score": candidate.clarity_score,
                "retention_potential_score": candidate.retention_potential_score,
                "retention_archetype": retention_archetype,
                "catalog_id": catalog.catalog_id,
                "usage_records_considered": len(before.records),
                "development_modes": candidate.preferred_development_modes,
            }
        )
        sync_shared_usage(root, catalog=catalog)
        return layout, candidate, score


def create_auto_short(
    *,
    projects_dir: Path | None = None,
    target_duration_seconds: int = 50,
    project_date: date | None = None,
    catalog_path: Path = DEFAULT_VERSE_CATALOG,
    production_mode: ProductionMode = "draft_one_shot",
) -> tuple[ProjectLayout, VerseCandidate, float]:
    return create_auto_video(
        projects_dir=projects_dir,
        target_duration_seconds=target_duration_seconds,
        video_format="short",
        project_date=project_date,
        catalog_path=catalog_path,
        production_mode=production_mode,
    )
