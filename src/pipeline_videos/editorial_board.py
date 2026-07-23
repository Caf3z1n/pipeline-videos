from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    NarrationVisualScene,
    PipelineModel,
    ScriptDocument,
    ScriptSection,
    ShortsScriptStrategy,
    SourceEntry,
    SourceLibrary,
    YouTubeMetadata,
)
from pipeline_videos.narration_style import (
    validate_authorial_narration_style,
    validate_jesus_first_person_narration,
)
from pipeline_videos.storage import read_json, write_json
from pipeline_videos.utils import sha256_value, slugify, tokenize_words, utc_now

EDITORIAL_BOARD_SCHEMA_VERSION = "1.0"
QUALITY_REFERENCE = "published_high_stakes_last_three_v1"
TopicStatus = Literal["suggested", "approved", "drafted", "withdrawn"]
DraftStatus = Literal["draft", "approved", "withdrawn"]
BatchStatus = Literal["prepared", "imported", "approved"]

TOPIC_ID_PATTERN = re.compile(r"^topic-[a-z0-9][a-z0-9-]{0,72}-[a-f0-9]{8}$")
DRAFT_ID_PATTERN = re.compile(r"^script-[a-z0-9][a-z0-9-]{0,72}-[a-f0-9]{8}$")
BATCH_ID_PATTERN = re.compile(r"^batch-\d{8}-\d{6}-[a-f0-9]{8}$")
HASHTAG_PATTERN = re.compile(r"(?<!\w)#[\wÀ-ÿ]+", flags=re.UNICODE)

_FIRST_PERSON_MARKERS = re.compile(
    r"\b(?:eu|meu|minha|meus|minhas|mim|comigo|me)\b",
    flags=re.IGNORECASE,
)
_JESUS_THIRD_PERSON_OPENING = re.compile(
    r"^\s*(?:por que\s+)?jesus\b",
    flags=re.IGNORECASE,
)
_HOOK_TENSION_MARKER = re.compile(
    r"\b(?:por que|mas|mesmo assim|então|ainda|nunca|apenas|só|se)\b|[?]",
    flags=re.IGNORECASE,
)


def _normalize(value: str) -> str:
    return " ".join(value.split())


def validate_editorial_hook(hook: str) -> None:
    normalized = _normalize(hook)
    if not normalized:
        raise ValueError("O hook não pode ficar vazio.")
    if not _FIRST_PERSON_MARKERS.search(normalized):
        raise ValueError("O hook precisa usar Jesus como eu lírico em primeira pessoa.")
    if _JESUS_THIRD_PERSON_OPENING.search(normalized):
        raise ValueError("O hook não pode abrir falando de Jesus em terceira pessoa.")
    if not _HOOK_TENSION_MARKER.search(normalized):
        raise ValueError("O hook precisa abrir uma tensão, contradição ou pergunta imediatamente.")


def _slug_id(prefix: str, label: str) -> str:
    slug = slugify(label)[:64].strip("-") or prefix
    return f"{prefix}-{slug}-{uuid4().hex[:8]}"


def _topic_payload(topic: EditorialTopic) -> dict[str, Any]:
    return {
        "title": topic.title,
        "central_question": topic.central_question,
        "hook_direction": topic.hook_direction,
        "catalog_candidate_id": topic.catalog_candidate_id,
        "source_passages": topic.source_passages,
        "required_answer_points": topic.required_answer_points,
        "uncertainty_boundaries": topic.uncertainty_boundaries,
        "target_duration_seconds": topic.target_duration_seconds,
        "editorial_profile": topic.editorial_profile,
        "narrator_identity": topic.narrator_identity,
        "quality_reference": topic.quality_reference,
    }


def _draft_payload(draft: EditorialScriptDraft) -> dict[str, Any]:
    return {
        "topic_id": draft.topic_id,
        "batch_id": draft.batch_id,
        "hook": draft.hook,
        "narration": draft.narration,
        "cta": draft.cta,
        "title": draft.title,
        "description": draft.description,
        "specific_hashtags": draft.specific_hashtags,
        "internal_tags": draft.internal_tags,
        "source_passages": draft.source_passages,
        "biblical_review_status": draft.biblical_review_status,
        "biblical_review_summary": draft.biblical_review_summary,
        "hook_strength_score": draft.hook_strength_score,
        "clarity_score": draft.clarity_score,
        "payoff_score": draft.payoff_score,
        "narrator_identity": draft.narrator_identity,
        "hook_contract": draft.hook_contract,
        "quality_reference": draft.quality_reference,
    }


class EditorialTopic(PipelineModel):
    schema_version: Literal["1.0"] = EDITORIAL_BOARD_SCHEMA_VERSION
    topic_id: str
    status: TopicStatus = "suggested"
    title: str = Field(min_length=3, max_length=120)
    central_question: str = Field(default="", max_length=260)
    hook_direction: str = Field(default="", max_length=500)
    catalog_candidate_id: str | None = None
    source_passages: list[str] = Field(default_factory=list, max_length=12)
    required_answer_points: list[str] = Field(default_factory=list, max_length=12)
    uncertainty_boundaries: list[str] = Field(default_factory=list, max_length=12)
    target_duration_seconds: int = Field(default=50, ge=1, le=180)
    editorial_profile: Literal["high_stakes_biblical_question_v1"] = (
        "high_stakes_biblical_question_v1"
    )
    narrator_identity: Literal["jesus_first_person_v1"] = "jesus_first_person_v1"
    quality_reference: Literal["published_high_stakes_last_three_v1"] = QUALITY_REFERENCE
    content_hash: str
    created_at: str
    updated_at: str
    approved_at: str | None = None
    approved_by: str | None = None
    drafted_batch_id: str | None = None

    @field_validator("topic_id")
    @classmethod
    def validate_topic_id(cls, value: str) -> str:
        if not TOPIC_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de tema editorial inválido.")
        return value

    @model_validator(mode="after")
    def validate_hash(self) -> EditorialTopic:
        if self.content_hash != sha256_value(_topic_payload(self)):
            raise ValueError("O hash do tema não corresponde ao conteúdo.")
        return self


class EditorialScriptDraft(PipelineModel):
    schema_version: Literal["1.0"] = EDITORIAL_BOARD_SCHEMA_VERSION
    script_id: str
    topic_id: str
    batch_id: str
    status: DraftStatus = "draft"
    hook: str = Field(min_length=10, max_length=600)
    narration: str = Field(min_length=1)
    cta: str = Field(min_length=3, max_length=180)
    title: str = Field(min_length=3, max_length=100)
    description: str = Field(min_length=30, max_length=5000)
    specific_hashtags: list[str] = Field(min_length=3, max_length=3)
    internal_tags: list[str] = Field(min_length=10, max_length=12)
    source_passages: list[str] = Field(min_length=1, max_length=12)
    biblical_review_status: Literal["not_required", "passed", "needs_changes"]
    biblical_review_summary: str = Field(min_length=5, max_length=1000)
    hook_strength_score: float = Field(ge=0, le=10)
    clarity_score: float = Field(ge=0, le=10)
    payoff_score: float = Field(ge=0, le=10)
    narrator_identity: Literal["jesus_first_person_v1"] = "jesus_first_person_v1"
    hook_contract: Literal["high_stakes_opening_v1"] = "high_stakes_opening_v1"
    quality_reference: Literal["published_high_stakes_last_three_v1"] = QUALITY_REFERENCE
    content_hash: str
    created_at: str
    updated_at: str
    approved_at: str | None = None
    approved_by: str | None = None

    @field_validator("script_id")
    @classmethod
    def validate_script_id(cls, value: str) -> str:
        if not DRAFT_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de roteiro editorial inválido.")
        return value

    @field_validator("topic_id")
    @classmethod
    def validate_topic_id(cls, value: str) -> str:
        if not TOPIC_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de tema editorial inválido.")
        return value

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        if not BATCH_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de lote editorial inválido.")
        return value

    @model_validator(mode="after")
    def validate_editorial_contract(self) -> EditorialScriptDraft:
        narration = _normalize(self.narration)
        hook = _normalize(self.hook)
        cta = _normalize(self.cta)
        if not narration.startswith(hook):
            raise ValueError("O hook precisa ser o prefixo literal da narração.")
        if not narration.endswith(cta):
            raise ValueError("O CTA precisa encerrar a narração sem despedida posterior.")
        validate_editorial_hook(hook)
        if len(tokenize_words(cta)) > 12:
            raise ValueError("O CTA precisa ter no máximo doze palavras.")
        if "|" not in self.title:
            raise ValueError("O título precisa seguir Enfoque concreto | Passagem.")
        hashtags = HASHTAG_PATTERN.findall(self.description)
        if len(hashtags) != 5:
            raise ValueError("A descrição precisa conter exatamente cinco hashtags.")
        if {tag.casefold() for tag in hashtags[-2:]} != {"#biblia", "#jesus"}:
            raise ValueError("As duas hashtags padrão precisam ser #biblia e #jesus.")
        expected_specific = {tag.casefold() for tag in self.specific_hashtags}
        if {tag.casefold() for tag in hashtags[:3]} != expected_specific:
            raise ValueError("As três hashtags específicas não correspondem à descrição.")
        if any("#" in tag for tag in self.internal_tags):
            raise ValueError("Tags internas do YouTube não usam #.")
        if len({tag.casefold() for tag in self.internal_tags}) != len(self.internal_tags):
            raise ValueError("Tags internas do YouTube precisam ser distintas.")
        document = ScriptDocument(
            title=self.title,
            sections=[
                ScriptSection(
                    section_id="editorial-draft",
                    role="opening",
                    title="Roteiro para aprovação",
                    narration=self.narration,
                    claim_classification=["biblical_text", "verified_context"],
                )
            ],
        )
        validate_authorial_narration_style(document)
        validate_jesus_first_person_narration(document)
        if self.content_hash != sha256_value(_draft_payload(self)):
            raise ValueError("O hash do roteiro não corresponde ao conteúdo.")
        return self


class EditorialBatch(PipelineModel):
    schema_version: Literal["1.0"] = EDITORIAL_BOARD_SCHEMA_VERSION
    batch_id: str
    status: BatchStatus = "prepared"
    topic_ids: list[str] = Field(min_length=1, max_length=12)
    script_ids: list[str] = Field(default_factory=list, max_length=12)
    aggregate_hash: str | None = None
    queue_path: str
    created_at: str
    updated_at: str
    approved_at: str | None = None
    approved_by: str | None = None
    production_script_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("batch_id")
    @classmethod
    def validate_batch_id(cls, value: str) -> str:
        if not BATCH_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de lote editorial inválido.")
        return value


def _topic_summary(topic: EditorialTopic) -> dict[str, Any]:
    return {
        "topic_id": topic.topic_id,
        "status": topic.status,
        "title": topic.title,
        "central_question": topic.central_question,
        "hook_direction": topic.hook_direction,
        "catalog_candidate_id": topic.catalog_candidate_id,
        "source_passages": topic.source_passages,
        "required_answer_points": topic.required_answer_points,
        "uncertainty_boundaries": topic.uncertainty_boundaries,
        "content_hash": topic.content_hash,
    }


def _validate_topic_alignment(
    topic: EditorialTopic,
    script: EditorialScriptDraft,
) -> None:
    topic_sources = {item.casefold().strip() for item in topic.source_passages}
    script_sources = {item.casefold().strip() for item in script.source_passages}
    if topic_sources and not topic_sources.intersection(script_sources):
        raise ValueError(
            f"O roteiro {script.script_id} não usa nenhuma fonte aprovada para o tema."
        )


def _split_narration_blocks(narration: str, *, maximum: int = 7) -> list[str]:
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r".+?(?:[.!?](?=\s|$)|$)", narration, flags=re.DOTALL)
        if match.group(0).strip()
    ]
    if not sentences:
        raise ValueError("A narração aprovada não pode ficar vazia.")
    while len(sentences) > maximum:
        merge_at = min(
            range(len(sentences) - 1),
            key=lambda index: len(sentences[index]) + len(sentences[index + 1]),
        )
        sentences[merge_at : merge_at + 2] = [f"{sentences[merge_at]} {sentences[merge_at + 1]}"]
    if len(sentences) >= 3:
        return sentences
    words = narration.split()
    if len(words) < 3:
        raise ValueError("A narração aprovada precisa conter ao menos três palavras.")
    boundaries = [round(len(words) * index / 3) for index in range(4)]
    return [" ".join(words[boundaries[index] : boundaries[index + 1]]) for index in range(3)]


def _editorial_sources(
    topic: EditorialTopic,
    script: ScriptDocument,
) -> SourceLibrary:
    from pipeline_videos.editorial import scripture_citations

    citations = scripture_citations(script)
    if not citations:
        raise ValueError(
            f"O roteiro do tema {topic.topic_id} precisa conter ao menos uma citação "
            "bíblica literal verificável antes da aprovação."
        )
    entries: list[SourceEntry] = []
    used_references: set[str] = set()
    for index, citation in enumerate(citations):
        reference = topic.source_passages[min(index, len(topic.source_passages) - 1)]
        source_id = f"editorial-source-{slugify(reference)}-{index + 1:02d}"
        entries.append(
            SourceEntry(
                source_id=source_id,
                kind="biblical_text",
                title=f"{reference} — Bíblia Portuguesa Mundial (BPM)",
                passage=reference,
                translation="Bíblia Portuguesa Mundial (BPM)",
                excerpt=citation.quote,
                use_in_video="Citação literal conferida no preflight editorial.",
                claim_scope="Somente o trecho literal aprovado.",
                rights_note="Domínio público conforme a edição BPM usada pelo pipeline.",
                verified=True,
            )
        )
        used_references.add(reference.casefold())
    for index, reference in enumerate(topic.source_passages):
        if reference.casefold() in used_references:
            continue
        entries.append(
            SourceEntry(
                source_id=f"editorial-context-{slugify(reference)}-{index + 1:02d}",
                kind="biblical_text",
                title=f"{reference} — fonte de contexto aprovada",
                passage=reference,
                translation="Bíblia Portuguesa Mundial (BPM)",
                use_in_video="Contexto e desenvolvimento do roteiro.",
                claim_scope="Somente os pontos aprovados no tema.",
                verified=True,
            )
        )
    return SourceLibrary(entries=entries)


def _approved_snapshot_from_editorial(
    topic: EditorialTopic,
    draft: EditorialScriptDraft,
    *,
    approved_by: str,
    approved_at: str,
) -> Any:
    from pipeline_videos.approved_scripts import (
        ApprovedCtaOption,
        ApprovedShortScript,
        approved_content_hash,
    )

    section_id = "editorial-approved"
    script = ScriptDocument(
        title=draft.title,
        sections=[
            ScriptSection(
                section_id=section_id,
                role="opening",
                title=topic.title,
                narration=draft.narration,
                biblical_basis=draft.source_passages,
                claim_classification=[
                    "biblical_text",
                    "verified_context",
                    *(["interpretation"] if topic.uncertainty_boundaries else []),
                ],
            )
        ],
        source_ids=[],
        notes=["Importado deterministicamente da bancada editorial aprovada."],
    )
    sources = _editorial_sources(topic, script)
    source_ids = [entry.source_id for entry in sources.entries]
    script = script.model_copy(update={"source_ids": source_ids})
    blocks = _split_narration_blocks(draft.narration)
    visual_scenes = [
        NarrationVisualScene(
            section_id=section_id,
            narration=block,
            scene_description=(
                "Cena bíblica concreta em andamento, com sujeito, ação e tensão visíveis, "
                f"representando este bloco: {block}"
                if index == 0
                else (
                    "Imagem simbólica cristã simples ligada ao convite final."
                    if index == len(blocks) - 1 and block == draft.cta
                    else f"Cena narrativa concreta que represente visualmente: {block}"
                )
            ),
            visual_relation=(
                "literal"
                if index == 0
                else "symbolic"
                if index == len(blocks) - 1 and block == draft.cta
                else "contextual"
            ),
        )
        for index, block in enumerate(blocks)
    ]
    main_source_id = source_ids[0]
    briefing = Briefing(
        title=draft.title,
        main_passage=topic.source_passages[0],
        complementary_references=topic.source_passages[1:],
        central_theme=topic.title,
        audience="Pessoas interessadas em curiosidades e dúvidas bíblicas.",
        emotional_intent="Criar curiosidade honesta e entregar uma resposta bíblica clara.",
        target_duration_seconds=topic.target_duration_seconds,
        bible_translation="Bíblia Portuguesa Mundial (BPM)",
        interpretation_limits=topic.uncertainty_boundaries,
        claims_to_avoid=topic.uncertainty_boundaries,
        call_to_action=draft.cta,
        retention_archetype="counterintuitive_reveal",
        central_question=topic.central_question,
        title_seed=draft.title.split("|", 1)[0].strip(),
        creative_mode="thematic_investigation",
        interpretive_stance="analytical",
        hook_policy="required",
        hook_family="contradiction",
        hook_contract="high_stakes_opening_v1",
        editorial_profile="high_stakes_biblical_question_v1",
        question_id=topic.topic_id,
        opening_hook=draft.hook,
        implicit_viewer_question=topic.central_question,
        primary_literal_source_id=main_source_id,
        required_answer_points=topic.required_answer_points or [topic.central_question],
        uncertainty_boundaries=topic.uncertainty_boundaries,
        answer_status="bounded_inference" if topic.uncertainty_boundaries else "direct",
        viewer_outcome=" ".join(topic.required_answer_points),
        creative_rationale="Roteiro aprovado na bancada editorial em massa.",
    )
    strategy = ShortsScriptStrategy(
        target_viewer=briefing.audience,
        viewer_need=topic.central_question,
        core_value="Resposta bíblica clara, direta e intelectualmente honesta.",
        content_promise=topic.central_question,
        hook=draft.hook,
        hook_type="curiosity_gap",
        hook_family="contradiction",
        hook_techniques=["curiosity_gap", "contrast", "specificity"],
        hook_target_end_seconds=3.5,
        open_loop=topic.central_question,
        payoff=" ".join(topic.required_answer_points) or topic.central_question,
        practical_value="Compreender a dúvida dentro das fontes e dos limites aprovados.",
        cta=draft.cta,
        reason_to_subscribe="Outras dúvidas bíblicas explicadas com clareza.",
        retention_beats=[],
        visual_scenes=visual_scenes,
        source_ids=source_ids,
        section_ids=[section_id],
        biblical_safeguards=topic.uncertainty_boundaries,
        compliance_checks=[
            "Roteiro e SEO congelados pelo hash agregado aprovado.",
            "Preflight de fontes concluído na mesma saída editorial.",
        ],
    )
    review = BiblicalReview(
        script_version=script.version,
        reviewed_at=approved_at,
        reviewer="inline-source-guard-v1",
        summary=draft.biblical_review_summary,
        approved=False,
        approval_note="Aprovação humana pertence ao hash do lote, não à revisão bíblica.",
    )
    metadata = YouTubeMetadata(
        status="draft",
        title=draft.title,
        alternative_titles=[],
        description=draft.description,
        chapters=[],
        topics=draft.internal_tags,
        pinned_comment=topic.central_question,
        publication_visibility="private",
    )
    cta_mode: Literal["comment", "subscribe", "share"] = (
        "comment"
        if "coment" in draft.cta.casefold()
        else "subscribe"
        if "inscrev" in draft.cta.casefold()
        else "share"
    )
    cta_options = [
        ApprovedCtaOption(
            option_id="aprovado",
            spoken_text=draft.cta,
            mode=cta_mode,
        )
    ]
    shell = ApprovedShortScript.model_construct(
        approved_script_id="approved-placeholder-00000000",
        status="approved",
        label=topic.title,
        source_project_id=f"editorial-{draft.script_id}",
        source_project_path=f"editorial-board/{draft.batch_id}/{draft.script_id}",
        target_duration_seconds=topic.target_duration_seconds,
        editorial_profile=topic.editorial_profile,
        briefing=briefing,
        sources=sources,
        script=script,
        biblical_review=review,
        strategy=strategy,
        metadata=metadata,
        cta_options=cta_options,
        selected_cta_id="aprovado",
        voice_variant="jesus_1",
        music_track="leaning",
        content_hash="",
        created_at=draft.created_at,
        updated_at=approved_at,
        approved_at=approved_at,
        approved_by=approved_by,
        approval_note=f"Aprovado pelo hash agregado do lote {draft.batch_id}.",
    )
    digest = approved_content_hash(shell)
    script_id = f"approved-{slugify(topic.title)[:60]}-{digest[:8]}"
    return ApprovedShortScript.model_validate(
        shell.model_copy(
            update={
                "approved_script_id": script_id,
                "content_hash": digest,
            }
        )
    )


def _draft_summary(draft: EditorialScriptDraft, *, include_text: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "script_id": draft.script_id,
        "topic_id": draft.topic_id,
        "batch_id": draft.batch_id,
        "status": draft.status,
        "content_hash": draft.content_hash,
        "scores": {
            "hook": draft.hook_strength_score,
            "clarity": draft.clarity_score,
            "payoff": draft.payoff_score,
        },
        "biblical_review_status": draft.biblical_review_status,
    }
    if include_text:
        payload.update(
            {
                "hook": draft.hook,
                "narration": draft.narration,
                "cta": draft.cta,
                "title": draft.title,
                "description": draft.description,
            }
        )
    return payload


class EditorialBoardStore:
    topic_statuses: tuple[TopicStatus, ...] = (
        "suggested",
        "approved",
        "drafted",
        "withdrawn",
    )
    draft_statuses: tuple[DraftStatus, ...] = ("draft", "approved", "withdrawn")

    def __init__(self, jobs_root: Path) -> None:
        self.root = jobs_root.expanduser().resolve() / "editorial-board"
        self.topics_root = self.root / "topics"
        self.scripts_root = self.root / "scripts"
        self.batches_root = self.root / "batches"
        for status in self.topic_statuses:
            (self.topics_root / status).mkdir(parents=True, exist_ok=True)
        for status in self.draft_statuses:
            (self.scripts_root / status).mkdir(parents=True, exist_ok=True)
        self.batches_root.mkdir(parents=True, exist_ok=True)

    def _topic_path(self, topic_id: str, status: TopicStatus) -> Path:
        if not TOPIC_ID_PATTERN.fullmatch(topic_id):
            raise ValueError("ID de tema editorial inválido.")
        return self.topics_root / status / f"{topic_id}.json"

    def _script_path(self, script_id: str, status: DraftStatus) -> Path:
        if not DRAFT_ID_PATTERN.fullmatch(script_id):
            raise ValueError("ID de roteiro editorial inválido.")
        return self.scripts_root / status / f"{script_id}.json"

    def _batch_path(self, batch_id: str) -> Path:
        if not BATCH_ID_PATTERN.fullmatch(batch_id):
            raise ValueError("ID de lote editorial inválido.")
        return self.batches_root / f"{batch_id}.json"

    def _save_topic(self, topic: EditorialTopic, *, old_path: Path | None = None) -> Path:
        path = self._topic_path(topic.topic_id, topic.status)
        write_json(path, topic)
        if old_path is not None and old_path != path:
            old_path.unlink(missing_ok=True)
        with suppress(OSError):
            path.chmod(0o600)
        return path

    def _save_script(
        self,
        script: EditorialScriptDraft,
        *,
        old_path: Path | None = None,
    ) -> Path:
        path = self._script_path(script.script_id, script.status)
        write_json(path, script)
        if old_path is not None and old_path != path:
            old_path.unlink(missing_ok=True)
        with suppress(OSError):
            path.chmod(0o600)
        return path

    def _save_batch(self, batch: EditorialBatch) -> Path:
        path = self._batch_path(batch.batch_id)
        write_json(path, batch)
        return path

    def find_topic(self, topic_id: str) -> tuple[EditorialTopic, Path]:
        for status in self.topic_statuses:
            path = self._topic_path(topic_id, status)
            if path.is_file():
                return EditorialTopic.model_validate_json(path.read_text(encoding="utf-8")), path
        raise FileNotFoundError(f"Tema editorial não encontrado: {topic_id}")

    def find_script(self, script_id: str) -> tuple[EditorialScriptDraft, Path]:
        for status in self.draft_statuses:
            path = self._script_path(script_id, status)
            if path.is_file():
                return (
                    EditorialScriptDraft.model_validate_json(path.read_text(encoding="utf-8")),
                    path,
                )
        raise FileNotFoundError(f"Roteiro editorial não encontrado: {script_id}")

    def find_batch(self, batch_id: str) -> EditorialBatch:
        path = self._batch_path(batch_id)
        if not path.is_file():
            raise FileNotFoundError(f"Lote editorial não encontrado: {batch_id}")
        return EditorialBatch.model_validate_json(path.read_text(encoding="utf-8"))

    def suggest_topic(
        self,
        *,
        title: str,
        central_question: str = "",
        hook_direction: str = "",
        catalog_candidate_id: str | None = None,
        source_passages: list[str] | None = None,
        required_answer_points: list[str] | None = None,
        uncertainty_boundaries: list[str] | None = None,
        target_duration_seconds: int = 50,
    ) -> EditorialTopic:
        now = utc_now()
        shell = EditorialTopic.model_construct(
            topic_id=_slug_id("topic", title),
            title=title.strip(),
            central_question=central_question.strip(),
            hook_direction=hook_direction.strip(),
            catalog_candidate_id=(catalog_candidate_id.strip() if catalog_candidate_id else None),
            source_passages=[item.strip() for item in source_passages or [] if item.strip()],
            required_answer_points=[
                item.strip() for item in required_answer_points or [] if item.strip()
            ],
            uncertainty_boundaries=[
                item.strip() for item in uncertainty_boundaries or [] if item.strip()
            ],
            target_duration_seconds=target_duration_seconds,
            content_hash="",
            created_at=now,
            updated_at=now,
        )
        topic = EditorialTopic.model_validate(
            shell.model_copy(update={"content_hash": sha256_value(_topic_payload(shell))})
        )
        self._save_topic(topic)
        return topic

    def list_topics(self, *, status: TopicStatus | None = None) -> list[EditorialTopic]:
        statuses = (status,) if status else self.topic_statuses
        items = [
            EditorialTopic.model_validate_json(path.read_text(encoding="utf-8"))
            for current in statuses
            for path in (self.topics_root / current).glob("topic-*.json")
        ]
        return sorted(items, key=lambda item: item.created_at)

    def approve_topics(
        self,
        topic_ids: list[str],
        *,
        approved_by: str,
    ) -> list[EditorialTopic]:
        approved: list[EditorialTopic] = []
        for topic_id in topic_ids:
            topic, old_path = self.find_topic(topic_id)
            if topic.status == "approved":
                approved.append(topic)
                continue
            if topic.status != "suggested":
                raise RuntimeError(f"O tema {topic_id} não está aguardando aprovação.")
            updated = topic.model_copy(
                update={
                    "status": "approved",
                    "approved_at": datetime.now(UTC).isoformat(),
                    "approved_by": approved_by.strip(),
                    "updated_at": utc_now(),
                }
            )
            self._save_topic(updated, old_path=old_path)
            approved.append(updated)
        return approved

    def prepare_batch(
        self,
        *,
        topic_ids: list[str] | None = None,
        count: int = 3,
    ) -> EditorialBatch:
        candidates = (
            [self.find_topic(topic_id)[0] for topic_id in topic_ids]
            if topic_ids
            else self.list_topics(status="approved")[:count]
        )
        if not candidates:
            raise RuntimeError("Não há temas aprovados aguardando roteiro.")
        if any(topic.status != "approved" for topic in candidates):
            raise RuntimeError("Todos os temas do lote precisam estar aprovados.")
        now = datetime.now(UTC)
        batch_id = f"batch-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        queue_path = self.batches_root / f"{batch_id}-queue.json"
        queue = {
            "schema_version": "1.0",
            "role": "shorts_batch_editor",
            "batch_id": batch_id,
            "interaction_contract": {
                "project_creation": False,
                "media_production": False,
                "seo_specialist": False,
                "visual_specialist": False,
                "maximum_script_specialists": 1,
                "maximum_biblical_reviewers": 0,
                "post_user_approval_model_calls": False,
                "scope": "Somente os roteiros deste lote.",
            },
            "quality_contract": {
                "quality_reference": QUALITY_REFERENCE,
                "narrator_identity": "jesus_first_person_v1",
                "hook_contract": "high_stakes_opening_v1",
                "hook_must_open_narration_exactly": True,
                "hook_must_create_immediate_tension": True,
                "honest_curiosity_and_full_payoff": True,
                "human_authorial_voice": "human_authorial_voice_v1",
                "duration_policy": "approved_script_locked_v1",
                "target_words": "sem mínimo ou máximo; preserve integralmente o roteiro aprovado",
                "biblical_validation": "inline_source_guard_v1",
                "biblical_status_owner": "deterministic_preflight_no_reviewer_v1",
                "literal_quote_policy": (
                    "Copie a citação literalmente da fonte verificada; nunca reconstrua o "
                    "versículo nem coloque fala e ação narrativa dentro da mesma fala."
                ),
                "bounded_interpretation_policy": (
                    "Aplique required_answer_points e uncertainty_boundaries durante a escrita. "
                    "Toda inferência delimitada usa formulação natural como 'pode ser "
                    "compreendido' ou 'você pode entender assim'."
                ),
                "approval_transition": "hash_only_no_model_v1",
                "cta_max_words": 12,
                "seo_in_same_output": True,
                "description_hashtags": "3 específicas + #biblia + #jesus",
                "internal_youtube_tags": "10-12 sem #",
            },
            "topics": [_topic_summary(topic) for topic in candidates],
            "required_output_path": str((self.batches_root / f"{batch_id}-package.json").resolve()),
            "response_policy": (
                "Não explique o processo. Grave o pacote e responda em uma frase curta."
            ),
        }
        write_json(queue_path, queue)
        batch = EditorialBatch(
            batch_id=batch_id,
            topic_ids=[topic.topic_id for topic in candidates],
            queue_path=str(queue_path.resolve()),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self._save_batch(batch)
        return batch

    def import_batch(self, package_path: Path) -> EditorialBatch:
        payload = read_json(package_path)
        batch_id = str(payload.get("batch_id") or "")
        batch = self.find_batch(batch_id)
        if batch.status != "prepared":
            raise RuntimeError("Este lote já foi importado.")
        raw_scripts = payload.get("scripts")
        if not isinstance(raw_scripts, list) or len(raw_scripts) != len(batch.topic_ids):
            raise ValueError("O pacote precisa conter exatamente um roteiro por tema.")
        now = utc_now()
        scripts: list[EditorialScriptDraft] = []
        for raw in raw_scripts:
            if not isinstance(raw, dict):
                raise ValueError("Cada roteiro do pacote precisa ser um objeto.")
            topic_id = str(raw.get("topic_id") or "")
            if topic_id not in batch.topic_ids:
                raise ValueError("O pacote contém um tema fora do lote.")
            topic = self.find_topic(topic_id)[0]
            script_id = _slug_id("script", topic.title)
            shell = EditorialScriptDraft.model_construct(
                script_id=script_id,
                topic_id=topic_id,
                batch_id=batch_id,
                hook=str(raw.get("hook") or "").strip(),
                narration=str(raw.get("narration") or "").strip(),
                cta=str(raw.get("cta") or "").strip(),
                title=str(raw.get("title") or "").strip(),
                description=str(raw.get("description") or "").strip(),
                specific_hashtags=list(raw.get("specific_hashtags") or []),
                internal_tags=list(raw.get("internal_tags") or []),
                source_passages=list(raw.get("source_passages") or []),
                biblical_review_status=str(raw.get("biblical_review_status") or ""),
                biblical_review_summary=str(raw.get("biblical_review_summary") or "").strip(),
                hook_strength_score=float(raw.get("hook_strength_score") or 0),
                clarity_score=float(raw.get("clarity_score") or 0),
                payoff_score=float(raw.get("payoff_score") or 0),
                content_hash="",
                created_at=now,
                updated_at=now,
            )
            script = EditorialScriptDraft.model_validate(
                shell.model_copy(update={"content_hash": sha256_value(_draft_payload(shell))})
            )
            _validate_topic_alignment(topic, script)
            self._save_script(script)
            scripts.append(script)
        aggregate_hash = sha256_value(
            [{"script_id": item.script_id, "content_hash": item.content_hash} for item in scripts]
        )
        updated = batch.model_copy(
            update={
                "status": "imported",
                "script_ids": [item.script_id for item in scripts],
                "aggregate_hash": aggregate_hash,
                "updated_at": now,
            }
        )
        self._save_batch(updated)
        return updated

    def list_scripts(self, *, status: DraftStatus | None = None) -> list[EditorialScriptDraft]:
        statuses = (status,) if status else self.draft_statuses
        items = [
            EditorialScriptDraft.model_validate_json(path.read_text(encoding="utf-8"))
            for current in statuses
            for path in (self.scripts_root / current).glob("script-*.json")
        ]
        return sorted(items, key=lambda item: item.created_at)

    def review_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.find_batch(batch_id)
        if batch.status not in {"imported", "approved"}:
            raise RuntimeError("O lote ainda não possui roteiros importados.")
        scripts = [self.find_script(script_id)[0] for script_id in batch.script_ids]
        return {
            "batch_id": batch.batch_id,
            "status": batch.status,
            "aggregate_hash": batch.aggregate_hash,
            "scripts": [_draft_summary(script) for script in scripts],
            "approval_command": (
                "approved-scripts draft-batch-approve "
                f"{batch.batch_id} --confirm-hash {batch.aggregate_hash} --approved-by Pedro"
            ),
        }

    def approve_batch(
        self,
        batch_id: str,
        *,
        confirm_hash: str,
        approved_by: str,
    ) -> EditorialBatch:
        batch = self.find_batch(batch_id)
        if batch.status == "approved":
            if batch.production_script_ids:
                return batch
            scripts = [self.find_script(script_id)[0] for script_id in batch.script_ids]
            production_ids = self._publish_approved_snapshots(
                scripts,
                approved_by=batch.approved_by or approved_by,
                approved_at=batch.approved_at or utc_now(),
            )
            recovered = batch.model_copy(
                update={
                    "production_script_ids": production_ids,
                    "updated_at": utc_now(),
                }
            )
            self._save_batch(recovered)
            return recovered
        if batch.status != "imported" or batch.aggregate_hash != confirm_hash:
            raise ValueError("O hash de confirmação não corresponde ao lote exibido.")
        scripts = [self.find_script(script_id) for script_id in batch.script_ids]
        for script, _ in scripts:
            if script.biblical_review_status not in {"not_required", "passed"}:
                raise RuntimeError(
                    f"O roteiro {script.script_id} não passou no preflight de fontes "
                    "incorporado à escrita."
                )
            if (
                min(
                    script.hook_strength_score,
                    script.clarity_score,
                    script.payoff_score,
                )
                < 8
            ):
                raise RuntimeError(
                    f"O roteiro {script.script_id} não atingiu nota 8 em hook, clareza e payoff."
                )
        now = datetime.now(UTC).isoformat()
        prepared_snapshots = self._build_approved_snapshots(
            [script for script, _ in scripts],
            approved_by=approved_by,
            approved_at=now,
        )
        for script, old_path in scripts:
            updated_script = script.model_copy(
                update={
                    "status": "approved",
                    "approved_at": now,
                    "approved_by": approved_by.strip(),
                    "updated_at": utc_now(),
                }
            )
            self._save_script(updated_script, old_path=old_path)
            topic, topic_path = self.find_topic(script.topic_id)
            updated_topic = topic.model_copy(
                update={
                    "status": "drafted",
                    "drafted_batch_id": batch_id,
                    "updated_at": utc_now(),
                }
            )
            self._save_topic(updated_topic, old_path=topic_path)
        production_ids = self._persist_approved_snapshots(prepared_snapshots)
        updated_batch = batch.model_copy(
            update={
                "status": "approved",
                "approved_at": now,
                "approved_by": approved_by.strip(),
                "production_script_ids": production_ids,
                "updated_at": utc_now(),
            }
        )
        self._save_batch(updated_batch)
        return updated_batch

    def _publish_approved_snapshots(
        self,
        scripts: list[EditorialScriptDraft],
        *,
        approved_by: str,
        approved_at: str,
    ) -> list[str]:
        snapshots = self._build_approved_snapshots(
            scripts,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        return self._persist_approved_snapshots(snapshots)

    def _build_approved_snapshots(
        self,
        scripts: list[EditorialScriptDraft],
        *,
        approved_by: str,
        approved_at: str,
    ) -> list[Any]:
        return [
            _approved_snapshot_from_editorial(
                self.find_topic(script.topic_id)[0],
                script,
                approved_by=approved_by.strip(),
                approved_at=approved_at,
            )
            for script in scripts
        ]

    def _persist_approved_snapshots(self, snapshots: list[Any]) -> list[str]:
        from pipeline_videos.approved_scripts import ApprovedScriptStore

        production_store = ApprovedScriptStore(self.root.parent)
        production_ids: list[str] = []
        for snapshot in snapshots:
            try:
                existing, _ = production_store.find(snapshot.approved_script_id)
            except FileNotFoundError:
                production_store.save(snapshot)
                existing = snapshot
            if existing.content_hash != snapshot.content_hash:
                raise RuntimeError(
                    f"O pacote de produção {snapshot.approved_script_id} já existe com outro hash."
                )
            production_ids.append(existing.approved_script_id)
        return production_ids

    def workflow_status(self) -> dict[str, Any]:
        topic_counts = {
            status: len(self.list_topics(status=status)) for status in self.topic_statuses
        }
        script_counts = {
            status: len(self.list_scripts(status=status)) for status in self.draft_statuses
        }
        pending_batches = [
            EditorialBatch.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.batches_root.glob("batch-????????-??????-????????.json")
        ]
        if topic_counts["suggested"]:
            next_step = "theme_approval"
            question = "Você quer revisar ou aprovar os temas sugeridos?"
        elif topic_counts["approved"] and not script_counts["draft"]:
            next_step = "script_generation"
            question = "Você quer gerar roteiros para quais temas aprovados?"
        elif script_counts["draft"]:
            next_step = "script_approval"
            question = "Você quer revisar, alterar ou aprovar os roteiros do lote?"
        elif script_counts["approved"]:
            next_step = "production_release"
            question = "Você quer apenas listar os aprovados ou iniciar a produção?"
        else:
            next_step = "theme_suggestion"
            question = "Você quer sugerir temas ou receber sugestões de temas?"
        return {
            "topic_counts": topic_counts,
            "script_counts": script_counts,
            "batches": [
                {
                    "batch_id": batch.batch_id,
                    "status": batch.status,
                    "topic_count": len(batch.topic_ids),
                }
                for batch in sorted(pending_batches, key=lambda item: item.created_at)
            ],
            "next_step": next_step,
            "required_question": question,
            "operational_rule": (
                "Faça somente a etapa confirmada. Não crie projeto, subagente ou mídia."
            ),
        }


def editorial_topic_summary(topic: EditorialTopic) -> dict[str, Any]:
    return _topic_summary(topic)


def editorial_script_summary(
    draft: EditorialScriptDraft,
    *,
    include_text: bool = True,
) -> dict[str, Any]:
    return _draft_summary(draft, include_text=include_text)
