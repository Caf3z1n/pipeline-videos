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

from pipeline_videos.creative_direction import (
    CREATIVE_MODE_SPECS,
    DEVELOPMENT_MODE_TO_CREATIVE_MODES,
    SHORT_AUTOMATIC_EXCLUSIONS,
    default_interpretive_stance,
    default_long_hook_policy,
    hook_families_for_mode,
    hook_matches_family,
    narration_tone_for_mode,
    viewer_outcome_for_mode,
)
from pipeline_videos.git_registry import (
    read_shared_registry,
    shared_registry_enabled,
    update_shared_registry,
)
from pipeline_videos.models import (
    COMPACT_SHORT_THRESHOLD_SECONDS,
    USER_SIX_HOOK_FAMILIES,
    Briefing,
    CreativeMode,
    HookContract,
    HookFamily,
    ProductionMode,
    SourceEntry,
    SourceLibrary,
    VerseCandidate,
    VerseCatalog,
    VerseUsageIndex,
    VerseUsageRecord,
)
from pipeline_videos.project import (
    COMPACT_MINIMUM_HOOK_STRENGTH_SCORE,
    COMPACT_MINIMUM_MESSAGE_VALUE_SCORE,
    COMPACT_MINIMUM_REWATCH_VALUE_SCORE,
    compact_narration_word_range,
    create_project,
    project_directory_name,
    short_literal_verse_word_limit,
)
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
DEFAULT_VERSE_EXPANSION_BANK = REPO_ROOT / "config" / "bible" / "shorts-verse-expansion-bank.json"
DEFAULT_CURATED_VERSE_BANK = REPO_ROOT / "config" / "bible" / "curated-verse-bank-v2.json"
DEFAULT_NARRATIVE_CHARACTER_BANK = (
    REPO_ROOT / "config" / "bible" / "narrative-character-bank-v1.json"
)
DEFAULT_VERSE_BANKS = (
    DEFAULT_VERSE_EXPANSION_BANK,
    DEFAULT_CURATED_VERSE_BANK,
    DEFAULT_NARRATIVE_CHARACTER_BANK,
)
VERSE_USAGE_FILENAME = "verse-usage.json"
REGISTRY_ENVIRONMENT_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_ENVIRONMENT"
RETENTION_ARCHETYPES = (
    "conflict_first",
    "counterintuitive_reveal",
    "stair_step_progression",
)
COMPACT_DEVOTIONAL_MODES: tuple[CreativeMode, ...] = (
    "devotional_application",
    "pastoral_encouragement",
)

BOOK_ALIASES = {
    "genesis": "genesis",
    "exodo": "exodo",
    "levitico": "levitico",
    "numeros": "numeros",
    "deuteronomio": "deuteronomio",
    "josue": "josue",
    "juizes": "juizes",
    "rute": "rute",
    "1 samuel": "1-samuel",
    "i samuel": "1-samuel",
    "2 samuel": "2-samuel",
    "ii samuel": "2-samuel",
    "1 reis": "1-reis",
    "i reis": "1-reis",
    "2 reis": "2-reis",
    "ii reis": "2-reis",
    "1 cronicas": "1-cronicas",
    "i cronicas": "1-cronicas",
    "2 cronicas": "2-cronicas",
    "ii cronicas": "2-cronicas",
    "esdras": "esdras",
    "neemias": "neemias",
    "ester": "ester",
    "jo": "jo",
    "salmo": "salmos",
    "salmos": "salmos",
    "proverbios": "proverbios",
    "eclesiastes": "eclesiastes",
    "cantares": "cantares",
    "cantares de salomao": "cantares",
    "isaias": "isaias",
    "jeremias": "jeremias",
    "lamentacoes": "lamentacoes",
    "ezequiel": "ezequiel",
    "daniel": "daniel",
    "oseias": "oseias",
    "joel": "joel",
    "amos": "amos",
    "obadias": "obadias",
    "jonas": "jonas",
    "miqueias": "miqueias",
    "naum": "naum",
    "habacuque": "habacuque",
    "sofonias": "sofonias",
    "ageu": "ageu",
    "zacarias": "zacarias",
    "malaquias": "malaquias",
    "mateus": "mateus",
    "marcos": "marcos",
    "lucas": "lucas",
    "joao": "joao",
    "atos": "atos",
    "romanos": "romanos",
    "1 corintios": "1-corintios",
    "i corintios": "1-corintios",
    "2 corintios": "2-corintios",
    "ii corintios": "2-corintios",
    "galatas": "galatas",
    "efesios": "efesios",
    "filipenses": "filipenses",
    "colossenses": "colossenses",
    "1 tessalonicenses": "1-tessalonicenses",
    "i tessalonicenses": "1-tessalonicenses",
    "2 tessalonicenses": "2-tessalonicenses",
    "ii tessalonicenses": "2-tessalonicenses",
    "1 timoteo": "1-timoteo",
    "i timoteo": "1-timoteo",
    "2 timoteo": "2-timoteo",
    "ii timoteo": "2-timoteo",
    "tito": "tito",
    "filemom": "filemom",
    "hebreus": "hebreus",
    "tiago": "tiago",
    "1 pedro": "1-pedro",
    "i pedro": "1-pedro",
    "2 pedro": "2-pedro",
    "ii pedro": "2-pedro",
    "1 joao": "1-joao",
    "i joao": "1-joao",
    "2 joao": "2-joao",
    "ii joao": "2-joao",
    "3 joao": "3-joao",
    "iii joao": "3-joao",
    "judas": "judas",
    "apocalipse": "apocalipse",
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


def load_verse_catalog(
    path: Path = DEFAULT_VERSE_CATALOG,
    *,
    include_expansion_bank: bool = True,
) -> VerseCatalog:
    catalog = read_model(path, VerseCatalog)
    if not include_expansion_bank or path.resolve() != DEFAULT_VERSE_CATALOG.resolve():
        return catalog
    candidates = list(catalog.candidates)
    known_ids = {candidate.candidate_id for candidate in candidates}
    known_standard_references = {
        token for candidate in candidates for token in reference_tokens(candidate.reference)
    }
    loaded_catalog_ids = [catalog.catalog_id]
    for bank_path in DEFAULT_VERSE_BANKS:
        if not bank_path.is_file():
            continue
        expansion = read_model(bank_path, VerseCatalog)
        if (
            expansion.translation != catalog.translation
            or expansion.rights_note != catalog.rights_note
        ):
            raise ValueError(
                f"O banco {bank_path.name} precisa usar a mesma tradução e os mesmos direitos."
            )
        loaded_catalog_ids.append(expansion.catalog_id)
        for candidate in expansion.candidates:
            tokens = reference_tokens(candidate.reference)
            narrative_story = candidate.catalog_segment == "narrative_story"
            if candidate.candidate_id in known_ids or (
                not narrative_story and tokens & known_standard_references
            ):
                continue
            candidates.append(candidate)
            known_ids.add(candidate.candidate_id)
            if not narrative_story:
                known_standard_references.update(tokens)
    return catalog.model_copy(
        update={
            "catalog_id": "+".join(loaded_catalog_ids),
            "candidates": candidates,
        }
    )


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
        supporting_references = [
            reference
            for reference in references
            if main_passage is None or not references_overlap(reference, main_passage)
        ]
        topic = str(briefing.get("central_theme") or title).strip()
        retention_archetype = str(briefing.get("retention_archetype") or "").strip() or None
        if retention_archetype not in RETENTION_ARCHETYPES:
            retention_archetype = None
        creative_mode = str(briefing.get("creative_mode") or "").strip() or None
        if creative_mode not in CREATIVE_MODE_SPECS:
            creative_mode = None
        hook_family = str(briefing.get("hook_family") or "").strip() or None
        if hook_family in {"auto", "none"}:
            hook_family = None
        if hook_family is None:
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
            creative_direction = strategy.get("creative_direction")
            if isinstance(creative_direction, dict):
                selected_mode = str(creative_direction.get("creative_mode") or "").strip()
                if selected_mode in CREATIVE_MODE_SPECS:
                    creative_mode = selected_mode
                selected_hook = str(creative_direction.get("hook_family") or "").strip()
                if selected_hook not in {"", "auto", "none"}:
                    hook_family = selected_hook
                engagement = creative_direction.get("engagement")
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
                supporting_references=supporting_references,
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
                creative_mode=creative_mode,
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
            supporting_references = list(
                dict.fromkeys([*current.supporting_references, *record.supporting_references])
            )
            merged[key] = record.model_copy(
                update={
                    "status": "used" if "used" in {current.status, record.status} else "reserved",
                    "references": references,
                    "supporting_references": supporting_references,
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
    creative_mode: CreativeMode | None = None,
    hook_family: HookFamily | None = None,
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
        supporting_references=[],
        normalized_references=sorted(
            {token for reference in references for token in reference_tokens(reference)}
        ),
        main_passage=candidate.reference,
        topic=candidate.central_theme,
        normalized_topic=_plain(candidate.central_theme),
        themes=list(dict.fromkeys([candidate.central_theme, *candidate.themes])),
        characters=candidate.characters,
        retention_archetype=retention_archetype,
        hook_family=hook_family or retention_archetype,
        creative_mode=creative_mode,
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
    creative_mode: CreativeMode | None = None,
    hook_family: HookFamily | None = None,
) -> VerseUsageIndex:
    if not candidates:
        raise ValueError("Informe ao menos uma passagem para reservar.")
    root = projects_dir.expanduser().resolve()
    local = build_usage_index(root, catalog=catalog)
    environment = registry_environment()
    registry_id = f"{environment}:{project_id}"

    def reserve(usage: VerseUsageIndex) -> VerseUsageIndex:
        other_main_passages = [
            record.main_passage
            for record in usage.records
            if record.registry_id != registry_id
            and record.video_format == video_format
            and record.main_passage
        ]
        repeated = next(
            (
                reference
                for reference in other_main_passages
                if references_overlap(candidates[0].reference, reference)
            ),
            None,
        )
        if repeated:
            raise RuntimeError(
                f"A passagem principal {candidates[0].reference} foi reservada por outro "
                f"projeto {video_format} durante a seleção."
            )
        main = candidates[0]
        reservation = _reservation_record(
            main,
            project_id=project_id,
            video_format=video_format,
            retention_archetype=retention_archetype,
            creative_mode=creative_mode,
            hook_family=hook_family,
        )
        references = list(
            dict.fromkeys(
                reference
                for candidate in candidates
                for reference in (candidate.reference, candidate.context_reference)
            )
        )
        supporting_references = list(
            dict.fromkeys(
                reference
                for candidate in candidates[1:]
                for reference in (candidate.reference, candidate.context_reference)
            )
        )
        reservation = reservation.model_copy(
            update={
                "references": references,
                "supporting_references": supporting_references,
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


def creative_modes_for_candidate(
    candidate: VerseCandidate,
    *,
    video_format: Literal["landscape", "short"],
) -> list[CreativeMode]:
    modes: list[str] = []
    for development_mode in candidate.preferred_development_modes:
        modes.extend(DEVELOPMENT_MODE_TO_CREATIVE_MODES.get(development_mode, ()))
    if candidate.characters:
        modes.extend(("character_portrait", "biblical_storytelling", "cause_and_consequence"))
    if candidate.possible_reveal.strip():
        modes.extend(("biblical_curiosity", "misconception_correction"))
    modes.extend(
        (
            "passage_explainer",
            "question_driven_essay",
            "devotional_application",
            "pastoral_encouragement",
        )
    )
    unique = [mode for mode in dict.fromkeys(modes) if mode in CREATIVE_MODE_SPECS]
    # Historical context needs an independently verified contextual source, which the automatic
    # verse catalog does not currently provide. It remains available for explicit briefings.
    unique = [mode for mode in unique if mode != "historical_context"]
    if video_format == "short":
        unique = [mode for mode in unique if mode not in SHORT_AUTOMATIC_EXCLUSIONS]
    return unique  # type: ignore[return-value]


def select_creative_mode(
    candidate: VerseCandidate,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"],
) -> CreativeMode:
    """Choose a passage-compatible mode while avoiding recent editorial repetition."""
    preferred = creative_modes_for_candidate(candidate, video_format=video_format)
    if not preferred:
        preferred = ["passage_explainer"]
    recent = sorted(
        (record for record in usage.records if record.video_format == video_format),
        key=lambda item: item.created_at,
        reverse=True,
    )
    last_four = {record.creative_mode for record in recent[:4] if record.creative_mode}
    frequencies = Counter(
        record.creative_mode for record in recent if record.creative_mode in preferred
    )
    return min(
        preferred,
        key=lambda mode: (
            mode in last_four,
            frequencies[mode],
            preferred.index(mode),
        ),
    )


def select_compact_devotional_mode(usage: VerseUsageIndex) -> CreativeMode:
    """Alternate only between the two emotional modes allowed by the compact profile."""

    recent = sorted(
        (record for record in usage.records if record.video_format == "short"),
        key=lambda item: item.created_at,
        reverse=True,
    )
    frequencies = Counter(record.creative_mode for record in recent)
    last_mode = recent[0].creative_mode if recent else None
    return min(
        COMPACT_DEVOTIONAL_MODES,
        key=lambda mode: (
            mode == last_mode,
            frequencies[mode],
            COMPACT_DEVOTIONAL_MODES.index(mode),
        ),
    )


def select_hook_family(
    creative_mode: str,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"],
    hook_policy: str,
    hook_contract: HookContract = "mode_compatible_v1",
) -> HookFamily:
    """Resolve an ethical hook family; long-form optional modes may deliberately use none."""
    if hook_policy == "none":
        return "none"
    recent = sorted(
        (record for record in usage.records if record.video_format == video_format),
        key=lambda item: item.created_at,
        reverse=True,
    )
    if video_format == "landscape" and hook_policy == "optional":
        latest_family = recent[0].hook_family if recent else None
        if latest_family not in {None, "none"}:
            return "none"
        if not recent:
            return "none"
    preferred = list(
        hook_families_for_mode(
            creative_mode,
            short=video_format == "short",
            hook_contract=hook_contract,
        )
    )
    last_three = {record.hook_family for record in recent[:3] if record.hook_family}
    frequencies = Counter(
        record.hook_family for record in recent if record.hook_family in preferred
    )
    return min(
        preferred,
        key=lambda family: (
            family in last_three,
            frequencies[family],
            preferred.index(family),
        ),
    )  # type: ignore[return-value]


def _main_candidate_usage(
    catalog: VerseCatalog,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"],
) -> tuple[dict[str, int], set[str]]:
    counts = {candidate.candidate_id: 0 for candidate in catalog.candidates}
    reserved: set[str] = set()
    for record in usage.records:
        if record.video_format != video_format or not record.main_passage:
            continue
        for candidate in catalog.candidates:
            if not references_overlap(candidate.reference, record.main_passage):
                continue
            counts[candidate.candidate_id] += 1
            if record.status == "reserved":
                reserved.add(candidate.candidate_id)
            break
    return counts, reserved


def catalog_capacity_summary(
    catalog: VerseCatalog,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"],
) -> dict[str, int | str]:
    counts, reserved = _main_candidate_usage(catalog, usage, video_format=video_format)
    eligible_ids = {
        candidate.candidate_id
        for candidate in catalog.candidates
        if _candidate_supports_format(candidate, video_format)
    }
    eligible_counts = {
        candidate_id: count
        for candidate_id, count in counts.items()
        if candidate_id in eligible_ids
    }
    minimum_usage = min(eligible_counts.values(), default=0)
    available = sum(
        count == minimum_usage and candidate_id not in reserved
        for candidate_id, count in eligible_counts.items()
    )
    return {
        "video_format": video_format,
        "catalog_candidates": len(eligible_ids),
        "selection_cycle": minimum_usage + 1,
        "available_in_cycle": available,
        "reserved_main_passages": len(reserved),
    }


def compact_package_word_count(candidate: VerseCandidate) -> int:
    """Count the exact compact hook + quote + spoken-reference package."""

    spoken_reference_words = len(tokenize_words(candidate.book)) + 4
    if "-" in candidate.reference.rsplit(" ", 1)[-1]:
        spoken_reference_words += 2
    return (
        len(tokenize_words(candidate.compact_hook))
        + len(tokenize_words(candidate.verse_text))
        + spoken_reference_words
    )


def _candidate_matches_compact_package(
    candidate: VerseCandidate,
    *,
    minimum_package_words: int | None,
    maximum_package_words: int | None,
    minimum_hook_strength_score: float,
    minimum_message_value_score: float,
    minimum_rewatch_value_score: float,
) -> bool:
    if minimum_package_words is None and maximum_package_words is None:
        return True
    if not candidate.compact_hook.strip():
        return False
    if not hook_matches_family(candidate.compact_hook, "emotional_interruption"):
        return False
    if not all(
        value.strip()
        for value in (
            candidate.reference,
            candidate.translation,
            candidate.verse_text,
            candidate.source_url,
            candidate.accessed_at,
            candidate.rights_note,
        )
    ):
        return False
    package_words = compact_package_word_count(candidate)
    if minimum_package_words is not None and package_words < minimum_package_words:
        return False
    if maximum_package_words is not None and package_words > maximum_package_words:
        return False
    return (
        candidate.compact_hook_strength_score >= minimum_hook_strength_score
        and candidate.compact_message_value_score >= minimum_message_value_score
        and candidate.compact_rewatch_value_score >= minimum_rewatch_value_score
    )


def _candidate_supports_format(
    candidate: VerseCandidate,
    video_format: Literal["landscape", "short"],
    *,
    requested_character: str | None = None,
) -> bool:
    requested = "short" if video_format == "short" else "long"
    if requested_character:
        if candidate.catalog_segment != "narrative_story":
            return False
        normalized = _plain(requested_character)
        names = {
            _plain(value)
            for value in [*candidate.characters, *candidate.character_aliases]
            if value.strip()
        }
        return requested in candidate.recommended_formats and normalized in names
    return (
        candidate.catalog_segment != "narrative_story"
        and requested in candidate.recommended_formats
    )


def eligible_main_candidate_ids(
    catalog: VerseCatalog,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"],
    max_verse_words: int | None = None,
    required_spoken_by: Literal["jesus"] | None = None,
    minimum_package_words: int | None = None,
    maximum_package_words: int | None = None,
    minimum_hook_strength_score: float = 0.0,
    minimum_message_value_score: float = 0.0,
    minimum_rewatch_value_score: float = 0.0,
    requested_character: str | None = None,
) -> set[str]:
    counts, reserved = _main_candidate_usage(catalog, usage, video_format=video_format)
    candidate_ids = {
        candidate.candidate_id
        for candidate in catalog.candidates
        if _candidate_supports_format(
            candidate,
            video_format,
            requested_character=requested_character,
        )
        if max_verse_words is None or len(tokenize_words(candidate.verse_text)) <= max_verse_words
        if required_spoken_by is None or candidate.spoken_by == required_spoken_by
        if _candidate_matches_compact_package(
            candidate,
            minimum_package_words=minimum_package_words,
            maximum_package_words=maximum_package_words,
            minimum_hook_strength_score=minimum_hook_strength_score,
            minimum_message_value_score=minimum_message_value_score,
            minimum_rewatch_value_score=minimum_rewatch_value_score,
        )
    }
    minimum_usage = min((counts[candidate_id] for candidate_id in candidate_ids), default=0)
    return {
        candidate_id
        for candidate_id, count in counts.items()
        if candidate_id in candidate_ids and count == minimum_usage and candidate_id not in reserved
    }


def select_unused_verse(
    catalog: VerseCatalog,
    usage: VerseUsageIndex,
    *,
    video_format: Literal["landscape", "short"] = "short",
    max_verse_words: int | None = None,
    required_spoken_by: Literal["jesus"] | None = None,
    minimum_package_words: int | None = None,
    maximum_package_words: int | None = None,
    minimum_hook_strength_score: float = 0.0,
    minimum_message_value_score: float = 0.0,
    minimum_rewatch_value_score: float = 0.0,
    requested_character: str | None = None,
) -> tuple[VerseCandidate, float]:
    counts, reserved = _main_candidate_usage(catalog, usage, video_format=video_format)
    candidate_pool = [
        candidate
        for candidate in catalog.candidates
        if _candidate_supports_format(
            candidate,
            video_format,
            requested_character=requested_character,
        )
        if max_verse_words is None or len(tokenize_words(candidate.verse_text)) <= max_verse_words
        if required_spoken_by is None or candidate.spoken_by == required_spoken_by
        if _candidate_matches_compact_package(
            candidate,
            minimum_package_words=minimum_package_words,
            maximum_package_words=maximum_package_words,
            minimum_hook_strength_score=minimum_hook_strength_score,
            minimum_message_value_score=minimum_message_value_score,
            minimum_rewatch_value_score=minimum_rewatch_value_score,
        )
    ]
    if not candidate_pool:
        if requested_character:
            raise RuntimeError(
                "O catálogo narrativo ainda não possui uma história verificada para "
                f'"{requested_character}" neste formato.'
            )
        raise RuntimeError(
            (
                "O catálogo não possui uma combinação de hook forte e fala direta de Jesus com "
                "valor e palavras suficientes para preencher a duração com narração."
            )
            if required_spoken_by == "jesus" and minimum_package_words is not None
            else (
                "O catálogo não possui fala direta de Jesus curta o bastante para a duração "
                "solicitada."
            )
            if required_spoken_by == "jesus"
            else "O catálogo não possui versículo curto o bastante para a duração solicitada."
        )
    minimum_usage = min(counts[candidate.candidate_id] for candidate in candidate_pool)
    available = [
        candidate
        for candidate in candidate_pool
        if counts[candidate.candidate_id] == minimum_usage
        and candidate.candidate_id not in reserved
    ]
    if not available:
        raise RuntimeError(
            "Todas as passagens principais deste formato estão reservadas por jobs ativos."
        )

    format_records = [record for record in usage.records if record.video_format == video_format]
    recent = sorted(format_records, key=lambda item: item.created_at, reverse=True)[:8]
    recent_books = Counter(
        token.split("|", 1)[0] for record in recent for token in record.normalized_references
    )
    recent_themes = Counter(theme for record in recent for theme in record.themes)
    testament_counts = Counter()
    for record in format_records:
        for candidate in catalog.candidates:
            if record.main_passage and references_overlap(candidate.reference, record.main_passage):
                testament_counts[candidate.testament] += 1
                break

    scored: list[tuple[float, int, VerseCandidate]] = []
    for order, candidate in enumerate(available):
        book_token = next(iter(reference_tokens(candidate.reference))).split("|", 1)[0]
        score = candidate.selection_weight * 100
        verse_word_count = len(tokenize_words(candidate.verse_text))
        if minimum_package_words is not None and maximum_package_words is not None:
            package_word_count = compact_package_word_count(candidate)
            target_word_count = (minimum_package_words + maximum_package_words) / 2
            brevity_bonus = max(0.0, 20.0 - abs(package_word_count - target_word_count) * 4)
        elif verse_word_count <= 18:
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
        if minimum_package_words is not None:
            score += candidate.compact_hook_strength_score * 24
            score += candidate.compact_message_value_score * 28
            score += candidate.compact_rewatch_value_score * 20
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
        score -= counts[candidate.candidate_id] * 250
        scored.append((score, -order, candidate))
    score, _, selected = max(scored, key=lambda item: (item[0], item[1]))
    return selected, round(score, 2)


def source_entries_for_candidate(
    candidate: VerseCandidate,
    *,
    publisher: str,
    long_form: bool,
) -> list[SourceEntry]:
    """Expand a narrative candidate into its verified, role-labelled source package."""
    entries = [
        SourceEntry(
            source_id=candidate.source_id,
            kind="biblical_text",
            title=f"{candidate.reference} — {candidate.translation}",
            passage=candidate.reference,
            context_passage=candidate.context_reference,
            translation=candidate.translation,
            author_or_publisher=publisher,
            url=candidate.source_url,
            accessed_at=candidate.accessed_at,
            excerpt=candidate.verse_text,
            use_in_video=(
                "Âncora literal verificada da narrativa do personagem."
                if candidate.catalog_segment == "narrative_story"
                else "Citação literal verificada distribuída na narrativa do vídeo longo."
                if long_form
                else "Citação literal obrigatória e base do desenvolvimento do Short."
            ),
            claim_scope="Texto bíblico; contexto e aplicações devem ser distinguidos.",
            rights_note=candidate.rights_note,
            verified=True,
            spoken_by=candidate.spoken_by,
            compact_hook=candidate.compact_hook,
            compact_hook_strength_score=candidate.compact_hook_strength_score,
            compact_message_value_score=candidate.compact_message_value_score,
            compact_rewatch_value_score=candidate.compact_rewatch_value_score,
        )
    ]
    for related in candidate.related_passages:
        entries.append(
            SourceEntry(
                source_id=f"{candidate.source_id}-{related.passage_id}",
                kind="biblical_text",
                title=f"{related.reference} — {candidate.translation}",
                passage=related.reference,
                context_passage=related.context_reference,
                translation=candidate.translation,
                author_or_publisher=publisher,
                url=related.source_url,
                accessed_at=candidate.accessed_at,
                excerpt=related.verse_text,
                use_in_video=(
                    "Passagem verificada relacionada ao personagem; função narrativa: "
                    f"{related.role}."
                ),
                claim_scope="Texto bíblico; contexto e aplicações devem ser distinguidos.",
                rights_note=candidate.rights_note,
                verified=True,
                spoken_by=related.spoken_by,
            )
        )
    return entries


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
    duration_min_seconds: float | None = None,
    duration_max_seconds: float | None = None,
    video_format: Literal["landscape", "short"],
    project_date: date | None = None,
    catalog_path: Path = DEFAULT_VERSE_CATALOG,
    production_mode: ProductionMode = "final_candidate_one_shot",
    creative_mode_override: CreativeMode | None = None,
    hook_family_override: HookFamily | None = None,
    requested_character: str | None = None,
) -> tuple[ProjectLayout, VerseCandidate, float]:
    if creative_mode_override == "auto":
        creative_mode_override = None
    if hook_family_override == "auto":
        hook_family_override = None
    compact_profile = (
        video_format == "short" and target_duration_seconds < COMPACT_SHORT_THRESHOLD_SECONDS
    )
    requested_character = requested_character.strip() if requested_character else None
    if compact_profile and requested_character:
        raise ValueError(
            "Histórias de personagens não entram no perfil compacto. "
            f"Use um Short de ao menos {COMPACT_SHORT_THRESHOLD_SECONDS} segundos ou vídeo longo."
        )
    if compact_profile:
        selection_minimum_seconds = int(duration_min_seconds or target_duration_seconds)
        selection_maximum_seconds = int(duration_max_seconds or target_duration_seconds)
        minimum_words = compact_narration_word_range(selection_minimum_seconds)[0]
        maximum_words = compact_narration_word_range(selection_maximum_seconds)[1]
        compact_word_range: tuple[int | None, int | None] = (minimum_words, maximum_words)
    else:
        selection_maximum_seconds = target_duration_seconds
        compact_word_range = (None, None)
    if compact_profile and creative_mode_override not in {None, *COMPACT_DEVOTIONAL_MODES}:
        raise ValueError(
            "Shorts compactos aceitam somente devotional_application ou pastoral_encouragement."
        )
    if compact_profile and hook_family_override not in {None, "emotional_interruption"}:
        raise ValueError("Shorts compactos exigem hook_family=emotional_interruption.")
    if (
        video_format == "short"
        and not compact_profile
        and hook_family_override
        not in {
            None,
            *USER_SIX_HOOK_FAMILIES,
        }
    ):
        raise ValueError("Novos Shorts aceitam somente uma das seis famílias oficiais de hook.")
    root = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    catalog = load_verse_catalog(catalog_path)
    with _selection_lock(root):
        local = build_usage_index(root, catalog=catalog)

        def select_and_reserve(
            usage: VerseUsageIndex,
        ) -> tuple[
            VerseUsageIndex,
            tuple[
                VerseCandidate,
                float,
                VerseUsageIndex,
                str | None,
                CreativeMode,
                str,
                HookFamily,
            ],
        ]:
            candidate, score = select_unused_verse(
                catalog,
                usage,
                video_format=video_format,
                max_verse_words=(
                    short_literal_verse_word_limit(selection_maximum_seconds)
                    if video_format == "short"
                    else None
                ),
                required_spoken_by="jesus" if compact_profile else None,
                minimum_package_words=compact_word_range[0],
                maximum_package_words=compact_word_range[1],
                minimum_hook_strength_score=(
                    COMPACT_MINIMUM_HOOK_STRENGTH_SCORE if compact_profile else 0.0
                ),
                minimum_message_value_score=(
                    COMPACT_MINIMUM_MESSAGE_VALUE_SCORE if compact_profile else 0.0
                ),
                minimum_rewatch_value_score=(
                    COMPACT_MINIMUM_REWATCH_VALUE_SCORE if compact_profile else 0.0
                ),
                requested_character=requested_character,
            )
            retention_archetype = (
                select_retention_archetype(candidate, usage) if video_format == "short" else None
            )
            creative_mode = creative_mode_override or (
                "character_portrait"
                if requested_character
                else select_compact_devotional_mode(usage)
                if compact_profile
                else select_creative_mode(candidate, usage, video_format=video_format)
            )
            hook_policy = (
                "required" if video_format == "short" else default_long_hook_policy(creative_mode)
            )
            hook_family = (
                "emotional_interruption"
                if compact_profile
                else hook_family_override
                or select_hook_family(
                    creative_mode,
                    usage,
                    video_format=video_format,
                    hook_policy=hook_policy,
                    hook_contract=(
                        "user_six_v1" if video_format == "short" else "mode_compatible_v1"
                    ),
                )
            )
            predicted_id = project_directory_name(candidate.slug, project_date=project_date)
            reservation = _reservation_record(
                candidate,
                project_id=predicted_id,
                video_format=video_format,
                retention_archetype=retention_archetype,
                creative_mode=creative_mode,
                hook_family=hook_family,
            )
            updated = merge_usage_indexes(
                usage,
                VerseUsageIndex(generated_at=utc_now(), records=[reservation]),
            )
            return updated, (
                candidate,
                score,
                usage,
                retention_archetype,
                creative_mode,
                hook_policy,
                hook_family,
            )

        if shared_registry_enabled():

            def mutate(
                raw: dict[str, object],
            ) -> tuple[
                dict[str, object],
                tuple[
                    VerseCandidate,
                    float,
                    VerseUsageIndex,
                    str | None,
                    CreativeMode,
                    str,
                    HookFamily,
                ],
            ]:
                shared = VerseUsageIndex.model_validate(raw)
                updated, result = select_and_reserve(merge_usage_indexes(shared, local))
                return updated.model_dump(mode="json"), result

            (
                candidate,
                score,
                before,
                retention_archetype,
                creative_mode,
                hook_policy,
                hook_family,
            ) = update_shared_registry(
                mutate,
                message="registry: reserve automatic passage",
            )
        else:
            reserved, selection = select_and_reserve(local)
            (
                candidate,
                score,
                before,
                retention_archetype,
                creative_mode,
                hook_policy,
                hook_family,
            ) = selection
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
            duration_min_seconds=duration_min_seconds,
            duration_max_seconds=duration_max_seconds,
            video_format=video_format,
            project_date=project_date,
            production_mode=production_mode,
        )
        creative_spec = CREATIVE_MODE_SPECS[creative_mode]
        practical_application = (
            candidate.practical_application if creative_spec.application_required else ""
        )
        viewer_outcome = viewer_outcome_for_mode(
            creative_mode,
            theme=candidate.central_theme,
            reveal=candidate.possible_reveal,
        )
        briefing = Briefing(
            title=project_title,
            main_passage=candidate.reference,
            complementary_references=[passage.reference for passage in candidate.related_passages],
            central_theme=candidate.central_theme,
            practical_application=practical_application,
            audience=candidate.audience,
            emotional_intent=candidate.emotional_intent,
            narration_tone=(
                "íntimo, emocional, cinematográfico e sereno, como uma fala de Jesus "
                "em um filme épico"
                if compact_profile
                else narration_tone_for_mode(creative_mode)
            ),
            target_duration_seconds=target_duration_seconds,
            bible_translation=candidate.translation,
            literal_quote_policy=(
                "Citar no roteiro o texto literal verificado desta fonte em domínio público; "
                "qualquer outra citação literal exige fonte, tradução e direitos registrados."
            ),
            interpretation_limits=candidate.interpretation_limits,
            claims_to_avoid=candidate.claims_to_avoid,
            visual_notes=(
                list(
                    dict.fromkeys(
                        [
                            *candidate.visual_notes,
                            (
                                "Jesus é o centro emocional e visual da cena, em linguagem "
                                "de filme épico."
                            ),
                            (
                                "A câmera aproxima Jesus do espectador; não usar pregador, "
                                "púlpito ou Bíblia aberta explicativa."
                            ),
                            (
                                "Cada imagem preserva silêncio, presença, olhar e "
                                "continuidade cinematográfica."
                            ),
                        ]
                    )
                )
                if compact_profile
                else candidate.visual_notes
            ),
            call_to_action=(
                ""
                if compact_profile
                else (
                    "Escolher exatamente uma ação natural — comentar, inscrever-se ou "
                    "compartilhar — conectada ao valor entregue, em até doze palavras e quatro "
                    "segundos."
                )
                if video_format == "short"
                else (
                    "Encerrar depois da conclusão com exatamente um convite comunitário natural, "
                    "sem exigir aplicação pessoal, promessa espiritual ou pressão."
                )
            ),
            retention_archetype=retention_archetype,
            human_conflict=candidate.human_conflict,
            central_question=candidate.central_question,
            visual_progression=candidate.visual_progression,
            possible_reveal=candidate.possible_reveal,
            title_seed=candidate.title_seed,
            creative_mode=creative_mode,
            interpretive_stance=default_interpretive_stance(creative_mode),
            hook_policy=hook_policy,
            hook_family=hook_family,
            hook_contract=(
                "cinematic_interruption_v1"
                if compact_profile
                else "user_six_v1"
                if video_format == "short"
                else "mode_compatible_v1"
            ),
            viewer_outcome=viewer_outcome,
            creative_rationale=(f"{creative_spec.label}: {creative_spec.purpose}"),
        )
        sources = SourceLibrary(
            entries=source_entries_for_candidate(
                candidate,
                publisher=catalog.publisher,
                long_form=video_format != "short",
            )
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
                "compact_hook": candidate.compact_hook if compact_profile else None,
                "compact_package_word_count": (
                    compact_package_word_count(candidate) if compact_profile else None
                ),
                "compact_hook_strength_score": candidate.compact_hook_strength_score,
                "compact_message_value_score": candidate.compact_message_value_score,
                "compact_rewatch_value_score": candidate.compact_rewatch_value_score,
                "retention_archetype": retention_archetype,
                "creative_mode": creative_mode,
                "interpretive_stance": default_interpretive_stance(creative_mode),
                "hook_policy": hook_policy,
                "hook_family": hook_family,
                "catalog_id": catalog.catalog_id,
                "usage_records_considered": len(before.records),
                "development_modes": candidate.preferred_development_modes,
                "requested_character": requested_character,
                "related_passages": [passage.reference for passage in candidate.related_passages],
            }
        )
        sync_shared_usage(root, catalog=catalog)
        return layout, candidate, score


def create_auto_short(
    *,
    projects_dir: Path | None = None,
    target_duration_seconds: int = 50,
    duration_min_seconds: float | None = None,
    duration_max_seconds: float | None = None,
    project_date: date | None = None,
    catalog_path: Path = DEFAULT_VERSE_CATALOG,
    production_mode: ProductionMode = "final_candidate_one_shot",
    creative_mode_override: CreativeMode | None = None,
    hook_family_override: HookFamily | None = None,
    requested_character: str | None = None,
) -> tuple[ProjectLayout, VerseCandidate, float]:
    return create_auto_video(
        projects_dir=projects_dir,
        target_duration_seconds=target_duration_seconds,
        duration_min_seconds=duration_min_seconds,
        duration_max_seconds=duration_max_seconds,
        video_format="short",
        project_date=project_date,
        catalog_path=catalog_path,
        production_mode=production_mode,
        creative_mode_override=creative_mode_override,
        hook_family_override=hook_family_override,
        requested_character=requested_character,
    )
