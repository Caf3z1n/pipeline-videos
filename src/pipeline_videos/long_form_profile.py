from __future__ import annotations

import os
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import Field, ValidationError, model_validator

from pipeline_videos.models import (
    AudioManifest,
    CutoutAssetCue,
    CutoutFullscreenShot,
    LongFormProductionProfileConfig,
    NarrationVisualScene,
    PipelineModel,
    ScriptDocument,
    WordTiming,
)
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

PROFILE_ID = "biblical_cutout_explainer_7m_v1"
EDIT_PLAN_SCHEMA_VERSION = "1.0"
OFFICIAL_BACKGROUND_PATH = "visual/backgrounds/warm-textured-parchment.png"
OFFICIAL_BLOCK_TYPES = (
    "assets_with_full_text_v1",
    "center_statement_v1",
    "fullscreen_image_sequence_v1",
)
QUOTE_GLYPHS = frozenset({"“", "”", "„", "‟", '"'})
ABSTRACT_PASSIVE_PATTERNS = (
    re.compile(
        r"\ba\s+(?:ordem|consequ[eê]ncia|verdade|confian[cç]a|l[oó]gica|percep[cç][aã]o)"
        r"\s+(?:foi|era|ficou)\s+"
        r"(?:deslocad[ao]|contradit[ao]|reorganizad[ao]|reposicionad[ao]|ressignificad[ao])\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:a\s+)?pergunta\s+(?:trocou|substituiu)\s+"
        r"(?:a\s+)?(?:abund[aâ]ncia|verdade|confian[cç]a)\s+pela\s+"
        r"(?:proibi[cç][aã]o|d[uú]vida|suspeita)\b",
        re.IGNORECASE,
    ),
)
GENERIC_CONFUSING_PHRASES = (
    "isso muda tudo",
    "a verdade foi reorganizada",
    "a ordem foi deslocada",
    "a consequência foi contradita",
)
QUESTION_SEMANTIC_KEYS = frozenset({"question", "question_or_doubt", "doubt"})
FRUIT_SEMANTIC_KEYS = frozenset({"fruit", "fruit_or_choice", "eating_fruit"})


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def _canonical_words(value: str) -> list[str]:
    return re.findall(r"\w+", _normalized(value), flags=re.UNICODE)


def _quote_free(value: str) -> bool:
    return not any(glyph in value for glyph in QUOTE_GLYPHS)


def _strip_quote_glyphs(value: str) -> str:
    return "".join(character for character in value if character not in QUOTE_GLYPHS)


class NormalizedBox(PipelineModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def fit_inside_frame(self) -> NormalizedBox:
        if self.x + self.width > 1.0 + 1e-9 or self.y + self.height > 1.0 + 1e-9:
            raise ValueError("A zona precisa permanecer dentro do quadro normalizado.")
        return self

    @property
    def area(self) -> float:
        return self.width * self.height


class CutoutTextPage(PipelineModel):
    page_id: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    text: str = Field(min_length=1)
    first_word_index: int = Field(ge=0)
    last_word_index_exclusive: int = Field(gt=0)
    quote_open: bool = False
    quote_close: bool = False
    emphasis_word_indexes: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_page(self) -> CutoutTextPage:
        if self.end <= self.start:
            raise ValueError("A página de texto precisa ter duração positiva.")
        if self.last_word_index_exclusive <= self.first_word_index:
            raise ValueError("A página precisa cobrir ao menos uma palavra.")
        if not _quote_free(self.text):
            raise ValueError(
                "O texto da página não pode conter aspas; o renderer é o único dono das marcas."
            )
        return self


class CutoutAssetRequirement(PipelineModel):
    asset_id: str = Field(min_length=1)
    kind: Literal["cutout", "fullscreen", "thumbnail"]
    description: str = Field(min_length=1)
    generation_prompt: str = Field(min_length=1)
    semantic_tags: list[str] = Field(min_length=1)
    existing_path: str | None = None
    max_uses: int = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def validate_asset(self) -> CutoutAssetRequirement:
        normalized_tags = [_normalized(tag) for tag in self.semantic_tags if tag.strip()]
        if not normalized_tags or len(set(normalized_tags)) != len(normalized_tags):
            raise ValueError("As tags semânticas do asset precisam ser distintas e não vazias.")
        if self.existing_path:
            candidate = Path(self.existing_path.replace("\\", "/"))
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("existing_path precisa ser relativo e seguro.")
        return self


class CutoutAssetEvent(PipelineModel):
    event_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    cue_word_index: int = Field(ge=0)
    semantic_key: str = Field(min_length=1)
    semantic_basis: str = Field(min_length=1)
    matched_narration_phrase: str = Field(min_length=1)
    zone: NormalizedBox

    @model_validator(mode="after")
    def validate_event(self) -> CutoutAssetEvent:
        if self.end <= self.start:
            raise ValueError("O asset precisa ter duração positiva.")
        return self


class CutoutSfxEvent(PipelineModel):
    kind: Literal["whoosh", "click"]
    at: float = Field(ge=0.0)
    reason: Literal["opening", "biblical_citation", "highlighted_asset"]
    block_id: str = Field(min_length=1)


class CutoutEditBlock(PipelineModel):
    block_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    block_type: Literal[
        "assets_with_full_text_v1",
        "center_statement_v1",
        "fullscreen_image_sequence_v1",
    ]
    role: Literal[
        "opening",
        "orientation",
        "explanation",
        "biblical_reference",
        "biblical_quote",
        "reengagement",
        "payoff",
        "conclusion",
        "cta",
    ]
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    narration: str = Field(min_length=1)
    citation_id: str | None = None
    use_standard_background: bool
    layout_variant: (
        Literal[
            "text_left_visual_right",
            "visual_left_text_right",
            "centered",
            "fullscreen",
        ]
        | None
    ) = None
    reference_label: str | None = None
    text_zone: NormalizedBox | None = None
    text_pages: list[CutoutTextPage] = Field(default_factory=list)
    asset_events: list[CutoutAssetEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_block_shape(self) -> CutoutEditBlock:
        if self.end <= self.start:
            raise ValueError("O bloco precisa ter duração positiva.")
        if (
            self.role in {"biblical_reference", "biblical_quote"}
            and not (self.citation_id or "").strip()
        ):
            raise ValueError("Referência e citação bíblica precisam declarar citation_id.")
        if self.role == "biblical_reference" and self.block_type != "center_statement_v1":
            raise ValueError("A referência bíblica usa um bloco central sem asset.")
        if (
            self.role == "biblical_reference"
            and self.layout_variant is not None
            and not (self.reference_label or "").strip()
        ):
            raise ValueError("A referência bíblica precisa declarar o rótulo compacto central.")
        if self.role == "biblical_quote" and self.block_type == "fullscreen_image_sequence_v1":
            raise ValueError("A citação bíblica inteira precisa permanecer em texto na tela.")
        if self.block_type == "assets_with_full_text_v1":
            if not self.use_standard_background or self.text_zone is None:
                raise ValueError("Asset + texto exige background padrão e zona de texto reservada.")
            if not self.text_pages or not self.asset_events:
                raise ValueError("Asset + texto exige texto completo e ao menos um asset.")
            if self.layout_variant is not None and self.layout_variant not in {
                "text_left_visual_right",
                "visual_left_text_right",
            }:
                raise ValueError("Asset + texto exige um dos dois layouts laterais oficiais.")
        elif self.block_type == "center_statement_v1":
            if not self.use_standard_background or self.text_zone is None:
                raise ValueError("A frase central exige background padrão e zona de texto.")
            if self.asset_events:
                raise ValueError("A frase central não aceita assets.")
            if self.role == "biblical_reference" and self.reference_label:
                if self.text_pages:
                    raise ValueError(
                        "A referência compacta é estática e não duplica a locução "
                        "palavra por palavra."
                    )
            elif not self.text_pages:
                raise ValueError("A frase central precisa declarar ao menos uma página de texto.")
            if self.layout_variant is not None and self.layout_variant != "centered":
                raise ValueError("A frase central exige layout_variant=centered.")
        else:
            if self.use_standard_background or self.text_zone is not None or self.text_pages:
                raise ValueError(
                    "A sequência fullscreen não aceita background padrão, texto ou overlays."
                )
            if not self.asset_events:
                raise ValueError("A sequência fullscreen exige ao menos uma imagem.")
            if self.layout_variant is not None and self.layout_variant != "fullscreen":
                raise ValueError("A sequência fullscreen exige layout_variant=fullscreen.")
        return self


class CutoutThumbnailPlan(PipelineModel):
    source_asset_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=100)
    text_lines: list[str] = Field(min_length=2, max_length=3)
    emphasis_line: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def validate_thumbnail(self) -> CutoutThumbnailPlan:
        if self.emphasis_line >= len(self.text_lines):
            raise ValueError("emphasis_line precisa apontar para uma linha da thumbnail.")
        return self


class LongFormCutoutEditPlan(PipelineModel):
    schema_version: Literal["1.0"] = EDIT_PLAN_SCHEMA_VERSION
    profile_id: Literal["biblical_cutout_explainer_7m_v1"] = PROFILE_ID
    generated_at: str
    script_hash: str = Field(min_length=64, max_length=64)
    audio_hash: str = Field(min_length=64, max_length=64)
    duration_seconds: float = Field(gt=0.0)
    background_path: str = Field(min_length=1)
    blocks: list[CutoutEditBlock] = Field(min_length=1)
    assets: list[CutoutAssetRequirement] = Field(default_factory=list)
    sfx_events: list[CutoutSfxEvent] = Field(default_factory=list)
    thumbnail: CutoutThumbnailPlan | None = None

    @model_validator(mode="after")
    def validate_unique_ids(self) -> LongFormCutoutEditPlan:
        for label, values in (
            ("block_id", [block.block_id for block in self.blocks]),
            ("asset_id", [asset.asset_id for asset in self.assets]),
            (
                "event_id",
                [event.event_id for block in self.blocks for event in block.asset_events],
            ),
            (
                "page_id",
                [page.page_id for block in self.blocks for page in block.text_pages],
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} precisa ser único no plano de edição.")
        return self


class EditPlanDiagnostic(PipelineModel):
    code: str
    message: str
    location: str
    severity: Literal["blocking", "warning"] = "blocking"


class EditPlanPreflightReport(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: str
    profile_id: str
    valid: bool
    diagnostics: list[EditPlanDiagnostic]
    metrics: dict[str, int | float | str | bool | dict[str, int]]


def official_profile_config() -> LongFormProductionProfileConfig:
    return LongFormProductionProfileConfig(
        enabled=True,
        profile_id=PROFILE_ID,
        target_duration_seconds=420,
        planning_standard="deterministic_script_blueprint_compiler_v2",
        renderer="remotion_layered_cutout_v1",
        block_contract="biblical_cutout_magnetic_blocks_v4",
        validation_mode="compiled_invariants_once_v2",
        script_language_standard="conversational_concrete_ptbr_v1",
        quote_rendering_standard="renderer_owned_single_boundary_v1",
        collision_policy="reserved_non_overlapping_zones_v1",
        asset_selection_policy="semantic_blueprint_dedup_v2",
        allowed_block_types=list(OFFICIAL_BLOCK_TYPES),
        first_30_minimum_blocks=6,
        first_30_minimum_visual_events=9,
        maximum_visual_idle_seconds=8.0,
        planner_model_passes=0,
        aggregate_model_repairs=0,
        captions_enabled=False,
    )


def _boxes_overlap(a: NormalizedBox, b: NormalizedBox, gap: float) -> bool:
    return not (
        a.x + a.width + gap <= b.x
        or b.x + b.width + gap <= a.x
        or a.y + a.height + gap <= b.y
        or b.y + b.height + gap <= a.y
    )


def _time_ranges_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _script_language_diagnostics(document: ScriptDocument) -> list[EditPlanDiagnostic]:
    diagnostics: list[EditPlanDiagnostic] = []
    for section in document.sections:
        narration = section.narration
        normalized = _normalized(narration)
        for phrase in GENERIC_CONFUSING_PHRASES:
            if _normalized(phrase) in normalized:
                diagnostics.append(
                    EditPlanDiagnostic(
                        code="confusing_generic_phrase",
                        location=f"script.sections.{section.section_id}",
                        message=(
                            f"A frase {phrase!r} é genérica ou abstrata. Declare quem fez o quê "
                            "e qual foi o efeito concreto, em português conversacional."
                        ),
                    )
                )
        for pattern in ABSTRACT_PASSIVE_PATTERNS:
            match = pattern.search(narration)
            if match:
                diagnostics.append(
                    EditPlanDiagnostic(
                        code="abstract_passive_phrase",
                        location=f"script.sections.{section.section_id}",
                        message=(
                            f"A construção {match.group(0)!r} é abstrata e pouco falada. "
                            "Use sujeito, ação e consequência concretos."
                        ),
                    )
                )
    return diagnostics


def validate_conversational_script(document: ScriptDocument) -> None:
    diagnostics = _script_language_diagnostics(document)
    if diagnostics:
        details = "\n".join(f"- {item.message}" for item in diagnostics)
        raise ValueError("O roteiro não cumpre conversational_concrete_ptbr_v1:\n" + details)


def validate_cutout_script_blueprint(document: ScriptDocument) -> None:
    """Check the compact construction contract before spending voice or image credits."""

    validate_conversational_script(document)
    strategy = document.long_form_strategy
    if strategy is None or strategy.youtube_package is None:
        raise ValueError(
            "O roteiro cutout precisa decidir o blueprint visual e o pacote do YouTube."
        )
    if any(scene.presentation == "auto" for scene in strategy.visual_scenes):
        raise ValueError("O blueprint cutout precisa decidir presentation em todos os blocos.")
    expected_section_order = [section.section_id for section in document.sections]
    actual_section_order = list(dict.fromkeys(scene.section_id for scene in strategy.visual_scenes))
    if actual_section_order != expected_section_order:
        raise ValueError("Os blocos visuais cutout precisam preservar a ordem completa das seções.")
    for section in document.sections:
        visual_narration = " ".join(
            scene.narration.strip()
            for scene in strategy.visual_scenes
            if scene.section_id == section.section_id
        )
        if _normalized(visual_narration) != _normalized(section.narration):
            raise ValueError(
                "Os blocos visuais cutout precisam reconstruir literalmente a seção "
                f"{section.section_id!r}."
            )


def _add(
    diagnostics: list[EditPlanDiagnostic],
    code: str,
    message: str,
    location: str,
    *,
    severity: Literal["blocking", "warning"] = "blocking",
) -> None:
    diagnostics.append(
        EditPlanDiagnostic(
            code=code,
            message=message,
            location=location,
            severity=severity,
        )
    )


def _validate_text_and_quotes(
    plan: LongFormCutoutEditPlan,
    diagnostics: list[EditPlanDiagnostic],
) -> None:
    quote_groups: dict[str, list[tuple[CutoutEditBlock, CutoutTextPage]]] = defaultdict(list)
    for block in plan.blocks:
        if (
            block.block_type
            in {
                "assets_with_full_text_v1",
                "center_statement_v1",
            }
            and block.role != "biblical_reference"
        ):
            page_text = " ".join(page.text for page in block.text_pages)
            if _canonical_words(page_text) != _canonical_words(block.narration):
                _add(
                    diagnostics,
                    "incomplete_block_text",
                    "Todo bloco com texto precisa exibir a frase integral falada neste take.",
                    f"blocks.{block.block_id}.text_pages",
                )
        if block.role == "biblical_quote":
            if block.block_type == "fullscreen_image_sequence_v1" or not block.text_pages:
                _add(
                    diagnostics,
                    "biblical_quote_without_full_text",
                    "Uma citação bíblica precisa permanecer integralmente em texto na tela.",
                    f"blocks.{block.block_id}",
                )
            for page in block.text_pages:
                quote_groups[block.citation_id or ""].append((block, page))
    for citation_id, entries in quote_groups.items():
        entries.sort(key=lambda item: (item[1].start, item[1].page_id))
        openings = [index for index, (_, page) in enumerate(entries) if page.quote_open]
        closings = [index for index, (_, page) in enumerate(entries) if page.quote_close]
        if openings != [0] or closings != [len(entries) - 1]:
            _add(
                diagnostics,
                "quote_boundary_ownership",
                (
                    "A citação precisa ter uma única aspa inicial na primeira página e uma única "
                    "aspa final na última página."
                ),
                f"citations.{citation_id}",
            )
        last_page = entries[-1][1]
        if (
            last_page.quote_close
            and last_page.last_word_index_exclusive <= last_page.first_word_index
        ):
            _add(
                diagnostics,
                "quote_close_without_last_word",
                "A aspa final só pode ser revelada depois da última palavra da citação.",
                f"citations.{citation_id}",
            )


def _validate_assets(
    plan: LongFormCutoutEditPlan,
    profile: LongFormProductionProfileConfig,
    diagnostics: list[EditPlanDiagnostic],
) -> None:
    assets = {asset.asset_id: asset for asset in plan.assets}
    uses: dict[str, list[tuple[CutoutEditBlock, CutoutAssetEvent]]] = defaultdict(list)
    thumbnail_asset_id = plan.thumbnail.source_asset_id if plan.thumbnail else None
    definitions: dict[tuple[str, str], str] = {}
    for asset in plan.assets:
        signature = (asset.kind, _normalized(asset.generation_prompt))
        previous = definitions.get(signature)
        if previous is not None:
            _add(
                diagnostics,
                "duplicate_asset_definition",
                (
                    f"Os assets {previous} e {asset.asset_id} pedem a mesma imagem. "
                    "Use um único asset compartilhado no inventário."
                ),
                f"assets.{asset.asset_id}",
            )
        else:
            definitions[signature] = asset.asset_id
    for block in plan.blocks:
        for page_index, page in enumerate(block.text_pages):
            if page.start < block.start - 0.05 or page.end > block.end + 0.05:
                _add(
                    diagnostics,
                    "text_page_outside_block",
                    "A página de texto precisa permanecer dentro do take correspondente.",
                    f"blocks.{block.block_id}.text_pages.{page_index}",
                )
        for index, event in enumerate(block.asset_events):
            location = f"blocks.{block.block_id}.asset_events.{index}"
            asset = assets.get(event.asset_id)
            if asset is None:
                _add(
                    diagnostics,
                    "unknown_asset",
                    f"O evento referencia asset inexistente: {event.asset_id}.",
                    location,
                )
                continue
            uses[event.asset_id].append((block, event))
            if event.start < block.start - 0.05 or event.end > block.end + 0.05:
                _add(
                    diagnostics,
                    "asset_event_outside_block",
                    "A entrada e a saída do asset precisam permanecer dentro do take.",
                    location,
                )
            if event.end - event.start < profile.minimum_asset_hold_seconds:
                _add(
                    diagnostics,
                    "asset_flash",
                    (
                        f"O asset permanece {event.end - event.start:.2f}s; o mínimo do perfil é "
                        f"{profile.minimum_asset_hold_seconds:.2f}s."
                    ),
                    location,
                )
            if asset.kind == "cutout" and event.zone.area < profile.minimum_asset_area_ratio:
                _add(
                    diagnostics,
                    "asset_too_small",
                    "O asset precisa ocupar melhor a área útil da tela.",
                    location,
                )
            if block.block_type == "fullscreen_image_sequence_v1":
                full_frame = NormalizedBox(x=0, y=0, width=1, height=1)
                if asset.kind != "fullscreen" or event.zone != full_frame:
                    _add(
                        diagnostics,
                        "fullscreen_asset_contract",
                        "A sequência fullscreen exige imagem integral cobrindo todo o quadro.",
                        location,
                    )
            elif asset.kind != "cutout":
                _add(
                    diagnostics,
                    "cutout_asset_contract",
                    "Bloco com background padrão aceita somente asset recortado.",
                    location,
                )
            normalized_phrase = _normalized(event.matched_narration_phrase)
            normalized_narration = _normalized(block.narration)
            if not normalized_phrase or normalized_phrase not in normalized_narration:
                _add(
                    diagnostics,
                    "asset_without_literal_cue",
                    (
                        "O asset precisa apontar para uma frase realmente presente na narração; "
                        "fallback genérico é proibido."
                    ),
                    location,
                )
            semantic_tags = {_normalized(tag) for tag in asset.semantic_tags}
            if _normalized(event.semantic_key) not in semantic_tags:
                _add(
                    diagnostics,
                    "asset_semantic_mismatch",
                    "A chave semântica usada não pertence às tags aprovadas do asset.",
                    location,
                )
            semantic_key = _normalized(event.semantic_key)
            if semantic_key in QUESTION_SEMANTIC_KEYS:
                question_context = "?" in block.narration or any(
                    token in normalized_narration
                    for token in ("pergunta", "duvida", "questionou", "interrogou", "por que")
                )
                if not question_context:
                    _add(
                        diagnostics,
                        "question_asset_out_of_context",
                        "Balão de dúvida só pode representar pergunta ou dúvida explícita.",
                        location,
                    )
            if semantic_key in FRUIT_SEMANTIC_KEYS and not any(
                token in normalized_narration
                for token in ("fruto", "fruta", "comeu", "comer", "tomou", "arvore")
            ):
                _add(
                    diagnostics,
                    "fruit_asset_out_of_context",
                    (
                        "Asset de fruto só pode aparecer quando a narração citar fruto, "
                        "comer ou árvore."
                    ),
                    location,
                )
            if block.text_zone is not None and _boxes_overlap(
                block.text_zone,
                event.zone,
                profile.minimum_text_asset_gap_ratio,
            ):
                _add(
                    diagnostics,
                    "text_asset_overlap",
                    (
                        "Texto e asset ocupam zonas que se cruzam. Paginar ou trocar o layout; "
                        "sobreposição é proibida."
                    ),
                    location,
                )
        for left_index, left in enumerate(block.asset_events):
            later_events = enumerate(
                block.asset_events[left_index + 1 :],
                left_index + 1,
            )
            for right_index, right in later_events:
                timing_overlaps = _time_ranges_overlap(
                    left.start,
                    left.end,
                    right.start,
                    right.end,
                )
                zones_overlap = _boxes_overlap(left.zone, right.zone, 0.0)
                if timing_overlaps and zones_overlap:
                    _add(
                        diagnostics,
                        "asset_asset_overlap",
                        "Dois assets ativos não podem ocupar a mesma região.",
                        f"blocks.{block.block_id}.asset_events.{left_index},{right_index}",
                    )
    for asset_id, entries in uses.items():
        entries.sort(key=lambda item: item[1].start)
        asset = assets[asset_id]
        allowed_uses = min(asset.max_uses, profile.maximum_asset_uses)
        if len(entries) > allowed_uses:
            _add(
                diagnostics,
                "asset_overused",
                f"O asset {asset_id} aparece {len(entries)} vezes; o limite é {allowed_uses}.",
                f"assets.{asset_id}",
            )
        opening_uses = sum(event.start < 30.0 for _, event in entries)
        if opening_uses > profile.first_30_maximum_asset_uses:
            _add(
                diagnostics,
                "opening_asset_repetition",
                f"O asset {asset_id} se repete nos primeiros 30 segundos.",
                f"assets.{asset_id}",
            )
        for (_, previous), (_, current) in zip(entries, entries[1:], strict=False):
            if current.start - previous.end < profile.minimum_asset_reuse_gap_seconds:
                _add(
                    diagnostics,
                    "asset_reuse_too_close",
                    (
                        f"O asset {asset_id} reaparece após "
                        f"{current.start - previous.end:.2f}s; o mínimo é "
                        f"{profile.minimum_asset_reuse_gap_seconds:.2f}s."
                    ),
                    f"assets.{asset_id}",
                )
    if thumbnail_asset_id is not None:
        thumbnail_asset = assets.get(thumbnail_asset_id)
        if thumbnail_asset is None:
            _add(
                diagnostics,
                "unknown_thumbnail_asset",
                "A thumbnail aponta para um asset inexistente.",
                "thumbnail.source_asset_id",
            )
        elif thumbnail_asset.kind != "thumbnail":
            _add(
                diagnostics,
                "thumbnail_asset_contract",
                "A base da thumbnail precisa usar kind=thumbnail.",
                "thumbnail.source_asset_id",
            )
    used_asset_ids = set(uses)
    if thumbnail_asset_id:
        used_asset_ids.add(thumbnail_asset_id)
    for asset_id in assets.keys() - used_asset_ids:
        _add(
            diagnostics,
            "unused_asset_definition",
            "O inventário não deve conter asset sem uso no plano visual.",
            f"assets.{asset_id}",
        )


def _validate_timing_and_opening(
    plan: LongFormCutoutEditPlan,
    profile: LongFormProductionProfileConfig,
    diagnostics: list[EditPlanDiagnostic],
    audio: AudioManifest | None,
) -> dict[str, int | float | str | bool | dict[str, int]]:
    ordered = sorted(plan.blocks, key=lambda block: (block.start, block.block_id))
    if ordered != plan.blocks:
        _add(
            diagnostics,
            "block_order",
            "Os blocos precisam estar ordenados pela entrada em tela.",
            "blocks",
        )
    if ordered and ordered[0].start > 0.2:
        _add(
            diagnostics,
            "late_opening",
            "O primeiro take precisa começar junto com a narração.",
            f"blocks.{ordered[0].block_id}",
        )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.start < previous.end - 0.05:
            _add(
                diagnostics,
                "block_overlap",
                "Blocos visuais principais não podem se sobrepor.",
                f"blocks.{previous.block_id},{current.block_id}",
            )
        if current.start - previous.end > 0.25:
            _add(
                diagnostics,
                "uncovered_timeline",
                "A timeline visual não pode ficar vazia entre blocos.",
                f"blocks.{previous.block_id},{current.block_id}",
            )
    if ordered and abs(ordered[-1].end - plan.duration_seconds) > 0.25:
        _add(
            diagnostics,
            "timeline_duration_mismatch",
            "O último bloco precisa terminar junto com a narração.",
            f"blocks.{ordered[-1].block_id}",
        )
    visual_events = sorted(
        {
            *(block.start for block in ordered),
            *(page.start for block in ordered for page in block.text_pages),
            *(event.start for block in ordered for event in block.asset_events),
        }
    )
    opening_blocks = [block for block in ordered if block.start < 30.0]
    opening_events = [event for event in visual_events if event < 30.0]
    opening_types = {block.block_type for block in opening_blocks}
    if len(opening_blocks) < profile.first_30_minimum_blocks:
        _add(
            diagnostics,
            "weak_first_30_blocks",
            (
                f"Os primeiros 30s têm {len(opening_blocks)} blocos; o mínimo é "
                f"{profile.first_30_minimum_blocks}."
            ),
            "opening",
        )
    if len(opening_events) < profile.first_30_minimum_visual_events:
        _add(
            diagnostics,
            "weak_first_30_events",
            (
                f"Os primeiros 30s têm {len(opening_events)} eventos visuais; o mínimo é "
                f"{profile.first_30_minimum_visual_events}."
            ),
            "opening",
        )
    if len(opening_types) < 2:
        _add(
            diagnostics,
            "weak_first_30_variety",
            "Os primeiros 30s precisam alternar pelo menos dois tipos de bloco.",
            "opening",
        )
    gaps = [right - left for left, right in zip(visual_events, visual_events[1:], strict=False)]
    maximum_idle = max(gaps, default=0.0)
    if maximum_idle > profile.maximum_visual_idle_seconds:
        _add(
            diagnostics,
            "visual_idle_too_long",
            (
                f"A timeline fica {maximum_idle:.2f}s sem mudança visual; o limite é "
                f"{profile.maximum_visual_idle_seconds:.2f}s."
            ),
            "timeline",
        )
    maximum_word_sync_offset = 0.0
    if audio is not None:
        if abs(plan.duration_seconds - audio.duration) > 0.05:
            _add(
                diagnostics,
                "audio_duration_mismatch",
                "A duração do plano precisa ser idêntica à duração real da narração.",
                "duration_seconds",
            )
        words_by_index = {word.index: word for word in audio.words}
        word_starts = {index: word.start for index, word in words_by_index.items()}
        for block in ordered:
            for page in block.text_pages:
                page_words = [
                    words_by_index[index]
                    for index in range(
                        page.first_word_index,
                        page.last_word_index_exclusive,
                    )
                    if index in words_by_index
                ]
                location = f"blocks.{block.block_id}.text_pages.{page.page_id}"
                expected_count = page.last_word_index_exclusive - page.first_word_index
                if len(page_words) != expected_count:
                    _add(
                        diagnostics,
                        "missing_audio_page_words",
                        "A página referencia palavras ausentes do alinhamento.",
                        location,
                    )
                elif _canonical_words(page.text) != _canonical_words(
                    " ".join(word.word for word in page_words)
                ):
                    _add(
                        diagnostics,
                        "page_audio_text_mismatch",
                        "O texto da página precisa copiar exatamente as palavras alinhadas.",
                        location,
                    )
                else:
                    start_offset = abs(page.start - page_words[0].start)
                    maximum_word_sync_offset = max(
                        maximum_word_sync_offset,
                        start_offset,
                    )
                    if start_offset > 2 / 30:
                        _add(
                            diagnostics,
                            "page_word_sync",
                            "A página precisa entrar junto com sua primeira palavra.",
                            location,
                        )
                    if page.end + 1 / 30 < page_words[-1].end:
                        _add(
                            diagnostics,
                            "page_ends_before_last_word",
                            "A página não pode sair antes da última palavra falada.",
                            location,
                        )
                    if page.quote_close and page.end - page_words[-1].end < 3 / 30:
                        _add(
                            diagnostics,
                            "quote_close_hold",
                            (
                                "A última página da citação precisa permanecer ao menos 0,1s "
                                "depois da última palavra para mostrar a aspa final uma única vez."
                            ),
                            location,
                        )
            for event in block.asset_events:
                if event.cue_word_index not in word_starts:
                    _add(
                        diagnostics,
                        "missing_audio_word_cue",
                        "O cue do asset não existe no alinhamento da narração.",
                        f"blocks.{block.block_id}.asset_events.{event.event_id}",
                    )
                    continue
                offset = abs(event.start - word_starts[event.cue_word_index])
                maximum_word_sync_offset = max(maximum_word_sync_offset, offset)
                if offset > 2 / 30:
                    _add(
                        diagnostics,
                        "asset_word_sync",
                        "O asset precisa entrar no mesmo instante da palavra que o justifica.",
                        f"blocks.{block.block_id}.asset_events.{event.event_id}",
                    )
    return {
        "block_count": len(ordered),
        "block_type_counts": dict(Counter(block.block_type for block in ordered)),
        "asset_event_count": sum(len(block.asset_events) for block in ordered),
        "unique_asset_count": len(plan.assets),
        "first_30_block_count": len(opening_blocks),
        "first_30_visual_event_count": len(opening_events),
        "maximum_visual_idle_seconds": round(maximum_idle, 3),
        "maximum_word_sync_offset_seconds": round(maximum_word_sync_offset, 3),
    }


def _validate_sfx(
    plan: LongFormCutoutEditPlan,
    diagnostics: list[EditPlanDiagnostic],
) -> None:
    blocks = {block.block_id: block for block in plan.blocks}
    opening_whoosh = [
        event for event in plan.sfx_events if event.kind == "whoosh" and event.reason == "opening"
    ]
    if len(opening_whoosh) != 1:
        _add(
            diagnostics,
            "opening_whoosh",
            "O primeiro take precisa ter exatamente um whoosh.",
            "sfx_events",
        )
    elif plan.blocks:
        first_block = min(plan.blocks, key=lambda item: item.start)
        opening = opening_whoosh[0]
        if opening.block_id != first_block.block_id or abs(opening.at - first_block.start) > 2 / 30:
            _add(
                diagnostics,
                "opening_whoosh_sync",
                "O whoosh de abertura precisa entrar junto com o primeiro take.",
                "sfx_events",
            )
    citation_first_blocks: dict[str, CutoutEditBlock] = {}
    for block in sorted(plan.blocks, key=lambda item: item.start):
        if block.role in {"biblical_reference", "biblical_quote"}:
            key = block.citation_id or block.block_id
            citation_first_blocks.setdefault(key, block)
    for citation_id, first_block in citation_first_blocks.items():
        matching = [
            event
            for event in plan.sfx_events
            if event.kind == "whoosh"
            and event.reason == "biblical_citation"
            and event.block_id == first_block.block_id
        ]
        if len(matching) != 1:
            _add(
                diagnostics,
                "citation_whoosh",
                "Cada citação bíblica precisa começar com exatamente um whoosh.",
                f"citations.{citation_id}",
            )
        elif abs(matching[0].at - first_block.start) > 2 / 30:
            _add(
                diagnostics,
                "citation_whoosh_sync",
                "O whoosh da citação precisa entrar junto com seu primeiro take.",
                f"citations.{citation_id}",
            )
    for index, event in enumerate(plan.sfx_events):
        block = blocks.get(event.block_id)
        if block is None:
            _add(
                diagnostics,
                "unknown_sfx_block",
                "O efeito sonoro referencia um bloco inexistente.",
                f"sfx_events.{index}",
            )
            continue
        if not block.start - 0.05 <= event.at <= block.end:
            _add(
                diagnostics,
                "sfx_outside_block",
                "O efeito sonoro precisa permanecer dentro do bloco que o justifica.",
                f"sfx_events.{index}",
            )
        if event.reason == "biblical_citation" and block.role not in {
            "biblical_reference",
            "biblical_quote",
        }:
            _add(
                diagnostics,
                "citation_whoosh_context",
                "Whoosh de citação só pode acompanhar referência ou texto bíblico.",
                f"sfx_events.{index}",
            )
        if event.kind == "click" and (
            event.reason != "highlighted_asset" or not block.asset_events
        ):
            _add(
                diagnostics,
                "click_context",
                "Click só pode acompanhar a entrada pontual de um asset em destaque.",
                f"sfx_events.{index}",
            )
        elif event.kind == "click":
            nearest_asset_start = min(
                abs(event.at - asset_event.start) for asset_event in block.asset_events
            )
            if nearest_asset_start > 2 / 30:
                _add(
                    diagnostics,
                    "click_asset_sync",
                    "O click precisa coincidir com a entrada do asset em destaque.",
                    f"sfx_events.{index}",
                )


def preflight_edit_plan_data(
    raw_plan: Any,
    *,
    profile: LongFormProductionProfileConfig | None = None,
    script: ScriptDocument | None = None,
    audio: AudioManifest | None = None,
) -> EditPlanPreflightReport:
    profile = profile or official_profile_config()
    diagnostics: list[EditPlanDiagnostic] = []
    try:
        plan = LongFormCutoutEditPlan.model_validate(raw_plan)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            _add(
                diagnostics,
                "schema_error",
                error["msg"],
                ".".join(str(part) for part in error["loc"]),
            )
        return EditPlanPreflightReport(
            generated_at=utc_now(),
            profile_id=PROFILE_ID,
            valid=False,
            diagnostics=diagnostics,
            metrics={"schema_valid": False},
        )

    if plan.background_path.replace("\\", "/") != OFFICIAL_BACKGROUND_PATH:
        _add(
            diagnostics,
            "background_contract",
            "O perfil usa exclusivamente o fundo de pergaminho texturizado aprovado.",
            "background_path",
        )
    if script is not None:
        diagnostics.extend(_script_language_diagnostics(script))
        expected_script_hash = sha256_value(script)
        if plan.script_hash != expected_script_hash:
            _add(
                diagnostics,
                "script_hash_mismatch",
                "O plano não corresponde ao roteiro atual.",
                "script_hash",
            )
    if audio is not None:
        expected_audio_hash = sha256_value(audio)
        if plan.audio_hash != expected_audio_hash:
            _add(
                diagnostics,
                "audio_hash_mismatch",
                "O plano não corresponde ao alinhamento de áudio atual.",
                "audio_hash",
            )
    _validate_text_and_quotes(plan, diagnostics)
    _validate_assets(plan, profile, diagnostics)
    metrics = _validate_timing_and_opening(plan, profile, diagnostics, audio)
    _validate_sfx(plan, diagnostics)
    metrics["schema_valid"] = True
    metrics["blocking_count"] = sum(item.severity == "blocking" for item in diagnostics)
    metrics["warning_count"] = sum(item.severity == "warning" for item in diagnostics)
    return EditPlanPreflightReport(
        generated_at=utc_now(),
        profile_id=plan.profile_id,
        valid=not any(item.severity == "blocking" for item in diagnostics),
        diagnostics=diagnostics,
        metrics=metrics,
    )


def preflight_project_edit_plan(layout: ProjectLayout) -> EditPlanPreflightReport:
    manifest = layout.load_manifest()
    profile = manifest.config.long_form_profile
    if not profile.enabled or profile.profile_id != PROFILE_ID:
        raise ValueError(f"O projeto não usa o perfil {PROFILE_ID}.")
    raw_plan = read_json(layout.long_form_edit_plan)
    script = read_model(layout.script, ScriptDocument) if layout.script.is_file() else None
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    if (
        profile.validation_mode == "compiled_invariants_once_v2"
        and script is not None
        and audio is not None
    ):
        plan = LongFormCutoutEditPlan.model_validate(raw_plan)
        report = _compiled_invariant_report(plan, script=script, audio=audio)
        write_json(layout.long_form_edit_preflight, report)
        return report
    report = preflight_edit_plan_data(
        raw_plan,
        profile=profile,
        script=script,
        audio=audio,
    )
    write_json(layout.long_form_edit_preflight, report)
    return report


REFERENCE_SPOKEN_PATTERN = re.compile(
    r"(?P<spoken>"
    r"(?:Em\s+)?"
    r"(?P<book>(?:[1-3]\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ]+"
    r"(?:\s+(?:de|dos|das)\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ]+)?)"
    r",?\s+cap[ií]tulo\s+(?P<chapter>\d+)"
    r",?\s+vers[ií]culos?\s+(?P<verse_start>\d+)"
    r"(?:\s+(?:a|-)\s+(?P<verse_end>\d+))?"
    r"(?:,?\s+(?:lemos|diz|afirma)\s*:?)?"
    r")",
    re.IGNORECASE,
)
QUOTE_PATTERN = re.compile(r"[“\"](?P<quote>[^”\"]+)[”\"]")
PAGE_STOPWORDS = frozenset(
    {
        "a",
        "ao",
        "aos",
        "as",
        "com",
        "como",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "ela",
        "ele",
        "em",
        "essa",
        "esse",
        "isso",
        "mas",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "ou",
        "para",
        "pela",
        "pelo",
        "por",
        "que",
        "se",
        "sua",
        "seu",
        "um",
        "uma",
    }
)


def _reference_label(match: re.Match[str]) -> str:
    verse_end = match.group("verse_end")
    verse = match.group("verse_start")
    if verse_end:
        verse = f"{verse}-{verse_end}"
    book = " ".join(part.capitalize() for part in match.group("book").split())
    return f"{book} {match.group('chapter')}:{verse}"


def _citation_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _normalized(label)).strip("-")
    return slug or "citation"


def _semantic_segments(text: str) -> list[dict[str, str | None]]:
    """Split narration around references and literal quotes without asking another model."""

    events: list[tuple[int, int, str, str, str | None]] = []
    references: list[tuple[int, int, str]] = []
    for match in REFERENCE_SPOKEN_PATTERN.finditer(text):
        label = _reference_label(match)
        events.append((match.start(), match.end(), "reference", match.group("spoken"), label))
        references.append((match.start(), match.end(), label))
    for match in QUOTE_PATTERN.finditer(text):
        preceding = [item for item in references if item[1] <= match.start()]
        following = [item for item in references if item[0] >= match.end()]
        label = preceding[-1][2] if preceding else following[0][2] if following else None
        events.append((match.start(), match.end(), "quote", match.group("quote"), label))
    events.sort(key=lambda item: (item[0], item[1]))
    segments: list[dict[str, str | None]] = []
    cursor = 0
    for start, end, kind, value, label in events:
        if start < cursor:
            continue
        before = text[cursor:start].strip()
        if before and _canonical_words(before):
            segments.append({"kind": "normal", "text": before, "reference_label": None})
        cleaned = value.strip()
        if cleaned and _canonical_words(cleaned):
            segments.append({"kind": kind, "text": cleaned, "reference_label": label})
        cursor = end
    after = text[cursor:].strip()
    if after and _canonical_words(after):
        segments.append({"kind": "normal", "text": after, "reference_label": None})
    return segments or [{"kind": "normal", "text": text.strip(), "reference_label": None}]


def _split_sentences(text: str) -> list[str]:
    pieces = [item.strip() for item in re.split(r"(?<=[.!?;])\s+", text.strip()) if item.strip()]
    return pieces or [text.strip()]


def _audio_token_stream(words: list[WordTiming]) -> list[tuple[str, int]]:
    stream: list[tuple[str, int]] = []
    for word in words:
        for token in _canonical_words(word.word):
            stream.append((token, word.index))
    return stream


def _match_word_span(
    words: list[WordTiming],
    text: str,
    *,
    cursor_word_index: int,
) -> tuple[int, int]:
    expected = _canonical_words(text)
    if not expected:
        raise ValueError("Um bloco compilado não pode ficar sem palavras.")
    stream = _audio_token_stream(words)
    first_candidate = next(
        (index for index, (_, word_index) in enumerate(stream) if word_index >= cursor_word_index),
        len(stream),
    )
    maximum_start = len(stream) - len(expected)
    for start in range(first_candidate, maximum_start + 1):
        if [token for token, _ in stream[start : start + len(expected)]] == expected:
            indexes = [word_index for _, word_index in stream[start : start + len(expected)]]
            return min(indexes), max(indexes) + 1
    excerpt = text[:120].replace("\n", " ")
    raise ValueError(
        f"O compilador não encontrou no alinhamento o trecho literal do roteiro: {excerpt!r}."
    )


def _word_slice(
    words_by_index: dict[int, WordTiming],
    first_word_index: int,
    last_word_index_exclusive: int,
) -> list[WordTiming]:
    return [
        words_by_index[index]
        for index in range(first_word_index, last_word_index_exclusive)
        if index in words_by_index
    ]


def _default_cue(
    narration: str,
    description: str,
    *,
    ordinal: int,
) -> CutoutAssetCue:
    narration_words = re.findall(r"\S+", narration)
    meaningful = [
        word
        for word in narration_words
        if _normalized(word) not in PAGE_STOPWORDS and len(_normalized(word)) >= 4
    ]
    anchor = meaningful[0] if meaningful else narration_words[0]
    semantic_key = re.sub(r"[^a-z0-9]+", "-", _normalized(anchor)).strip("-")
    return CutoutAssetCue(
        semantic_key=semantic_key or f"biblical-scene-{ordinal}",
        cue_phrase=anchor,
        description=(
            f"Cena educacional bíblica concreta para a fala {narration!r}. "
            f"Representação visual: {description}."
        ),
    )


def _cue_matches(cue_phrase: str, narration: str) -> bool:
    return bool(_normalized(cue_phrase)) and _normalized(cue_phrase) in _normalized(narration)


def _cue_word(
    cue_phrase: str,
    words: list[WordTiming],
    *,
    after_index: int = -1,
) -> WordTiming | None:
    cue_tokens = set(_canonical_words(cue_phrase))
    return next(
        (
            word
            for word in words
            if word.index > after_index and cue_tokens.intersection(_canonical_words(word.word))
        ),
        None,
    )


def _asset_slots(
    *,
    text_on_left: bool,
    count: int,
) -> tuple[NormalizedBox, list[NormalizedBox], str]:
    if text_on_left:
        text_zone = NormalizedBox(x=0.055, y=0.095, width=0.405, height=0.81)
        visual_x = 0.525
        variant = "text_left_visual_right"
    else:
        text_zone = NormalizedBox(x=0.54, y=0.095, width=0.405, height=0.81)
        visual_x = 0.055
        variant = "visual_left_text_right"
    if count <= 1:
        slots = [NormalizedBox(x=visual_x, y=0.075, width=0.42, height=0.85)]
    elif count == 2:
        slots = [
            NormalizedBox(x=visual_x, y=0.10, width=0.195, height=0.78),
            NormalizedBox(x=visual_x + 0.225, y=0.10, width=0.195, height=0.78),
        ]
    else:
        slots = [
            NormalizedBox(x=visual_x, y=0.095, width=0.195, height=0.38),
            NormalizedBox(x=visual_x + 0.225, y=0.095, width=0.195, height=0.38),
            NormalizedBox(x=visual_x + 0.105, y=0.525, width=0.21, height=0.38),
        ]
    return text_zone, slots[:count], variant


def _emphasis_indexes(
    words: list[WordTiming],
    requested_terms: list[str],
) -> list[int]:
    requested = {_normalized(term) for term in requested_terms if term.strip()}
    selected = [word.index for word in words if _normalized(word.word) in requested]
    candidates = sorted(
        (
            word
            for word in words
            if _normalized(word.word) not in PAGE_STOPWORDS
            and len(_normalized(word.word)) >= 6
            and word.index not in selected
        ),
        key=lambda item: (-len(_normalized(item.word)), item.index),
    )
    for word in candidates:
        if len(selected) >= 2:
            break
        selected.append(word.index)
    return sorted(selected[:2])


def _text_pages(
    words: list[WordTiming],
    *,
    block_id: str,
    block_end: float,
    quote: bool,
    requested_emphasis: list[str],
) -> list[CutoutTextPage]:
    if not words:
        return []
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for word in words:
        current.append(word)
        elapsed = current[-1].end - current[0].start
        punctuation_break = word.word.rstrip('”"').endswith((".", "?", "!", ";", ":"))
        if len(current) >= 11 or (len(current) >= 6 and elapsed >= 4.2 and punctuation_break):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    pages: list[CutoutTextPage] = []
    for index, group in enumerate(groups):
        next_start = groups[index + 1][0].start if index + 1 < len(groups) else block_end
        page_end = max(group[-1].end + 0.12, next_start)
        page_end = min(page_end, block_end)
        pages.append(
            CutoutTextPage(
                page_id=f"{block_id}-page-{index + 1:02d}",
                start=group[0].start,
                end=max(group[0].start + 1 / 30, page_end),
                text=" ".join(_strip_quote_glyphs(word.word) for word in group).strip(),
                first_word_index=group[0].index,
                last_word_index_exclusive=group[-1].index + 1,
                quote_open=quote and index == 0,
                quote_close=quote and index == len(groups) - 1,
                emphasis_word_indexes=_emphasis_indexes(group, requested_emphasis),
            )
        )
    return pages


def _block_role(
    document: ScriptDocument,
    section_id: str,
    *,
    kind: str,
    block_index: int,
    narration: str,
) -> str:
    if kind == "reference":
        return "biblical_reference"
    if kind == "quote":
        return "biblical_quote"
    if block_index == 0:
        return "opening"
    section = next(item for item in document.sections if item.section_id == section_id)
    strategy = document.long_form_strategy
    functions: set[str] = set()
    if strategy is not None:
        beat = next(
            (item for item in strategy.progression if item.section_id == section_id),
            None,
        )
        if beat is not None:
            functions = set(beat.functions)
        if strategy.engagement.spoken_text and _normalized(
            strategy.engagement.spoken_text
        ) in _normalized(narration):
            return "cta"
    if "reengagement" in functions:
        return "reengagement"
    if {"payoff", "resolution", "reveal"}.intersection(functions):
        return "payoff"
    if section.role == "closing":
        return "conclusion"
    if section.role == "opening":
        return "orientation"
    return "explanation"


def _expanded_script_blocks(document: ScriptDocument) -> list[dict[str, Any]]:
    strategy = document.long_form_strategy
    if strategy is None:
        raise ValueError("O compilador cutout exige long_form_strategy no roteiro.")
    raw_segments: list[dict[str, Any]] = []
    for visual_scene in strategy.visual_scenes:
        semantic_segments = _semantic_segments(visual_scene.narration)
        for segment in semantic_segments:
            text = str(segment["text"]).strip()
            kind = str(segment["kind"])
            if (
                kind == "normal"
                and visual_scene.presentation == "auto"
                and not visual_scene.asset_cues
                and not visual_scene.fullscreen_shots
            ):
                pieces = _split_sentences(text)
            else:
                pieces = [text]
            for piece in pieces:
                if piece:
                    raw_segments.append(
                        {
                            "scene": visual_scene,
                            "section_id": visual_scene.section_id,
                            "kind": kind,
                            "text": piece,
                            "reference_label": (
                                str(segment["reference_label"])
                                if segment["reference_label"]
                                else None
                            ),
                        }
                    )
    expanded: list[dict[str, Any]] = []
    previous_reference: str | None = None
    for index, segment in enumerate(raw_segments):
        reference_label = segment["reference_label"]
        if segment["kind"] == "reference" and reference_label:
            previous_reference = str(reference_label)
        elif segment["kind"] == "quote" and not reference_label:
            following_reference = next(
                (
                    str(candidate["reference_label"])
                    for candidate in raw_segments[index + 1 :]
                    if candidate["kind"] == "reference" and candidate["reference_label"]
                ),
                None,
            )
            reference_label = previous_reference or following_reference
        expanded.append({**segment, "reference_label": reference_label})
    return expanded


def _asset_signature(kind: str, description: str) -> str:
    return sha256_value({"kind": kind, "description": _normalized(description)})[:14]


def _compiled_invariant_report(
    plan: LongFormCutoutEditPlan,
    *,
    script: ScriptDocument,
    audio: AudioManifest,
) -> EditPlanPreflightReport:
    diagnostics: list[EditPlanDiagnostic] = []
    if plan.script_hash != sha256_value(script):
        _add(diagnostics, "script_hash_mismatch", "Hash de roteiro divergente.", "script_hash")
    if plan.audio_hash != sha256_value(audio):
        _add(diagnostics, "audio_hash_mismatch", "Hash de áudio divergente.", "audio_hash")
    if abs(plan.duration_seconds - audio.duration) > 0.05:
        _add(
            diagnostics,
            "audio_duration_mismatch",
            "A timeline compilada não termina junto com a narração.",
            "duration_seconds",
        )
    asset_ids = {asset.asset_id for asset in plan.assets}
    referenced = {event.asset_id for block in plan.blocks for event in block.asset_events}
    if plan.thumbnail:
        referenced.add(plan.thumbnail.source_asset_id)
    missing = referenced - asset_ids
    if missing:
        _add(
            diagnostics,
            "unknown_asset",
            "O plano compilado referencia assets ausentes: " + ", ".join(sorted(missing)),
            "assets",
        )
    return EditPlanPreflightReport(
        generated_at=utc_now(),
        profile_id=plan.profile_id,
        valid=not diagnostics,
        diagnostics=diagnostics,
        metrics={
            "compiler": "deterministic_script_blueprint_compiler_v2",
            "schema_valid": True,
            "block_count": len(plan.blocks),
            "block_type_counts": dict(Counter(block.block_type for block in plan.blocks)),
            "unique_asset_count": len(plan.assets),
            "model_repair_count": 0,
        },
    )


def compile_project_edit_plan(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> tuple[LongFormCutoutEditPlan, EditPlanPreflightReport, dict[str, Any]]:
    """Compile the timed edit plan locally from the script's compact visual blueprint."""

    manifest = layout.load_manifest()
    profile = manifest.config.long_form_profile
    if not profile.enabled or profile.profile_id != PROFILE_ID:
        raise ValueError(f"O projeto não usa o perfil {PROFILE_ID}.")
    if not layout.script.is_file() or not layout.audio.is_file():
        raise FileNotFoundError("Roteiro e alinhamento de áudio precisam existir antes do plano.")
    script = read_model(layout.script, ScriptDocument)
    audio = read_model(layout.audio, AudioManifest)
    if script.long_form_strategy and script.long_form_strategy.youtube_package:
        validate_cutout_script_blueprint(script)
    else:
        validate_conversational_script(script)
    input_hash = sha256_value(
        {
            "script": script,
            "audio": audio,
            "compiler": "deterministic_script_blueprint_compiler_v2",
        }
    )
    if layout.long_form_edit_plan.is_file() and not force:
        existing = read_model(layout.long_form_edit_plan, LongFormCutoutEditPlan)
        if existing.script_hash == sha256_value(script) and existing.audio_hash == sha256_value(
            audio
        ):
            report = _compiled_invariant_report(existing, script=script, audio=audio)
            inventory = build_project_asset_inventory(layout, report=report)
            write_json(layout.long_form_edit_preflight, report)
            return existing, report, inventory

    words_by_index = {word.index: word for word in audio.words}
    expanded = _expanded_script_blocks(script)
    planned: list[dict[str, Any]] = []
    cursor_word_index = min(words_by_index, default=0)
    for item in expanded:
        first, last = _match_word_span(
            audio.words,
            str(item["text"]),
            cursor_word_index=cursor_word_index,
        )
        cursor_word_index = last
        item["first_word_index"] = first
        item["last_word_index_exclusive"] = last
        planned.append(item)
    if not planned:
        raise ValueError("O roteiro não produziu nenhum bloco visual compilável.")

    assets_by_id: dict[str, CutoutAssetRequirement] = {}
    shareable_assets: dict[str, str] = {}
    blocks: list[CutoutEditBlock] = []
    sfx_events: list[CutoutSfxEvent] = []
    seen_citations: set[str] = set()
    last_click_at = -999.0

    for index, item in enumerate(planned):
        block_id = f"block-{index + 1:03d}"
        scene: NarrationVisualScene = item["scene"]
        first_index = int(item["first_word_index"])
        last_index = int(item["last_word_index_exclusive"])
        block_words = _word_slice(words_by_index, first_index, last_index)
        if not block_words:
            raise ValueError(f"{block_id} não encontrou palavras alinhadas.")
        start = 0.0 if index == 0 else block_words[0].start
        if index + 1 < len(planned):
            next_first = int(planned[index + 1]["first_word_index"])
            end = words_by_index[next_first].start
        else:
            end = audio.duration
        narration = str(item["text"]).strip()
        kind = str(item["kind"])
        reference_label = str(item["reference_label"]) if item["reference_label"] else None
        citation_id = _citation_id(reference_label) if reference_label else None
        role = _block_role(
            script,
            str(item["section_id"]),
            kind=kind,
            block_index=index,
            narration=narration,
        )
        if kind == "reference":
            block_type = "center_statement_v1"
        elif kind == "quote":
            block_type = "assets_with_full_text_v1"
        elif scene.presentation != "auto":
            block_type = scene.presentation
        elif index > 0 and index % 5 == 2 and end - start >= 6.0:
            block_type = "fullscreen_image_sequence_v1"
        else:
            block_type = "assets_with_full_text_v1"
        if index == 0 and block_type == "center_statement_v1":
            block_type = "assets_with_full_text_v1"

        if block_type == "center_statement_v1":
            text_zone = NormalizedBox(x=0.12, y=0.20, width=0.76, height=0.60)
            pages = (
                []
                if kind == "reference"
                else _text_pages(
                    block_words,
                    block_id=block_id,
                    block_end=end,
                    quote=False,
                    requested_emphasis=scene.emphasis_terms,
                )
            )
            block = CutoutEditBlock(
                block_id=block_id,
                section_id=str(item["section_id"]),
                block_type=block_type,
                role=role,
                start=start,
                end=end,
                narration=narration,
                citation_id=citation_id,
                use_standard_background=True,
                layout_variant="centered",
                reference_label=reference_label,
                text_zone=text_zone,
                text_pages=pages,
                asset_events=[],
            )
        elif block_type == "fullscreen_image_sequence_v1":
            shots = list(scene.fullscreen_shots)
            if len(shots) < 2:
                sentence_parts = _split_sentences(narration)
                while len(sentence_parts) < 2:
                    sentence_parts.append(f"Continuação da ação: {scene.scene_description}")
                shots = [
                    CutoutFullscreenShot(
                        cue_phrase=(
                            next(
                                (
                                    word
                                    for word in re.findall(r"\S+", narration)
                                    if _normalized(word) not in PAGE_STOPWORDS
                                ),
                                re.findall(r"\S+", narration)[0],
                            )
                        ),
                        description=(
                            f"Ação bíblica {shot_index + 1}: {part}. "
                            f"Direção: {scene.scene_description}."
                        ),
                    )
                    for shot_index, part in enumerate(sentence_parts[:2])
                ]
            shots = shots[:4]
            cue_words: list[WordTiming] = []
            last_cue_index = first_index - 1
            for shot in shots:
                cue_word = _cue_word(
                    shot.cue_phrase,
                    block_words,
                    after_index=last_cue_index,
                )
                if cue_word is None:
                    cue_word = block_words[0]
                cue_words.append(cue_word)
                last_cue_index = max(last_cue_index, cue_word.index)
            shot_duration = (end - start) / len(shots)
            event_starts = [start]
            minimum_hold = min(
                profile.minimum_asset_hold_seconds,
                shot_duration,
            )
            for shot_index in range(1, len(shots)):
                desired = cue_words[shot_index].start
                earliest = event_starts[-1] + minimum_hold
                latest = end - minimum_hold * (len(shots) - shot_index)
                if earliest <= desired <= latest:
                    event_starts.append(desired)
                else:
                    fallback = start + shot_duration * shot_index
                    event_starts.append(max(earliest, min(fallback, latest)))
            events: list[CutoutAssetEvent] = []
            for shot_index, shot in enumerate(shots):
                asset_id = f"fullscreen-{_asset_signature('fullscreen', shot.description)}"
                if asset_id not in assets_by_id:
                    assets_by_id[asset_id] = CutoutAssetRequirement(
                        asset_id=asset_id,
                        kind="fullscreen",
                        description=shot.description,
                        generation_prompt=shot.description,
                        semantic_tags=[
                            f"fullscreen-action-{index + 1}-{shot_index + 1}",
                            _normalized(str(item["section_id"])),
                        ],
                        max_uses=1,
                    )
                event_start = event_starts[shot_index]
                event_end = end if shot_index == len(shots) - 1 else event_starts[shot_index + 1]
                events.append(
                    CutoutAssetEvent(
                        event_id=f"{block_id}-shot-{shot_index + 1:02d}",
                        asset_id=asset_id,
                        start=event_start,
                        end=event_end,
                        cue_word_index=cue_words[shot_index].index,
                        semantic_key=f"fullscreen-action-{index + 1}-{shot_index + 1}",
                        semantic_basis=shot.description,
                        matched_narration_phrase=narration,
                        zone=NormalizedBox(x=0, y=0, width=1, height=1),
                    )
                )
            block = CutoutEditBlock(
                block_id=block_id,
                section_id=str(item["section_id"]),
                block_type=block_type,
                role=role,
                start=start,
                end=end,
                narration=narration,
                citation_id=citation_id,
                use_standard_background=False,
                layout_variant="fullscreen",
                text_zone=None,
                text_pages=[],
                asset_events=events,
            )
        else:
            cues = [cue for cue in scene.asset_cues if _cue_matches(cue.cue_phrase, narration)]
            if not cues:
                cues = [
                    _default_cue(
                        narration,
                        scene.scene_description,
                        ordinal=index + 1,
                    )
                ]
            cues = cues[:3]
            text_on_left = (
                sum(block.block_type == "assets_with_full_text_v1" for block in blocks) % 2 == 0
            )
            text_zone, slots, layout_variant = _asset_slots(
                text_on_left=text_on_left,
                count=len(cues),
            )
            events = []
            for cue_index, (cue, zone) in enumerate(zip(cues, slots, strict=True)):
                signature = _asset_signature("cutout", cue.description)
                if cue.shareable and signature in shareable_assets:
                    asset_id = shareable_assets[signature]
                else:
                    asset_id = f"cutout-{signature}-{index + 1:02d}-{cue_index + 1}"
                    if cue.shareable:
                        shareable_assets[signature] = asset_id
                    assets_by_id[asset_id] = CutoutAssetRequirement(
                        asset_id=asset_id,
                        kind="cutout",
                        description=cue.description,
                        generation_prompt=cue.description,
                        semantic_tags=list(
                            dict.fromkeys(
                                [
                                    cue.semantic_key,
                                    _normalized(str(item["section_id"])),
                                    "biblical-education",
                                ]
                            )
                        ),
                        max_uses=3 if cue.shareable else 1,
                    )
                cue_words = [
                    word
                    for word in block_words
                    if _normalized(cue.cue_phrase) in _normalized(word.word)
                    or _normalized(word.word) in _normalized(cue.cue_phrase)
                ]
                cue_word = cue_words[0] if cue_words else block_words[0]
                event_start = cue_word.start
                if end - event_start < profile.minimum_asset_hold_seconds:
                    event_start = max(start, end - profile.minimum_asset_hold_seconds)
                    cue_word = min(block_words, key=lambda word: abs(word.start - event_start))
                events.append(
                    CutoutAssetEvent(
                        event_id=f"{block_id}-asset-{cue_index + 1:02d}",
                        asset_id=asset_id,
                        start=event_start,
                        end=end,
                        cue_word_index=cue_word.index,
                        semantic_key=cue.semantic_key,
                        semantic_basis=cue.description,
                        matched_narration_phrase=cue.cue_phrase,
                        zone=zone,
                    )
                )
            block = CutoutEditBlock(
                block_id=block_id,
                section_id=str(item["section_id"]),
                block_type=block_type,
                role=role,
                start=start,
                end=end,
                narration=narration,
                citation_id=citation_id,
                use_standard_background=True,
                layout_variant=layout_variant,
                reference_label=None,
                text_zone=text_zone,
                text_pages=_text_pages(
                    block_words,
                    block_id=block_id,
                    block_end=end,
                    quote=kind == "quote",
                    requested_emphasis=scene.emphasis_terms,
                ),
                asset_events=events,
            )
            if start - last_click_at >= 24.0 and role not in {
                "opening",
                "biblical_quote",
                "biblical_reference",
            }:
                first_event = events[0]
                sfx_events.append(
                    CutoutSfxEvent(
                        kind="click",
                        at=first_event.start,
                        reason="highlighted_asset",
                        block_id=block_id,
                    )
                )
                last_click_at = first_event.start
        blocks.append(block)
        if index == 0:
            sfx_events.append(
                CutoutSfxEvent(
                    kind="whoosh",
                    at=0.0,
                    reason="opening",
                    block_id=block_id,
                )
            )
        if citation_id and citation_id not in seen_citations:
            sfx_events.append(
                CutoutSfxEvent(
                    kind="whoosh",
                    at=start,
                    reason="biblical_citation",
                    block_id=block_id,
                )
            )
            seen_citations.add(citation_id)

    package = script.long_form_strategy.youtube_package
    thumbnail = None
    if package is not None:
        thumbnail_asset_id = "thumbnail-" + _asset_signature(
            "thumbnail", package.thumbnail_visual_description
        )
        assets_by_id[thumbnail_asset_id] = CutoutAssetRequirement(
            asset_id=thumbnail_asset_id,
            kind="thumbnail",
            description=package.thumbnail_visual_description,
            generation_prompt=package.thumbnail_visual_description,
            semantic_tags=["youtube-thumbnail", "biblical-packaging"],
            max_uses=1,
        )
        thumbnail = CutoutThumbnailPlan(
            source_asset_id=thumbnail_asset_id,
            title=package.title,
            text_lines=package.thumbnail_text_lines,
            emphasis_line=package.thumbnail_emphasis_line,
        )

    deduplicated_sfx: list[CutoutSfxEvent] = []
    for event in sorted(sfx_events, key=lambda item: (item.at, item.kind)):
        if any(
            previous.kind == event.kind and abs(previous.at - event.at) < 0.35
            for previous in deduplicated_sfx
        ):
            continue
        deduplicated_sfx.append(event)
    plan = LongFormCutoutEditPlan(
        generated_at=utc_now(),
        script_hash=sha256_value(script),
        audio_hash=sha256_value(audio),
        duration_seconds=audio.duration,
        background_path=OFFICIAL_BACKGROUND_PATH,
        blocks=blocks,
        assets=list(assets_by_id.values()),
        sfx_events=deduplicated_sfx,
        thumbnail=thumbnail,
    )
    report = _compiled_invariant_report(plan, script=script, audio=audio)
    if not report.valid:
        raise ValueError("O compilador produziu invariantes estruturais inválidas.")
    write_json(layout.long_form_edit_plan, plan)
    write_json(layout.long_form_edit_preflight, report)
    inventory = build_asset_inventory(plan, report=report)
    write_json(layout.long_form_asset_inventory, inventory)
    write_json(
        layout.long_form_edit_plan_queue,
        {
            "schema_version": "2.0",
            "status": "completed",
            "input_hash": input_hash,
            "compiler": "deterministic_script_blueprint_compiler_v2",
            "model_calls": 0,
            "repairs": 0,
            "output": relative_posix(layout.long_form_edit_plan, layout.root),
        },
    )
    return plan, report, inventory


def prepare_edit_plan_queue(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    manifest = layout.load_manifest()
    profile = manifest.config.long_form_profile
    if not profile.enabled or profile.profile_id != PROFILE_ID:
        raise ValueError(f"O projeto não usa o perfil {PROFILE_ID}.")
    if profile.planning_standard == "deterministic_script_blueprint_compiler_v2":
        plan, report, inventory = compile_project_edit_plan(layout, force=force)
        return {
            "schema_version": "2.0",
            "status": "completed",
            "profile_id": PROFILE_ID,
            "compiler": profile.planning_standard,
            "model_calls": 0,
            "repairs": 0,
            "plan_hash": sha256_value(plan),
            "valid": report.valid,
            "generation_count": inventory["generation_count"],
            "output": relative_posix(layout.long_form_edit_plan, layout.root),
        }
    if not layout.script.is_file() or not layout.audio.is_file():
        raise FileNotFoundError(
            "O plano visual só é preparado depois do roteiro e do alinhamento único de áudio."
        )
    script = read_model(layout.script, ScriptDocument)
    audio = read_model(layout.audio, AudioManifest)
    input_hash = sha256_value(
        {
            "script": script,
            "audio": audio,
            "profile": profile,
        }
    )
    if layout.long_form_edit_plan_queue.is_file() and not force:
        existing = read_json(layout.long_form_edit_plan_queue)
        if isinstance(existing, dict) and existing.get("input_hash") == input_hash:
            return existing
    queue = {
        "schema_version": "1.0",
        "job_id": f"long-form-visual-editor-{input_hash[:12]}",
        "status": "waiting_external",
        "profile_id": PROFILE_ID,
        "input_hash": input_hash,
        "role": "long_form_visual_editor",
        "reasoning_effort": "medium",
        "model_pass_limit": profile.planner_model_passes,
        "aggregate_repair_limit": profile.aggregate_model_repairs,
        "context_policy": "declared_files_only_no_chat_history",
        "inputs": {
            "script": relative_posix(layout.script, layout.root),
            "audio_alignment": relative_posix(layout.audio, layout.root),
            "briefing": relative_posix(layout.briefing, layout.root),
            "style": relative_posix(layout.style_profile, layout.root),
        },
        "output": relative_posix(layout.long_form_edit_plan_staging_file, layout.root),
        "output_schema": compact_json_schema(LongFormCutoutEditPlan.model_json_schema()),
        "instructions": [
            "Planeje todos os blocos antes de solicitar qualquer asset.",
            "Use exatamente os três block_type declarados no schema.",
            "Reserve zonas independentes para texto e asset; nenhuma caixa pode se cruzar.",
            "Asset + texto exibe toda a narração do bloco e pagina quando necessário.",
            "Sequência fullscreen cobre todo o quadro e não recebe texto nem overlay.",
            "Cada asset exige frase literal de cue e base semântica; fallback genérico é proibido.",
            "Aspas não entram em text; use quote_open/quote_close somente nas fronteiras reais.",
            "A aspa final pertence à última página e aparece depois da última palavra.",
            (
                "Evite repetição, flashes e vazio visual; reforce especialmente os "
                "primeiros 30 segundos."
            ),
            "Use somente whoosh na abertura/citações e click em destaques pontuais.",
        ],
        "next_command": (f'uv run pipeline-videos profile plan-import "{manifest.project_id}"'),
    }
    write_json(layout.long_form_edit_plan_queue, queue)
    return queue


def import_project_edit_plan(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> tuple[LongFormCutoutEditPlan, EditPlanPreflightReport, dict[str, Any]]:
    if not layout.long_form_edit_plan_staging_file.is_file():
        raise FileNotFoundError(
            f"Plano em staging não encontrado: {layout.long_form_edit_plan_staging_file}"
        )
    if layout.long_form_edit_plan.is_file() and not force:
        raise FileExistsError(
            "O projeto já possui edit-plan.json; use --force somente para uma "
            "substituição deliberada."
        )
    raw_plan = read_json(layout.long_form_edit_plan_staging_file)
    script = read_model(layout.script, ScriptDocument)
    audio = read_model(layout.audio, AudioManifest)
    profile = layout.load_manifest().config.long_form_profile
    report = preflight_edit_plan_data(
        raw_plan,
        profile=profile,
        script=script,
        audio=audio,
    )
    write_json(layout.long_form_edit_preflight, report)
    if not report.valid:
        messages = "\n".join(
            f"- {item.location}: {item.message}"
            for item in report.diagnostics
            if item.severity == "blocking"
        )
        raise ValueError(
            "O preflight agregado rejeitou o plano antes da geração de assets:\n" + messages
        )
    plan = LongFormCutoutEditPlan.model_validate(raw_plan)
    write_json(layout.long_form_edit_plan, plan)
    inventory = build_asset_inventory(plan, report=report)
    write_json(layout.long_form_asset_inventory, inventory)
    return plan, report, inventory


def build_asset_inventory(
    plan: LongFormCutoutEditPlan,
    *,
    report: EditPlanPreflightReport,
) -> dict[str, Any]:
    if not report.valid:
        raise ValueError("O inventário só pode ser criado depois de um preflight agregado válido.")
    used_ids = {event.asset_id for block in plan.blocks for event in block.asset_events}
    if plan.thumbnail is not None:
        used_ids.add(plan.thumbnail.source_asset_id)
    assets = [asset for asset in plan.assets if asset.asset_id in used_ids]
    generation_items = [
        {
            "asset_id": asset.asset_id,
            "kind": asset.kind,
            "description": asset.description,
            "prompt": asset.generation_prompt,
            "semantic_tags": asset.semantic_tags,
            "existing_path": asset.existing_path,
            "needs_generation": not bool(asset.existing_path),
            "path": asset.existing_path,
            "status": "completed" if asset.existing_path else "pending",
        }
        for asset in assets
    ]
    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "profile_id": PROFILE_ID,
        "edit_plan_hash": sha256_value(plan),
        "selection_policy": (
            "semantic_blueprint_dedup_v2"
            if plan.thumbnail is not None
            else "semantic_inventory_no_fallback_v1"
        ),
        "deduplicated": True,
        "items": generation_items,
        "generation_count": sum(item["needs_generation"] for item in generation_items),
        "reuse_count": sum(not item["needs_generation"] for item in generation_items),
    }


def build_project_asset_inventory(
    layout: ProjectLayout,
    *,
    report: EditPlanPreflightReport | None = None,
) -> dict[str, Any]:
    report = report or preflight_project_edit_plan(layout)
    plan = read_model(layout.long_form_edit_plan, LongFormCutoutEditPlan)
    inventory = build_asset_inventory(plan, report=report)
    if layout.long_form_asset_inventory.is_file():
        existing = read_json(layout.long_form_asset_inventory)
        if (
            isinstance(existing, dict)
            and existing.get("edit_plan_hash") == inventory["edit_plan_hash"]
            and isinstance(existing.get("items"), list)
        ):
            completed = {
                item["asset_id"]: item
                for item in existing["items"]
                if isinstance(item, dict) and item.get("status") == "completed" and item.get("path")
            }
            for item in inventory["items"]:
                previous = completed.get(item["asset_id"])
                if previous is not None:
                    item.update(
                        {
                            "path": previous["path"],
                            "status": "completed",
                            "sha256": previous.get("sha256"),
                        }
                    )
    write_json(layout.long_form_asset_inventory, inventory)
    return inventory


def _asset_prompt(item: dict[str, Any]) -> str:
    style = (
        "Use case: illustration-story\n"
        "Visual standard: biblical-cutout-clay-v1.\n"
        "Estilo mascote 3D de massinha, cute clay render, soft vinyl toy, proporções chibi, "
        "formas arredondadas, olhos ovais simples, textura suave e fosca, cores vibrantes "
        "controladas, iluminação difusa de estúdio e sombras macias. Aparência de brinquedo "
        "colecionável, não realista. Sem texto, letras, logotipo ou marca-d'água.\n"
    )
    if item["kind"] == "cutout":
        format_contract = (
            "Asset recortável isolado, centralizado e grande, com silhueta inteira, folga limpa "
            "em todos os lados e fundo chroma key verde sólido uniforme. Sem cenário, chão, "
            "moldura, gradiente, sombra fora da base ou outro objeto não solicitado.\n"
        )
    elif item["kind"] == "fullscreen":
        format_contract = (
            "Imagem narrativa horizontal 16:9 fullscreen, cobrindo todo o quadro com uma única "
            "ação contínua. Sem background de pergaminho, sem espaço reservado para texto, sem "
            "overlay, colagem, painéis ou tela dividida.\n"
        )
    else:
        format_contract = (
            "Base horizontal 16:9 para thumbnail do YouTube. Use o fundo bíblico quente, "
            "texturizado e iluminado do perfil. Concentre personagens e objetos no lado direito, "
            "grandes e legíveis em tela pequena. Preserve os 45% do lado esquerdo limpos para a "
            "tipografia aplicada depois pelo compositor local. Não gere texto dentro da imagem.\n"
        )
    return style + format_contract + f"Pedido concreto: {item['prompt'].strip()}"


def _remove_chroma_and_trim(image: Image.Image) -> Image.Image:
    prepared = image.convert("RGBA")
    pixels: list[tuple[int, int, int, int]] = []
    for red, green, blue, alpha in prepared.getdata():
        green_separation = green - max(red, blue)
        if green >= 90 and green_separation >= 45:
            resolved_alpha = 0
        elif green >= 75 and green_separation > 15:
            resolved_alpha = round(alpha * (45 - green_separation) / 30)
        else:
            resolved_alpha = alpha
        pixels.append((red, green, blue, max(0, min(alpha, resolved_alpha))))
    prepared.putdata(pixels)
    alpha_channel = prepared.getchannel("A")
    bounds = alpha_channel.getbbox()
    if bounds is None:
        raise ValueError("O chroma key removeu toda a imagem; o asset precisa ser regenerado.")
    cropped = prepared.crop(bounds)
    padding = max(8, round(max(cropped.size) * 0.03))
    output = Image.new(
        "RGBA",
        (cropped.width + padding * 2, cropped.height + padding * 2),
        (0, 0, 0, 0),
    )
    output.alpha_composite(cropped, (padding, padding))
    return output


def prepare_profile_asset_queue(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    report = preflight_project_edit_plan(layout)
    if not report.valid:
        raise ValueError("O preflight agregado precisa estar válido antes da fila de assets.")
    inventory = build_project_asset_inventory(layout, report=report)
    project = layout.load_manifest()
    items: list[dict[str, Any]] = []
    pending = [
        item
        for item in inventory["items"]
        if item["needs_generation"]
        and (force or item.get("status") != "completed" or not item.get("path"))
    ]
    worker_count = min(project.config.long_form_profile.image_worker_limit, len(pending))
    for index, item in enumerate(pending):
        prompt = _asset_prompt(item)
        asset_id = item["asset_id"]
        staging = layout.long_form_asset_staging / f"{asset_id}.png"
        destination = layout.long_form_asset_files / f"{asset_id}.png"
        job_id = f"cutout-{asset_id}"
        items.append(
            {
                "job_id": job_id,
                "asset_id": asset_id,
                "kind": item["kind"],
                "prompt": prompt,
                "prompt_hash": sha256_value(prompt),
                "staging_path": relative_posix(staging, layout.root),
                "expected_path": relative_posix(destination, layout.root),
                "worker_id": (
                    f"image-worker-{(index % worker_count) + 1}" if worker_count else None
                ),
                "status": "waiting_external",
                "materialize_command": (
                    f'uv run pipeline-videos profile asset-stage "{project.project_id}" '
                    f'--job-id "{job_id}" --source "<IMAGEGEN_SAVED_PATH>"'
                ),
            }
        )
    queue = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "profile_id": PROFILE_ID,
        "edit_plan_hash": inventory["edit_plan_hash"],
        "preflight_valid": True,
        "worker_count": worker_count,
        "context_policy": "compact_jobs_only",
        "fork_turns": "none",
        "reasoning_effort": "low",
        "items": items,
        "status": "waiting_external" if items else "completed",
        "next_command": (f'uv run pipeline-videos profile asset-import "{project.project_id}"'),
    }
    write_json(layout.long_form_asset_queue, queue)
    for worker_index in range(1, worker_count + 1):
        worker_id = f"image-worker-{worker_index}"
        jobs = [item for item in items if item["worker_id"] == worker_id]
        write_json(
            layout.long_form_asset_jobs / f"{worker_id}.json",
            {
                "schema_version": "1.0",
                "project_id": project.project_id,
                "profile_id": PROFILE_ID,
                "worker_id": worker_id,
                "role": "image_worker",
                "context_policy": "read_this_file_only",
                "fork_turns": "none",
                "reasoning_effort": "low",
                "instructions": [
                    "Use uma chamada imagegen por job e preserve integralmente o prompt.",
                    (
                        "Depois do sucesso, use apenas savedPath no materialize_command; "
                        "não use base64, revisedPrompt ou output_hint como caminho."
                    ),
                    "Não importe nem altere arquivos compartilhados do projeto.",
                ],
                "jobs": jobs,
            },
        )
    return queue


def stage_profile_asset(
    layout: ProjectLayout,
    *,
    job_id: str,
    source: Path,
) -> Path:
    queue = read_json(layout.long_form_asset_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise ValueError("Fila de assets cutout inválida.")
    entry = next(
        (
            item
            for item in queue["items"]
            if isinstance(item, dict) and item.get("job_id") == job_id
        ),
        None,
    )
    if entry is None:
        raise ValueError(f"Job desconhecido: {job_id}.")
    resolved_source = source.expanduser().resolve()
    if not resolved_source.is_file():
        raise FileNotFoundError(f"Imagem gerada não encontrada: {resolved_source}")
    destination = (layout.root / entry["staging_path"]).resolve()
    if not destination.is_relative_to(layout.long_form_asset_staging.resolve()):
        raise ValueError("O staging do asset escaparia da pasta isolada.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".png.tmp")
    try:
        with Image.open(resolved_source) as image:
            image.load()
            prepared = (
                _remove_chroma_and_trim(image)
                if entry["kind"] == "cutout"
                else image.convert("RGBA")
            )
            prepared.save(temp, format="PNG", optimize=True)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def import_profile_assets(layout: ProjectLayout) -> dict[str, Any]:
    queue = read_json(layout.long_form_asset_queue)
    inventory = read_json(layout.long_form_asset_inventory)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise ValueError("Fila de assets cutout inválida.")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
        raise ValueError("Inventário de assets cutout inválido.")
    staged: list[tuple[dict[str, Any], Path]] = []
    for entry in queue["items"]:
        if not isinstance(entry, dict):
            raise ValueError("Item inválido na fila de assets.")
        source = (layout.root / entry["staging_path"]).resolve()
        if not source.is_file():
            raise FileNotFoundError(
                f"O lote ainda está incompleto; staging ausente para {entry['asset_id']}."
            )
        with Image.open(source) as image:
            image.verify()
        staged.append((entry, source))
    inventory_by_id = {
        item["asset_id"]: item
        for item in inventory["items"]
        if isinstance(item, dict) and isinstance(item.get("asset_id"), str)
    }
    for entry, source in staged:
        destination = (layout.root / entry["expected_path"]).resolve()
        if not destination.is_relative_to(layout.long_form_asset_files.resolve()):
            raise ValueError("O destino do asset escaparia da pasta oficial.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(".png.tmp")
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
        entry["status"] = "completed"
        entry["actual_path"] = relative_posix(destination, layout.root)
        item = inventory_by_id[entry["asset_id"]]
        item["path"] = entry["actual_path"]
        item["status"] = "completed"
        item["sha256"] = sha256_file(destination)
    for item in inventory["items"]:
        if item.get("existing_path"):
            item["path"] = item["existing_path"]
            item["status"] = "completed"
    queue["status"] = "completed"
    inventory["completed_at"] = utc_now()
    write_json(layout.long_form_asset_queue, queue)
    write_json(layout.long_form_asset_inventory, inventory)
    return inventory


def profile_summary(config: LongFormProductionProfileConfig | None = None) -> dict[str, Any]:
    selected = config or official_profile_config()
    return {
        **selected.model_dump(mode="json"),
        "pipeline_order": [
            "single_script_visual_blueprint_and_youtube_package",
            "single_audio_and_word_alignment",
            "local_timed_plan_compiler_zero_model_calls",
            "compiled_hash_time_and_file_invariants",
            "deduplicated_asset_inventory",
            "generate_missing_assets_with_two_workers",
            "local_thumbnail_and_metadata",
            "single_final_render",
            "technical_media_validation",
        ],
        "construction_contracts": [
            "all_visual_decisions_live_in_the_script_blueprint",
            "renderer_owns_quote_marks",
            "closing_quote_after_last_spoken_word_only",
            "asset_text_side_by_side",
            "one_large_or_two_to_three_smaller_assets",
            "centered_reference_without_asset",
            "fullscreen_sequences_have_no_overlay",
            "fullscreen_shots_are_distinct_actions",
            "opening_starts_with_visual_action",
            "conversational_concrete_ptbr",
        ],
    }
