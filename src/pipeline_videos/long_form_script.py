from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from pipeline_videos.creative_direction import creative_evidence_errors, hook_matches_family
from pipeline_videos.editorial import quoted_comment_response, scripture_citations
from pipeline_videos.models import (
    Briefing,
    LongFormScriptConfig,
    ScriptDocument,
    SourceEntry,
    SourceLibrary,
)
from pipeline_videos.narration_style import validate_authorial_narration_style
from pipeline_videos.utils import tokenize_words

_GENERIC_OPENING_PATTERNS = (
    re.compile(r"^\s*ol[áa][,!.\s]", flags=re.IGNORECASE),
    re.compile(r"^\s*seja(?:m)?\s+bem[- ]?vind", flags=re.IGNORECASE),
    re.compile(r"\bno\s+v[íi]deo\s+de\s+hoje\b", flags=re.IGNORECASE),
    re.compile(r"^\s*antes\s+de\s+come[çc]ar\b", flags=re.IGNORECASE),
)
_CTA_MARKERS = {
    "comment": ("coment", "escrev", "digite"),
    "subscribe": ("inscrev", "inscric"),
    "share": ("compartilh", "envie este video", "mande este video"),
}
_STORYTELLING_META_TITLE_MARKERS = (
    "analise",
    "comparacao",
    "evidencia",
    "o relato",
    "passagem paralela",
    "sintese do retrato",
    "traco do retrato",
)


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def _contains(container: str, value: str) -> bool:
    return bool(value.strip()) and _normalized(value) in _normalized(container)


def _verified_biblical_sources(sources: SourceLibrary | None) -> dict[str, SourceEntry]:
    if sources is None:
        return {}
    return {
        entry.source_id: entry
        for entry in sources.entries
        if entry.kind == "biblical_text" and entry.verified and entry.excerpt
    }


def minimum_long_form_reengagements(
    duration_seconds: int,
    config: LongFormScriptConfig,
) -> int:
    if duration_seconds < 180:
        return 1
    return max(2, math.floor(duration_seconds / config.maximum_reengagement_gap_seconds))


def validate_biblical_storytelling_voice(document: ScriptDocument) -> None:
    """Keep factual safeguards backstage instead of narrating the analysis process."""

    validate_authorial_narration_style(document)
    strategy = document.long_form_strategy
    if strategy is None or strategy.creative_mode != "biblical_storytelling":
        return

    for section in document.sections:
        normalized_title = _normalized(section.title)
        if any(marker in normalized_title for marker in _STORYTELLING_META_TITLE_MARKERS):
            raise ValueError(
                "O storytelling bíblico precisa nomear movimentos da história, não etapas de "
                f"análise: {section.title!r}."
            )

    analytical_beats = [
        beat.beat_id
        for beat in strategy.progression
        if {"analysis", "comparison"}.intersection(beat.functions)
    ]
    if analytical_beats:
        raise ValueError(
            "O perfil de storytelling não aceita beats de analysis/comparison; transforme-os em "
            "acontecimento, contexto, progressão, consequência ou revelação."
        )

    development_sections = document.sections[1:-1]
    if development_sections:
        narrative_sections = sum(
            section.role == "biblical_narrative" for section in development_sections
        )
        minimum_narrative_sections = max(1, math.ceil(len(development_sections) * 0.6))
        if narrative_sections < minimum_narrative_sections:
            raise ValueError(
                "O perfil de storytelling exige que a maior parte do desenvolvimento seja "
                "narrativa bíblica em progressão, não reflexão ou comentário."
            )


def _validate_story_alignment(
    document: ScriptDocument,
    config: LongFormScriptConfig,
    briefing: Briefing | None,
    sources: SourceLibrary | None,
) -> None:
    strategy = document.long_form_strategy
    if strategy is None:
        raise ValueError("O padrão longo configurado exige long_form_strategy no roteiro.")
    if strategy.standard != config.narrative_standard:
        raise ValueError("O padrão da estratégia longa não corresponde à configuração do projeto.")
    if not document.sections:
        raise ValueError("O roteiro longo precisa de seções.")
    if document.sections[0].role != "opening" or document.sections[-1].role != "closing":
        raise ValueError("O roteiro longo precisa começar com opening e terminar com closing.")
    validate_biblical_storytelling_voice(document)

    section_ids = [section.section_id for section in document.sections]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("section_id precisa ser único no roteiro longo.")
    beat_section_ids = [beat.section_id for beat in strategy.progression]
    if beat_section_ids != section_ids:
        raise ValueError(
            "A progressão longa precisa mapear cada seção exatamente uma vez e em ordem."
        )
    _validate_visual_scenes(document)

    for section, beat in zip(document.sections, strategy.progression, strict=True):
        if not (section.retention_goal or "").strip():
            raise ValueError(f"{section.section_id} precisa declarar retention_goal.")
        if section.retention_goal.strip() != beat.viewer_value.strip():
            raise ValueError(f"{section.section_id} precisa copiar viewer_value em retention_goal.")
        if config.require_visual_direction:
            if not (section.visual_direction or "").strip():
                raise ValueError(f"{section.section_id} precisa declarar visual_direction.")
            if section.visual_direction.strip() != beat.visual_progression.strip():
                raise ValueError(
                    f"{section.section_id} precisa copiar visual_progression em visual_direction."
                )

    opening = document.sections[0].narration.strip()
    if any(pattern.search(opening) for pattern in _GENERIC_OPENING_PATTERNS):
        raise ValueError("A abertura longa não pode usar saudação ou introdução genérica.")

    if strategy.standard == "retention_story_long_v1":
        if not _normalized(opening).startswith(_normalized(strategy.opening_hook)):
            raise ValueError("opening_hook precisa ser o prefixo literal da locução de abertura.")
        if len(tokenize_words(strategy.opening_hook)) > config.opening_hook_max_words:
            raise ValueError(f"opening_hook ultrapassa {config.opening_hook_max_words} palavras.")
        if not _contains(opening, strategy.content_promise):
            raise ValueError("A abertura precisa verbalizar content_promise.")
        if not _contains(opening, strategy.central_question):
            raise ValueError("A abertura precisa verbalizar central_question.")
    else:
        if not _normalized(opening).startswith(_normalized(strategy.opening_line)):
            raise ValueError("opening_line precisa ser o prefixo literal da locução de abertura.")
        if strategy.hook_family != "none":
            if not hook_matches_family(strategy.opening_hook, strategy.hook_family):
                raise ValueError("O hook não corresponde à família declarada.")
            if len(tokenize_words(strategy.opening_hook)) > config.opening_hook_max_words:
                raise ValueError(
                    f"opening_hook ultrapassa {config.opening_hook_max_words} palavras."
                )
        early_narration = " ".join(section.narration for section in document.sections[:2])
        if not _contains(early_narration, strategy.content_promise):
            raise ValueError("A promessa de conteúdo precisa ficar clara nos movimentos iniciais.")
        if strategy.central_question.strip() and not _contains(
            early_narration, strategy.central_question
        ):
            raise ValueError("A pergunta organizadora declarada precisa aparecer no início.")
    if briefing is not None:
        if (
            briefing.audience.strip()
            and strategy.target_viewer.strip() != briefing.audience.strip()
        ):
            raise ValueError("target_viewer precisa copiar briefing.audience.")
        if strategy.standard == "retention_story_long_v1":
            if (
                briefing.practical_application.strip()
                and strategy.practical_application.strip() != briefing.practical_application.strip()
            ):
                raise ValueError(
                    "practical_application precisa copiar briefing.practical_application."
                )
        else:
            if strategy.creative_mode != briefing.creative_mode:
                raise ValueError("creative_mode precisa copiar o modo resolvido no briefing.")
            if strategy.interpretive_stance != briefing.interpretive_stance:
                raise ValueError(
                    "interpretive_stance precisa copiar a postura definida no briefing."
                )
            if strategy.hook_policy != briefing.hook_policy:
                raise ValueError("hook_policy precisa copiar a política definida no briefing.")
            if briefing.hook_family != "auto" and strategy.hook_family != briefing.hook_family:
                raise ValueError("hook_family precisa copiar a família definida no briefing.")
            if (
                briefing.viewer_outcome.strip()
                and strategy.viewer_takeaway.strip() != briefing.viewer_outcome.strip()
            ):
                raise ValueError("viewer_takeaway precisa copiar briefing.viewer_outcome.")
            if (
                briefing.practical_application.strip()
                and strategy.practical_application.strip() != briefing.practical_application.strip()
            ):
                raise ValueError(
                    "A aplicação declarada precisa copiar briefing.practical_application."
                )

    resolution_functions = (
        {"payoff"} if strategy.standard == "retention_story_long_v1" else {"resolution"}
    )
    resolution_text = " ".join(
        section.narration
        for section, beat in zip(document.sections, strategy.progression, strict=True)
        if resolution_functions.intersection(beat.functions)
    )
    application_text = " ".join(
        section.narration
        for section, beat in zip(document.sections, strategy.progression, strict=True)
        if "application" in beat.functions
    )
    closing = document.sections[-1].narration.strip()
    if strategy.standard == "retention_story_long_v1":
        if not _contains(resolution_text, strategy.payoff):
            raise ValueError(
                "O payoff declarado precisa aparecer literalmente no movimento de payoff."
            )
        if not _contains(application_text, strategy.practical_application):
            raise ValueError("A aplicação declarada precisa aparecer no movimento de aplicação.")
        if config.require_callback and not _contains(closing, strategy.callback):
            raise ValueError("A closing precisa retomar a abertura pelo callback declarado.")
    else:
        if not _contains(resolution_text, strategy.resolution):
            raise ValueError("A resolução declarada precisa aparecer no movimento de resolução.")
        if not _contains(resolution_text, strategy.viewer_takeaway):
            raise ValueError("O valor final precisa aparecer no movimento de resolução.")
        if strategy.practical_application.strip() and not _contains(
            application_text, strategy.practical_application
        ):
            raise ValueError("A aplicação opcional precisa aparecer em um beat de application.")
        if strategy.callback.strip() and not _contains(closing, strategy.callback):
            raise ValueError("O callback declarado precisa aparecer na closing.")
        evidence_errors = creative_evidence_errors(
            document,
            sources,
            creative_mode=strategy.creative_mode,
            interpretive_stance=strategy.interpretive_stance,
        )
        if evidence_errors:
            raise ValueError(evidence_errors[0])
    if not _normalized(closing).endswith(_normalized(strategy.engagement.spoken_text)):
        raise ValueError("O CTA precisa ser o último movimento da locução.")


def _validate_visual_scenes(document: ScriptDocument) -> None:
    strategy = document.long_form_strategy
    assert strategy is not None
    expected_section_ids = [section.section_id for section in document.sections]
    planned_section_ids: list[str] = []
    for visual_scene in strategy.visual_scenes:
        if visual_scene.section_id not in expected_section_ids:
            raise ValueError(
                f"A cena visual aponta para seção inexistente: {visual_scene.section_id}."
            )
        if not planned_section_ids or planned_section_ids[-1] != visual_scene.section_id:
            planned_section_ids.append(visual_scene.section_id)
    if planned_section_ids != expected_section_ids:
        raise ValueError(
            "visual_scenes precisa cobrir todas as seções uma única vez e na ordem da locução."
        )

    for section in document.sections:
        blocks = [
            scene.narration
            for scene in strategy.visual_scenes
            if scene.section_id == section.section_id
        ]
        reconstructed = " ".join(block.strip() for block in blocks)
        if _normalized(reconstructed) != _normalized(section.narration):
            raise ValueError(
                f"Os blocos visuais de {section.section_id} não reconstroem integralmente "
                "a narração."
            )

    descriptions = [_normalized(scene.scene_description) for scene in strategy.visual_scenes]
    if any(left == right for left, right in zip(descriptions, descriptions[1:], strict=False)):
        raise ValueError("Cenas visuais consecutivas precisam ser concretas e distintas.")


def _validate_timing_and_retention(
    document: ScriptDocument,
    *,
    target_duration_seconds: int,
    config: LongFormScriptConfig,
) -> dict[str, Any]:
    strategy = document.long_form_strategy
    assert strategy is not None
    word_count = len(tokenize_words(document.narration_text()))
    estimated_duration = word_count / config.estimated_narration_wpm * 60
    minimum_duration = target_duration_seconds * config.minimum_duration_ratio
    maximum_duration = target_duration_seconds * config.maximum_duration_ratio
    if not minimum_duration <= estimated_duration <= maximum_duration:
        raise ValueError(
            "A duração estimada do roteiro longo precisa ficar entre "
            f"{minimum_duration:.0f}s e {maximum_duration:.0f}s; ficou em "
            f"{estimated_duration:.0f}s."
        )

    previous_end = 0.0
    for index, beat in enumerate(strategy.progression):
        if index == 0 and beat.target_start_seconds != 0:
            raise ValueError("O primeiro beat longo precisa começar em 0s.")
        if abs(beat.target_start_seconds - previous_end) > 3.0:
            raise ValueError("Os beats longos precisam formar uma progressão temporal contínua.")
        previous_end = beat.target_end_seconds
    if not minimum_duration <= previous_end <= maximum_duration:
        raise ValueError("O último beat longo precisa terminar dentro da janela de duração.")

    first_beat = strategy.progression[0]
    if strategy.standard == "retention_story_long_v1":
        if "hook" not in first_beat.functions:
            raise ValueError("O primeiro beat longo precisa cumprir a função de hook.")
        if first_beat.target_end_seconds > config.opening_target_max_seconds:
            raise ValueError(
                f"O cold open longo precisa terminar até {config.opening_target_max_seconds:.0f}s."
            )
        resolution_starts = [
            beat.target_start_seconds for beat in strategy.progression if "payoff" in beat.functions
        ]
        resolution_start = min(resolution_starts)
        minimum_resolution_ratio = config.payoff_start_minimum_ratio
    else:
        hook_used = strategy.hook_family != "none"
        if hook_used and "hook" not in first_beat.functions:
            raise ValueError("O primeiro beat precisa cumprir o hook declarado.")
        opening_limit = (
            config.opening_target_max_seconds if hook_used else config.opening_value_max_seconds
        )
        if first_beat.target_end_seconds > opening_limit:
            raise ValueError(
                f"O primeiro movimento precisa entregar valor até {opening_limit:.0f}s."
            )
        resolution_starts = [
            beat.target_start_seconds
            for beat in strategy.progression
            if "resolution" in beat.functions
        ]
        resolution_start = min(resolution_starts)
        minimum_resolution_ratio = {
            "distributed": 0.35,
            "late": 0.60,
            "final_quarter": 0.75,
        }[strategy.resolution_timing]
    if resolution_start < target_duration_seconds * minimum_resolution_ratio:
        raise ValueError("A resolução foi antecipada antes de o desenvolvimento sustentá-la.")

    reengagements = [
        beat.target_start_seconds
        for beat in strategy.progression
        if "reengagement" in beat.functions
    ]
    required = minimum_long_form_reengagements(target_duration_seconds, config)
    if len(reengagements) < required:
        raise ValueError(
            f"O vídeo longo precisa de ao menos {required} reengajamentos; recebeu "
            f"{len(reengagements)}."
        )
    if strategy.standard == "retention_story_long_v1" and any(
        timestamp >= resolution_start for timestamp in reengagements
    ):
        raise ValueError("Reengajamentos precisam preparar o payoff, não ocorrer depois dele.")
    retention_anchors = (
        [0.0, *reengagements, resolution_start]
        if strategy.standard == "retention_story_long_v1"
        else [0.0, *reengagements, previous_end]
    )
    gaps = [
        right - left for left, right in zip(retention_anchors, retention_anchors[1:], strict=False)
    ]
    if gaps and max(gaps) > config.maximum_reengagement_gap_seconds:
        raise ValueError(
            "A progressão longa ficou tempo demais sem nova informação ou reengajamento; "
            f"maior intervalo: {max(gaps):.0f}s."
        )
    return {
        "word_count": word_count,
        "estimated_duration_seconds": round(estimated_duration, 2),
        "reengagement_count": len(reengagements),
        "reengagement_timestamps": reengagements,
        "payoff_start_seconds": resolution_start,
        "resolution_start_seconds": resolution_start,
        "hook_used": strategy.standard == "retention_story_long_v1"
        or strategy.hook_family != "none",
        "creative_mode": (
            strategy.creative_mode if strategy.standard == "creative_retention_long_v2" else None
        ),
    }


def _validate_citation_cadence(
    document: ScriptDocument,
    *,
    config: LongFormScriptConfig,
    sources: SourceLibrary | None,
    estimated_duration_seconds: float,
) -> list[float]:
    strategy = document.long_form_strategy
    assert strategy is not None
    citations = scripture_citations(
        document,
        words_per_minute=config.estimated_narration_wpm,
    )
    anchors = strategy.scripture_anchors
    if len(anchors) != len(citations):
        raise ValueError(
            "scripture_anchors precisa mapear cada citação literal exatamente uma vez."
        )

    verified_sources = _verified_biblical_sources(sources)
    tolerance = 20.0
    for citation, anchor in zip(citations, anchors, strict=True):
        if citation.section_id != anchor.section_id:
            raise ValueError("A ordem de scripture_anchors não corresponde às citações narradas.")
        if abs(citation.estimated_start_seconds - anchor.target_start_seconds) > tolerance:
            raise ValueError(
                "O timestamp planejado de uma citação difere mais de 20s da locução estimada."
            )
        section = next(item for item in document.sections if item.section_id == anchor.section_id)
        if anchor.reference not in section.biblical_basis:
            raise ValueError(
                f"{anchor.section_id} precisa registrar {anchor.reference!r} em biblical_basis."
            )
        if verified_sources:
            source = verified_sources.get(anchor.source_id)
            if source is None:
                raise ValueError(
                    f"A âncora {anchor.reference!r} não aponta para uma fonte bíblica verificada."
                )
            if source.passage and _normalized(source.passage) != _normalized(anchor.reference):
                raise ValueError("A referência da âncora não corresponde à fonte verificada.")
            if _normalized(source.excerpt or "") != _normalized(citation.quote):
                raise ValueError(
                    "A citação narrada não corresponde ao excerpt da âncora planejada."
                )

    timestamps = [citation.estimated_start_seconds for citation in citations]
    if not timestamps:
        raise ValueError("O roteiro longo precisa de citações bíblicas periódicas.")
    if timestamps[0] > config.first_scripture_citation_max_seconds:
        raise ValueError(
            "A primeira citação bíblica precisa entrar até "
            f"{config.first_scripture_citation_max_seconds:.0f}s."
        )
    gaps = [right - left for left, right in zip(timestamps, timestamps[1:], strict=False)]
    if any(gap < config.minimum_scripture_citation_gap_seconds for gap in gaps):
        raise ValueError("As citações bíblicas estão agrupadas; distribua-as ao longo da história.")
    if any(gap > config.maximum_scripture_citation_gap_seconds for gap in gaps):
        raise ValueError(
            "A narrativa ficou mais de "
            f"{config.maximum_scripture_citation_gap_seconds:.0f}s sem uma citação bíblica."
        )
    if (
        estimated_duration_seconds - timestamps[-1]
        > config.final_scripture_citation_max_distance_seconds
    ):
        raise ValueError("A parte final ficou distante demais da última âncora bíblica.")
    return timestamps


def _validate_engagement(document: ScriptDocument, config: LongFormScriptConfig) -> None:
    strategy = document.long_form_strategy
    assert strategy is not None
    spoken = strategy.engagement.spoken_text
    word_count = len(tokenize_words(spoken))
    if word_count > config.cta_max_words:
        raise ValueError(f"O CTA longo ultrapassa {config.cta_max_words} palavras.")
    estimated_seconds = word_count / config.estimated_narration_wpm * 60
    if estimated_seconds > config.cta_max_seconds:
        raise ValueError(f"O CTA longo ultrapassa {config.cta_max_seconds:.0f}s.")
    normalized = _normalized(spoken)
    detected = {
        action
        for action, markers in _CTA_MARKERS.items()
        if any(marker in normalized for marker in markers)
    }
    if strategy.engagement.action not in detected:
        raise ValueError("O texto do CTA não executa a ação declarada.")
    if detected != {strategy.engagement.action}:
        raise ValueError("O encerramento deve pedir exatamente uma ação de CTA.")
    if strategy.engagement.action == "comment" and not quoted_comment_response(spoken):
        raise ValueError("CTA de comentário precisa trazer a resposta específica entre aspas.")


def validate_long_form_retention_script(
    document: ScriptDocument,
    *,
    target_duration_seconds: int,
    config: LongFormScriptConfig,
    sources: SourceLibrary | None = None,
    briefing: Briefing | None = None,
) -> dict[str, Any]:
    if not config.enabled or config.narrative_standard == "legacy":
        return {}
    _validate_story_alignment(document, config, briefing, sources)
    diagnostics = _validate_timing_and_retention(
        document,
        target_duration_seconds=target_duration_seconds,
        config=config,
    )
    diagnostics["scripture_citation_timestamps"] = _validate_citation_cadence(
        document,
        config=config,
        sources=sources,
        estimated_duration_seconds=diagnostics["estimated_duration_seconds"],
    )
    _validate_engagement(document, config)
    return diagnostics
