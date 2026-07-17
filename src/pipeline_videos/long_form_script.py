from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from pipeline_videos.editorial import quoted_comment_response, scripture_citations
from pipeline_videos.models import (
    Briefing,
    LongFormScriptConfig,
    ScriptDocument,
    SourceEntry,
    SourceLibrary,
)
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


def _validate_story_alignment(
    document: ScriptDocument,
    config: LongFormScriptConfig,
    briefing: Briefing | None,
) -> None:
    strategy = document.long_form_strategy
    if strategy is None:
        raise ValueError("retention_story_long_v1 exige long_form_strategy no roteiro.")
    if strategy.standard != config.narrative_standard:
        raise ValueError("O padrão da estratégia longa não corresponde à configuração do projeto.")
    if not document.sections:
        raise ValueError("O roteiro longo precisa de seções.")
    if document.sections[0].role != "opening" or document.sections[-1].role != "closing":
        raise ValueError("O roteiro longo precisa começar com opening e terminar com closing.")

    section_ids = [section.section_id for section in document.sections]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError("section_id precisa ser único no roteiro longo.")
    beat_section_ids = [beat.section_id for beat in strategy.progression]
    if beat_section_ids != section_ids:
        raise ValueError(
            "A progressão longa precisa mapear cada seção exatamente uma vez e em ordem."
        )

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
        raise ValueError("A abertura longa deve começar no conflito, sem saudação ou introdução.")
    if not _normalized(opening).startswith(_normalized(strategy.opening_hook)):
        raise ValueError("opening_hook precisa ser o prefixo literal da locução de abertura.")
    if len(tokenize_words(strategy.opening_hook)) > config.opening_hook_max_words:
        raise ValueError(f"opening_hook ultrapassa {config.opening_hook_max_words} palavras.")
    if not _contains(opening, strategy.content_promise):
        raise ValueError("A abertura precisa verbalizar content_promise.")
    if not _contains(opening, strategy.central_question):
        raise ValueError("A abertura precisa verbalizar central_question.")
    if briefing is not None:
        if (
            briefing.audience.strip()
            and strategy.target_viewer.strip() != briefing.audience.strip()
        ):
            raise ValueError("target_viewer precisa copiar briefing.audience.")
        if (
            briefing.practical_application.strip()
            and strategy.practical_application.strip() != briefing.practical_application.strip()
        ):
            raise ValueError("practical_application precisa copiar briefing.practical_application.")

    payoff_text = " ".join(
        section.narration
        for section, beat in zip(document.sections, strategy.progression, strict=True)
        if "payoff" in beat.functions
    )
    application_text = " ".join(
        section.narration
        for section, beat in zip(document.sections, strategy.progression, strict=True)
        if "application" in beat.functions
    )
    closing = document.sections[-1].narration.strip()
    if not _contains(payoff_text, strategy.payoff):
        raise ValueError("O payoff declarado precisa aparecer literalmente no movimento de payoff.")
    if not _contains(application_text, strategy.practical_application):
        raise ValueError("A aplicação declarada precisa aparecer no movimento de aplicação.")
    if config.require_callback and not _contains(closing, strategy.callback):
        raise ValueError("A closing precisa retomar a abertura por meio do callback declarado.")
    if not _normalized(closing).endswith(_normalized(strategy.engagement.spoken_text)):
        raise ValueError("O CTA precisa ser o último movimento da locução.")


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
    if "hook" not in first_beat.functions:
        raise ValueError("O primeiro beat longo precisa cumprir a função de hook.")
    if first_beat.target_end_seconds > config.opening_target_max_seconds:
        raise ValueError(
            f"O cold open longo precisa terminar até {config.opening_target_max_seconds:.0f}s."
        )

    payoff_starts = [
        beat.target_start_seconds for beat in strategy.progression if "payoff" in beat.functions
    ]
    payoff_start = min(payoff_starts)
    if payoff_start < target_duration_seconds * config.payoff_start_minimum_ratio:
        raise ValueError(
            "O payoff longo foi antecipado antes de a progressão sustentar a promessa."
        )

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
    if any(timestamp >= payoff_start for timestamp in reengagements):
        raise ValueError("Reengajamentos precisam preparar o payoff, não ocorrer depois dele.")
    retention_anchors = [0.0, *reengagements, payoff_start]
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
        "payoff_start_seconds": payoff_start,
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
    _validate_story_alignment(document, config, briefing)
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
