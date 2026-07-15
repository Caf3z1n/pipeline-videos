from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

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
from pipeline_videos.project import create_project
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


def _project_references(layout: ProjectLayout) -> list[str]:
    references: list[str] = []
    if layout.briefing.is_file():
        data = read_json(layout.briefing)
        references.extend(
            [str(data.get("main_passage", "")), *data.get("complementary_references", [])]
        )
    if layout.sources.is_file():
        data = read_json(layout.sources)
        references.extend(str(item.get("passage", "")) for item in data.get("entries", []))
    if layout.script.is_file():
        data = read_json(layout.script)
        for section in data.get("sections", []):
            references.extend(str(item) for item in section.get("biblical_basis", []))
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
            manifest = layout.load_manifest()
            references = _project_references(layout)
            briefing = read_model(layout.briefing, Briefing)
        except (ValueError, FileNotFoundError, json.JSONDecodeError):
            continue
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
                project_id=manifest.project_id,
                project_path=manifest_path.parent.relative_to(root).as_posix(),
                video_format=manifest.video_format,
                status="used" if layout.script.is_file() else "reserved",
                references=references,
                normalized_references=tokens,
                main_passage=briefing.main_passage or None,
                themes=list(dict.fromkeys(theme for item in matched for theme in item.themes)),
                characters=list(
                    dict.fromkeys(name for item in matched for name in item.characters)
                ),
                created_at=manifest.created_at,
            )
        )
    return VerseUsageIndex(generated_at=utc_now(), records=records)


def select_unused_verse(
    catalog: VerseCatalog, usage: VerseUsageIndex
) -> tuple[VerseCandidate, float]:
    used_references = [reference for record in usage.records for reference in record.references]
    available = [
        candidate
        for candidate in catalog.candidates
        if not any(
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
        before = build_usage_index(root, catalog=catalog)
        candidate, score = select_unused_verse(catalog, before)
        layout = create_project(
            candidate.slug,
            candidate.title,
            projects_dir=root,
            target_duration_seconds=target_duration_seconds,
            video_format=video_format,
            project_date=project_date,
            production_mode=production_mode,
        )
        manifest = layout.load_manifest()
        briefing = Briefing(
            title=candidate.title,
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
                    "Escolher um convite natural para comentar uma palavra específica e finalizar "
                    f"exatamente com: {manifest.config.shorts_script.final_subscribe_cta}"
                )
                if video_format == "short"
                else (
                    "Concluir com uma aplicação prática fiel à passagem e um convite comunitário "
                    "natural, sem promessas espirituais ou pressão para engajamento."
                )
            ),
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
                "catalog_id": catalog.catalog_id,
                "usage_records_considered": len(before.records),
                "development_modes": candidate.preferred_development_modes,
            }
        )
        write_json(root / VERSE_USAGE_FILENAME, build_usage_index(root, catalog=catalog))
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
