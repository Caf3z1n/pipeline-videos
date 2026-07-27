from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline_videos.creative_direction import (
    HOOK_TEMPLATE_EXAMPLES,
    creative_evidence_errors,
    creative_mode_payload,
    hook_matches_family,
    hook_matches_user_six_template,
)
from pipeline_videos.editorial import quoted_comment_response
from pipeline_videos.models import (
    HIGH_STAKES_HOOK_FAMILIES,
    USER_SIX_HOOK_FAMILIES,
    BiblicalReview,
    Briefing,
    CompactVisualPlan,
    ScriptDocument,
    ScriptSection,
    ShortsCinematicDevotionalDirection,
    ShortsRetentionBeat,
    ShortsScriptConfig,
    ShortsScriptPackage,
    ShortsScriptStrategy,
    ShortsVerseOpening,
    ShortsVisualScene,
    SourceEntry,
    SourceLibrary,
    StageStatus,
)
from pipeline_videos.narration_style import validate_authorial_narration_style
from pipeline_videos.review import save_biblical_review
from pipeline_videos.script import save_script
from pipeline_videos.state import complete_stage, invalidate, start_stage, wait_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    tokenize_words,
    utc_now,
)

LEGACY_SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION = "5.2"
SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION = "5.4"
LEGACY_SHORTS_SCRIPT_ALGORITHM_VERSION = "6.2"
SHORTS_SCRIPT_ALGORITHM_VERSION = "6.5"
LEGACY_SCRIPTURE_BOUNDARY_PHRASE = "Até aqui, o texto bíblico."


class ShortsScriptPackageValidationError(ValueError):
    """All independently detectable staging violations from one validation pass."""

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        details = "\n".join(f"- {error}" for error in self.errors)
        super().__init__(f"O pacote do roteirista possui {len(self.errors)} erro(s):\n{details}")


@dataclass(frozen=True)
class ShortsScriptStagingCheck:
    valid: bool
    errors: tuple[str, ...]
    normalizations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "normalizations": list(self.normalizations),
        }


_MIN_COMMENT_CUE_SIGNIFICANT_CHARACTERS = 6
_WEAK_SINGLE_WORD_COMMENT_CUES = frozenset(
    {
        "aqui",
        "amem",
        "bem",
        "eu",
        "fe",
        "fiel",
        "forca",
        "paz",
        "perto",
        "presente",
        "sim",
    }
)

_GENERIC_AI_PHRASES = (
    "a coragem nasce de",
    "acolha a sua dor",
    "essa passagem nos convida",
    "escolha um próximo passo responsável",
    "nomeie o seu medo",
    "para a vida real",
)
_MULTI_SCENE_VISUAL_TERMS = (
    "corte rápido para",
    "colagem",
    "díptico",
    "momentos diferentes",
    "montagem de",
    "painéis",
    "sequência de cenas",
    "split-screen",
    "storyboard",
    "tela dividida",
    "três ações",
    "três cenas",
    "três vinhetas",
    "tríptico",
    "várias cenas",
    "várias ações",
    "vinhetas",
)

_BIBLICAL_REFERENCE_PATTERN = re.compile(
    r"^(?P<book>.+?)\s+(?P<chapter>\d+)\s*:\s*(?P<verse>\d+)"
    r"(?:\s*[-–—]\s*(?P<end_verse>\d+))?$"
)
_RAW_NUMERIC_REFERENCE_PATTERN = re.compile(r"\b\d+\s*:\s*\d+\b")


def _short_performance_profile_contract(
    config: ShortsScriptConfig,
) -> dict[str, Any] | None:
    if config.performance_profile is None:
        return None
    profile_rules = (
        {
            "opening": (
                "hook concreto de 10 a 12 palavras e um único foco visual com ação já em curso"
            ),
            "progression": "hook, fala direta de Jesus e referência; nenhum CTA",
            "first_frame_avoid": [
                "acolhimento genérico sem conflito legível",
                "multidão, fundo carregado ou plano geral que esconda a ação",
                "imagem escura sem contraste local no sujeito dominante",
            ],
        }
        if config.performance_profile == "feed_compact_15_20_v1"
        else {
            "opening": ("uma pergunta, contradição ou consequência concreta em até 14 palavras"),
            "progression": (
                "um loop central, de três a cinco avanços com informação nova, "
                "e payoff antes do CTA"
            ),
            "first_frame_avoid": [
                "contexto abstrato antes da pergunta central",
                "multidão ou cenário detalhado competindo com o sujeito",
                "primeiro quadro contemplativo sem conflito observável",
            ],
        }
    )
    return {
        "profile": config.performance_profile,
        "evidence_policy": config.performance_evidence_policy,
        "learning_is_advisory": True,
        "minimum_observation_hours": config.minimum_performance_observation_hours,
        "minimum_feed_exposures": config.minimum_feed_exposures_for_learning,
        "minimum_engaged_views": config.minimum_engaged_views_for_learning,
        "candidate_reference_metrics": {
            "stayed_to_watch_percent": (config.candidate_reference_stayed_to_watch_percent),
            "average_percentage_viewed": (config.candidate_reference_average_percentage_viewed),
        },
        "opening_performance_standard": config.opening_performance_standard,
        "maximum_progression_beats": config.maximum_progression_beats,
        **profile_rules,
    }


def _short_timeline_seconds(
    duration_seconds: int,
    config: ShortsScriptConfig,
) -> dict[str, list[float | int] | None]:
    if config.performance_profile == "feed_depth_45_60_v1":
        cta_start = max(
            config.hook_target_seconds + 12.0,
            duration_seconds - config.cta_max_seconds,
        )
        return {
            "hook": [0, config.hook_target_seconds],
            "question_or_conflict": [config.hook_target_seconds, 8],
            "three_to_five_advances": [8, round(duration_seconds * 0.62, 1)],
            "scripture_adaptive": [
                config.scripture_citation_min_start_seconds,
                config.scripture_citation_max_end_seconds,
            ],
            "payoff_before_cta": [round(duration_seconds * 0.58, 1), cta_start],
            "single_action_cta": [cta_start, duration_seconds],
        }
    if config.scripture_citation_timing_policy == "adaptive_payoff_v1":
        cta_start = max(config.hook_target_seconds + 8.0, duration_seconds - config.cta_max_seconds)
        return {
            "hook": [0, config.hook_target_seconds],
            "question_and_evidence": [
                config.hook_target_seconds,
                round(duration_seconds * 0.62, 1),
            ],
            "scripture_adaptive": [
                config.scripture_citation_min_start_seconds,
                config.scripture_citation_max_end_seconds,
            ],
            "resolution_or_synthesis": [round(duration_seconds * 0.58, 1), cta_start],
            "single_action_cta": [cta_start, duration_seconds],
        }
    if config.duration_profile == "standard_45_55_v1":
        return {
            "hook": [0, 2],
            "orientation_or_stakes": [2, 8],
            "scripture": [8, 20],
            "development_and_reengagement": [20, 36],
            "resolution_or_synthesis": [32, 48],
            "single_action_cta": [45, 55],
        }
    return {
        "hook": [0, config.hook_target_seconds],
        "direct_jesus_quote": [
            config.scripture_citation_min_start_seconds,
            config.scripture_citation_max_end_seconds,
        ],
        "spoken_reference": [
            config.scripture_citation_max_end_seconds,
            duration_seconds,
        ],
        "cta": None,
    }


def _compact_cinematic_instructions(
    *,
    project_id: str,
    main_passage_spoken: str,
    required_hook: str,
    config: ShortsScriptConfig,
    main_agent_mode: bool,
) -> list[str]:
    return [
        (
            "No modo light, o agente principal produz o pacote diretamente, sem criar outro "
            "roteirista."
            if main_agent_mode
            else "O shorts_script_specialist produz o pacote isoladamente no staging."
        ),
        (
            "Leia somente inputs.compact_context e use uma única scripture_candidate marcada "
            "como fala direta de Jesus."
        ),
        (
            "Use cinematic_jesus_devotional_v1. O modo deve ser devotional_application ou "
            "pastoral_encouragement e a postura deve ser devotional."
        ),
        (
            "O vídeo deve parecer um momento emocional retirado de um filme épico sobre Jesus, "
            "não uma explicação bíblica, sermão, aula, lista de conselhos ou narração de história."
        ),
        (
            "A primeira seção contém somente o hook curado para esta passagem, sem reescrever, "
            f"encurtar ou completar: '{required_hook}'."
        ),
        (
            "Logo após o hook, cite o excerpt literal de Jesus. A seção bíblica deve conter "
            "somente o versículo entre aspas curvas, sem 'Em', sem 'lemos', sem explicação e sem "
            "referência antes da fala."
        ),
        (
            "A última seção contém somente a referência falada depois das aspas: "
            f"'{main_passage_spoken}.' Não use números no formato Livro X:Y."
        ),
        (
            "Não crie CTA, pedido de comentário, inscrição, compartilhamento, despedida ou "
            "resumo. Deixe strategy.cta e reason_to_subscribe vazios e comment_prompt nulo."
        ),
        (
            "Preencha strategy.cinematic_devotional, deixe creative_direction, retention_story e "
            "narrative_spine nulos e use exatamente três seções: opening, "
            "biblical_narrative e closing."
        ),
        (
            "Use exatamente três retention_beats: hook, evidence e reference, cobrindo toda a "
            "timeline. Cada beat declara informação e mudança visual sem inventar nova fala."
        ),
        (
            f"O roteiro completo deve ter de {config.min_words} a {config.max_words} palavras. "
            f"O excerpt literal pode ter no máximo {config.max_literal_verse_words} palavras e "
            "não pode ser cortado, parafraseado ou completado. Se faltar duração, o pacote deve "
            "falhar; nunca reserve ou peça silêncio para preencher o vídeo."
        ),
        (
            "A scripture_candidate pode conter uma faixa contínua de dois ou mais versículos "
            "quando a sequência for necessária ou benéfica para preservar sentido e continuidade. "
            "Use integralmente o excerpt verificado, mantenha a referência exata da faixa e "
            "respeite os limites de palavras e tempo; nunca amplie a citação apenas para preencher "
            "duração."
            if config.allow_contiguous_multi_verse_citations
            else "Use somente uma referência de versículo único neste projeto."
        ),
        (
            "As direções visuais preservam a reverência e descrevem um único momento, um único "
            "ambiente e uma ação principal por imagem. O estilo é aplicado depois pelo perfil "
            "visual do projeto."
        ),
        (
            "A revisão bíblica independente deve confirmar que a fala é atribuída diretamente "
            "a Jesus e que o excerpt coincide com a fonte."
        ),
        (
            "Repita job_id e input_hash exatamente, grave somente o package no staging e "
            "execute preflight.command até valid=true."
        ),
        "Não altere project.json, aprovações, manifestos ou arquivos fora do staging.",
        f"Projeto em validação: {project_id}.",
    ]


def _target_scene_instruction(config: ShortsScriptConfig) -> str:
    if config.target_scene_min == 8 and config.target_scene_max == 10:
        return "oito a dez cenas"
    return f"{config.target_scene_min} a {config.target_scene_max} cenas"


def _retention_timeline_seconds(
    duration_seconds: int,
    config: ShortsScriptConfig,
) -> dict[str, list[float | int]]:
    if config.performance_profile == "feed_depth_45_60_v1":
        cta_start = round(duration_seconds - config.cta_max_seconds, 1)
        return {
            "cold_open": [0, config.hook_target_seconds],
            "conflict_or_action": [config.hook_target_seconds, 8],
            "scripture": [
                config.scripture_citation_min_start_seconds,
                config.scripture_citation_max_end_seconds,
            ],
            "progression_and_reengagement": [20, round(duration_seconds * 0.58, 1)],
            "payoff_and_application": [round(duration_seconds * 0.58, 1), cta_start],
            "single_action_cta": [cta_start, duration_seconds],
        }
    if config.duration_profile == "standard_45_55_v1":
        return {
            "cold_open": [0, 2],
            "conflict_or_action": [2, 8],
            "scripture": [8, 20],
            "progression_and_reengagement": [20, 32],
            "payoff_and_application": [32, 45],
            "single_action_cta": [45, 55],
        }
    creative = _short_timeline_seconds(duration_seconds, config)
    return {
        "cold_open": creative["hook"],
        "conflict_or_action": creative["orientation_or_stakes"],
        "scripture": creative["scripture"],
        "progression_and_reengagement": creative["development_and_reengagement"],
        "payoff_and_application": creative["resolution_or_synthesis"],
        "single_action_cta": creative["single_action_cta"],
    }


def spoken_biblical_reference(reference: str) -> str:
    """Converte uma referência compacta para a forma natural exigida na locução."""
    normalized = " ".join(reference.split())
    match = _BIBLICAL_REFERENCE_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"Referência bíblica sem formato reconhecido para locução: {reference!r}.")
    book = match.group("book")
    chapter = match.group("chapter")
    verse = match.group("verse")
    end_verse = match.group("end_verse")
    if end_verse:
        return f"{book}, capítulo {chapter}, versículos {verse} a {end_verse}"
    return f"{book}, capítulo {chapter}, versículo {verse}"


def _literal_opening_candidates(layout: ProjectLayout) -> list[SourceEntry]:
    sources = read_model(layout.sources, SourceLibrary)
    return [
        source
        for source in sources.entries
        if source.kind == "biblical_text"
        and source.verified
        and source.passage
        and source.translation
        and source.excerpt
        and source.author_or_publisher
        and source.accessed_at
        and source.rights_note
    ]


def _require_literal_opening_source(layout: ProjectLayout) -> list[SourceEntry]:
    candidates = _literal_opening_candidates(layout)
    if candidates:
        return candidates
    raise ValueError(
        "O roteiro de Short exige ao menos uma fonte biblical_text verificada com passage, "
        "translation, excerpt, author_or_publisher, accessed_at e rights_note. Registre o "
        "texto exato de um versículo conhecido, curto e claro antes de preparar o roteiro."
    )


def _uses_scroll_stop_contract(layout: ProjectLayout) -> bool:
    return layout.load_manifest().config.images.opening_visual_standard == "scroll_stop_concrete_v1"


def _queue_schema_version(layout: ProjectLayout) -> str:
    return (
        SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION
        if _uses_scroll_stop_contract(layout)
        else LEGACY_SHORTS_SCRIPT_QUEUE_SCHEMA_VERSION
    )


def _stable_inputs(
    layout: ProjectLayout,
    *,
    performance_advisories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = {
        "briefing": layout.briefing,
        "sources": layout.sources,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Entradas ausentes para o roteirista de Shorts: " + ", ".join(missing)
        )
    project = layout.load_manifest()
    if project.video_format != "short":
        raise RuntimeError("A etapa shorts_script só é válida para projetos no formato Short.")
    scroll_stop = _uses_scroll_stop_contract(layout)
    if scroll_stop and performance_advisories is None:
        from pipeline_videos.analytics import compact_short_performance_advisories

        performance_advisories = compact_short_performance_advisories(layout)
    payload = {
        "algorithm": (
            SHORTS_SCRIPT_ALGORITHM_VERSION
            if scroll_stop
            else LEGACY_SHORTS_SCRIPT_ALGORITHM_VERSION
        ),
        "project_id": project.project_id,
        "title": project.title,
        "target_duration_seconds": project.target_duration_seconds,
        "config": project.config.shorts_script,
        "briefing": read_json(layout.briefing),
        "sources": read_json(layout.sources),
    }
    if scroll_stop:
        payload["opening_visual_standard"] = project.config.images.opening_visual_standard
        payload["visual_config"] = {
            "visual_standard_id": project.config.images.visual_standard_id,
            "scene_planning_standard": project.config.images.scene_planning_standard,
            "composition_safety_mode": project.config.images.composition_safety_mode,
            "spoken_reference_image_mode": (project.config.images.spoken_reference_image_mode),
            "spoken_reference_image_path": (project.config.images.spoken_reference_image_path),
        }
        payload["confirmed_performance_advisories"] = performance_advisories or []
    return payload


def shorts_script_input_hash(
    layout: ProjectLayout,
    *,
    performance_advisories: list[dict[str, Any]] | None = None,
) -> str:
    return sha256_value(_stable_inputs(layout, performance_advisories=performance_advisories))


def _frozen_performance_advisories(
    layout: ProjectLayout,
    queue: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the external evidence frozen when this queue was materialized."""

    raw = queue.get("frozen_performance_advisories")
    if raw is None and layout.shorts_script_context.is_file():
        context = read_json(layout.shorts_script_context)
        if isinstance(context, dict):
            raw = context.get("confirmed_performance_advisories", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("A fila contém performance advisories congelados inválidos.")
    return deepcopy(raw)


def _baseline_hash(layout: ProjectLayout) -> str | None:
    return sha256_file(layout.script) if layout.script.is_file() else None


def _recent_decisions(layout: ProjectLayout, *, limit: int = 12) -> list[dict[str, Any]]:
    if not layout.decisions.is_file():
        return []
    decisions: list[dict[str, Any]] = []
    for line in layout.decisions.read_text(encoding="utf-8").splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {"raw": line}
        if isinstance(value, dict):
            decisions.append(value)
    return decisions


def _shorts_context_payload(
    layout: ProjectLayout,
    literal_candidates: list[SourceEntry],
    *,
    performance_advisories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    sources = read_model(layout.sources, SourceLibrary)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "project": {
            "project_id": project.project_id,
            "title": project.title,
            "target_duration_seconds": project.target_duration_seconds,
            "narrative_standard": config.narrative_standard,
            "citation_policy": _shorts_citation_policy(config),
        },
        "briefing": read_json(layout.briefing),
        "sources": [source.model_dump(mode="json") for source in sources.entries],
        "scripture_candidates": [source.source_id for source in literal_candidates],
        "verse_opening_candidates": [source.source_id for source in literal_candidates],
        "recent_decisions": _recent_decisions(layout),
    }
    if layout.script.is_file():
        payload["current_script_for_critique"] = read_json(layout.script)
    if layout.biblical_review.is_file():
        payload["current_biblical_review_for_critique"] = read_json(layout.biblical_review)
    if _uses_scroll_stop_contract(layout) and performance_advisories:
        payload["confirmed_performance_advisories"] = performance_advisories
    return payload


def _shorts_citation_policy(config: ShortsScriptConfig) -> dict[str, Any]:
    return {
        "allow_contiguous_multi_verse_citations": (config.allow_contiguous_multi_verse_citations),
        "multi_verse_citation_policy": config.multi_verse_citation_policy,
        "verified_single_excerpt_required": True,
        "exact_range_reference_required": True,
        "max_literal_verse_words": config.max_literal_verse_words,
        "respect_timing_window": True,
        "padding_only_expansion_forbidden": True,
    }


_STRONG_COMPACT_BOUNDARY_RE = re.compile(r"[.!?;:]+[\"'’”]*?(?=\s|$)")
_COMMA_COMPACT_BOUNDARY_RE = re.compile(r",+[\"'’”]*?(?=\s|$)")
_SAFE_COMPACT_END_RE = re.compile(r"[.!?;:,]+[\"'’”]*$")


def _split_compact_at_matches(text: str, pattern: re.Pattern[str]) -> list[str]:
    chunks: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        chunk = text[cursor : match.end()].strip()
        if chunk:
            chunks.append(chunk)
        cursor = match.end()
    remainder = text[cursor:].strip()
    if remainder:
        chunks.append(remainder)
    return chunks


def _compact_word_count(text: str) -> int:
    return len(text.split())


def _best_compact_comma_split(chunks: list[str]) -> tuple[int, list[str]] | None:
    candidates: list[tuple[tuple[int, int, int], int, list[str]]] = []
    for index, chunk in enumerate(chunks):
        split = _split_compact_at_matches(chunk, _COMMA_COMPACT_BOUNDARY_RE)
        if len(split) < 2:
            continue
        for boundary in range(1, len(split)):
            left = " ".join(split[:boundary])
            right = " ".join(split[boundary:])
            left_words = _compact_word_count(left)
            right_words = _compact_word_count(right)
            if min(left_words, right_words) < 3:
                continue
            score = (
                min(left_words, right_words),
                left_words + right_words,
                -abs(left_words - right_words),
            )
            candidates.append((score, index, [left, right]))
    if not candidates:
        return None
    _score, index, split = max(candidates, key=lambda item: item[0])
    return index, split


def _balanced_compact_chunks(text: str, count: int) -> list[str]:
    """Split a compact quotation only at natural linguistic boundaries.

    ``count`` is an upper bound.  If the quotation does not contain enough safe
    punctuation, the visual plan intentionally uses fewer scenes instead of
    cutting a clause at an arbitrary word.
    """

    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("A fala direta de Jesus não pode estar vazia.")
    count = max(1, count)
    chunks = _split_compact_at_matches(normalized, _STRONG_COMPACT_BOUNDARY_RE)
    if not chunks:
        chunks = [normalized]

    while len(chunks) > count:
        merge_at = min(
            range(len(chunks) - 1),
            key=lambda index: (
                _compact_word_count(chunks[index]) + _compact_word_count(chunks[index + 1])
            ),
        )
        chunks[merge_at : merge_at + 2] = [f"{chunks[merge_at]} {chunks[merge_at + 1]}"]

    while len(chunks) < count:
        candidate = _best_compact_comma_split(chunks)
        if candidate is None:
            break
        index, split = candidate
        chunks[index : index + 1] = split
    return chunks


def _has_safe_compact_boundary(text: str) -> bool:
    return _SAFE_COMPACT_END_RE.search(text.strip()) is not None


_OPENING_TRANSITION_BY_MOTIF: dict[str, tuple[str, str, str]] = {
    "courageous_next_step": (
        "body_translation",
        "um pé permanece no apoio seguro e a mão prende o último poste",
        "o pé alcança a ponte enquanto a mão solta o poste",
    ),
    "contextual_decision": (
        "choice_executed",
        "dois objetos permanecem alinhados diante das mãos indecisas",
        "um objeto é reposicionado sob a luz enquanto o outro fica para trás",
    ),
    "mercy_with_truth": (
        "interaction_change",
        "a pedra permanece erguida diante da pessoa ferida",
        "a pedra fica abaixada enquanto a faixa limpa alcança o ferimento",
    ),
    "release_overload": (
        "object_state_change",
        "muitas tigelas instáveis ocupam os dois braços",
        "uma tigela permanece nas mãos e as demais repousam em superfície segura",
    ),
    "peace_before_circumstances": (
        "choice_executed",
        "a mão aperta a tranca e o corpo permanece voltado para a tempestade",
        "a mão relaxa sobre a tranca e o corpo se volta para Jesus",
    ),
    "hope_beyond_now": (
        "environment_change",
        "o ferrolho mantém a porta escura completamente fechada",
        "o ferrolho está retirado e uma fresta de luz atravessa a porta",
    ),
    "remain_connected": (
        "interaction_change",
        "o ramo aparece tensionado para longe da videira",
        "o ramo volta a tocar a videira e é preso pelas mãos",
    ),
    "guidance_in_confusion": (
        "object_state_change",
        "o mapa permanece desalinhado diante de várias trilhas",
        "o mapa fica alinhado com um único caminho iluminado",
    ),
    "inspect_the_fruit": (
        "object_state_change",
        "o fruto de casca intacta esconde o interior",
        "o fruto aberto expõe o interior estragado ao lado do fruto saudável",
    ),
    "joy_through_grief": (
        "environment_change",
        "a lágrima permanece no rosto diante da janela fechada",
        "a janela está aberta para a luz enquanto a lágrima é enxugada",
    ),
    "need_placed_in_prayer": (
        "object_state_change",
        "o saco pesado permanece apertado contra o corpo",
        "o saco repousa no chão e as duas mãos aparecem abertas",
    ),
    "good_without_applause": (
        "interaction_change",
        "o pão permanece nas mãos longe da pessoa necessitada",
        "o pão passa pelas mãos da porta lateral enquanto a multidão olha para outro lado",
    ),
    "uncover_the_light": (
        "environment_change",
        "a tigela de barro cobre a lamparina e mantém o rosto no escuro",
        "a lamparina descoberta lança luz sobre o rosto próximo",
    ),
    "truth_practiced": (
        "object_state_change",
        "a instrução permanece dobrada e escondida sob a mesa",
        "a instrução está aberta sob a luz diante da porta",
    ),
    "costly_love": (
        "body_translation",
        "a pessoa permanece atrás da porta trancada com a faixa nas mãos",
        "a porta está aberta e a pessoa atravessa com a faixa em direção ao amigo",
    ),
    "held_not_abandoned": (
        "interaction_change",
        "a mão se prende apenas à corda desgastada diante do vazio",
        "a corda está solta e a mão segura quem a alcançou",
    ),
    "word_that_remains": (
        "object_state_change",
        "o pergaminho está solto entre objetos levados pelo vento",
        "as mãos firmam o pergaminho sobre a pedra enquanto os objetos se afastam",
    ),
    "confession_to_action": (
        "choice_executed",
        "o medalhão permanece erguido diante da porta sem movimento",
        "o medalhão repousa na mesa e o primeiro passo atravessa a porta",
    ),
    "hunger_choice": (
        "object_state_change",
        "as tigelas vazias cercam as mãos ainda afastadas do pão",
        "as tigelas ficam para trás e as mãos alcançam o pão oferecido",
    ),
    "hidden_cost": (
        "choice_executed",
        "a lembrança de família avança em direção à balança cheia de moedas",
        "a mão retira a lembrança enquanto as moedas ainda caem da balança",
    ),
    "difference_without_enemy": (
        "interaction_change",
        "duas ferramentas permanecem erguidas sobre a linha de carvão",
        "as ferramentas estão abaixadas e duas mãos apagam juntas a linha",
    ),
    "recognize_who_is_present": (
        "body_translation",
        "o espelho cobre o campo de visão enquanto Jesus permanece além da porta",
        "o espelho está abaixado e o corpo se orienta para Jesus presente",
    ),
    "kingdom_beyond_power": (
        "choice_executed",
        "a pessoa permanece voltada para o pequeno trono com o pão intacto",
        "o trono fica vazio e a pessoa reparte o pão ajoelhada no chão",
    ),
    "protect_small_beginning": (
        "object_state_change",
        "a muda pequena permanece suspensa sobre o recipiente de descarte",
        "a muda está plantada no solo e recebe apoio das mãos",
    ),
    "service_over_status": (
        "body_translation",
        "a pessoa permanece de pé junto ao assento elevado e à bacia",
        "a pessoa está ajoelhada com a bacia junto aos pés feridos",
    ),
    "found_on_the_search": (
        "body_translation",
        "o corpo se afasta pela trilha errada enquanto a lamparina se aproxima",
        "o corpo muda de direção para o caminho indicado pela lamparina",
    ),
    "notice_the_present_gift": (
        "object_state_change",
        "a lista de comparação permanece aberta sobre o presente fechado",
        "a lista está fechada e o presente começa a ser aberto pelas mãos",
    ),
    "follow_in_action": (
        "body_translation",
        "a corda mantém o corpo preso aos fardos fora da trilha",
        "a corda está desatada e o primeiro pé ocupa a trilha aberta",
    ),
    "wake_and_prepare": (
        "environment_change",
        "a lamparina permanece apagada e as sandálias ainda estão soltas",
        "a lamparina está acesa e as mãos apertam as sandálias",
    ),
    "open_table": (
        "object_state_change",
        "a divisória bloqueia a cadeira vazia diante do viajante",
        "a divisória está removida e a cadeira avança para junto da mesa",
    ),
}


def _opening_contract(
    motif_id: str,
    scene_description: str,
    dominant_subject_or_object: str,
    observable_action: str,
    concrete_tension: str,
    *,
    shot_scale: str = "close",
    action_kind: str | None = None,
    initial_state: str | None = None,
    resulting_state: str | None = None,
) -> dict[str, str]:
    transition = _OPENING_TRANSITION_BY_MOTIF.get(motif_id)
    if transition is None and (
        action_kind is None or initial_state is None or resulting_state is None
    ):
        raise ValueError(f"Motivo de abertura sem transição visual persistida: {motif_id}")
    resolved_kind, resolved_initial, resolved_resulting = transition or (
        action_kind,
        initial_state,
        resulting_state,
    )
    assert resolved_kind is not None
    assert resolved_initial is not None
    assert resolved_resulting is not None
    return {
        "motif_id": motif_id,
        "scene_description": scene_description,
        "dominant_subject_or_object": dominant_subject_or_object,
        "observable_action": observable_action,
        "action_kind": action_kind or resolved_kind,
        "initial_state": initial_state or resolved_initial,
        "resulting_state": resulting_state or resolved_resulting,
        "concrete_tension": concrete_tension,
        "shot_scale": shot_scale,
    }


def _marker_matches_signal(signal: str, marker: str) -> bool:
    """Match editorial markers as whole token sequences, never as substrings."""

    signal_tokens = re.findall(r"[^\W_]+", signal.casefold(), flags=re.UNICODE)
    marker_tokens = re.findall(r"[^\W_]+", marker.casefold(), flags=re.UNICODE)
    if not marker_tokens or len(marker_tokens) > len(signal_tokens):
        return False
    width = len(marker_tokens)
    return any(
        signal_tokens[index : index + width] == marker_tokens
        for index in range(len(signal_tokens) - width + 1)
    )


def _compact_opening_fallback(
    hook: str,
    *,
    central_theme: str = "",
    human_conflict: str = "",
    central_question: str = "",
    verse_text: str = "",
) -> dict[str, str]:
    """Map the sealed editorial theme to a concrete, reproducible opening action."""

    theme = _normalized(central_theme)
    combined = _normalized(
        " ".join((central_theme, hook, human_conflict, central_question, verse_text))
    )
    rules = (
        (
            ("fome",),
            _opening_contract(
                "hunger_choice",
                "Close de uma pessoa empurrando tigelas vazias para longe e estendendo as mãos "
                "ao pão que Jesus oferece; a fome e a escolha ocupam o mesmo instante.",
                "as mãos entre tigelas vazias e o pão oferecido",
                "afasta as tigelas vazias e alcança o pão",
                "continuar buscando no que não satisfaz ou receber alimento verdadeiro",
            ),
        ),
        (
            ("grandeza que jesus mede", "grandeza que escolhe servir"),
            _opening_contract(
                "service_over_status",
                "Plano médio de uma pessoa deixando um assento elevado e ajoelhando-se com uma "
                "bacia para cuidar dos pés feridos de outra pessoa.",
                "a pessoa, o assento elevado e a bacia",
                "abandona o lugar de destaque e se ajoelha para servir",
                "proteger o próprio status ou cuidar de quem está abaixo",
                shot_scale="medium",
            ),
        ),
        (
            ("liberdade", "verdade que confronta"),
            _opening_contract(
                "truth_practiced",
                "Close de uma pessoa retirando debaixo da mesa uma instrução dobrada e abrindo-a "
                "sob a luz, com a porta da decisão já diante dela.",
                "as mãos, a instrução aberta e a porta",
                "para de esconder a instrução e a abre antes de atravessar",
                "apenas ouvir a verdade ou responder a ela",
            ),
        ),
        (
            ("mesa do reino",),
            _opening_contract(
                "open_table",
                "Plano médio de uma pessoa removendo uma divisória da mesa e puxando uma cadeira "
                "para o viajante que permanecia do lado de fora.",
                "a divisória sendo retirada e a cadeira vazia",
                "retira a barreira e abre um lugar à mesa",
                "preservar a fronteira humana ou acolher quem foi deixado de fora",
                shot_scale="medium",
            ),
        ),
        (
            ("palavra de jesus",),
            _opening_contract(
                "word_that_remains",
                "Close de mãos firmando um pergaminho sobre a pedra enquanto cartazes e objetos "
                "ao redor são levados por uma rajada forte.",
                "as mãos, o pergaminho e os objetos em movimento",
                "firma o pergaminho enquanto o cenário ao redor se desfaz",
                "tratar o que passa como permanente ou sustentar o que permanece",
            ),
        ),
        (
            ("resposta verdadeira",),
            _opening_contract(
                "confession_to_action",
                "Close de uma pessoa pousando um medalhão religioso sobre a mesa e movendo o pé "
                "através de uma porta onde alguém espera ajuda.",
                "o medalhão abandonado e o pé atravessando a porta",
                "pousa o símbolo sobre a mesa e executa a primeira ação de obediência",
                "manter apenas a aparência ou transformar a confissão em resposta",
            ),
        ),
        (
            ("alegria",),
            _opening_contract(
                "joy_through_grief",
                "Close de uma pessoa enxugando uma lágrima com uma mão enquanto a outra abre uma "
                "janela para a primeira luz do dia.",
                "o rosto com lágrima e a janela se abrindo",
                "enxuga a lágrima e abre a janela sem esconder a tristeza",
                "fingir que não dói ou permitir que a esperança atravesse a dor",
            ),
        ),
        (
            ("prioridades do reino",),
            _opening_contract(
                "hidden_cost",
                "Close de moedas derramando de uma balança enquanto a pessoa puxa de volta a mão "
                "que quase entregava uma lembrança de família em troca.",
                "a balança, as moedas e a lembrança quase entregue",
                "interrompe a troca quando o custo escondido fica visível",
                "reter o ganho aparente ou preservar o que ele estava consumindo",
            ),
        ),
        (
            ("atenção e preparo",),
            _opening_contract(
                "wake_and_prepare",
                "Close de uma pessoa despertando, acendendo uma lamparina e apertando as sandálias "
                "enquanto sinais da mudança já aparecem pela janela.",
                "a lamparina acesa e as mãos ajustando as sandálias",
                "acende a luz e começa a se preparar imediatamente",
                "apenas conhecer o aviso ou responder antes que o momento chegue",
            ),
        ),
        (
            ("bondade", "generosidade"),
            _opening_contract(
                "good_without_applause",
                "Plano médio de uma pessoa entregando pão por uma porta lateral enquanto a "
                "multidão e os aplausos permanecem voltados para outro lugar.",
                "as mãos entregando pão fora do foco da multidão",
                "oferece o alimento sem se mover em direção aos aplausos",
                "ser visto ou atender a necessidade real",
                shot_scale="medium",
            ),
        ),
        (
            ("coragem", "fé quando a dúvida", "permanecer fiel"),
            _opening_contract(
                "courageous_next_step",
                "Close de um pé já atravessando uma ponte estreita enquanto a mão solta o último "
                "poste seguro; o medo continua legível no rosto.",
                "o pé sobre a ponte e a mão soltando o poste",
                "solta o apoio e executa o próximo passo apesar do medo",
                "deixar a pressão decidir ou permanecer fiel em movimento",
            ),
        ),
        (
            ("descanso", "foco quando", "ansiedade pelo amanhã"),
            _opening_contract(
                "release_overload",
                "Close de uma pessoa tentando equilibrar muitas tigelas de barro e, no instante em "
                "que uma começa a cair, pousando todas menos a única necessária.",
                "as mãos sobrecarregadas e as tigelas instáveis",
                "interrompe o malabarismo e escolhe uma única responsabilidade",
                "controlar tudo ao mesmo tempo ou receber limite e prioridade",
            ),
        ),
        (
            ("discernimento",),
            _opening_contract(
                "inspect_the_fruit",
                "Close de uma faca abrindo um fruto de casca impecável e revelando o interior "
                "estragado, enquanto outro fruto saudável permanece ao lado.",
                "o fruto aberto e o contraste entre casca e interior",
                "corta o fruto e examina o que a aparência escondia",
                "confiar na superfície ou reconhecer os frutos reais",
            ),
        ),
        (
            ("esperança",),
            _opening_contract(
                "hope_beyond_now",
                "Close de uma pessoa retirando o ferrolho de uma porta escura enquanto a luz da "
                "manhã atravessa a primeira fresta antes de o cenário mudar por completo.",
                "a mão no ferrolho e a fresta de luz",
                "abre a porta antes de conseguir ver todo o caminho",
                "encerrar a história no momento atual ou agir em direção ao que ainda virá",
            ),
        ),
        (
            ("misericórdia", "perdão"),
            _opening_contract(
                "mercy_with_truth",
                "Plano médio de uma pessoa baixando a pedra que segurava e estendendo uma "
                "faixa limpa ao ferido, sem apagar a porta quebrada entre os dois.",
                "a pedra baixada, a faixa e a porta quebrada",
                "abandona o gesto de ferir e começa a cuidar sem esconder o dano",
                "repetir a violência ou responder com verdade e misericórdia",
                shot_scale="medium",
            ),
        ),
        (
            ("amor reconhecível",),
            _opening_contract(
                "costly_love",
                "Plano médio de uma pessoa retirando a tranca da própria porta e levando uma faixa "
                "ao amigo ferido do lado de fora.",
                "a tranca aberta e a faixa nas mãos",
                "abre a porta e atravessa em direção ao amigo ferido",
                "endurecer para não sofrer ou tornar o amor visível em uma escolha",
                shot_scale="medium",
            ),
        ),
        (
            ("espírito santo",),
            _opening_contract(
                "guidance_in_confusion",
                "Close de uma pessoa num cruzamento girando um mapa confuso até alinhá-lo com um "
                "único caminho iluminado diante dela.",
                "o mapa desalinhado, as mãos e o caminho iluminado",
                "reorienta o mapa e escolhe a direção que finalmente se torna legível",
                "continuar girando sem ajuda ou receber direção e avançar",
            ),
        ),
        (
            ("chamado de deus", "cuidado de jesus com quem se perdeu"),
            _opening_contract(
                "found_on_the_search",
                "Plano médio de uma pessoa perdida entre duas trilhas interrompendo a fuga ao ver "
                "Jesus aproximar uma lamparina e indicar o caminho de volta.",
                "a pessoa entre as trilhas e a lamparina aproximando-se",
                "para de se afastar e volta o corpo em direção ao caminho indicado",
                "concluir que está longe demais ou responder a quem já veio procurar",
                shot_scale="medium",
            ),
        ),
        (
            ("cuidado do pai", "segurança na presença", "valor que o medo"),
            _opening_contract(
                "held_not_abandoned",
                "Close de uma pessoa soltando uma corda desgastada no instante em que Jesus firma "
                "a mão dela, com o vazio ainda visível atrás.",
                "as duas mãos, a corda desgastada e o vazio",
                "solta a corda falha e segura a mão que a alcançou",
                "agir como se estivesse abandonada ou confiar no cuidado presente",
            ),
        ),
        (
            ("reino cresce",),
            _opening_contract(
                "protect_small_beginning",
                "Close de uma mão prestes a descartar uma muda minúscula, interrompendo o gesto e "
                "plantando-a em solo preparado.",
                "a muda pequena entre a mão e o solo",
                "retira a muda do descarte e a planta com cuidado",
                "abandonar o que parece pequeno ou sustentar seu crescimento",
            ),
        ),
        (
            ("reino de deus além",),
            _opening_contract(
                "kingdom_beyond_power",
                "Plano médio de uma pessoa virando as costas para um pequeno trono ornamentado e "
                "ajoelhando-se para repartir pão com alguém no chão.",
                "o trono vazio e o pão sendo repartido",
                "abandona o símbolo de poder e começa a repartir o pão",
                "procurar o Reino no domínio visível ou reconhecê-lo em outra lógica",
                shot_scale="medium",
            ),
        ),
        (
            ("oração",),
            _opening_contract(
                "need_placed_in_prayer",
                "Close de uma pessoa interrompendo o transporte de um saco pesado, pousando-o ao "
                "lado de um lugar de oração e abrindo as mãos.",
                "o saco pousado e as mãos que deixam de apertá-lo",
                "coloca o peso no chão e abre as mãos em oração",
                "continuar carregando a necessidade sozinha ou colocá-la diante de Deus",
            ),
        ),
        (
            ("paz de jesus", "oferece paz", "tenham paz", "aflição"),
            _opening_contract(
                "peace_before_circumstances",
                "Close de uma mão soltando com cuidado a tranca de uma porta durante a tempestade, "
                "enquanto a pessoa volta o rosto para Jesus antes de sair.",
                "a mão na tranca, a tempestade e o olhar que muda de direção",
                "relaxa a mão e se volta para Jesus antes de atravessar",
                "esperar o cenário melhorar ou receber paz antes da mudança",
            ),
        ),
        (
            ("permanecer e seguir",),
            _opening_contract(
                "follow_in_action",
                "Plano médio de uma pessoa soltando a corda que a prende a vários fardos e "
                "colocando o primeiro pé na trilha já aberta por Jesus.",
                "a corda desprendida, os fardos e o primeiro passo",
                "desata a corda e começa a seguir pela trilha",
                "manter a intenção parada ou transformar seguir em movimento concreto",
                shot_scale="medium",
            ),
        ),
        (
            ("permanecer em jesus", "permanecer nele"),
            _opening_contract(
                "remain_connected",
                "Close de mãos que param de puxar um ramo para longe e o prendem novamente à "
                "videira viva antes que ele se rompa.",
                "o ramo tensionado, as mãos e a videira",
                "interrompe a força própria e reconecta o ramo à videira",
                "insistir separado até quebrar ou permanecer ligado à fonte",
            ),
        ),
        (
            ("quem jesus declara",),
            _opening_contract(
                "recognize_who_is_present",
                "Plano médio de uma pessoa baixando um pequeno espelho que cobria seu campo de "
                "visão quando Jesus atravessa a porta e se aproxima.",
                "o espelho baixado e Jesus entrando no campo visual",
                "abaixa o reflexo e direciona toda a atenção para Jesus presente",
                "manter Jesus como ideia distante ou reconhecer quem está diante dela",
                shot_scale="medium",
            ),
        ),
        (
            ("unidade",),
            _opening_contract(
                "difference_without_enemy",
                "Plano médio de duas pessoas baixando as ferramentas erguidas e apagando juntas a "
                "linha de carvão que as separava no chão.",
                "as ferramentas baixadas e a linha divisória",
                "interrompem o confronto e removem a marca de separação",
                "transformar diferença em inimigo ou reconhecer uma causa compartilhada",
                shot_scale="medium",
            ),
        ),
        (
            ("gratidão",),
            _opening_contract(
                "notice_the_present_gift",
                "Close de uma pessoa fechando uma lista de comparação e finalmente abrindo o "
                "presente que permanecia esquecido ao lado.",
                "a lista fechada e o presente ainda embrulhado",
                "interrompe a comparação e começa a abrir o que já recebeu",
                "continuar medindo a vida dos outros ou perceber a dádiva presente",
            ),
        ),
        (
            ("testemunho",),
            _opening_contract(
                "uncover_the_light",
                "Close de mãos removendo de uma vez a tigela de barro que cobria uma lamparina, "
                "fazendo a luz alcançar o rosto de quem esperava no escuro.",
                "a lamparina descoberta e o rosto alcançado pela luz",
                "retira a cobertura e expõe a luz imediatamente",
                "proteger a luz escondendo-a ou permitir que ela cumpra sua missão",
            ),
        ),
    )
    for signal in (theme, combined):
        for markers, contract in rules:
            if any(_marker_matches_signal(signal, marker) for marker in markers):
                return contract
    return _opening_contract(
        "contextual_decision",
        "Plano médio de uma pessoa interrompendo uma escolha entre dois objetos concretos sobre a "
        "mesa e voltando um deles para a luz antes de agir.",
        "as mãos, os dois objetos e a decisão interrompida",
        "para o gesto automático e reposiciona a escolha diante da luz",
        "repetir a resposta antiga ou agir sobre a pergunta central da passagem",
        shot_scale="medium",
    )


def _compact_narration_blocks(
    *,
    source: SourceEntry,
    briefing: Briefing,
    spoken_reference: str,
    config: ShortsScriptConfig,
) -> list[dict[str, Any]]:
    assert source.excerpt is not None
    scene_count = config.target_scene_max
    verse_chunks = _balanced_compact_chunks(source.excerpt, max(1, scene_count - 2))
    opening_fallback = _compact_opening_fallback(
        source.compact_hook,
        central_theme=briefing.central_theme,
        human_conflict=briefing.human_conflict,
        central_question=briefing.central_question,
        verse_text=source.excerpt,
    )
    fallbacks = (
        opening_fallback["scene_description"],
        "Jesus ora com um pequeno grupo reunido ao ar livre.",
        "Jesus prega com serenidade a pessoas atentas em uma colina.",
        "Moradas acolhedoras aparecem sob um céu luminoso, como símbolo de esperança.",
        "Um jardim amplo e pacífico evoca o Éden sob a luz da manhã.",
        "Jesus caminha ao lado de pessoas renovadas em uma paisagem aberta.",
        "Uma trilha iluminada conduz um pequeno grupo para além da sombra.",
        "Pessoas se reúnem em paz enquanto Jesus permanece próximo delas.",
        "A luz atravessa árvores antigas e alcança pessoas em oração.",
    )
    blocks: list[dict[str, Any]] = [
        {
            "block_id": "block-001",
            "section_id": "section-001",
            "narration": source.compact_hook,
            "creative_concept_required": True,
            "fallback_scene_description": fallbacks[0],
            "fallback_visual_relation": "contextual",
            "fallback_attention_contract": opening_fallback,
        }
    ]
    for index, chunk in enumerate(verse_chunks, start=2):
        narration = chunk
        if index == 2:
            narration = f"“{narration}"
        if index == len(verse_chunks) + 1:
            narration = f"{narration}”"
        blocks.append(
            {
                "block_id": f"block-{index:03d}",
                "section_id": "section-002",
                "narration": narration,
                "creative_concept_required": True,
                "fallback_scene_description": fallbacks[(index - 1) % len(fallbacks)],
                "fallback_visual_relation": (
                    "literal" if index == 2 else "contextual" if index % 2 else "symbolic"
                ),
            }
        )
    blocks.append(
        {
            "block_id": f"block-{len(blocks) + 1:03d}",
            "section_id": "section-003",
            "narration": f"{spoken_reference}.",
            "creative_concept_required": False,
            "fallback_scene_description": (
                "Uma Bíblia aberta repousa sobre uma mesa de madeira diante de uma paisagem serena."
            ),
            "fallback_visual_relation": "contextual",
        }
    )
    return blocks


def _compact_visual_context_payload(
    layout: ProjectLayout,
    *,
    briefing: Briefing,
    blocks: list[dict[str, Any]],
    performance_advisories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project = layout.load_manifest()
    creative_blocks = [item for item in blocks if item["creative_concept_required"]]
    first_block_id = creative_blocks[0]["block_id"] if creative_blocks else None
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "project_id": project.project_id,
        "task": "Describe one distinct visual scene for each creative narration block.",
        "creative_direction": {
            "central_theme": briefing.central_theme,
            "audience": briefing.audience,
            "emotional_intent": briefing.emotional_intent,
            "visual_notes": briefing.visual_notes,
        },
        "performance_profile_contract": _short_performance_profile_contract(
            project.config.shorts_script
        ),
        "visual_standard_id": project.config.images.visual_standard_id,
        "allowed_relations": ["literal", "contextual", "symbolic"],
        "blocks": [
            {
                "block_id": item["block_id"],
                "narration": item["narration"],
                "fallback_scene_description": item["fallback_scene_description"],
                "attention_role": (
                    "scroll_stop_opening"
                    if item["block_id"] == first_block_id
                    and project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
                    else "progression"
                ),
            }
            for item in creative_blocks
        ],
    }
    if (
        first_block_id is not None
        and project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
    ):
        payload["opening_attention_contract"] = {
            "standard": "scroll_stop_concrete_v1",
            "block_id": first_block_id,
            "required_output_fields": [
                "attention_role=scroll_stop_opening",
                "dominant_subject_or_object",
                "observable_action",
                "action_kind",
                "initial_state",
                "resulting_state",
                "concrete_tension",
                "shot_scale=close ou medium",
            ],
            "requirements": [
                "um sujeito ou objeto dominante",
                "uma ação observável já em curso",
                "um tipo de mudança física e estados inicial/final visualmente distintos",
                "tensão, escolha ou contraste concreto coerente com o hook",
                "emoção legível em menos de um segundo",
                "plano médio, close ou objeto específico dominante",
                "imagem e hook abrem a mesma pergunta que a fala de Jesus resolve",
                "um único foco legível em tela de celular, sem multidão ou fundo carregado",
                "contraste local suficiente para separar imediatamente sujeito e ação",
            ],
            "avoid": [
                "retrato contemplativo genérico",
                "Jesus apenas posando ou acolhendo sem conflito visível",
                "plano geral cujo ambiente domina o sujeito",
                "estímulo aleatório sem ligação com a passagem",
                "primeiro quadro escuro ou visualmente congestionado",
            ],
        }
    if _uses_scroll_stop_contract(layout) and performance_advisories:
        payload["confirmed_performance_advisories"] = performance_advisories
    return payload


def _compact_visual_plan_queue(
    layout: ProjectLayout,
    *,
    input_hash: str,
    baseline_hash: str | None,
    next_version: int,
    job_id: str,
    briefing: Briefing,
    source: SourceEntry,
    compact_reference: str,
    spoken_reference: str,
    config: ShortsScriptConfig,
    performance_advisories: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    project = layout.load_manifest()
    blocks = _compact_narration_blocks(
        source=source,
        briefing=briefing,
        spoken_reference=spoken_reference,
        config=config,
    )
    context = _compact_visual_context_payload(
        layout,
        briefing=briefing,
        blocks=blocks,
        performance_advisories=performance_advisories,
    )
    write_json(layout.shorts_script_context, context)
    payload: dict[str, Any] = {
        "schema_version": _queue_schema_version(layout),
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "job_id": job_id,
        "input_hash": input_hash,
        "baseline_script_hash": baseline_hash,
        "next_script_version": next_version,
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "frozen_performance_advisories": performance_advisories or [],
        "generation_mode": "deterministic_compact_visual_plan_v1",
        "output_contract": "compact_visual_plan_v1",
        "orchestration": {
            "mode": "subagent",
            "worker_id": "shorts-visual-planner",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "medium",
            "recommended_model": "gpt-5.6-sol",
            "context_policy": "visual_blocks_only",
            "fork_turns": "none",
        },
        "inputs": {
            "compact_context": relative_posix(layout.shorts_script_context, layout.root),
        },
        "outputs": {
            "package": relative_posix(layout.shorts_script_package_staging, layout.root),
        },
        "schemas": {
            "package": compact_json_schema(CompactVisualPlan.model_json_schema()),
        },
        "preflight": {
            "read_only": True,
            "command": f'uv run pipeline-videos script shorts-check-staging "{project.project_id}"',
            "success_condition": "valid=true",
            "returns_all_detectable_errors": True,
            "max_model_repairs": project.config.production.max_model_repairs_per_stage,
            "fallback_command": (
                f'uv run pipeline-videos script shorts-fallback "{project.project_id}"'
            ),
        },
        "deterministic_contract": {
            "source_id": source.source_id,
            "compact_reference": compact_reference,
            "spoken_reference": spoken_reference,
            "blocks": blocks,
            "biblical_review_mode": config.biblical_review_mode,
            "opening_visual_standard": project.config.images.opening_visual_standard,
            "performance_profile": _short_performance_profile_contract(config),
        },
        "instructions": [
            "Leia somente inputs.compact_context.",
            (
                "Devolva um conceito visual para cada block_id, na mesma ordem, sem "
                "reescrever a narração."
            ),
            "Varie personagens, ações, ambientes e relações literal, contextual ou simbólica.",
            "Descreva um único momento visual por conceito; o estilo será aplicado pelo pipeline.",
            (
                "No primeiro bloco, cumpra opening_attention_contract: comece no meio de uma "
                "ação concreta, com sujeito dominante e tensão visual alinhada ao hook; não use "
                "plano geral contemplativo nem gesto genérico de acolhimento. Preencha também "
                "todos os required_output_fields estruturados."
                if project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
                else "Preserve o padrão visual de abertura já salvo no projeto."
            ),
            "Repita job_id e input_hash exatamente e grave somente outputs.package.",
            (
                "Execute preflight.command; se houver erros, faça no máximo uma correção "
                "agregada. Persistindo erro, execute preflight.fallback_command e encerre."
            ),
            "Não altere project.json, roteiro, fontes, filas, manifestos ou aprovações.",
        ],
    }
    start_stage(layout, "shorts_script", input_hash, provider="deterministic-compact-visual-plan")
    write_json(layout.shorts_script_queue, payload)
    wait_stage(
        layout,
        "shorts_script",
        input_hash,
        "Aguardando somente os conceitos visuais do Short compacto.",
        [
            relative_posix(layout.shorts_script_queue, layout.root),
            relative_posix(layout.shorts_script_context, layout.root),
        ],
    )
    return payload


def prepare_shorts_script_queue(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    is_retention_v2 = config.narrative_standard == "retention_story_v2"
    is_creative_v3 = config.narrative_standard == "creative_retention_v3"
    is_compact_cinematic = config.duration_profile == "adaptive_compact_v1"
    is_high_stakes = config.editorial_profile == "high_stakes_biblical_question_v1"
    main_agent_mode = not config.use_specialist_subagent
    if not config.enabled:
        raise RuntimeError("A etapa especializada de roteiro está desativada neste projeto.")
    stage = project.stages["shorts_script"]
    if (
        not force
        and layout.shorts_script_queue.is_file()
        and layout.shorts_script_context.is_file()
        and stage.status in {StageStatus.WAITING_EXTERNAL, StageStatus.COMPLETED}
    ):
        queue = read_json(layout.shorts_script_queue)
        if isinstance(queue, dict):
            frozen_advisories = _frozen_performance_advisories(layout, queue)
            frozen_input_hash = shorts_script_input_hash(
                layout,
                performance_advisories=frozen_advisories,
            )
            if stage.input_hash == queue.get("input_hash") == frozen_input_hash:
                return queue
    literal_candidates = (
        _require_literal_opening_source(layout)
        if config.require_verified_scripture or config.require_literal_verse_opening
        else _literal_opening_candidates(layout)
    )
    briefing_source = read_model(layout.briefing, Briefing)
    if is_high_stakes:
        literal_candidates = [
            source
            for source in literal_candidates
            if source.source_id == briefing_source.primary_literal_source_id
        ]
        if not literal_candidates:
            raise ValueError(
                "O perfil de grande pergunta exige a fonte literal primaria selada no briefing."
            )
    if is_compact_cinematic:
        literal_candidates = [
            source
            for source in literal_candidates
            if len(tokenize_words(source.excerpt or "")) <= config.max_literal_verse_words
            and source.spoken_by == "jesus"
            and source.compact_hook.strip()
            and source.compact_hook_strength_score >= config.minimum_hook_strength_score
            and source.compact_message_value_score >= config.minimum_message_value_score
            and source.compact_rewatch_value_score >= config.minimum_rewatch_value_score
        ]
        if not literal_candidates:
            raise ValueError(
                "O Short compacto exige uma fala direta de Jesus verificada, um hook curado forte "
                "e valor de mensagem/replay acima dos pisos configurados."
            )
    if _uses_scroll_stop_contract(layout):
        from pipeline_videos.analytics import compact_short_performance_advisories

        performance_advisories = compact_short_performance_advisories(layout)
    else:
        performance_advisories = []
    input_hash = shorts_script_input_hash(
        layout,
        performance_advisories=performance_advisories,
    )
    existing_script = read_json(layout.script) if layout.script.is_file() else None
    next_version = int(existing_script.get("version", 0)) + 1 if existing_script else 1
    baseline_hash = _baseline_hash(layout)
    job_id = f"shorts-script-{input_hash[:16]}"
    context = _shorts_context_payload(
        layout,
        literal_candidates,
        performance_advisories=performance_advisories,
    )
    briefing = Briefing.model_validate(context["briefing"])
    if is_compact_cinematic:
        unresolved = []
        if briefing.creative_mode not in {
            "devotional_application",
            "pastoral_encouragement",
        }:
            unresolved.append("creative_mode devocional aplicado ou encorajamento pastoral")
        if briefing.hook_policy != "required":
            unresolved.append("hook_policy=required")
        if briefing.hook_contract != "cinematic_interruption_v1":
            unresolved.append("hook_contract=cinematic_interruption_v1")
        if briefing.hook_family != "emotional_interruption":
            unresolved.append("hook_family=emotional_interruption")
        if unresolved:
            raise ValueError(
                "Resolva o perfil cinematográfico compacto antes do roteiro: "
                + ", ".join(unresolved)
            )
    elif is_creative_v3:
        unresolved = []
        if briefing.creative_mode == "auto":
            unresolved.append("creative_mode")
        if briefing.hook_policy == "auto":
            unresolved.append("hook_policy")
        if briefing.hook_family in {"auto", "none"}:
            unresolved.append("hook_family")
        if (
            briefing.hook_contract == "user_six_v1"
            and briefing.hook_family not in USER_SIX_HOOK_FAMILIES
        ):
            unresolved.append("hook_family precisa ser uma das seis famílias oficiais")
        if is_high_stakes:
            if briefing.hook_contract != "high_stakes_opening_v1":
                unresolved.append("hook_contract=high_stakes_opening_v1")
            if not briefing.opening_hook.strip():
                unresolved.append("opening_hook curado")
            if not briefing.required_answer_points:
                unresolved.append("required_answer_points")
        if unresolved:
            raise ValueError(
                "Resolva a direção criativa do briefing antes de preparar o Short: "
                + ", ".join(unresolved)
            )
    write_json(layout.shorts_script_context, context)
    main_passage = str(context["briefing"].get("main_passage", "")).strip()
    main_passage_spoken = spoken_biblical_reference(main_passage)
    if is_compact_cinematic and config.generation_mode == "deterministic_compact_visual_plan_v1":
        return _compact_visual_plan_queue(
            layout,
            input_hash=input_hash,
            baseline_hash=baseline_hash,
            next_version=next_version,
            job_id=job_id,
            briefing=briefing,
            source=literal_candidates[0],
            compact_reference=main_passage,
            spoken_reference=main_passage_spoken,
            config=config,
            performance_advisories=performance_advisories,
        )
    inputs: dict[str, str] = {
        "compact_context": relative_posix(layout.shorts_script_context, layout.root),
    }
    current_render = layout.root / "render" / "final-v1.mp4"
    if not current_render.is_file() and layout.final_video.is_file():
        current_render = layout.final_video
    if current_render.is_file():
        inputs["current_video_for_critique"] = relative_posix(current_render, layout.root)

    payload: dict[str, Any] = {
        "schema_version": _queue_schema_version(layout),
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "job_id": job_id,
        "input_hash": input_hash,
        "baseline_script_hash": baseline_hash,
        "next_script_version": next_version,
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "orchestration": {
            "mode": "main_agent" if main_agent_mode else "subagent",
            "worker_id": "main" if main_agent_mode else "shorts-script-specialist",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "medium" if main_agent_mode else "high",
            "recommended_model": "gpt-5.6-sol",
            "context_policy": "compact_only",
            "fork_turns": "none" if not main_agent_mode else None,
        },
        "inputs": inputs,
        "outputs": {
            "package": relative_posix(layout.shorts_script_package_staging, layout.root),
        },
        "schemas": {
            "package": compact_json_schema(ShortsScriptPackage.model_json_schema()),
        },
        "preflight": {
            "read_only": True,
            "command": (
                f'uv run pipeline-videos script shorts-check-staging "{project.project_id}"'
            ),
            "success_condition": "valid=true",
            "returns_all_detectable_errors": True,
            "independent_biblical_review_required": (config.require_independent_biblical_review),
            "independent_review_prepare_command": (
                f'uv run pipeline-videos script shorts-review-prepare "{project.project_id}"'
                if config.require_independent_biblical_review
                else None
            ),
            "final_reviewed_preflight_command": (
                "uv run pipeline-videos script shorts-check-reviewed-staging "
                f'"{project.project_id}"'
                if config.require_independent_biblical_review
                else None
            ),
        },
        "scripture_candidates": [source.source_id for source in literal_candidates],
        "verse_opening_candidates": [source.source_id for source in literal_candidates],
        "spoken_reference_standard": {
            "compact_reference": main_passage,
            "spoken_reference": main_passage_spoken,
            "lead_in_example": f"Em {main_passage_spoken}, lemos:",
        },
        "scripture_boundary_standard": {
            "spoken_phrase": None,
            "boundary_mode": "quotation_sections_and_claim_classification",
            "direct_quote_caption_style": "italic",
            "context_labels_enabled": False,
        },
        "scripture_quote_standard": {
            "scope": "verse_text_only",
            "opening_quote": "“",
            "closing_quote": "”",
            "opening_example": f"Em {main_passage_spoken}, lemos: “texto do versículo.”",
        },
        "citation_policy": _shorts_citation_policy(config),
        "performance_profile_contract": _short_performance_profile_contract(config),
        "editorial_profile_contract": (
            {
                "profile": briefing.editorial_profile,
                "question_id": briefing.question_id,
                "opening_hook": briefing.opening_hook,
                "implicit_viewer_question": briefing.implicit_viewer_question,
                "central_question": briefing.central_question,
                "required_answer_points": briefing.required_answer_points,
                "uncertainty_boundaries": briefing.uncertainty_boundaries,
                "answer_status": briefing.answer_status,
                "primary_literal_source_id": briefing.primary_literal_source_id,
                "scripture_timing_policy": config.scripture_citation_timing_policy,
            }
            if is_high_stakes
            else None
        ),
        "retention_contract": (
            {
                "standard": "creative_retention_v3",
                "duration_profile": config.duration_profile,
                "creative_mode": briefing.creative_mode,
                "interpretive_stance": briefing.interpretive_stance,
                "mode_guidance": creative_mode_payload(briefing.creative_mode),
                "hook": {
                    "required": True,
                    "contract": briefing.hook_contract,
                    "selected_family": briefing.hook_family,
                    "allowed_families": (
                        list(USER_SIX_HOOK_FAMILIES)
                        if briefing.hook_contract == "user_six_v1"
                        else list(HIGH_STAKES_HOOK_FAMILIES)
                        if briefing.hook_contract == "high_stakes_opening_v1"
                        else list(HOOK_TEMPLATE_EXAMPLES)
                    ),
                    "target_end_seconds": config.hook_target_seconds,
                    "templates": (
                        {
                            family: HOOK_TEMPLATE_EXAMPLES[family]
                            for family in USER_SIX_HOOK_FAMILIES
                        }
                        if briefing.hook_contract == "user_six_v1"
                        else {briefing.hook_family: briefing.opening_hook}
                        if briefing.hook_contract == "high_stakes_opening_v1"
                        else HOOK_TEMPLATE_EXAMPLES
                    ),
                },
                **(
                    {
                        "attention_model": {
                            "standard": "appeal_engagement_satisfaction_v1",
                            "first_frame_standard": (project.config.images.opening_visual_standard),
                            "opening_requirements": [
                                "situação específica ou conflito imediatamente compreensível",
                                "sujeito dominante e ação observável no primeiro quadro",
                                "imagem e fala abrem a mesma pergunta",
                                "promessa honesta que a resolução cumpre",
                                "um único foco legível em tela de celular",
                                "sem multidão, fundo carregado ou escuridão que esconda a ação",
                            ],
                            "forbidden_tactics": [
                                "erro deliberado para provocar correção",
                                "comentário falso ou conta alternativa",
                                "medo, consequência ou promessa bíblica inventados",
                                "estímulo visual aleatório sem função narrativa",
                            ],
                        },
                        "promise_progression_payoff": {
                            "single_central_loop": True,
                            "every_development_beat_adds_new_information": True,
                            "resolution_closes_opening_promise": True,
                            "filler_to_delay_payoff_forbidden": True,
                            "scripture_may_not_be_fragmented_to_manufacture_suspense": True,
                        },
                    }
                    if _uses_scroll_stop_contract(layout)
                    else {}
                ),
                "timeline_seconds": _short_timeline_seconds(
                    project.target_duration_seconds,
                    config,
                ),
                "application_required": creative_mode_payload(briefing.creative_mode)[
                    "application_required"
                ],
                "cta_max_words": config.cta_max_words,
                "cta_max_seconds": config.cta_max_seconds,
                "target_scene_count": [config.target_scene_min, config.target_scene_max],
            }
            if is_creative_v3
            else {
                "standard": "retention_story_v2",
                "selected_archetype": context["briefing"].get("retention_archetype"),
                "allowed_archetypes": [
                    "conflict_first",
                    "counterintuitive_reveal",
                    "stair_step_progression",
                ],
                "timeline_seconds": _retention_timeline_seconds(
                    project.target_duration_seconds,
                    config,
                ),
                "cta_max_words": config.cta_max_words,
                "cta_max_seconds": config.cta_max_seconds,
                "target_scene_count": [config.target_scene_min, config.target_scene_max],
            }
            if is_retention_v2
            else None
        ),
        "instructions": [
            (
                "No modo light, o agente principal deve produzir este pacote diretamente em uma "
                "única passagem, sem criar um subagente de roteiro separado."
                if main_agent_mode
                else "O shorts_script_specialist deve produzir o pacote isoladamente no staging."
            ),
            (
                "Leia primeiro e normalmente somente inputs.compact_context. Ele já reúne "
                "briefing, fontes, decisões recentes e material anterior necessário; abra o "
                "vídeo anterior apenas quando ele existir e a crítica visual for indispensável."
            ),
            (
                "Analise criticamente o roteiro e o vídeo atuais quando existirem; "
                "não os copie de forma mecânica."
            ),
            (
                "A voz autoral é sempre Jesus em primeira pessoa. Escreva como Jesus falando "
                "diretamente ao espectador ou contando o acontecimento que viveu; não use um "
                "narrador externo descrevendo Jesus em terceira pessoa."
            ),
            (
                "Não apresente a dramatização autoral como Escritura. Somente o excerpt "
                "verificado, entre aspas e ligado à fonte, é citação bíblica literal."
            ),
            (
                "Toda locução autoral segue human_authorial_voice_v1. Narre diretamente o fato, "
                "a ação, a dúvida ou a explicação; nunca descreva o próprio método de leitura. "
                "São proibidas fórmulas como 'o relato registra', 'o relato não explica', 'a "
                "sequência é compacta', 'o retrato começa', 'a passagem paralela acrescenta', "
                "'comparar é observar' e equivalentes."
            ),
            (
                "Não valorize uma frase dizendo antes o que alguém não fez e não defina uma "
                "cena por listas de ausência como 'afirmação, sem A, B ou C'. Hooks negativos "
                "legítimos, como 'Nunca faça X', continuam permitidos; a proibição é contra "
                "andaimes retóricos artificiais. Também não use a fórmula 'não A nem B, mas C'; "
                "declare C diretamente. Citações bíblicas literais permanecem intactas."
            ),
            *(
                [
                    (
                        "Aplique performance_profile_contract: abra uma única pergunta, "
                        "contradição ou consequência concreta em até 14 palavras; avance de três "
                        "a cinco vezes com informação realmente nova; entregue o payoff antes de "
                        "um CTA curto de uma só ação. Não gaste a abertura contextualizando."
                    )
                ]
                if config.performance_profile == "feed_depth_45_60_v1"
                else []
            ),
            (
                (
                    (
                        "Use obrigatoriamente creative_retention_v3 e o creative_mode do briefing. "
                        "Retenção organiza o ritmo, mas não transforma todo vídeo em devocional: "
                        "a entrega pode ser fato, explicação, comparação, argumento, reflexão, "
                        "conclusão ou aplicação conforme mode_guidance."
                        if is_creative_v3
                        else "Use obrigatoriamente retention_story_v2. Abra dentro de uma ação, "
                        "conflito ou revelação concreta; sustente uma pergunta central, faça o "
                        "valor crescer e entregue o payoff somente depois de uma mudança real de "
                        "entendimento."
                    )
                    if is_creative_v3 or is_retention_v2
                    else "Use obrigatoriamente o padrão human_hook_to_bible. Comece com uma frase "
                    "humana curta que faça o espectador se reconhecer numa dor, pergunta ou "
                    "contraste; leve essa tensão imediatamente ao contexto bíblico e cite o "
                    "versículo verificado logo depois."
                )
                if config.narrative_standard
                in {"creative_retention_v3", "retention_story_v2", "human_hook_to_bible"}
                else (
                    "Use o padrão legado verse_first: comece com a referência e o versículo, "
                    "mas faça a transição seguinte soar natural e não use rótulos falados."
                )
            ),
            (
                "Preencha strategy.verse_opening com a citação bíblica principal escolhida entre "
                "scripture_candidates. Copie verse_text exatamente do excerpt verificado e "
                "registre tradução e fonte. Dê preferência a um versículo conhecido, curto e "
                "fácil de entender. Use additional_scriptures somente se houver outra fonte "
                "verificada e se a segunda citação realmente acrescentar algo sem apertar o ritmo."
            ),
            (
                "Uma scripture_candidate pode abranger versículos consecutivos quando a faixa "
                "preservar melhor o sentido ou a continuidade. Nesse caso, trate a faixa como uma "
                "única citação literal, copie o excerpt verificado integralmente, mantenha a "
                "referência exata e respeite palavras e tempo. Não expanda a faixa só para "
                "preencher."
                if config.allow_contiguous_multi_verse_citations
                else "Use somente referências de versículo único neste projeto."
            ),
            (
                "Neste perfil de duração, verse_text pode ter no máximo "
                f"{config.max_literal_verse_words} palavras. Não corte, resuma ou complete o "
                "excerpt; escolha integralmente uma das scripture_candidates já filtradas."
            ),
            (
                "Toda referência bíblica falada deve escrever capítulo e versículo por extenso "
                "na locução. Nunca narre apenas 'Livro X:Y'. Use o padrão 'Livro, capítulo X, "
                "versículo Y'; por exemplo: "
                f"'{main_passage_spoken}'. Na seção da citação, use uma construção como "
                f"'Em {main_passage_spoken}, lemos:' em verse_opening.lead_in."
            ),
            (
                "Na seção da citação, deixe a apresentação do narrador fora das aspas e coloque "
                "somente verse_text entre aspas. Use exatamente o padrão "
                f"'Em {main_passage_spoken}, lemos: “texto literal do versículo.”' Não abra "
                "aspas antes de 'Em' e não inclua a referência dentro da citação. As aspas são "
                "pontuação visual e não devem ser pronunciadas pela voz."
            ),
            (
                "A legenda não deve exibir os rótulos TEXTO BÍBLICO ou REFLEXÃO. Somente as "
                "palavras copiadas literalmente de verse_text recebem itálico; apresentação, "
                "referência, paráfrases, contexto e reflexão permanecem sem itálico."
            ),
            (
                "Não fale nem escreva 'Até aqui, o texto bíblico' e não use outro rótulo para "
                "anunciar a mudança. Deixe o limite da citação claro pelas aspas, pelo itálico, "
                "pelas seções estruturadas e pela classificação das afirmações; depois siga com "
                "uma transição cotidiana e natural."
            ),
            (
                "A locução inteira deve permanecer em português. Termos técnicos podem existir "
                "nos campos de estratégia, mas nunca narre expressões como 'open loop', 'payoff', "
                "'storytelling' ou 'call to action'. Não diga 'o texto fala/diz/registra'; prefira "
                "'a Bíblia diz' ou verbalize Livro, capítulo e versículo."
            ),
            (
                (
                    (
                        (
                            "Copie exatamente o opening_hook curado do briefing "
                            "como primeira fala: "
                            f"'{briefing.opening_hook}'. Ele pode ser pergunta, fato, contradição, "
                            "consequência ou reversão de crença; não o transforme automaticamente "
                            "em pergunta. A abertura precisa criar a pergunta "
                            "na mente do espectador "
                            "e terminar até "
                            f"{config.hook_target_seconds:.1f}s."
                            if is_high_stakes
                            else "O Short exige um hook inicial forte e honesto. "
                            "Copie-o como prefixo "
                            "exato da primeira seção, use no máximo "
                            f"{config.hook_max_words} palavras, termine até "
                            f"{config.hook_target_seconds:.1f}s e siga a selected_family. "
                            "Nos Shorts novos, os seis modelos fornecidos pelo usuário são o "
                            "contrato exclusivo: "
                            "a primeira fala reproduz literalmente a estrutura selecionada, sem "
                            "clickbait ou promessa que o vídeo não entrega."
                        )
                        if is_creative_v3
                        else "O texto exato de strategy.hook deve abrir uma cena já em movimento, "
                        "copiar o início da primeira seção, ter no máximo "
                        f"{config.hook_max_words} palavras e terminar até "
                        f"{config.hook_target_seconds:.1f}s. Não cumprimente, contextualize o "
                        "canal nem comece com pergunta terapêutica genérica."
                    )
                    if is_creative_v3 or is_retention_v2
                    else "O texto exato de strategy.hook deve ser o prefixo da abertura "
                    "humana, usar "
                    f"no máximo {config.hook_max_words} palavras, mirar "
                    f"{config.hook_target_seconds:.1f}s e usar de uma a três técnicas em "
                    "hook_techniques: identificação, espelhamento emocional, curiosidade, "
                    "contraste, especificidade, quebra de padrão ou open loop. A técnica deve "
                    "servir à verdade da mensagem, nunca manipular."
                )
                if config.narrative_standard
                in {"creative_retention_v3", "retention_story_v2", "human_hook_to_bible"}
                else (
                    "No padrão verse_first, strategy.hook é o prefixo da locução bíblica, com "
                    f"no máximo {config.hook_max_words} palavras e alvo de "
                    f"{config.hook_target_seconds:.1f}s."
                )
            ),
            (
                (
                    (
                        "Preencha strategy.creative_direction e não retention_story nem "
                        "narrative_spine. Copie creative_mode, interpretive_stance e hook_family "
                        "do "
                        "briefing. Mapeie abertura, uma ou duas citações, ao menos dois movimentos "
                        "de desenvolvimento, reengajamento, resolução e CTA. Aplicação e callback "
                        "só existem quando o modo e o roteiro realmente precisarem deles."
                        if is_creative_v3
                        else "Preencha strategy.retention_story e não narrative_spine. Use o "
                        "arquétipo selecionado no briefing: conflict_first, "
                        "counterintuitive_reveal ou stair_step_progression. Estruture ao menos "
                        "sete seções: cold open, ação ou conflito, citação, progressão, "
                        "reengajamento, payoff/aplicação e CTA."
                    )
                    if is_creative_v3 or is_retention_v2
                    else "Siga seis movimentos: gancho humano, citação bíblica, contexto ou "
                    "conflito "
                    "bíblico, verdade central que resolve o gancho, aplicação concreta e "
                    "encerramento. Em narrative_spine, use tension_section_id para o gancho, "
                    "verse_section_id para a citação e bridge_section_id para o contexto."
                )
                if config.narrative_standard
                in {"creative_retention_v3", "retention_story_v2", "human_hook_to_bible"}
                else (
                    "Siga seis movimentos: versículo, pergunta de tensão, ponte bíblica, verdade "
                    "central, aplicação concreta e encerramento."
                )
            ),
            (
                (
                    "Em creative_direction, copie content_promise, resolution e viewer_takeaway "
                    "para as seções mapeadas. organizing_question, practical_application e "
                    "callback são opcionais. Cada desenvolvimento acrescenta fato, evidência, "
                    "contexto, comparação, argumento, consequência ou mudança de entendimento."
                    if is_creative_v3
                    else "Em retention_story, copie opening_promise, central_question, cada item "
                    "de progression, reengagement, payoff e practical_response para as seções "
                    "mapeadas. Cada progressão deve acrescentar fato, ação, consequência ou "
                    "mudança de entendimento; não apenas reformular a mesma tese."
                )
                if is_creative_v3 or is_retention_v2
                else "Escolha exatamente um development_mode: contextual_reflection para explicar "
                "o contexto e refletir; biblical_story_bridge para uma cena/personagem "
                "bíblico; biblical_image_bridge para uma imagem ou metáfora explicitamente "
                "bíblica; cross_reference_bridge para correlacionar outro versículo; ou "
                "promise_boundary para explicar o que o texto afirma e o que ele não promete."
            ),
            *(
                [
                    "Trate a pergunta do briefing como autoridade editorial, não o "
                    "versículo isolado. A progressão deve cobrir todos os "
                    "required_answer_points, usar as fontes de apoio somente no escopo "
                    "registrado e fechar exatamente a curiosidade aberta pelo hook.",
                    "Respeite answer_status e uncertainty_boundaries. Quando a resposta "
                    "for open ou bounded_inference, diga com clareza o que a Bíblia "
                    "afirma, o que permite inferir e o que permanece sem resposta; "
                    "nunca apresente interpretação como fato bíblico.",
                ]
                if is_high_stakes
                else []
            ),
            (
                (
                    "Não existe posição fixa de 8–20 segundos para a citação neste perfil. Escolha "
                    "sua posição pela função narrativa: ela pode estabelecer a tensão, comprovar "
                    "uma virada ou entregar a recompensa. Registre início e fim em verse_opening, "
                    "dentro do limite técnico de 0 a "
                    f"{config.scripture_citation_max_end_seconds:g}s, "
                    "e nunca esconda a resposta com enchimento apenas para adiar a citação."
                    if is_high_stakes
                    else "Faça a citação ocupar a janela entre "
                    f"{config.scripture_citation_min_start_seconds:g}s e "
                    f"{config.scripture_citation_max_end_seconds:g}s e registre seu "
                    "início e fim em "
                    "verse_opening. A Bíblia entra como evidência narrativa, explicativa ou "
                    "argumentativa coerente com o modo, não como bloco decorativo."
                )
                if is_creative_v3
                else "Faça a citação ocupar a janela entre "
                f"{config.scripture_citation_min_start_seconds:g}s e "
                f"{config.scripture_citation_max_end_seconds:g}s e registre seu início e "
                "fim em verse_opening. A Bíblia entra como evidência que muda a leitura do "
                "conflito, não como bloco decorativo ou resposta entregue cedo demais."
                if is_retention_v2
                else "Use contextual_reflection como padrão seguro. Só escolha story, image ou "
                "cross_reference quando houver outra fonte bíblica verificada nas entradas; "
                "registre-a em narrative_spine.bridge_source_ids. Não invente alegorias."
            ),
            (
                "A promessa e o hook precisam ser honestos, reconhecíveis e específicos. Entregue "
                "a resolução adequada ao modo; não force conflito, esconda informação trivial, "
                "ameace, use culpa, explore medo ou prometa benefício espiritual."
                if is_creative_v3
                else "A tensão precisa ser honesta, reconhecível e específica. Resolva o loop na "
                "verdade central; não esconda informação trivial, ameace, use culpa, explore medo "
                "ou prometa benefício espiritual para manipular retenção."
            ),
            (
                "Marque new_information e visual_change em todos os retention_beats. Inclua "
                "um reengajamento entre 40% e 60% da duração, com nova consequência, reversão "
                "ou revelação; registre exatamente como ele altera a pergunta do espectador."
                if is_retention_v2 or is_creative_v3
                else "Copie literalmente tension_question, bridge_statement, central_truth e "
                "practical_response para a narration das seções apontadas pelos respectivos "
                "section_id. O pipeline normaliza open_loop deterministicamente para repetir "
                "tension_question; não crie uma segunda formulação concorrente."
            ),
            (
                "Toda seção precisa preencher biblical_basis e claim_classification. Use somente "
                "bases presentes nas fontes compactas e classifique texto bíblico, contexto "
                "verificado, interpretação e elaboração criativa sem deixar listas vazias."
            ),
            (
                f"Entregue entre {config.min_words} e {config.max_words} palavras, "
                f"adequado ao limite de {project.target_duration_seconds}s."
            ),
            (
                f"Mapeie pelo menos {config.minimum_retention_beats} beats de "
                "retenção, com valor real para o espectador e tempos crescentes."
            ),
            (
                "Planeje a narração e as direções visuais para produzir "
                f"{_target_scene_instruction(config)}: "
                "uma ação concreta por imagem e mudança visual no meio. Callback final é "
                "obrigatório apenas no contrato v2 legado."
                if is_retention_v2 or is_creative_v3
                else "Preserve o perfil visual e o ritmo configurados no projeto."
            ),
            (
                "Inclua mudanças de ritmo e uma direção visual específica em cada seção, sem "
                "inventar fatos bíblicos. Cada visual_direction descreve um único momento, um "
                "único ambiente contínuo e uma ação principal; nunca peça vinhetas, painéis, "
                "colagem, tela dividida, storyboard ou várias cenas na mesma imagem."
            ),
            (
                "Em visual_direction, registre apenas a orientação geral da seção; o plano "
                "visual por bloco em strategy.visual_scenes é a autoridade para cada imagem."
                if project.config.images.scene_planning_standard == "narration_block_visual_v1"
                else "Na primeira seção, descreva em visual_direction uma única imagem-âncora "
                "vertical, fiel ao gancho e visualmente forte: assunto dominante, emoção ou "
                "gesto legível, conflito simples, luz direcional e espaço negativo no terço "
                "superior. Não escreva texto dentro da imagem e não use choque gratuito."
            ),
            *(
                [
                    "Em creative_direction.opening_visual_contract, declare o verbo físico "
                    "principal no início de observable_action, sem sujeito, auxiliar ou negação; "
                    "classifique action_kind e registre initial_state/resulting_state visivelmente "
                    "distintos, sujeito dominante, tensão concreta e shot_scale close ou medium. "
                    "Esse contrato é a autoridade da primeira imagem."
                ]
                if is_creative_v3 and _uses_scroll_stop_contract(layout)
                else []
            ),
            (
                "Não force aplicação pessoal. Em modos factuais, termine com compreensão ou "
                "conclusão factual; em modos analíticos, com síntese; em modos exploratórios, "
                "com posição argumentada ou incerteza preservada. Só os modos devocionais "
                "exigem resposta prática."
                if is_creative_v3
                else "A aplicação precisa oferecer uma resposta prática específica para hoje, "
                "diferenciar texto bíblico de interpretação e evitar promessa de vitória "
                "automática."
            ),
            (
                "Use briefing.narration_tone e interpretive_stance. A voz pode ser narrativa, "
                "didática, investigativa, filosófica ou pastoral conforme o modo; não a "
                "converta automaticamente em aconselhamento. Escreva em português brasileiro "
                "falado, com frases claras, verbos concretos e ritmo natural."
                if is_creative_v3
                else "Use uma voz pastoral simples: a clareza acolhedora de um bom pastor ou "
                "padre conversando com uma pessoa, sem caricatura religiosa e sem tom de "
                "palestra. Escreva em português brasileiro falado, com frases curtas, palavras "
                "comuns, verbos concretos, contrações naturais e ritmo cotidiano. Leia tudo "
                "em voz alta e reescreva o que soar formal, terapêutico, publicitário ou com "
                "cara de IA."
            ),
            (
                "Evite fórmulas com aparência de texto gerado, como 'a coragem nasce de', "
                "'nomeie o seu medo', 'escolha um próximo passo responsável', 'acolha a sua dor', "
                "'essa passagem nos convida' e 'para a vida real'. Diga concretamente o que a "
                "pessoa pode pensar, sentir ou fazer, sem empilhar substantivos abstratos."
            ),
            (
                "Não repita a mesma construção entre vídeos. A voz pode usar perguntas naturais, "
                "contrações e transições como 'talvez você pense', 'repara nisso', 'só que' e "
                "'então', quando combinarem com o tom; não transforme essas expressões em bordões."
            ),
            (
                "Faça as frases serem magnéticas pela verdade e pela forma: fale diretamente com "
                "a experiência do público, crie imagens mentais, use contraste, detalhes concretos "
                "e perguntas que a pessoa realmente faria. Não use palavras raras só para soar "
                "profundo e não escreva frases de efeito vazias."
            ),
            (
                "Preencha o engagement do contrato ativo com exatamente uma ação: comment, "
                "subscribe ou share. O spoken_text precisa ter no máximo "
                f"{config.cta_max_words} palavras, ocupar até {config.cta_max_seconds:.1f}s "
                "e nascer do valor entregue. Não empilhe curtir, comentar, inscrever-se e "
                "compartilhar."
                if is_retention_v2 or is_creative_v3
                else "Antes do CTA, escolha exatamente um comment_prompt adequado ao vídeo: "
                "keyword_reaffirmation para uma palavra temática; next_step_commitment para "
                "um compromisso curto; sentence_completion para o espectador completar uma "
                "frase; message_declaration para reafirmar a mensagem em até oito palavras; "
                "ou community_encouragement para deixar uma palavra de apoio."
            ),
            (
                "Use comment_prompt somente quando engagement.mode=comment. Nesse caso, "
                "prompt_text deve ser o mesmo spoken_text; response_cue pode ser uma resposta "
                "natural ou uma palavra específica. Aspas e pausa isolada só são exigidas "
                "quando você escolher explicitamente uma palavra-chave."
                if is_retention_v2 or is_creative_v3
                else "Preencha comment_prompt.prompt_text com o convite completo falado, "
                "response_cue com a palavra, frase ou início de frase esperado, objective com "
                "o objetivo humano da interação e fit_reason explicando por que esse modo "
                "combina com este vídeo. Coloque response_cue entre aspas tipográficas no "
                "convite. Use somente um pedido de comentário e mantenha-o natural."
            ),
            (
                (
                    "Callback é opcional no creative_retention_v3. Quando usado, copie-o em "
                    "creative_direction.callback e faça-o acrescentar significado; não crie loop "
                    "circular artificial."
                    if is_creative_v3
                    else "Preencha replay_bridge com um elemento real da abertura, um callback "
                    "final e o novo significado que ele ganha. O closing_callback deve aparecer "
                    "no spoken_text do CTA e encerrar o vídeo imediatamente, sem despedida."
                )
                if is_creative_v3 or is_retention_v2
                else "Quando response_cue for uma palavra, escolha uma palavra-síntese expressiva, "
                "específica e ligada à verdade central, ao payoff ou à aplicação. Ela deve "
                f"ter ao menos {_MIN_COMMENT_CUE_SIGNIFICANT_CHARACTERS} letras. Boas referências "
                "de densidade semântica são REFÚGIO, PERSISTÊNCIA, CONFIANÇA, FIDELIDADE, "
                "ESPERANÇA, PERSEVERANÇA, RECOMEÇO, DESCANSO e CORAGEM."
            ),
            (
                "Faça o último beat terminar entre "
                f"{config.retention_target_min_seconds:.0f}s e "
                f"{config.retention_target_max_seconds:.0f}s. O CTA é o último movimento; "
                "não acrescente resumo, agradecimento, despedida ou segunda chamada depois."
                if is_retention_v2 or is_creative_v3
                else "Nunca use como resposta isolada palavras vagas ou fracas como BEM, SIM, "
                "AMÉM, EU, AQUI, PERTO, FÉ, PAZ, FORÇA ou FIEL. Explique concretamente a "
                "ligação da resposta com a resolução do roteiro."
            ),
            (
                "O comentário nunca pode funcionar como condição para bênção, resposta de Deus, "
                "cura, proteção, milagre ou prova de fé. Não use culpa, urgência artificial, "
                "promessa de resultado nem frases como 'comente amém para receber'."
            ),
            (
                "Copie engagement.spoken_text do contrato ativo em strategy.cta e use esse texto "
                "como toda a narração da seção closing mapeada por cta_section_id."
                if is_retention_v2 or is_creative_v3
                else "Encerre sempre com o CTA exato, imediatamente depois do convite para "
                f"comentar: '{config.final_subscribe_cta}' Não acrescente texto depois."
            ),
            (
                "Produza uma revisão bíblica separada, registre incertezas e não "
                "deixe finding blocking aberto."
            ),
            (
                "Use somente source_id e referências presentes nas entradas do projeto. "
                "Ao menos uma citação literal verificada é obrigatória. Não estenda uma citação "
                "para versos vizinhos não registrados e não use o versículo fora do significado "
                "permitido pelo contexto e pelos limites de interpretação."
            ),
            "Repita job_id e input_hash exatamente e grave somente o package declarado no staging.",
            (
                "Depois de gravar o package, execute preflight.command. Ele é read-only e retorna "
                "todos os erros detectáveis de uma vez. Corrija o staging até receber valid=true; "
                "não entregue o pacote apenas com uma validação informal."
            ),
            (
                "Não altere project.json, roteiro ativo, aprovações, manifestos ou "
                "qualquer arquivo fora do staging."
            ),
            "Não prometa viralização, retenção ou CTR e não invente métricas.",
        ],
    }
    if is_compact_cinematic:
        timeline = _short_timeline_seconds(project.target_duration_seconds, config)
        required_hook = literal_candidates[0].compact_hook
        payload["spoken_reference_standard"] = {
            "compact_reference": main_passage,
            "spoken_reference": main_passage_spoken,
            "placement": "after_closing_quote",
            "lead_in_example": None,
        }
        payload["scripture_quote_standard"] = {
            "scope": "verse_text_only",
            "opening_quote": "“",
            "closing_quote": "”",
            "reference_position": "after_closing_quote",
            "opening_example": f"“texto do versículo.” {main_passage_spoken}.",
        }
        payload["retention_contract"] = {
            "standard": "cinematic_jesus_devotional_v1",
            "duration_profile": config.duration_profile,
            "allowed_creative_modes": [
                "devotional_application",
                "pastoral_encouragement",
            ],
            "hook": {
                "required": True,
                "contract": "cinematic_interruption_v1",
                "selected_family": "emotional_interruption",
                "target_end_seconds": config.hook_target_seconds,
                "required_text": required_hook,
                "strength_score": literal_candidates[0].compact_hook_strength_score,
            },
            "minimum_estimated_duration_ratio": config.minimum_estimated_duration_ratio,
            "minimum_audio_duration_ratio": config.minimum_audio_duration_ratio,
            "maximum_narration_gap_seconds": config.maximum_narration_gap_seconds,
            "timeline_seconds": timeline,
            "direct_jesus_speech_required": True,
            "reference_after_quote_required": True,
            "cta_allowed": False,
            "target_scene_count": [config.target_scene_min, config.target_scene_max],
            "music_library_profile": "adaptive_compact_devotional_v1",
            "citation_policy": _shorts_citation_policy(config),
        }
        payload["instructions"] = _compact_cinematic_instructions(
            project_id=project.project_id,
            main_passage_spoken=main_passage_spoken,
            required_hook=required_hook,
            config=config,
            main_agent_mode=main_agent_mode,
        )
    if project.config.images.scene_planning_standard == "narration_block_visual_v1":
        payload["visual_scene_contract"] = {
            "standard": "narration_block_visual_v1",
            "target_scene_count": [config.target_scene_min, config.target_scene_max],
            "visual_standard_id": project.config.images.visual_standard_id,
            "composition_safety_mode": project.config.images.composition_safety_mode,
            "allowed_relations": ["literal", "contextual", "symbolic"],
            "spoken_reference_image": {
                "mode": project.config.images.spoken_reference_image_mode,
                "path": project.config.images.spoken_reference_image_path,
                "section_id_field": "strategy.cinematic_devotional.reference_section_id",
            },
        }
        payload["instructions"].extend(
            [
                (
                    "Preencha strategy.visual_scenes com um item para cada futuro bloco de áudio, "
                    f"totalizando de {config.target_scene_min} a {config.target_scene_max} cenas."
                ),
                (
                    "Em cada item, section_id aponta para a seção de origem e narration copia "
                    "literalmente um trecho contíguo dessa seção. Em conjunto e na mesma ordem, "
                    "os blocos precisam reconstruir integralmente a narração de cada seção, sem "
                    "omitir, repetir, parafrasear ou mover palavras."
                ),
                (
                    "scene_description deve descrever a cena concreta que passará enquanto o "
                    "bloco for narrado: personagens ou objeto central, ação e ambiente. Não use "
                    "uma indicação genérica de câmera e não repita a mesma conversa, composição "
                    "ou ação em vários blocos."
                ),
                (
                    "Use visual_relation=literal quando a cena mostra diretamente o conteúdo, "
                    "contextual quando mostra uma situação coerente e symbolic quando usa uma "
                    "imagem artística clara. Isso permite, quando for benéfico, alternar Jesus "
                    "acolhendo alguém, uma oração em grupo, moradas preparadas, pregação, jardim "
                    "ou Bíblia aberta, sempre conforme a narração e sem transformar elaboração "
                    "simbólica em fato bíblico."
                ),
                (
                    "Descreva somente o conteúdo da cena; não repita estilo, paleta, proporção, "
                    "ou regras de legenda. O prompt matriz do perfil visual será acrescentado "
                    "automaticamente depois."
                ),
                (
                    "Mantenha o bloco de strategy.cinematic_devotional.reference_section_id no "
                    "plano e associe-o a uma Bíblia aberta. O pipeline reutilizará a imagem fixa "
                    "do perfil nesse bloco e não criará um job de imagegen para ele."
                    if project.config.images.spoken_reference_image_mode == "fixed_profile_asset_v1"
                    else "O perfil atual gera normalmente a imagem do bloco de referência."
                ),
            ]
        )
    if not config.require_independent_biblical_review:
        for key in (
            "independent_biblical_review_required",
            "independent_review_prepare_command",
            "final_reviewed_preflight_command",
        ):
            payload["preflight"].pop(key, None)
    if _uses_scroll_stop_contract(layout):
        payload["frozen_performance_advisories"] = performance_advisories
    start_stage(layout, "shorts_script", input_hash, provider=config.specialist_role)
    write_json(layout.shorts_script_queue, payload)
    wait_stage(
        layout,
        "shorts_script",
        input_hash,
        "Aguardando o subagente shorts_script_specialist escrever somente no staging.",
        [
            relative_posix(layout.shorts_script_queue, layout.root),
            relative_posix(layout.shorts_script_context, layout.root),
        ],
    )
    return payload


def _safe_staging_file(layout: ProjectLayout, relative_path: str) -> Path:
    path = (layout.root / relative_path).resolve()
    try:
        path.relative_to(layout.shorts_script_staging.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho fora do staging do roteirista: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Saída do roteirista ausente: {path}")
    return path


def _normalized(text: str) -> str:
    return " ".join(token.casefold() for token in tokenize_words(text))


_GENERIC_VISUAL_ACTION_TOKENS = {
    "acolhe",
    "acolhendo",
    "acolher",
    "contempla",
    "contemplando",
    "contemplar",
    "encara",
    "encarando",
    "encarar",
    "escuta",
    "escutando",
    "escutar",
    "expressa",
    "expressando",
    "expressar",
    "está",
    "esta",
    "estar",
    "fala",
    "falando",
    "falar",
    "fica",
    "ficando",
    "ficar",
    "olha",
    "olhando",
    "olhar",
    "observa",
    "observando",
    "observar",
    "permanece",
    "permanecendo",
    "permanecer",
    "posa",
    "posando",
    "posar",
    "sorri",
    "sorrindo",
    "sorrir",
}

_FORBIDDEN_ACTION_OPENERS = _GENERIC_VISUAL_ACTION_TOKENS | {
    "a",
    "as",
    "apenas",
    "calma",
    "calmamente",
    "calmo",
    "começa",
    "comeca",
    "continua",
    "câmera",
    "camera",
    "davi",
    "deixa",
    "ela",
    "ele",
    "expressão",
    "expressao",
    "figura",
    "homem",
    "jesus",
    "luz",
    "mãos",
    "mão",
    "maos",
    "mao",
    "mulher",
    "não",
    "nao",
    "nunca",
    "o",
    "objeto",
    "olhar",
    "os",
    "pessoa",
    "porta",
    "profundamente",
    "rosto",
    "serena",
    "serenamente",
    "serenidade",
    "sereno",
    "volta",
}


def _opening_action_contract_errors(
    action: str,
    *,
    initial_state: str,
    resulting_state: str,
) -> list[str]:
    """Validate an explicit state transition instead of guessing arbitrary Portuguese verbs."""

    errors: list[str] = []
    stripped = action.strip()
    tokens = re.findall(r"[^\W_]+", stripped.casefold(), flags=re.UNICODE)
    if len(tokens) < 2:
        errors.append("observable_action precisa declarar verbo físico e complemento visível.")
    elif tokens[0] in _FORBIDDEN_ACTION_OPENERS:
        errors.append(
            "observable_action precisa começar pelo verbo físico principal, sem sujeito, "
            "auxiliar, negação, olhar, sorriso ou estado emocional."
        )
    if {"não", "nao", "nunca"}.intersection(tokens[:3]):
        errors.append("observable_action precisa afirmar a mudança executada, sem ação negada.")
    if _normalized(initial_state) == _normalized(resulting_state):
        errors.append(
            "initial_state e resulting_state precisam registrar estados visíveis distintos."
        )
    return errors


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _contains_normalized(container: str, fragment: str) -> bool:
    normalized_fragment = _normalized(fragment)
    return bool(normalized_fragment) and normalized_fragment in _normalized(container)


def _validate_scripture_source(
    citation: Any,
    source_by_id: dict[str, SourceEntry],
    *,
    compact_cinematic: bool = False,
) -> None:
    spoken_reference = spoken_biblical_reference(citation.reference)
    if compact_cinematic:
        if citation.lead_in.strip():
            raise ValueError("A citação compacta não pode usar lead-in antes da fala de Jesus.")
    elif not _contains_normalized(citation.lead_in, spoken_reference):
        raise ValueError(
            "A introdução da citação precisa narrar a referência no formato "
            "'Livro, capítulo X, versículo Y'."
        )
    source = source_by_id.get(citation.source_id)
    if source is None or source.kind != "biblical_text" or not source.verified:
        raise ValueError("Toda citação precisa apontar para uma fonte bíblica verificada.")
    if compact_cinematic and source.spoken_by != "jesus":
        raise ValueError("O perfil compacto aceita somente uma fala atribuída diretamente a Jesus.")
    required_source_fields = {
        "passage": source.passage,
        "translation": source.translation,
        "excerpt": source.excerpt,
        "author_or_publisher": source.author_or_publisher,
        "accessed_at": source.accessed_at,
        "rights_note": source.rights_note,
    }
    missing_fields = [name for name, value in required_source_fields.items() if not value]
    if missing_fields:
        raise ValueError("A fonte literal está incompleta: " + ", ".join(missing_fields) + ".")
    if _normalized(source.translation or "") != _normalized(citation.translation):
        raise ValueError("A tradução do versículo diverge da tradução registrada na fonte.")
    if citation.quote_mode != "literal":
        raise ValueError("A citação bíblica obrigatória precisa ser literal e verificada.")
    if _normalized(source.excerpt or "") != _normalized(citation.verse_text):
        raise ValueError("O texto literal da citação diverge do excerpt verificado da fonte.")


def _citation_forms(citation: Any) -> set[str]:
    return {
        _collapsed(f'{citation.lead_in.strip()} "{citation.verse_text.strip()}"'),
        _collapsed(f"{citation.lead_in.strip()} “{citation.verse_text.strip()}”"),
    }


def _quote_only_forms(citation: Any) -> set[str]:
    return {
        _collapsed(f'"{citation.verse_text.strip()}"'),
        _collapsed(f"“{citation.verse_text.strip()}”"),
    }


def _validate_narrative_spine(package: ShortsScriptPackage) -> dict[str, Any]:
    strategy = package.strategy
    spine = strategy.narrative_spine
    if spine is None:
        raise ValueError("O roteiro precisa preencher strategy.narrative_spine.")
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    spine_ids = [
        spine.verse_section_id,
        spine.tension_section_id,
        spine.bridge_section_id,
        spine.meaning_section_id,
        spine.application_section_id,
    ]
    if len(set(spine_ids)) != len(spine_ids):
        raise ValueError("Cada movimento da espinha narrativa precisa de uma seção distinta.")
    if not set(spine_ids).issubset(section_by_id):
        raise ValueError("A espinha narrativa referencia section_id inexistente.")
    if len(sections) < 6 or sections[-1].section_id in spine_ids:
        raise ValueError("O Short precisa de cinco movimentos editoriais e um encerramento.")
    if section_by_id[spine.application_section_id].role != "application":
        raise ValueError("A resposta prática precisa usar uma seção role=application.")
    mapped_text = (
        (spine.tension_section_id, spine.tension_question, "tension_question"),
        (spine.bridge_section_id, spine.bridge_statement, "bridge_statement"),
        (spine.meaning_section_id, spine.central_truth, "central_truth"),
        (spine.application_section_id, spine.practical_response, "practical_response"),
    )
    for section_id, required_text, field_name in mapped_text:
        if not _contains_normalized(section_by_id[section_id].narration, required_text):
            raise ValueError(f"narrative_spine.{field_name} precisa aparecer na seção mapeada.")
    if _normalized(strategy.open_loop) != _normalized(spine.tension_question):
        raise ValueError("strategy.open_loop precisa repetir a tensão central da espinha.")
    return section_by_id


def _validate_scripture_contract(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
    section_by_id: dict[str, Any],
) -> set[str]:
    project = layout.load_manifest()
    strategy = package.strategy
    primary = strategy.verse_opening
    if primary is None:
        raise ValueError("O roteiro precisa citar ao menos um versículo bíblico verificado.")
    citations = [primary, *strategy.additional_scriptures]
    compact_cinematic = project.config.shorts_script.duration_profile == "adaptive_compact_v1"
    if compact_cinematic and len(citations) != 1:
        raise ValueError("O momento cinematográfico compacto exige exatamente uma fala de Jesus.")
    if len(citations) > 2:
        raise ValueError("Um Short pode usar no máximo duas citações literais para manter clareza.")
    source_by_id = {source.source_id: source for source in sources.entries}
    for citation in citations:
        _validate_scripture_source(
            citation,
            source_by_id,
            compact_cinematic=compact_cinematic,
        )
        if (
            citation.target_end_seconds
            > project.config.shorts_script.scripture_citation_max_end_seconds
        ):
            raise ValueError("A citação bíblica termina tarde demais para o ritmo do Short.")
    spine = strategy.narrative_spine
    story = strategy.retention_story
    creative = strategy.creative_direction
    cinematic = strategy.cinematic_devotional
    verse_section_id = (
        cinematic.scripture_section_id
        if cinematic is not None
        else creative.scripture_section_ids[0]
        if creative is not None
        else story.scripture_section_id
        if story is not None
        else spine.verse_section_id
        if spine is not None
        else None
    )
    if verse_section_id is None or verse_section_id not in section_by_id:
        raise ValueError("A estratégia não mapeia uma seção bíblica válida.")
    primary_section = section_by_id[verse_section_id]
    expected_forms = _quote_only_forms(primary) if compact_cinematic else _citation_forms(primary)
    if _collapsed(primary_section.narration) not in expected_forms:
        raise ValueError(
            "A seção bíblica compacta deve conter somente a fala literal de Jesus entre aspas."
            if compact_cinematic
            else "A seção bíblica deve deixar a referência fora das aspas e colocar somente o "
            "versículo literal entre aspas."
        )
    if compact_cinematic:
        if cinematic is None or cinematic.reference_section_id not in section_by_id:
            raise ValueError("O perfil compacto não mapeia a seção final de referência.")
        expected_reference = spoken_biblical_reference(primary.reference)
        reference_narration = section_by_id[cinematic.reference_section_id].narration
        if _normalized(reference_narration.rstrip(" .!?")) != _normalized(expected_reference):
            raise ValueError(
                "A última seção deve conter somente a referência falada depois das aspas."
            )
    for citation in strategy.additional_scriptures:
        forms = _citation_forms(citation)
        if not any(
            any(form in _collapsed(section.narration) for form in forms)
            for section in package.script.sections
        ):
            raise ValueError("Uma citação complementar declarada não aparece no roteiro.")
    return {citation.source_id for citation in citations}


def _validate_bridge_sources(
    package: ShortsScriptPackage,
    sources: SourceLibrary,
    required_source_ids: set[str],
) -> None:
    strategy = package.strategy
    spine = strategy.narrative_spine
    assert spine is not None
    source_by_id = {source.source_id: source for source in sources.entries}
    bridge_source_ids = set(spine.bridge_source_ids)
    external_bridge_modes = {
        "biblical_story_bridge",
        "biblical_image_bridge",
        "cross_reference_bridge",
    }
    if spine.development_mode in external_bridge_modes:
        complementary_ids = bridge_source_ids - required_source_ids
        if not complementary_ids:
            raise ValueError("O modo de ponte escolhido exige outra fonte bíblica verificada.")
        if any(
            source_id not in source_by_id
            or source_by_id[source_id].kind != "biblical_text"
            or not source_by_id[source_id].verified
            for source_id in complementary_ids
        ):
            raise ValueError("A ponte referencia fonte bíblica ausente ou não verificada.")
    required_source_ids |= bridge_source_ids
    if not required_source_ids.issubset(package.script.source_ids):
        raise ValueError("O roteiro não registra todas as fontes bíblicas usadas.")
    if not required_source_ids.issubset(strategy.source_ids):
        raise ValueError("A estratégia não registra todas as fontes bíblicas usadas.")


def _validate_verse_first_standard(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
) -> None:
    project = layout.load_manifest()
    strategy = package.strategy
    spine = strategy.narrative_spine
    if strategy.verse_opening is None or spine is None:
        raise ValueError(
            "O padrão verse_first exige strategy.verse_opening e strategy.narrative_spine."
        )
    if spine.pattern != "verse_first" or strategy.hook_type != "scripture_opening":
        raise ValueError("O gancho verse_first precisa usar pattern e hook_type bíblicos.")
    if (
        strategy.verse_opening.target_end_seconds
        > project.config.shorts_script.verse_opening_max_seconds
    ):
        raise ValueError("O versículo termina tarde demais para a abertura configurada.")
    section_by_id = _validate_narrative_spine(package)
    if spine.verse_section_id != package.script.sections[0].section_id:
        raise ValueError("O versículo precisa ocupar a primeira seção do padrão verse_first.")
    if section_by_id[spine.verse_section_id].role != "opening":
        raise ValueError("A seção do versículo precisa usar role=opening.")
    required_source_ids = _validate_scripture_contract(layout, package, sources, section_by_id)
    _validate_bridge_sources(package, sources, required_source_ids)


def _validate_human_hook_standard(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    sources: SourceLibrary,
) -> None:
    strategy = package.strategy
    spine = strategy.narrative_spine
    if strategy.verse_opening is None or spine is None:
        raise ValueError("O padrão human_hook_to_bible exige citação bíblica e espinha narrativa.")
    if spine.pattern != "human_hook_to_bible":
        raise ValueError("narrative_spine.pattern precisa ser human_hook_to_bible.")
    if strategy.hook_type == "scripture_opening":
        raise ValueError(
            "O novo padrão precisa começar com um gancho humano, não com a referência."
        )
    if not 1 <= len(strategy.hook_techniques) <= 3:
        raise ValueError("O gancho precisa registrar de uma a três técnicas naturais de atenção.")
    section_by_id = _validate_narrative_spine(package)
    ordered_ids = [section.section_id for section in package.script.sections]
    expected_order = [
        spine.tension_section_id,
        spine.verse_section_id,
        spine.bridge_section_id,
        spine.meaning_section_id,
        spine.application_section_id,
    ]
    if [ordered_ids.index(section_id) for section_id in expected_order] != sorted(
        ordered_ids.index(section_id) for section_id in expected_order
    ):
        raise ValueError("A estrutura deve seguir gancho, Bíblia, contexto, sentido e aplicação.")
    if spine.tension_section_id != package.script.sections[0].section_id:
        raise ValueError("A primeira seção precisa ser o gancho humano do Short.")
    if ordered_ids.index(spine.verse_section_id) != 1:
        raise ValueError("A citação bíblica precisa entrar imediatamente depois do gancho.")
    if section_by_id[spine.verse_section_id].role != "biblical_narrative":
        raise ValueError("A citação após o gancho precisa usar role=biblical_narrative.")
    required_source_ids = _validate_scripture_contract(layout, package, sources, section_by_id)
    _validate_bridge_sources(package, sources, required_source_ids)


def _validate_retention_engagement_contract(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
) -> None:
    config = layout.load_manifest().config.shorts_script
    strategy = package.strategy
    story = strategy.retention_story
    if story is None:
        raise ValueError("retention_story_v2 exige strategy.retention_story.")
    engagement = story.engagement
    spoken_text = _collapsed(engagement.spoken_text)
    if _collapsed(strategy.cta) != spoken_text:
        raise ValueError("strategy.cta precisa copiar retention_story.engagement.spoken_text.")
    if len(tokenize_words(spoken_text)) > config.cta_max_words:
        raise ValueError("O CTA de retention_story_v2 ultrapassa doze palavras.")
    if engagement.target_end_seconds - engagement.target_start_seconds > config.cta_max_seconds:
        raise ValueError("O CTA de retention_story_v2 ultrapassa quatro segundos.")

    normalized = _normalized(spoken_text)
    action_markers = {
        "comment": ("coment", "escrev", "conta pra"),
        "subscribe": ("inscrev",),
        "share": ("compartilh", "envia", "mande"),
    }
    detected = {
        mode
        for mode, markers in action_markers.items()
        if any(marker in normalized for marker in markers)
    }
    if detected != {engagement.mode}:
        raise ValueError(
            "O CTA precisa pedir exatamente a única ação declarada em engagement.mode."
        )
    if not engagement.objective.strip() or not engagement.fit_reason.strip():
        raise ValueError("O engagement precisa registrar objetivo e adequação ao payoff.")

    forbidden_engagement_bait = (
        "comente amem para receber",
        "escreva amem para receber",
        "para receber uma bencao",
        "para deus te abencoar",
        "prove sua fe",
        "se voce ama deus",
    )
    if any(forbidden in normalized for forbidden in forbidden_engagement_bait):
        raise ValueError("O CTA usa pressão ou promessa espiritual indevida.")

    comment_prompt = strategy.comment_prompt
    if engagement.mode == "comment":
        if comment_prompt is None:
            raise ValueError("engagement.mode=comment exige strategy.comment_prompt.")
        if _collapsed(comment_prompt.prompt_text) != spoken_text:
            raise ValueError("comment_prompt.prompt_text precisa copiar o CTA falado.")
        if not _contains_normalized(comment_prompt.prompt_text, comment_prompt.response_cue):
            raise ValueError("A resposta sugerida precisa aparecer no convite falado.")
        if len(tokenize_words(comment_prompt.response_cue)) > config.comment_response_max_words:
            raise ValueError("A resposta sugerida para o comentário está longa demais.")
    elif comment_prompt is not None:
        raise ValueError("comment_prompt só pode existir quando engagement.mode=comment.")

    closing = package.script.sections[-1]
    if closing.section_id != story.cta_section_id or closing.role != "closing":
        raise ValueError("O CTA precisa ocupar a última seção closing.")
    if _collapsed(closing.narration) != spoken_text:
        raise ValueError("A seção closing deve conter somente o CTA de uma ação.")
    if not _contains_normalized(closing.narration, story.replay_bridge.closing_callback):
        raise ValueError("O CTA final precisa conter o callback declarado.")


def _validate_creative_engagement_contract(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
) -> None:
    config = layout.load_manifest().config.shorts_script
    strategy = package.strategy
    direction = strategy.creative_direction
    if direction is None:
        raise ValueError("creative_retention_v3 exige strategy.creative_direction.")
    engagement = direction.engagement
    spoken_text = _collapsed(engagement.spoken_text)
    if _collapsed(strategy.cta) != spoken_text:
        raise ValueError("strategy.cta precisa copiar creative_direction.engagement.spoken_text.")
    if len(tokenize_words(spoken_text)) > config.cta_max_words:
        raise ValueError("O CTA de creative_retention_v3 ultrapassa doze palavras.")
    if engagement.target_end_seconds - engagement.target_start_seconds > config.cta_max_seconds:
        raise ValueError("O CTA de creative_retention_v3 ultrapassa quatro segundos.")

    normalized = _normalized(spoken_text)
    action_markers = {
        "comment": ("coment", "escrev", "conta pra"),
        "subscribe": ("inscrev",),
        "share": ("compartilh", "envia", "mande"),
    }
    detected = {
        mode
        for mode, markers in action_markers.items()
        if any(marker in normalized for marker in markers)
    }
    if detected != {engagement.mode}:
        raise ValueError("O CTA precisa pedir exatamente a ação declarada em engagement.mode.")
    if not engagement.objective.strip() or not engagement.fit_reason.strip():
        raise ValueError("O engagement precisa registrar objetivo e adequação ao valor entregue.")
    forbidden_engagement_bait = (
        "comente amem para receber",
        "escreva amem para receber",
        "para receber uma bencao",
        "para deus te abencoar",
        "prove sua fe",
        "se voce ama deus",
    )
    if any(forbidden in normalized for forbidden in forbidden_engagement_bait):
        raise ValueError("O CTA usa pressão ou promessa espiritual indevida.")

    comment_prompt = strategy.comment_prompt
    if engagement.mode == "comment":
        if comment_prompt is None:
            raise ValueError("engagement.mode=comment exige strategy.comment_prompt.")
        if _collapsed(comment_prompt.prompt_text) != spoken_text:
            raise ValueError("comment_prompt.prompt_text precisa copiar o CTA falado.")
        if not _contains_normalized(comment_prompt.prompt_text, comment_prompt.response_cue):
            raise ValueError("A resposta sugerida precisa aparecer no convite falado.")
        if len(tokenize_words(comment_prompt.response_cue)) > config.comment_response_max_words:
            raise ValueError("A resposta sugerida para o comentário está longa demais.")
    elif comment_prompt is not None:
        raise ValueError("comment_prompt só pode existir quando engagement.mode=comment.")

    closing = package.script.sections[-1]
    if closing.section_id != direction.cta_section_id or closing.role != "closing":
        raise ValueError("O CTA precisa ocupar a última seção closing.")
    if _collapsed(closing.narration) != spoken_text:
        raise ValueError("A seção closing deve conter somente o CTA de uma ação.")
    if direction.callback.strip() and not _contains_normalized(
        closing.narration, direction.callback
    ):
        raise ValueError("O callback declarado precisa aparecer no CTA final.")


def _validate_engagement_contract(layout: ProjectLayout, package: ShortsScriptPackage) -> None:
    config = layout.load_manifest().config.shorts_script
    if config.duration_profile == "adaptive_compact_v1":
        strategy = package.strategy
        if strategy.cta.strip() or strategy.reason_to_subscribe.strip():
            raise ValueError("O perfil compacto não permite CTA nem reason_to_subscribe.")
        if strategy.comment_prompt is not None:
            raise ValueError("O perfil compacto não permite convite para comentários.")
        if any(beat.purpose == "cta" for beat in strategy.retention_beats):
            raise ValueError("O perfil compacto não permite beat de CTA.")
        return
    if config.narrative_standard == "creative_retention_v3":
        _validate_creative_engagement_contract(layout, package)
        return
    if config.narrative_standard == "retention_story_v2":
        _validate_retention_engagement_contract(layout, package)
        return
    strategy = package.strategy
    comment_prompt = strategy.comment_prompt
    if config.require_comment_prompt and comment_prompt is None:
        raise ValueError("O encerramento precisa escolher um strategy.comment_prompt.")
    if comment_prompt is not None:
        if not all(
            value.strip()
            for value in (
                comment_prompt.prompt_text,
                comment_prompt.response_cue,
                comment_prompt.objective,
                comment_prompt.fit_reason,
            )
        ):
            raise ValueError(
                "O comment_prompt precisa explicar texto, resposta, objetivo e escolha."
            )
        if len(tokenize_words(comment_prompt.response_cue)) > config.comment_response_max_words:
            raise ValueError("A resposta sugerida para o comentário está longa demais.")
        if not _contains_normalized(comment_prompt.prompt_text, comment_prompt.response_cue):
            raise ValueError("comment_prompt.response_cue precisa aparecer no convite falado.")
        normalized_prompt = _normalized(comment_prompt.prompt_text)
        if "coment" not in normalized_prompt and "escrev" not in normalized_prompt:
            raise ValueError("O comment_prompt precisa convidar claramente a escrever ou comentar.")
        forbidden_engagement_bait = (
            "comente amém para receber",
            "escreva amém para receber",
            "para receber uma bênção",
            "para deus te abençoar",
            "prove sua fé",
            "se você ama deus",
        )
        if any(
            _normalized(forbidden) in normalized_prompt for forbidden in forbidden_engagement_bait
        ):
            raise ValueError("O convite para comentar usa pressão ou promessa espiritual indevida.")
        cue_words = [
            _normalized(word)
            for word in tokenize_words(comment_prompt.response_cue)
            if _normalized(word)
        ]
        normalized_cue = " ".join(cue_words)
        significant_cue_words = [
            word for word in cue_words if len(word) >= _MIN_COMMENT_CUE_SIGNIFICANT_CHARACTERS
        ]
        if (
            len(cue_words) == 1 and normalized_cue in _WEAK_SINGLE_WORD_COMMENT_CUES
        ) or not significant_cue_words:
            raise ValueError(
                "A resposta sugerida para o comentário é vaga demais; escolha uma "
                "palavra-síntese expressiva e contextual."
            )
        normalized_fit_reason = _normalized(comment_prompt.fit_reason)
        if not any(word in normalized_fit_reason for word in significant_cue_words):
            raise ValueError(
                "comment_prompt.fit_reason precisa ligar explicitamente a palavra-síntese "
                "à resolução do vídeo."
            )
        if layout.load_manifest().config.editorial_policy.require_quoted_comment_response:
            quoted_response = quoted_comment_response(comment_prompt.prompt_text)
            if not quoted_response or _normalized(quoted_response) != _normalized(
                comment_prompt.response_cue
            ):
                raise ValueError(
                    "comment_prompt.response_cue precisa aparecer entre aspas no convite falado."
                )

    if config.require_subscribe_cta:
        if _collapsed(strategy.cta) != config.final_subscribe_cta:
            raise ValueError("O CTA final diverge do texto padronizado do projeto.")
        expected_closing = (
            f"{comment_prompt.prompt_text.strip()} {config.final_subscribe_cta}"
            if comment_prompt is not None
            else config.final_subscribe_cta
        )
        if _collapsed(package.script.sections[-1].narration) != _collapsed(expected_closing):
            raise ValueError(
                "O encerramento deve conter somente o convite para comentar seguido do CTA fixo."
            )
        if config.narrative_standard == "human_hook_to_bible":
            normalized_cta = _normalized(strategy.cta)
            if "curt" not in normalized_cta or "compartilh" not in normalized_cta:
                raise ValueError(
                    "O CTA novo precisa pedir para curtir, compartilhar e se inscrever."
                )


def _validate_language_and_visuals(package: ShortsScriptPackage) -> None:
    narration = _normalized(package.script.narration_text())
    if _normalized(LEGACY_SCRIPTURE_BOUNDARY_PHRASE) in narration:
        raise ValueError("O roteiro não pode narrar o rótulo 'Até aqui, o texto bíblico'.")
    if any(_normalized(phrase) in narration for phrase in _GENERIC_AI_PHRASES):
        raise ValueError(
            "O roteiro contém uma construção genérica ou com aparência de texto de IA."
        )
    for section in package.script.sections:
        direction = _normalized(section.visual_direction or "")
        if any(_normalized(term) in direction for term in _MULTI_SCENE_VISUAL_TERMS):
            raise ValueError(
                f"A direção visual de {section.section_id} pede múltiplas cenas na mesma imagem."
            )


def _collect_visual_scene_errors(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    errors: list[str],
) -> None:
    project = layout.load_manifest()
    if project.config.images.scene_planning_standard != "narration_block_visual_v1":
        return
    config = project.config.shorts_script
    planned = package.strategy.visual_scenes
    minimum_scenes = (
        3
        if config.generation_mode == "deterministic_compact_visual_plan_v1"
        else config.target_scene_min
    )
    if not minimum_scenes <= len(planned) <= config.target_scene_max:
        _add_validation_error(
            errors,
            "strategy.visual_scenes precisa conter de "
            f"{minimum_scenes} a {config.target_scene_max} blocos; recebeu "
            f"{len(planned)}.",
        )
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    section_order = {section.section_id: index for index, section in enumerate(sections)}
    previous_section_order = -1
    narration_by_section: dict[str, list[str]] = {section.section_id: [] for section in sections}
    descriptions: set[str] = set()
    for index, scene in enumerate(planned, start=1):
        if scene.section_id not in section_by_id:
            _add_validation_error(
                errors,
                f"visual_scenes[{index}] referencia section_id inexistente.",
            )
            continue
        current_order = section_order[scene.section_id]
        if current_order < previous_section_order:
            _add_validation_error(
                errors,
                "strategy.visual_scenes precisa preservar a ordem das seções do roteiro.",
            )
        previous_section_order = current_order
        narration_by_section[scene.section_id].append(scene.narration)
        normalized_description = _normalized(scene.scene_description)
        if normalized_description in descriptions:
            _add_validation_error(
                errors,
                "Cada bloco de áudio precisa ter uma descrição de cena específica e distinta.",
            )
        descriptions.add(normalized_description)
    for section in sections:
        section_scenes = [scene for scene in planned if scene.section_id == section.section_id]
        for scene in section_scenes[:-1]:
            if not _has_safe_compact_boundary(scene.narration):
                _add_validation_error(
                    errors,
                    "Um bloco visual intermediário de "
                    f"{section.section_id} termina sem pontuação natural segura: "
                    f"{scene.narration!r}.",
                )
        reconstructed = _collapsed(" ".join(narration_by_section[section.section_id]))
        if reconstructed != _collapsed(section.narration):
            _add_validation_error(
                errors,
                "Os blocos visuais de "
                f"{section.section_id} não reconstroem literalmente a narração da seção.",
            )


def _add_validation_error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _collect_cinematic_devotional_errors(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    errors: list[str],
) -> tuple[dict[str, Any], bool]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    strategy = package.strategy
    direction = strategy.cinematic_devotional
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    if direction is None:
        _add_validation_error(
            errors,
            "adaptive_compact_v1 exige strategy.cinematic_devotional.",
        )
        return section_by_id, False
    if any(
        value is not None
        for value in (
            strategy.creative_direction,
            strategy.retention_story,
            strategy.narrative_spine,
        )
    ):
        _add_validation_error(
            errors,
            "O perfil cinematográfico não deve preencher contratos narrativos dos outros perfis.",
        )

    briefing = read_model(layout.briefing, Briefing)
    if direction.creative_mode not in {"devotional_application", "pastoral_encouragement"}:
        _add_validation_error(errors, "O perfil compacto aceita somente os dois modos devocionais.")
    if direction.creative_mode != briefing.creative_mode:
        _add_validation_error(
            errors, "creative_mode diverge do modo compacto resolvido no briefing."
        )
    if (
        direction.interpretive_stance != "devotional"
        or briefing.interpretive_stance != "devotional"
    ):
        _add_validation_error(errors, "O perfil compacto exige postura devotional.")
    if direction.hook_family != "emotional_interruption":
        _add_validation_error(errors, "O perfil compacto exige emotional_interruption.")
    if strategy.hook_family != "emotional_interruption":
        _add_validation_error(errors, "strategy.hook_family precisa ser emotional_interruption.")
    if strategy.hook_type != "emotional_interruption":
        _add_validation_error(errors, "strategy.hook_type precisa ser emotional_interruption.")
    if not hook_matches_family(strategy.hook, "emotional_interruption"):
        _add_validation_error(
            errors,
            "A primeira frase não parece uma interrupção emocional curta dirigida ao espectador.",
        )
    if _normalized(direction.emotional_hook) != _normalized(strategy.hook):
        _add_validation_error(errors, "cinematic_devotional.emotional_hook precisa copiar o hook.")

    sources = read_model(layout.sources, SourceLibrary)
    compact_source = next(
        (source for source in sources.entries if source.source_id == direction.source_id),
        None,
    )
    if compact_source is None:
        _add_validation_error(errors, "O hook compacto não possui uma fonte bíblica associada.")
    else:
        if not compact_source.compact_hook.strip():
            _add_validation_error(errors, "A fonte compacta não possui um hook forte curado.")
        elif _normalized(strategy.hook) != _normalized(compact_source.compact_hook):
            _add_validation_error(
                errors,
                "O hook precisa copiar exatamente a combinação curada para esta passagem.",
            )
        if compact_source.compact_hook_strength_score < config.minimum_hook_strength_score:
            _add_validation_error(errors, "O hook ficou abaixo do piso de força configurado.")
        if compact_source.compact_message_value_score < config.minimum_message_value_score:
            _add_validation_error(errors, "A mensagem ficou abaixo do piso de valor configurado.")
        if compact_source.compact_rewatch_value_score < config.minimum_rewatch_value_score:
            _add_validation_error(errors, "A mensagem ficou abaixo do piso de replay configurado.")

    mapped_ids = [
        direction.opening_section_id,
        direction.scripture_section_id,
        direction.reference_section_id,
    ]
    structurally_valid = len(set(mapped_ids)) == 3 and set(mapped_ids).issubset(section_by_id)
    if len(sections) != 3:
        _add_validation_error(
            errors,
            "O momento cinematográfico compacto exige exatamente três seções.",
        )
        structurally_valid = False
    if not structurally_valid:
        _add_validation_error(
            errors, "cinematic_devotional referencia seções inválidas ou repetidas."
        )
    elif sections:
        ordered_ids = [section.section_id for section in sections]
        if mapped_ids != ordered_ids:
            _add_validation_error(
                errors,
                "A ordem deve ser hook, fala direta de Jesus e referência final.",
            )
        opening, scripture, reference = (section_by_id[item] for item in mapped_ids)
        if opening.role != "opening" or _collapsed(opening.narration) != _collapsed(strategy.hook):
            _add_validation_error(errors, "A abertura deve conter somente o hook emocional.")
        if scripture.role != "biblical_narrative":
            _add_validation_error(errors, "A fala de Jesus precisa usar role=biblical_narrative.")
        if reference.role != "closing":
            _add_validation_error(errors, "A referência final precisa usar role=closing.")
        if "lemos" in _normalized(scripture.narration) or "lemos" in _normalized(
            reference.narration
        ):
            _add_validation_error(errors, "O perfil compacto não usa a palavra 'lemos'.")

    if strategy.verse_opening is None:
        _add_validation_error(errors, "O perfil compacto exige uma fala direta de Jesus.")
    else:
        verse = strategy.verse_opening
        expected_reference = spoken_biblical_reference(verse.reference)
        if verse.lead_in.strip():
            _add_validation_error(errors, "A fala direta de Jesus não pode ter lead-in.")
        if verse.target_start_seconds != config.scripture_citation_min_start_seconds:
            _add_validation_error(errors, "A fala de Jesus deve começar imediatamente após o hook.")
        if verse.target_end_seconds > config.scripture_citation_max_end_seconds:
            _add_validation_error(errors, "A fala de Jesus invade o tempo reservado à referência.")
        if _normalized(direction.spoken_reference) != _normalized(expected_reference):
            _add_validation_error(errors, "spoken_reference diverge da passagem principal.")
        if direction.source_id != verse.source_id:
            _add_validation_error(errors, "cinematic_devotional.source_id diverge da citação.")
        if _normalized(strategy.payoff) != _normalized(verse.verse_text):
            _add_validation_error(errors, "strategy.payoff precisa ser a própria fala de Jesus.")
    if strategy.additional_scriptures:
        _add_validation_error(errors, "O perfil compacto aceita exatamente uma citação.")
    if _normalized(strategy.content_promise) != _normalized(direction.content_promise):
        _add_validation_error(errors, "content_promise precisa copiar cinematic_devotional.")
    if _normalized(strategy.practical_value) != _normalized(direction.viewer_takeaway):
        _add_validation_error(errors, "practical_value precisa copiar viewer_takeaway.")

    beats = strategy.retention_beats
    expected_purposes = ["hook", "evidence", "reference"]
    if [beat.purpose for beat in beats] != expected_purposes:
        _add_validation_error(errors, "Os beats compactos devem ser hook, evidence e reference.")
    if [beat.section_id for beat in beats] != mapped_ids:
        _add_validation_error(errors, "Os beats compactos precisam mapear as três seções em ordem.")
    final_end = max((beat.target_end_seconds for beat in beats), default=0.0)
    if not config.retention_target_min_seconds <= final_end <= config.retention_target_max_seconds:
        _add_validation_error(errors, "A referência final precisa encerrar na duração compacta.")
    if beats:
        if (
            beats[0].target_start_seconds != 0
            or beats[0].target_end_seconds != config.hook_target_seconds
        ):
            _add_validation_error(
                errors,
                f"O beat de hook precisa ocupar os primeiros {config.hook_target_seconds:g} "
                "segundos.",
            )
        if (
            len(beats) >= 3
            and beats[-1].target_start_seconds != config.scripture_citation_max_end_seconds
        ):
            _add_validation_error(errors, "O beat final deve começar no fechamento das aspas.")
    return section_by_id, structurally_valid


def _collect_narrative_spine_errors(
    package: ShortsScriptPackage,
    errors: list[str],
) -> tuple[dict[str, Any], bool]:
    strategy = package.strategy
    spine = strategy.narrative_spine
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    if spine is None:
        _add_validation_error(errors, "O roteiro precisa preencher strategy.narrative_spine.")
        return section_by_id, False

    spine_ids = [
        spine.verse_section_id,
        spine.tension_section_id,
        spine.bridge_section_id,
        spine.meaning_section_id,
        spine.application_section_id,
    ]
    structurally_valid = True
    if len(set(spine_ids)) != len(spine_ids):
        _add_validation_error(
            errors,
            "Cada movimento da espinha narrativa precisa de uma seção distinta.",
        )
        structurally_valid = False
    if not set(spine_ids).issubset(section_by_id):
        _add_validation_error(errors, "A espinha narrativa referencia section_id inexistente.")
        structurally_valid = False
    if len(sections) < 6 or (sections and sections[-1].section_id in spine_ids):
        _add_validation_error(
            errors,
            "O Short precisa de cinco movimentos editoriais e um encerramento.",
        )
    application = section_by_id.get(spine.application_section_id)
    if application is not None and application.role != "application":
        _add_validation_error(
            errors,
            "A resposta prática precisa usar uma seção role=application.",
        )

    mapped_text = (
        (spine.tension_section_id, spine.tension_question, "tension_question"),
        (spine.bridge_section_id, spine.bridge_statement, "bridge_statement"),
        (spine.meaning_section_id, spine.central_truth, "central_truth"),
        (spine.application_section_id, spine.practical_response, "practical_response"),
    )
    for section_id, required_text, field_name in mapped_text:
        section = section_by_id.get(section_id)
        if section is not None and not _contains_normalized(section.narration, required_text):
            _add_validation_error(
                errors,
                f"narrative_spine.{field_name} precisa aparecer na seção mapeada.",
            )
    if _normalized(strategy.open_loop) != _normalized(spine.tension_question):
        _add_validation_error(
            errors,
            "strategy.open_loop precisa repetir a tensão central da espinha.",
        )
    return section_by_id, structurally_valid


def _collect_retention_story_errors(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    errors: list[str],
) -> tuple[dict[str, Any], bool]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    strategy = package.strategy
    story = strategy.retention_story
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    if story is None:
        _add_validation_error(errors, "retention_story_v2 exige strategy.retention_story.")
        return section_by_id, False
    if strategy.narrative_spine is not None:
        _add_validation_error(
            errors,
            "retention_story_v2 não deve preencher o narrative_spine legado.",
        )

    briefing = read_model(layout.briefing, Briefing)
    if briefing.retention_archetype and story.archetype != briefing.retention_archetype:
        _add_validation_error(
            errors,
            "O arquétipo do roteiro diverge do arquétipo reservado no briefing.",
        )

    core_ids = [
        story.opening_section_id,
        story.scripture_section_id,
        story.reengagement_section_id,
        story.payoff_section_id,
        story.cta_section_id,
    ]
    structurally_valid = True
    if len(sections) < 7:
        _add_validation_error(
            errors,
            "retention_story_v2 exige ao menos sete movimentos editoriais.",
        )
    if len(set(story.progression_section_ids)) != len(story.progression_section_ids):
        _add_validation_error(errors, "progression_section_ids contém seção repetida.")
        structurally_valid = False
    if not set([*core_ids, *story.progression_section_ids]).issubset(section_by_id):
        _add_validation_error(errors, "retention_story referencia section_id inexistente.")
        structurally_valid = False
    if len(story.progression) != len(story.progression_section_ids):
        _add_validation_error(
            errors,
            "Cada item de progression precisa corresponder a uma progression_section_id.",
        )
        structurally_valid = False

    if structurally_valid and sections:
        ordered_ids = [section.section_id for section in sections]
        if story.opening_section_id != ordered_ids[0]:
            _add_validation_error(errors, "O cold open precisa ocupar a primeira seção.")
        if story.cta_section_id != ordered_ids[-1]:
            _add_validation_error(errors, "O CTA precisa ocupar a última seção.")
        progression_positions = [
            ordered_ids.index(section_id) for section_id in story.progression_section_ids
        ]
        if progression_positions != sorted(progression_positions):
            _add_validation_error(errors, "As progressões precisam seguir a ordem do roteiro.")
        if story.reengagement_section_id not in story.progression_section_ids:
            _add_validation_error(
                errors,
                "O reengajamento precisa fazer parte da progressão narrativa.",
            )
        if ordered_ids.index(story.scripture_section_id) <= 0:
            _add_validation_error(errors, "A citação precisa entrar depois do cold open.")
        if ordered_ids.index(story.payoff_section_id) <= ordered_ids.index(
            story.reengagement_section_id
        ):
            _add_validation_error(errors, "O payoff precisa vir depois do reengajamento.")

        opening = section_by_id[story.opening_section_id]
        scripture = section_by_id[story.scripture_section_id]
        payoff = section_by_id[story.payoff_section_id]
        cta = section_by_id[story.cta_section_id]
        if opening.role != "opening":
            _add_validation_error(errors, "O cold open precisa usar role=opening.")
        if scripture.role != "biblical_narrative":
            _add_validation_error(errors, "A citação precisa usar role=biblical_narrative.")
        if payoff.role != "application":
            _add_validation_error(errors, "O payoff precisa usar role=application.")
        if cta.role != "closing":
            _add_validation_error(errors, "O CTA precisa usar role=closing.")

        mapped_text = [
            (story.opening_section_id, story.opening_promise, "opening_promise"),
            (story.reengagement_section_id, story.reengagement, "reengagement"),
            (story.payoff_section_id, story.payoff, "payoff"),
            (story.payoff_section_id, story.practical_response, "practical_response"),
        ]
        mapped_text.extend(
            (section_id, text, f"progression[{index}]")
            for index, (section_id, text) in enumerate(
                zip(story.progression_section_ids, story.progression, strict=True)
            )
        )
        for section_id, required_text, field_name in mapped_text:
            if not _contains_normalized(section_by_id[section_id].narration, required_text):
                _add_validation_error(
                    errors,
                    f"retention_story.{field_name} precisa aparecer na seção mapeada.",
                )
        if not _contains_normalized(package.script.narration_text(), story.central_question):
            _add_validation_error(
                errors,
                "retention_story.central_question precisa aparecer na locução.",
            )
        opening_contract = f"{opening.narration} {opening.visual_direction or ''}"
        if not _contains_normalized(opening_contract, story.replay_bridge.opening_element):
            _add_validation_error(
                errors,
                "O elemento do replay_bridge precisa existir na abertura ou em sua direção visual.",
            )

    if _normalized(strategy.content_promise) != _normalized(story.opening_promise):
        _add_validation_error(errors, "content_promise precisa copiar opening_promise.")
    if _normalized(strategy.open_loop) != _normalized(story.central_question):
        _add_validation_error(errors, "open_loop precisa copiar central_question.")
    if _normalized(strategy.payoff) != _normalized(story.payoff):
        _add_validation_error(errors, "strategy.payoff precisa copiar retention_story.payoff.")
    if _normalized(strategy.practical_value) != _normalized(story.practical_response):
        _add_validation_error(
            errors,
            "practical_value precisa copiar retention_story.practical_response.",
        )
    if strategy.verse_opening is not None:
        verse = strategy.verse_opening
        if _normalized(story.scripture_anchor) != _normalized(verse.verse_text):
            _add_validation_error(errors, "scripture_anchor precisa copiar o versículo literal.")
        if verse.target_start_seconds < config.scripture_citation_min_start_seconds:
            _add_validation_error(
                errors,
                "A citação bíblica começa antes da janela configurada de "
                f"{config.scripture_citation_min_start_seconds:g}s.",
            )
        if verse.target_end_seconds > config.scripture_citation_max_end_seconds:
            _add_validation_error(
                errors,
                "A citação bíblica termina depois da janela configurada de "
                f"{config.scripture_citation_max_end_seconds:g}s.",
            )
        if len(tokenize_words(verse.verse_text)) > config.max_literal_verse_words:
            _add_validation_error(
                errors,
                "A citação literal ultrapassa o limite de "
                f"{config.max_literal_verse_words} palavras deste perfil.",
            )
        if verse.source_id not in story.source_ids:
            _add_validation_error(errors, "retention_story não registra a fonte da citação.")

    beats = strategy.retention_beats
    final_end = max((beat.target_end_seconds for beat in beats), default=0.0)
    if not config.retention_target_min_seconds <= final_end <= config.retention_target_max_seconds:
        _add_validation_error(
            errors,
            "retention_story_v2 precisa terminar entre "
            f"{config.retention_target_min_seconds:g} e "
            f"{config.retention_target_max_seconds:g} segundos.",
        )
    engagement = story.engagement
    if engagement.target_end_seconds != final_end:
        _add_validation_error(errors, "O CTA precisa terminar junto com o último beat.")
    cta_beats = [beat for beat in beats if beat.purpose == "cta"]
    if len(cta_beats) != 1 or cta_beats[0].section_id != story.cta_section_id:
        _add_validation_error(errors, "A estratégia precisa de exatamente um beat de CTA final.")
    elif (
        cta_beats[0].target_start_seconds != engagement.target_start_seconds
        or cta_beats[0].target_end_seconds != engagement.target_end_seconds
    ):
        _add_validation_error(errors, "O beat de CTA precisa usar os tempos do engagement.")

    progression_beats = [
        beat for beat in beats if beat.purpose in {"open_loop", "stakes", "progression"}
    ]
    if len(progression_beats) < config.minimum_progression_beats:
        _add_validation_error(errors, "O roteiro possui poucas progressões reais.")
    reengagement_beats = [
        beat
        for beat in beats
        if beat.section_id == story.reengagement_section_id
        and beat.purpose in {"pattern_interrupt", "reveal"}
    ]
    if len(reengagement_beats) != 1:
        _add_validation_error(errors, "O roteiro precisa de um reengajamento estruturado.")
    elif final_end:
        reengagement_share = reengagement_beats[0].target_start_seconds / final_end
        if not 0.4 <= reengagement_share <= 0.6:
            _add_validation_error(errors, "O reengajamento precisa começar entre 40% e 60%.")
    if not any(
        beat.section_id == story.payoff_section_id and beat.purpose == "payoff" for beat in beats
    ):
        _add_validation_error(errors, "O payoff precisa de um beat próprio.")

    new_information = []
    for beat in beats:
        if not (beat.new_information or "").strip() or not (beat.visual_change or "").strip():
            _add_validation_error(
                errors,
                "Cada beat de retention_story_v2 precisa declarar new_information e visual_change.",
            )
            break
        new_information.append(_normalized(beat.new_information or ""))
    if len(new_information) != len(set(new_information)):
        _add_validation_error(errors, "Os beats repetem a mesma informação em vez de progredir.")
    return section_by_id, structurally_valid


def _collect_creative_direction_errors(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    errors: list[str],
) -> tuple[dict[str, Any], bool]:
    project = layout.load_manifest()
    config = project.config.shorts_script
    strategy = package.strategy
    direction = strategy.creative_direction
    sections = package.script.sections
    section_by_id = {section.section_id: section for section in sections}
    if direction is None:
        _add_validation_error(errors, "creative_retention_v3 exige strategy.creative_direction.")
        return section_by_id, False
    if strategy.retention_story is not None or strategy.narrative_spine is not None:
        _add_validation_error(
            errors,
            "creative_retention_v3 não deve preencher contratos narrativos legados.",
        )

    briefing = read_model(layout.briefing, Briefing)
    if direction.creative_mode != briefing.creative_mode:
        _add_validation_error(errors, "creative_mode diverge do modo resolvido no briefing.")
    if direction.interpretive_stance != briefing.interpretive_stance:
        _add_validation_error(errors, "interpretive_stance diverge do briefing.")
    if direction.hook_family != briefing.hook_family:
        _add_validation_error(errors, "hook_family diverge da família resolvida no briefing.")
    if strategy.hook_family != direction.hook_family:
        _add_validation_error(errors, "strategy.hook_family precisa copiar creative_direction.")
    if briefing.hook_contract == "high_stakes_opening_v1" and _normalized(
        strategy.hook
    ) != _normalized(briefing.opening_hook):
        _add_validation_error(errors, "O hook investigativo precisa copiar o opening_hook curado.")
    hook_is_valid = (
        hook_matches_user_six_template(strategy.hook, direction.hook_family)
        if briefing.hook_contract == "user_six_v1"
        else hook_matches_family(strategy.hook, direction.hook_family)
    )
    if not hook_is_valid:
        _add_validation_error(errors, "O hook não corresponde à família declarada.")

    core_ids = [
        direction.opening_section_id,
        *direction.scripture_section_ids,
        direction.resolution_section_id,
        direction.cta_section_id,
    ]
    mapped_ids = [*core_ids, *direction.development_section_ids]
    structurally_valid = True
    if len(sections) < config.minimum_sections:
        _add_validation_error(
            errors,
            "creative_retention_v3 exige ao menos "
            f"{config.minimum_sections} movimentos editoriais neste perfil.",
        )
    if len(set(direction.development_section_ids)) != len(direction.development_section_ids):
        _add_validation_error(errors, "development_section_ids contém seção repetida.")
        structurally_valid = False
    if not set(mapped_ids).issubset(section_by_id):
        _add_validation_error(errors, "creative_direction referencia section_id inexistente.")
        structurally_valid = False
    if not set(direction.reengagement_section_ids).issubset(direction.development_section_ids):
        _add_validation_error(
            errors,
            "Todo reengajamento precisa fazer parte dos movimentos de desenvolvimento.",
        )
        structurally_valid = False

    if structurally_valid and sections:
        ordered_ids = [section.section_id for section in sections]
        if direction.opening_section_id != ordered_ids[0]:
            _add_validation_error(errors, "O hook precisa ocupar a primeira seção.")
        if direction.cta_section_id != ordered_ids[-1]:
            _add_validation_error(errors, "O CTA precisa ocupar a última seção.")
        development_positions = [
            ordered_ids.index(section_id) for section_id in direction.development_section_ids
        ]
        if development_positions != sorted(development_positions):
            _add_validation_error(errors, "O desenvolvimento precisa seguir a ordem do roteiro.")
        if ordered_ids.index(direction.resolution_section_id) <= max(
            ordered_ids.index(section_id) for section_id in direction.reengagement_section_ids
        ):
            _add_validation_error(errors, "A resolução precisa vir depois do reengajamento.")

        opening = section_by_id[direction.opening_section_id]
        resolution = section_by_id[direction.resolution_section_id]
        cta = section_by_id[direction.cta_section_id]
        if opening.role != "opening":
            _add_validation_error(errors, "O hook precisa usar role=opening.")
        if _uses_scroll_stop_contract(layout):
            opening_visual = direction.opening_visual_contract
            if opening_visual is None:
                _add_validation_error(
                    errors,
                    "creative_direction precisa declarar opening_visual_contract para a abertura.",
                )
            else:
                for visual_error in _opening_action_contract_errors(
                    opening_visual.observable_action,
                    initial_state=opening_visual.initial_state,
                    resulting_state=opening_visual.resulting_state,
                ):
                    _add_validation_error(errors, visual_error)
                normalized_tension = _normalized(opening_visual.concrete_tension)
                tension_markers = (
                    " ou ",
                    " enquanto ",
                    " contra ",
                    " mas ",
                    " entre ",
                    " apesar ",
                )
                if not any(marker in f" {normalized_tension} " for marker in tension_markers):
                    _add_validation_error(
                        errors,
                        "opening_visual_contract precisa tornar duas respostas ou consequências "
                        "visualmente distinguíveis.",
                    )
        for scripture_id in direction.scripture_section_ids:
            if section_by_id[scripture_id].role != "biblical_narrative":
                _add_validation_error(errors, "Toda citação precisa usar role=biblical_narrative.")
                break
        if resolution.role not in {"biblical_narrative", "reflection", "application"}:
            _add_validation_error(errors, "A resolução precisa usar uma role de desenvolvimento.")
        if direction.practical_application.strip() and resolution.role != "application":
            _add_validation_error(errors, "A aplicação declarada precisa usar role=application.")
        if cta.role != "closing":
            _add_validation_error(errors, "O CTA precisa usar role=closing.")

        mapped_text = (
            (direction.opening_section_id, direction.content_promise, "content_promise"),
            (direction.resolution_section_id, direction.resolution, "resolution"),
            (direction.resolution_section_id, direction.viewer_takeaway, "viewer_takeaway"),
        )
        for section_id, required_text, field_name in mapped_text:
            if not _contains_normalized(section_by_id[section_id].narration, required_text):
                _add_validation_error(
                    errors,
                    f"creative_direction.{field_name} precisa aparecer na seção mapeada.",
                )
        if (
            briefing.hook_contract != "high_stakes_opening_v1"
            and direction.organizing_question.strip()
            and not _contains_normalized(
                package.script.narration_text(), direction.organizing_question
            )
        ):
            _add_validation_error(
                errors,
                "A organizing_question declarada precisa aparecer na locução.",
            )
        if direction.practical_application.strip() and not _contains_normalized(
            resolution.narration, direction.practical_application
        ):
            _add_validation_error(
                errors,
                "A practical_application declarada precisa aparecer na resolução.",
            )

    if _normalized(strategy.content_promise) != _normalized(direction.content_promise):
        _add_validation_error(errors, "content_promise precisa copiar creative_direction.")
    expected_loop = direction.organizing_question or direction.content_promise
    if _normalized(strategy.open_loop) != _normalized(expected_loop):
        _add_validation_error(
            errors,
            "open_loop precisa copiar organizing_question ou content_promise.",
        )
    if _normalized(strategy.payoff) != _normalized(direction.resolution):
        _add_validation_error(errors, "strategy.payoff precisa copiar a resolução.")
    if _normalized(strategy.practical_value) != _normalized(direction.viewer_takeaway):
        _add_validation_error(errors, "practical_value precisa copiar viewer_takeaway.")

    if strategy.verse_opening is not None:
        verse = strategy.verse_opening
        if (
            briefing.hook_contract == "high_stakes_opening_v1"
            and verse.source_id != briefing.primary_literal_source_id
        ):
            _add_validation_error(
                errors,
                "A citação investigativa precisa usar a fonte literal primária selada.",
            )
        if verse.target_start_seconds < config.scripture_citation_min_start_seconds:
            _add_validation_error(
                errors,
                "A citação bíblica começa antes da janela configurada de "
                f"{config.scripture_citation_min_start_seconds:g}s.",
            )
        if verse.target_end_seconds > config.scripture_citation_max_end_seconds:
            _add_validation_error(
                errors,
                "A citação bíblica termina depois da janela configurada de "
                f"{config.scripture_citation_max_end_seconds:g}s.",
            )
        if len(tokenize_words(verse.verse_text)) > config.max_literal_verse_words:
            _add_validation_error(
                errors,
                "A citação literal ultrapassa o limite de "
                f"{config.max_literal_verse_words} palavras deste perfil.",
            )
        if verse.source_id not in direction.source_ids:
            _add_validation_error(errors, "creative_direction não registra a fonte da citação.")

    beats = strategy.retention_beats
    final_end = max((beat.target_end_seconds for beat in beats), default=0.0)
    if not config.retention_target_min_seconds <= final_end <= config.retention_target_max_seconds:
        _add_validation_error(
            errors,
            "creative_retention_v3 precisa terminar entre "
            f"{config.retention_target_min_seconds:g} e "
            f"{config.retention_target_max_seconds:g} segundos.",
        )
    engagement = direction.engagement
    if engagement.target_end_seconds != final_end:
        _add_validation_error(errors, "O CTA precisa terminar junto com o último beat.")
    cta_beats = [beat for beat in beats if beat.purpose == "cta"]
    if len(cta_beats) != 1 or cta_beats[0].section_id != direction.cta_section_id:
        _add_validation_error(errors, "A estratégia precisa de exatamente um beat de CTA final.")
    elif (
        cta_beats[0].target_start_seconds != engagement.target_start_seconds
        or cta_beats[0].target_end_seconds != engagement.target_end_seconds
    ):
        _add_validation_error(errors, "O beat de CTA precisa usar os tempos do engagement.")

    development_purposes = {
        "orientation",
        "stakes",
        "progression",
        "evidence",
        "context",
        "analysis",
        "comparison",
        "reveal",
    }
    development_beats = [beat for beat in beats if beat.purpose in development_purposes]
    if len(development_beats) < config.minimum_progression_beats:
        _add_validation_error(errors, "O roteiro possui poucos movimentos de desenvolvimento.")
    if (
        config.performance_profile == "feed_depth_45_60_v1"
        and len(development_beats) > config.maximum_progression_beats
    ):
        _add_validation_error(
            errors,
            "O perfil de 45 a 60 segundos aceita no máximo "
            f"{config.maximum_progression_beats} avanços antes do payoff.",
        )
    reengagement_beats = [
        beat
        for beat in beats
        if beat.section_id in direction.reengagement_section_ids
        and beat.purpose in {"pattern_interrupt", "reveal"}
    ]
    if len(reengagement_beats) != len(direction.reengagement_section_ids):
        _add_validation_error(errors, "O roteiro não mapeia todos os reengajamentos declarados.")
    elif final_end and any(
        not 0.4 <= beat.target_start_seconds / final_end <= 0.6 for beat in reengagement_beats
    ):
        _add_validation_error(errors, "O reengajamento precisa começar entre 40% e 60%.")
    if not any(
        beat.section_id == direction.resolution_section_id and beat.purpose == "resolution"
        for beat in beats
    ):
        _add_validation_error(errors, "A resolução precisa de um beat próprio.")
    if not any(beat.purpose == "hook" for beat in beats):
        _add_validation_error(errors, "Shorts exigem um beat inicial de hook.")
    if _uses_scroll_stop_contract(layout):
        expected_loop = direction.organizing_question.strip() or direction.content_promise.strip()
        opening_beats = [
            beat
            for beat in beats
            if beat.section_id == direction.opening_section_id and beat.purpose == "hook"
        ]
        if len(opening_beats) != 1:
            _add_validation_error(
                errors,
                "A abertura precisa ter exatamente um beat de hook para abrir o loop central.",
            )
        elif _normalized(opening_beats[0].opens_loop or "") != _normalized(expected_loop):
            _add_validation_error(
                errors,
                "O beat inicial precisa abrir exatamente a pergunta ou promessa central.",
            )
        resolution_beats = [
            beat
            for beat in beats
            if beat.section_id == direction.resolution_section_id and beat.purpose == "resolution"
        ]
        if len(resolution_beats) != 1:
            _add_validation_error(
                errors,
                "A resolução precisa ter exatamente um beat para fechar o loop central.",
            )
        elif _normalized(resolution_beats[0].resolves_loop or "") != _normalized(expected_loop):
            _add_validation_error(
                errors,
                "O beat de resolução precisa fechar exatamente o mesmo loop aberto no hook.",
            )
        unauthorized_openers = [
            beat
            for beat in beats
            if beat.opens_loop
            and not (beat.section_id == direction.opening_section_id and beat.purpose == "hook")
        ]
        unauthorized_resolvers = [
            beat
            for beat in beats
            if beat.resolves_loop
            and not (
                beat.section_id == direction.resolution_section_id and beat.purpose == "resolution"
            )
        ]
        if unauthorized_openers or unauthorized_resolvers:
            _add_validation_error(
                errors,
                "O contrato exige um único loop central: somente o hook pode abri-lo e somente "
                "a resolução pode fechá-lo.",
            )

    new_information = []
    for beat in beats:
        if not (beat.new_information or "").strip() or not (beat.visual_change or "").strip():
            _add_validation_error(
                errors,
                "Cada beat de creative_retention_v3 precisa declarar new_information e "
                "visual_change.",
            )
            break
        new_information.append(_normalized(beat.new_information or ""))
    if len(new_information) != len(set(new_information)):
        _add_validation_error(errors, "Os beats repetem informação em vez de progredir.")

    sources = read_model(layout.sources, SourceLibrary)
    for evidence_error in creative_evidence_errors(
        package.script,
        sources,
        creative_mode=direction.creative_mode,
        interpretive_stance=direction.interpretive_stance,
    ):
        _add_validation_error(errors, evidence_error)
    return section_by_id, structurally_valid


def _hook_exceeds_configured_limit(
    *,
    hook: str,
    hook_max_words: int,
    briefing: Briefing,
    allow_curated_override: bool,
) -> bool:
    if (
        allow_curated_override
        and briefing.hook_contract == "high_stakes_opening_v1"
        and _normalized(hook) == _normalized(briefing.opening_hook)
    ):
        return False
    return len(tokenize_words(hook)) > hook_max_words


def _collect_package_validation_errors(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
) -> list[str]:
    errors: list[str] = []
    project = layout.load_manifest()
    config = project.config.shorts_script
    briefing = read_model(layout.briefing, Briefing)
    script = package.script
    strategy = package.strategy
    sections = script.sections
    try:
        validate_authorial_narration_style(script)
    except ValueError as exc:
        _add_validation_error(errors, str(exc))
    if not sections or sections[0].role != "opening" or sections[-1].role != "closing":
        _add_validation_error(
            errors, "O roteiro precisa começar com opening e terminar com closing."
        )
    section_ids = [section.section_id for section in sections]
    if len(section_ids) != len(set(section_ids)):
        _add_validation_error(errors, "O roteiro contém section_id duplicado.")
    _collect_visual_scene_errors(layout, package, errors)
    if any(_RAW_NUMERIC_REFERENCE_PATTERN.search(section.narration) for section in sections):
        _add_validation_error(
            errors,
            "Referências bíblicas faladas não podem usar o formato numérico 'Livro X:Y'; "
            "narre 'Livro, capítulo X, versículo Y'.",
        )
    words = tokenize_words(script.narration_text())
    if (
        project.duration_policy != "adaptive_range_v1"
        and not config.min_words <= len(words) <= config.max_words
    ):
        _add_validation_error(
            errors,
            f"O roteiro tem {len(words)} palavras; esperado entre "
            f"{config.min_words} e {config.max_words}.",
        )
    estimated_duration = len(words) / config.estimated_narration_wpm * 60
    uses_duration_window = project.duration_policy in {
        "preferred_target_v1",
        "adaptive_range_v1",
    }
    preferred_minimum = (
        project.requested_duration_min_seconds
        if uses_duration_window
        else project.target_duration_seconds
    )
    preferred_maximum = (
        project.requested_duration_max_seconds
        if uses_duration_window
        else project.target_duration_seconds
    )
    assert preferred_minimum is not None and preferred_maximum is not None
    minimum_estimated_duration = preferred_minimum * config.minimum_estimated_duration_ratio
    if estimated_duration < minimum_estimated_duration:
        _add_validation_error(
            errors,
            f"O roteiro estima somente {estimated_duration:.1f}s de narração; precisa chegar a "
            f"pelo menos {minimum_estimated_duration:.1f}s com hook ou versículo mais longo, "
            "sem preencher o restante com silêncio.",
        )
    if estimated_duration > preferred_maximum + config.duration_tolerance_seconds:
        _add_validation_error(
            errors,
            f"O roteiro estima {estimated_duration:.1f}s na voz configurada e ultrapassa o "
            f"limite de {preferred_maximum:g}s.",
        )
    hook_words = tokenize_words(strategy.hook)
    if len(hook_words) < config.hook_min_words:
        _add_validation_error(errors, "O gancho está curto demais para o perfil configurado.")
    if _hook_exceeds_configured_limit(
        hook=strategy.hook,
        hook_max_words=config.hook_max_words,
        briefing=briefing,
        allow_curated_override=config.performance_profile != "feed_depth_45_60_v1",
    ):
        _add_validation_error(errors, "O gancho ultrapassa o limite de palavras configurado.")
    if strategy.hook_target_end_seconds > config.hook_target_seconds + 0.5:
        _add_validation_error(errors, "O gancho termina tarde demais para a meta inicial do Short.")
    if sections and not _normalized(sections[0].narration).startswith(_normalized(strategy.hook)):
        _add_validation_error(errors, "strategy.hook precisa ser o prefixo exato da abertura.")
    closing = _normalized(sections[-1].narration) if sections else ""
    if sections and not closing.endswith(_normalized(strategy.cta)):
        _add_validation_error(errors, "strategy.cta precisa ser o sufixo exato do encerramento.")
    if config.require_subscribe_cta and "inscrev" not in closing:
        _add_validation_error(errors, "O CTA final precisa convidar o espectador a se inscrever.")
    if (
        config.narrative_standard not in {"retention_story_v2", "creative_retention_v3"}
        and not strategy.reason_to_subscribe.strip()
    ):
        _add_validation_error(errors, "O CTA precisa explicar o benefício de acompanhar o canal.")
    if len(strategy.retention_beats) < config.minimum_retention_beats:
        _add_validation_error(errors, "A estratégia contém poucos beats de retenção.")
    beat_ids = [beat.section_id for beat in strategy.retention_beats]
    if not set(beat_ids).issubset(section_ids):
        _add_validation_error(errors, "A estratégia referencia section_id inexistente.")
    previous_start = -1.0
    for beat in strategy.retention_beats:
        if beat.target_start_seconds < previous_start:
            _add_validation_error(errors, "Os beats de retenção não estão em ordem temporal.")
        if beat.target_end_seconds > project.target_duration_seconds:
            _add_validation_error(errors, "Um beat ultrapassa a duração-alvo do Short.")
        previous_start = beat.target_start_seconds
    if config.narrative_standard != "creative_retention_v3":
        if not any(beat.opens_loop for beat in strategy.retention_beats):
            _add_validation_error(
                errors, "A estratégia precisa abrir ao menos um loop de curiosidade."
            )
        if not any(beat.resolves_loop for beat in strategy.retention_beats):
            _add_validation_error(errors, "A estratégia precisa resolver o loop de curiosidade.")

    narration = _normalized(script.narration_text())
    if _normalized(LEGACY_SCRIPTURE_BOUNDARY_PHRASE) in narration:
        _add_validation_error(
            errors,
            "O roteiro não pode narrar o rótulo 'Até aqui, o texto bíblico'.",
        )
    if any(_normalized(phrase) in narration for phrase in _GENERIC_AI_PHRASES):
        _add_validation_error(
            errors,
            "O roteiro contém uma construção genérica ou com aparência de texto de IA.",
        )
    for section in sections:
        direction = _normalized(section.visual_direction or "")
        if any(_normalized(term) in direction for term in _MULTI_SCENE_VISUAL_TERMS):
            _add_validation_error(
                errors,
                f"A direção visual de {section.section_id} pede múltiplas cenas na mesma imagem.",
            )

    sources = read_model(layout.sources, SourceLibrary)
    spine = strategy.narrative_spine
    story = strategy.retention_story
    creative = strategy.creative_direction
    cinematic = strategy.cinematic_devotional
    is_compact_cinematic = config.duration_profile == "adaptive_compact_v1"
    if is_compact_cinematic:
        section_by_id, structure_ready = _collect_cinematic_devotional_errors(
            layout,
            package,
            errors,
        )
        if strategy.verse_opening is None:
            _add_validation_error(errors, "O momento compacto exige uma fala direta de Jesus.")
        if not 1 <= len(strategy.hook_techniques) <= 3:
            _add_validation_error(
                errors,
                "O hook emocional precisa registrar de uma a três técnicas de atenção.",
            )
    elif config.narrative_standard == "creative_retention_v3":
        section_by_id, structure_ready = _collect_creative_direction_errors(
            layout,
            package,
            errors,
        )
        if strategy.verse_opening is None:
            _add_validation_error(
                errors,
                "creative_retention_v3 exige ao menos uma citação bíblica literal.",
            )
        if strategy.hook_type == "scripture_opening":
            _add_validation_error(errors, "O hook inicial não pode ser a referência bíblica.")
        if not 1 <= len(strategy.hook_techniques) <= 3:
            _add_validation_error(
                errors,
                "O hook precisa registrar de uma a três técnicas de atenção.",
            )
    elif config.narrative_standard == "retention_story_v2":
        section_by_id, structure_ready = _collect_retention_story_errors(
            layout,
            package,
            errors,
        )
        if strategy.verse_opening is None:
            _add_validation_error(
                errors,
                "retention_story_v2 exige ao menos uma citação bíblica literal.",
            )
        if strategy.hook_type == "scripture_opening":
            _add_validation_error(errors, "O cold open não pode começar pela referência bíblica.")
        if not 1 <= len(strategy.hook_techniques) <= 3:
            _add_validation_error(
                errors,
                "O cold open precisa registrar de uma a três técnicas de atenção.",
            )
    else:
        section_by_id, structure_ready = _collect_narrative_spine_errors(package, errors)

    if config.narrative_standard == "verse_first":
        if strategy.verse_opening is None or spine is None:
            _add_validation_error(
                errors,
                "O padrão verse_first exige strategy.verse_opening e strategy.narrative_spine.",
            )
        elif spine.pattern != "verse_first" or strategy.hook_type != "scripture_opening":
            _add_validation_error(
                errors,
                "O gancho verse_first precisa usar pattern e hook_type bíblicos.",
            )
        if (
            strategy.verse_opening is not None
            and strategy.verse_opening.target_end_seconds > config.verse_opening_max_seconds
        ):
            _add_validation_error(
                errors,
                "O versículo termina tarde demais para a abertura configurada.",
            )
        if spine is not None and sections:
            if spine.verse_section_id != sections[0].section_id:
                _add_validation_error(
                    errors,
                    "O versículo precisa ocupar a primeira seção do padrão verse_first.",
                )
            verse_section = section_by_id.get(spine.verse_section_id)
            if verse_section is not None and verse_section.role != "opening":
                _add_validation_error(
                    errors,
                    "A seção do versículo precisa usar role=opening.",
                )
    elif config.narrative_standard == "human_hook_to_bible":
        if strategy.verse_opening is None or spine is None:
            _add_validation_error(
                errors,
                "O padrão human_hook_to_bible exige citação bíblica e espinha narrativa.",
            )
        elif spine.pattern != "human_hook_to_bible":
            _add_validation_error(
                errors,
                "narrative_spine.pattern precisa ser human_hook_to_bible.",
            )
        if strategy.hook_type == "scripture_opening":
            _add_validation_error(
                errors,
                "O novo padrão precisa começar com um gancho humano, não com a referência.",
            )
        if not 1 <= len(strategy.hook_techniques) <= 3:
            _add_validation_error(
                errors,
                "O gancho precisa registrar de uma a três técnicas naturais de atenção.",
            )
        if spine is not None and structure_ready and sections:
            ordered_ids = [section.section_id for section in sections]
            expected_order = [
                spine.tension_section_id,
                spine.verse_section_id,
                spine.bridge_section_id,
                spine.meaning_section_id,
                spine.application_section_id,
            ]
            positions = [ordered_ids.index(section_id) for section_id in expected_order]
            if positions != sorted(positions):
                _add_validation_error(
                    errors,
                    "A estrutura deve seguir gancho, Bíblia, contexto, sentido e aplicação.",
                )
            if spine.tension_section_id != sections[0].section_id:
                _add_validation_error(
                    errors,
                    "A primeira seção precisa ser o gancho humano do Short.",
                )
            if ordered_ids.index(spine.verse_section_id) != 1:
                _add_validation_error(
                    errors,
                    "A citação bíblica precisa entrar imediatamente depois do gancho.",
                )
            verse_section = section_by_id.get(spine.verse_section_id)
            if verse_section is not None and verse_section.role != "biblical_narrative":
                _add_validation_error(
                    errors,
                    "A citação após o gancho precisa usar role=biblical_narrative.",
                )

    strategy_structure = (
        cinematic is not None
        if is_compact_cinematic
        else creative is not None
        if config.narrative_standard == "creative_retention_v3"
        else story is not None
        if config.narrative_standard == "retention_story_v2"
        else spine is not None
    )
    scripture_structure_ready = strategy_structure and structure_ready
    if scripture_structure_ready and strategy.verse_opening is not None:
        try:
            required_source_ids = _validate_scripture_contract(
                layout,
                package,
                sources,
                section_by_id,
            )
        except ValueError as exc:
            _add_validation_error(errors, str(exc))
        else:
            if is_compact_cinematic and cinematic is not None:
                required_source_ids.add(cinematic.source_id)
                if not required_source_ids.issubset(package.script.source_ids):
                    _add_validation_error(
                        errors,
                        "O roteiro não registra a fonte da fala direta de Jesus.",
                    )
                if not required_source_ids.issubset(strategy.source_ids):
                    _add_validation_error(
                        errors,
                        "A estratégia não registra a fonte da fala direta de Jesus.",
                    )
            elif config.narrative_standard == "creative_retention_v3" and creative is not None:
                required_source_ids |= set(creative.source_ids)
                if not required_source_ids.issubset(package.script.source_ids):
                    _add_validation_error(
                        errors,
                        "O roteiro não registra todas as fontes da direção criativa.",
                    )
                if not required_source_ids.issubset(strategy.source_ids):
                    _add_validation_error(
                        errors,
                        "A estratégia não registra todas as fontes da direção criativa.",
                    )
            elif config.narrative_standard == "retention_story_v2" and story is not None:
                required_source_ids |= set(story.source_ids)
                if not required_source_ids.issubset(package.script.source_ids):
                    _add_validation_error(
                        errors,
                        "O roteiro não registra todas as fontes do retention_story.",
                    )
                if not required_source_ids.issubset(strategy.source_ids):
                    _add_validation_error(
                        errors,
                        "A estratégia não registra todas as fontes do retention_story.",
                    )
            else:
                try:
                    _validate_bridge_sources(package, sources, required_source_ids)
                except ValueError as exc:
                    _add_validation_error(errors, str(exc))

    try:
        _validate_engagement_contract(layout, package)
    except ValueError as exc:
        _add_validation_error(errors, str(exc))

    valid_source_ids = {entry.source_id for entry in sources.entries}
    referenced_sources = set(script.source_ids) | set(strategy.source_ids)
    if not referenced_sources.issubset(valid_source_ids):
        _add_validation_error(errors, "O roteiro ou a estratégia referencia source_id inexistente.")
    if set(strategy.section_ids) != set(section_ids):
        _add_validation_error(
            errors, "strategy.section_ids precisa mapear todas as seções do roteiro."
        )
    for section in sections:
        if not section.biblical_basis:
            _add_validation_error(
                errors,
                f"A seção {section.section_id} não registra base bíblica.",
            )
        if not section.claim_classification:
            _add_validation_error(
                errors,
                f"A seção {section.section_id} não classifica suas afirmações.",
            )
    review = package.biblical_review
    if review.script_version != script.version:
        _add_validation_error(
            errors,
            "A revisão bíblica do pacote não corresponde ao roteiro entregue.",
        )
    if review.has_open_blockers:
        _add_validation_error(
            errors,
            "A revisão bíblica do pacote ainda possui bloqueios abertos.",
        )
    return errors


def _validate_package(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
    queue: dict[str, Any],
) -> None:
    if package.job_id != queue.get("job_id") or package.input_hash != queue.get("input_hash"):
        raise RuntimeError("O pacote do roteirista pertence a uma fila antiga.")
    errors = _collect_package_validation_errors(layout, package)
    if errors:
        raise ShortsScriptPackageValidationError(errors)


def _load_shorts_script_queue(layout: ProjectLayout) -> dict[str, Any]:
    if not layout.shorts_script_queue.is_file():
        raise FileNotFoundError(f"Fila do roteirista ausente: {layout.shorts_script_queue}")
    queue = read_json(layout.shorts_script_queue)
    if not isinstance(queue, dict) or queue.get("role") != "shorts_script_specialist":
        raise ValueError("Fila do roteirista inválida.")
    frozen_advisories = _frozen_performance_advisories(layout, queue)
    if queue.get("input_hash") != shorts_script_input_hash(
        layout,
        performance_advisories=frozen_advisories,
    ):
        raise RuntimeError("A fila do roteirista ficou obsoleta após mudanças editoriais.")
    baseline = queue.get("baseline_script_hash")
    if baseline != _baseline_hash(layout):
        raise RuntimeError("O roteiro ativo mudou depois que a fila do especialista foi criada.")
    return queue


def _normalize_staging_package_payload(payload: Any) -> tuple[Any, tuple[str, ...]]:
    normalized = deepcopy(payload)
    normalizations: list[str] = []
    if not isinstance(normalized, dict):
        return normalized, ()
    strategy = normalized.get("strategy")
    if not isinstance(strategy, dict):
        return normalized, ()
    creative = strategy.get("creative_direction")
    if isinstance(creative, dict):
        organizing_question = creative.get("organizing_question")
        content_promise = creative.get("content_promise")
        expected_loop = (
            organizing_question
            if isinstance(organizing_question, str) and organizing_question.strip()
            else content_promise
        )
        if isinstance(expected_loop, str) and strategy.get("open_loop") != expected_loop:
            strategy["open_loop"] = expected_loop
            normalizations.append("strategy.open_loop sincronizado com creative_direction.")
        field_pairs = (
            ("content_promise", "content_promise"),
            ("payoff", "resolution"),
            ("practical_value", "viewer_takeaway"),
            ("hook_family", "hook_family"),
        )
        for strategy_field, creative_field in field_pairs:
            value = creative.get(creative_field)
            if isinstance(value, str) and strategy.get(strategy_field) != value:
                strategy[strategy_field] = value
                normalizations.append(
                    f"strategy.{strategy_field} sincronizado com "
                    f"creative_direction.{creative_field}."
                )
        engagement = creative.get("engagement")
        spoken_text = engagement.get("spoken_text") if isinstance(engagement, dict) else None
        if isinstance(spoken_text, str) and strategy.get("cta") != spoken_text:
            strategy["cta"] = spoken_text
            normalizations.append(
                "strategy.cta sincronizado com creative_direction.engagement.spoken_text."
            )
        return normalized, tuple(normalizations)
    story = strategy.get("retention_story")
    if isinstance(story, dict):
        central_question = story.get("central_question")
        if isinstance(central_question, str) and central_question.strip():
            open_loop = strategy.get("open_loop")
            if not isinstance(open_loop, str) or _normalized(open_loop) != _normalized(
                central_question
            ):
                strategy["open_loop"] = central_question
                normalizations.append(
                    "strategy.open_loop sincronizado com retention_story.central_question."
                )
        field_pairs = (
            ("payoff", "payoff"),
            ("practical_value", "practical_response"),
        )
        for strategy_field, story_field in field_pairs:
            value = story.get(story_field)
            if isinstance(value, str) and strategy.get(strategy_field) != value:
                strategy[strategy_field] = value
                normalizations.append(
                    f"strategy.{strategy_field} sincronizado com retention_story.{story_field}."
                )
        engagement = story.get("engagement")
        spoken_text = engagement.get("spoken_text") if isinstance(engagement, dict) else None
        if isinstance(spoken_text, str) and strategy.get("cta") != spoken_text:
            strategy["cta"] = spoken_text
            normalizations.append(
                "strategy.cta sincronizado com retention_story.engagement.spoken_text."
            )
        return normalized, tuple(normalizations)
    spine = strategy.get("narrative_spine")
    if not isinstance(spine, dict):
        return normalized, ()
    tension_question = spine.get("tension_question")
    if not isinstance(tension_question, str) or not tension_question.strip():
        return normalized, ()
    open_loop = strategy.get("open_loop")
    if not isinstance(open_loop, str) or _normalized(open_loop) != _normalized(tension_question):
        strategy["open_loop"] = tension_question
        normalizations.append(
            "strategy.open_loop sincronizado com narrative_spine.tension_question."
        )
    return normalized, tuple(normalizations)


def _load_compact_visual_plan(
    layout: ProjectLayout,
    queue: dict[str, Any],
) -> CompactVisualPlan:
    package_path = _safe_staging_file(layout, queue["outputs"]["package"])
    plan = CompactVisualPlan.model_validate(read_json(package_path))
    errors: list[str] = []
    if plan.job_id != queue.get("job_id"):
        errors.append("job_id não corresponde à fila visual atual.")
    if plan.input_hash != queue.get("input_hash"):
        errors.append("input_hash não corresponde à fila visual atual.")
    blocks = queue.get("deterministic_contract", {}).get("blocks", [])
    expected_ids = [
        item.get("block_id")
        for item in blocks
        if isinstance(item, dict) and item.get("creative_concept_required") is True
    ]
    actual_ids = [item.block_id for item in plan.concepts]
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("O plano visual contém block_id duplicado.")
    missing = [item for item in expected_ids if item not in actual_ids]
    unexpected = [item for item in actual_ids if item not in expected_ids]
    if missing:
        errors.append("Faltam conceitos para: " + ", ".join(str(item) for item in missing) + ".")
    if unexpected:
        errors.append(
            "Há conceitos para blocos desconhecidos: "
            + ", ".join(str(item) for item in unexpected)
            + "."
        )
    if actual_ids != expected_ids:
        errors.append("Os conceitos visuais precisam preservar a ordem dos blocos.")
    opening_standard = queue.get("deterministic_contract", {}).get("opening_visual_standard")
    if opening_standard == "scroll_stop_concrete_v1" and expected_ids:
        opening = next(
            (concept for concept in plan.concepts if concept.block_id == expected_ids[0]),
            None,
        )
        if opening is not None:
            if opening.attention_role != "scroll_stop_opening":
                errors.append(
                    "O primeiro conceito precisa declarar attention_role=scroll_stop_opening."
                )
            for field_name, value in (
                ("dominant_subject_or_object", opening.dominant_subject_or_object),
                ("observable_action", opening.observable_action),
                ("action_kind", opening.action_kind),
                ("initial_state", opening.initial_state),
                ("resulting_state", opening.resulting_state),
                ("concrete_tension", opening.concrete_tension),
            ):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"O primeiro conceito precisa preencher {field_name}.")
            if opening.shot_scale not in {"close", "medium"}:
                errors.append("O primeiro conceito precisa usar shot_scale close ou medium.")
            normalized_opening = _normalized(opening.scene_description)
            opening_tokens = [
                token.strip(".,;:!?()[]{}\"'“”") for token in normalized_opening.split()
            ]
            forbidden_static_tokens = {
                "parado",
                "parada",
                "posando",
                "contemplativo",
                "contemplativa",
            }

            has_wide_phrase = any(
                token == "plano" and opening_tokens[index + 1] == "geral"
                for index, token in enumerate(opening_tokens[:-1])
            )
            if has_wide_phrase or forbidden_static_tokens.intersection(opening_tokens):
                errors.append(
                    "O primeiro conceito contradiz o contrato: não use plano geral, pose "
                    "parada ou contemplação estática."
                )
            errors.extend(
                _opening_action_contract_errors(
                    opening.observable_action or "",
                    initial_state=opening.initial_state or "",
                    resulting_state=opening.resulting_state or "",
                )
            )
            normalized_tension = _normalized(opening.concrete_tension or "")
            tension_markers = (" ou ", " enquanto ", " contra ", " mas ", " entre ", " apesar ")
            if not any(marker in f" {normalized_tension} " for marker in tension_markers):
                errors.append(
                    "A tensão da abertura precisa tornar duas respostas ou consequências "
                    "visualmente distinguíveis."
                )
    if errors:
        raise ShortsScriptPackageValidationError(errors)
    return plan


def materialize_compact_visual_fallback(layout: ProjectLayout) -> CompactVisualPlan:
    """Write a deterministic, usable visual plan after the bounded model repair."""

    queue = _load_shorts_script_queue(layout)
    if queue.get("generation_mode") != "deterministic_compact_visual_plan_v1":
        raise RuntimeError("A fila atual não usa o plano visual compacto determinístico.")
    creative_blocks = [
        item
        for item in queue["deterministic_contract"]["blocks"]
        if item["creative_concept_required"]
    ]
    scroll_stop = (
        queue["deterministic_contract"].get("opening_visual_standard") == "scroll_stop_concrete_v1"
    )
    concepts = []
    for index, item in enumerate(creative_blocks):
        opening = index == 0 and scroll_stop
        fallback_attention = item.get("fallback_attention_contract", {})
        concepts.append(
            {
                "block_id": item["block_id"],
                "scene_description": item["fallback_scene_description"],
                "visual_relation": item["fallback_visual_relation"],
                "attention_role": "scroll_stop_opening" if opening else "progression",
                "dominant_subject_or_object": (
                    fallback_attention.get("dominant_subject_or_object") if opening else None
                ),
                "observable_action": (
                    fallback_attention.get("observable_action") if opening else None
                ),
                "action_kind": fallback_attention.get("action_kind") if opening else None,
                "initial_state": fallback_attention.get("initial_state") if opening else None,
                "resulting_state": (fallback_attention.get("resulting_state") if opening else None),
                "concrete_tension": (
                    fallback_attention.get("concrete_tension") if opening else None
                ),
                "shot_scale": fallback_attention.get("shot_scale") if opening else None,
            }
        )
    plan = CompactVisualPlan(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        concepts=concepts,
    )
    write_json(layout.shorts_script_package_staging, plan)
    layout.append_event(
        {
            "event": "compact_visual_fallback_materialized",
            "job_id": queue["job_id"],
            "concepts": len(plan.concepts),
        }
    )
    return plan


def _effective_compact_scene_description(concept: Any) -> str:
    description = concept.scene_description
    if concept.attention_role != "scroll_stop_opening":
        return description
    return " ".join(
        (
            description.rstrip(" .") + ".",
            f"Sujeito dominante: {concept.dominant_subject_or_object}.",
            f"Ação já em curso: {concept.observable_action}.",
            f"Mudança visível ({concept.action_kind}), em um único momento de transição: "
            f"{concept.initial_state} cedendo a {concept.resulting_state}.",
            f"Tensão visual concreta: {concept.concrete_tension}.",
            f"Enquadramento: {concept.shot_scale}.",
        )
    )


def _build_deterministic_compact_package(
    layout: ProjectLayout,
    queue: dict[str, Any],
    plan: CompactVisualPlan,
) -> ShortsScriptPackage:
    project = layout.load_manifest()
    config = project.config.shorts_script
    briefing = read_model(layout.briefing, Briefing)
    sources = read_model(layout.sources, SourceLibrary)
    contract = queue["deterministic_contract"]
    source_id = contract["source_id"]
    source = next((item for item in sources.entries if item.source_id == source_id), None)
    if (
        source is None
        or source.excerpt is None
        or source.passage is None
        or source.translation is None
    ):
        raise ValueError("A fonte bíblica selada do Short compacto não está mais disponível.")
    if not source.verified or source.spoken_by != "jesus" or not source.compact_hook.strip():
        raise ValueError("A fonte compacta precisa permanecer verificada e atribuída a Jesus.")
    compact_reference = str(contract["compact_reference"])
    spoken_reference = spoken_biblical_reference(compact_reference)
    if _normalized(spoken_reference) != _normalized(contract["spoken_reference"]):
        raise ValueError("A referência falada mudou depois da criação da fila.")

    promise = (
        briefing.central_theme.strip()
        or f"Jesus responde diretamente a quem precisa ouvir {compact_reference}."
    )
    takeaway = (
        briefing.viewer_outcome.strip()
        or briefing.practical_application.strip()
        or briefing.central_theme.strip()
        or "A fala de Jesus permanece como resposta direta para o espectador."
    )
    target_viewer = briefing.audience.strip() or "pessoas que precisam ouvir a fala de Jesus"
    viewer_need = briefing.emotional_intent.strip() or takeaway
    hook = source.compact_hook.strip()
    verse = source.excerpt.strip()
    reference_narration = f"{spoken_reference}."
    sections = [
        ScriptSection(
            section_id="section-001",
            role="opening",
            title="Interrupção",
            narration=hook,
            biblical_basis=[compact_reference],
            claim_classification=["creative_elaboration"],
            retention_goal="Interromper o pensamento do espectador com o hook curado.",
            visual_direction="Jesus encontra uma pessoa em um momento emocional concreto.",
        ),
        ScriptSection(
            section_id="section-002",
            role="biblical_narrative",
            title="Fala de Jesus",
            narration=f"“{verse}”",
            biblical_basis=[compact_reference],
            claim_classification=["biblical_text"],
            retention_goal="Apresentar integralmente a fala direta e verificada de Jesus.",
            visual_direction="A fala de Jesus avança por imagens reverentes e distintas.",
        ),
        ScriptSection(
            section_id="section-003",
            role="closing",
            title="Referência",
            narration=reference_narration,
            biblical_basis=[compact_reference],
            claim_classification=["biblical_text"],
            retention_goal="Encerrar identificando com clareza a origem bíblica da citação.",
            visual_direction="Uma Bíblia aberta repousa sobre uma mesa de madeira.",
        ),
    ]
    concept_by_id = {item.block_id: item for item in plan.concepts}
    visual_scenes: list[ShortsVisualScene] = []
    for block in contract["blocks"]:
        concept = concept_by_id.get(block["block_id"])
        visual_scenes.append(
            ShortsVisualScene(
                section_id=block["section_id"],
                narration=block["narration"],
                scene_description=(
                    _effective_compact_scene_description(concept)
                    if concept is not None
                    else block["fallback_scene_description"]
                ),
                visual_relation=(
                    concept.visual_relation
                    if concept is not None
                    else block["fallback_visual_relation"]
                ),
            )
        )

    direction = ShortsCinematicDevotionalDirection(
        creative_mode=briefing.creative_mode,
        opening_section_id="section-001",
        scripture_section_id="section-002",
        reference_section_id="section-003",
        emotional_hook=hook,
        content_promise=promise,
        viewer_takeaway=takeaway,
        spoken_reference=spoken_reference,
        source_id=source.source_id,
        visual_emotional_arc=[
            visual_scenes[0].scene_description,
            visual_scenes[min(1, len(visual_scenes) - 1)].scene_description,
            visual_scenes[-1].scene_description,
        ],
    )
    quote_start = config.scripture_citation_min_start_seconds
    quote_end = config.scripture_citation_max_end_seconds
    final_end = float(project.target_duration_seconds)
    beats = [
        ShortsRetentionBeat(
            section_id="section-001",
            purpose="hook",
            viewer_question="Jesus percebe o que estou vivendo?",
            viewer_value="Receber uma interrupção emocional direta.",
            target_start_seconds=0,
            target_end_seconds=config.hook_target_seconds,
            opens_loop=promise,
            new_information="O hook curado nomeia a necessidade do espectador.",
            visual_change=visual_scenes[0].scene_description,
        ),
        ShortsRetentionBeat(
            section_id="section-002",
            purpose="evidence",
            viewer_question="O que Jesus diz?",
            viewer_value="Ouvir a fala literal e verificada de Jesus.",
            target_start_seconds=quote_start,
            target_end_seconds=quote_end,
            resolves_loop=promise,
            new_information="A própria fala de Jesus entrega a resposta bíblica.",
            visual_change=visual_scenes[min(1, len(visual_scenes) - 1)].scene_description,
        ),
        ShortsRetentionBeat(
            section_id="section-003",
            purpose="reference",
            viewer_question="Onde essa fala está registrada?",
            viewer_value="Receber a referência bíblica exata.",
            target_start_seconds=quote_end,
            target_end_seconds=final_end,
            new_information="A referência identifica a fonte da citação.",
            visual_change=visual_scenes[-1].scene_description,
        ),
    ]
    strategy = ShortsScriptStrategy(
        target_viewer=target_viewer,
        viewer_need=viewer_need,
        core_value=takeaway,
        content_promise=promise,
        hook=hook,
        hook_type="emotional_interruption",
        hook_family="emotional_interruption",
        hook_techniques=["pattern_interrupt", "emotional_mirroring"],
        hook_target_end_seconds=config.hook_target_seconds,
        verse_opening=ShortsVerseOpening(
            source_id=source.source_id,
            reference=compact_reference,
            translation=source.translation,
            lead_in="",
            verse_text=verse,
            target_start_seconds=quote_start,
            target_end_seconds=quote_end,
        ),
        cinematic_devotional=direction,
        open_loop=promise,
        payoff=verse,
        practical_value=takeaway,
        cta="",
        reason_to_subscribe="",
        retention_beats=beats,
        visual_scenes=visual_scenes,
        source_ids=[source.source_id],
        section_ids=[section.section_id for section in sections],
        biblical_safeguards=[
            "Citação copiada integralmente da fonte local verificada e atribuída a Jesus."
        ],
        compliance_checks=["Sem CTA, sem comentário e sem introdução antes da citação."],
    )
    script = ScriptDocument(
        version=int(queue["next_script_version"]),
        title=briefing.title or project.title,
        sections=sections,
        word_count=len(tokenize_words(" ".join(item.narration for item in sections))),
        source_ids=[source.source_id],
        notes=["Roteiro compacto selado deterministicamente a partir do catálogo verificado."],
    )
    script.estimated_duration_seconds = round(
        script.word_count / config.estimated_narration_wpm * 60,
        2,
    )
    review = BiblicalReview(
        script_version=script.version,
        reviewed_at=utc_now(),
        reviewer="deterministic-verified-compact-v1",
        summary=(
            "Citação literal, referência, tradução, atribuição a Jesus e hash da fonte "
            f"conferidos deterministicamente ({source.content_hash})."
        ),
    )
    return ShortsScriptPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        script=script,
        biblical_review=review,
        strategy=strategy,
    )


def _load_normalized_staging_package(
    layout: ProjectLayout,
    queue: dict[str, Any],
) -> tuple[ShortsScriptPackage, tuple[str, ...]]:
    if queue.get("generation_mode") == "deterministic_compact_visual_plan_v1":
        plan = _load_compact_visual_plan(layout, queue)
        package = _build_deterministic_compact_package(layout, queue, plan)
        _validate_package(layout, package, queue)
        return package, ()
    package_path = _safe_staging_file(layout, queue["outputs"]["package"])
    payload, normalizations = _normalize_staging_package_payload(read_json(package_path))
    package = ShortsScriptPackage.model_validate(payload)
    _validate_package(layout, package, queue)
    return package, normalizations


def validate_shorts_script_package_staging(
    layout: ProjectLayout,
) -> tuple[dict[str, Any], ShortsScriptPackage]:
    queue = _load_shorts_script_queue(layout)
    package, _ = _load_normalized_staging_package(layout, queue)
    return queue, package


def check_shorts_script_package_staging(layout: ProjectLayout) -> ShortsScriptStagingCheck:
    """Validate the staging package without writing project or staging state."""

    normalizations: tuple[str, ...] = ()
    try:
        queue = _load_shorts_script_queue(layout)
        if queue.get("generation_mode") == "deterministic_compact_visual_plan_v1":
            plan = _load_compact_visual_plan(layout, queue)
            package = _build_deterministic_compact_package(layout, queue, plan)
            _validate_package(layout, package, queue)
        else:
            package_path = _safe_staging_file(layout, queue["outputs"]["package"])
            payload, normalizations = _normalize_staging_package_payload(read_json(package_path))
            package = ShortsScriptPackage.model_validate(payload)
            _validate_package(layout, package, queue)
    except ShortsScriptPackageValidationError as exc:
        return ShortsScriptStagingCheck(False, exc.errors, normalizations)
    except ValidationError as exc:
        errors = tuple(
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return ShortsScriptStagingCheck(False, errors, normalizations)
    except (FileNotFoundError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return ShortsScriptStagingCheck(False, (str(exc),), normalizations)
    return ShortsScriptStagingCheck(True, (), normalizations)


def prepare_shorts_biblical_review_queue(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Prepare one compact, independent biblical review before shared-state import."""

    config = layout.load_manifest().config.shorts_script
    if config.biblical_review_mode == "deterministic_verified_compact_v1":
        raise RuntimeError(
            "Este Short usa verificação bíblica determinística do catálogo selado; "
            "não há fila de revisor LLM."
        )
    queue, package = validate_shorts_script_package_staging(layout)
    package_hash = sha256_file(layout.shorts_script_package_staging)
    if not force and layout.shorts_biblical_review_queue.is_file():
        existing = read_json(layout.shorts_biblical_review_queue)
        if (
            isinstance(existing, dict)
            and existing.get("package_hash") == package_hash
            and layout.shorts_biblical_review_context.is_file()
        ):
            return existing
    manifest = layout.load_manifest()
    context = {
        "project_id": manifest.project_id,
        "target_duration_seconds": manifest.target_duration_seconds,
        "citation_policy": _shorts_citation_policy(manifest.config.shorts_script),
        "package_hash": package_hash,
        "briefing": read_json(layout.briefing),
        "sources": read_json(layout.sources),
        "script": package.script.model_dump(mode="json"),
        "strategy": package.strategy.model_dump(mode="json"),
        "author_review": package.biblical_review.model_dump(mode="json"),
    }
    if layout.shorts_biblical_review_staging_file.is_file():
        layout.shorts_biblical_review_staging_file.unlink()
    write_json(layout.shorts_biblical_review_context, context)
    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "project_id": layout.load_manifest().project_id,
        "job_id": f"biblical-review-{package_hash[:16]}",
        "package_hash": package_hash,
        "script_job_id": queue["job_id"],
        "role": "biblical_reviewer",
        "orchestration": {
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "recommended_model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "context_policy": "compact_only",
            "fork_turns": "none",
        },
        "inputs": {
            "compact_context": relative_posix(
                layout.shorts_biblical_review_context,
                layout.root,
            )
        },
        "outputs": {
            "review": relative_posix(
                layout.shorts_biblical_review_staging_file,
                layout.root,
            )
        },
        "schema": compact_json_schema(BiblicalReview.model_json_schema()),
        "instructions": [
            "Leia somente inputs.compact_context e revise o roteiro antes de qualquer import.",
            (
                "Aplique citation_policy do contexto, inclusive o limite literal específico da "
                "duração; faixas consecutivas são válidas quando preservam continuidade e cumprem "
                "esse contrato."
            ),
            "Relate em uma única passagem todos os bloqueios de citação, contexto, interpretação, "
            "elaboração criativa, direitos e referência.",
            *(
                [
                    "No perfil high_stakes_biblical_question_v1, confirme também que cada "
                    "required_answer_point é sustentado pelas fontes, que uncertainty_boundaries "
                    "foram preservadas e que nenhuma inferência foi narrada como texto bíblico."
                ]
                if config.editorial_profile == "high_stakes_biblical_question_v1"
                else []
            ),
            "Não reescreva o roteiro, não aprove em nome do usuário e não altere o projeto.",
            "Grave somente outputs.review; script_version precisa copiar a versão do staging.",
        ],
    }
    write_json(layout.shorts_biblical_review_queue, payload)
    return payload


def _load_independent_biblical_review(
    layout: ProjectLayout,
    package: ShortsScriptPackage,
) -> BiblicalReview:
    if not layout.shorts_biblical_review_queue.is_file():
        raise FileNotFoundError(
            "Revisão bíblica independente ausente; execute script shorts-review-prepare."
        )
    queue = read_json(layout.shorts_biblical_review_queue)
    current_hash = sha256_file(layout.shorts_script_package_staging)
    if queue.get("package_hash") != current_hash:
        raise RuntimeError("A revisão bíblica independente pertence a um staging antigo.")
    relative = queue.get("outputs", {}).get("review")
    if not isinstance(relative, str):
        raise ValueError("Fila de revisão bíblica independente inválida.")
    path = (layout.root / relative).resolve()
    try:
        path.relative_to(layout.shorts_biblical_review_staging.resolve())
    except ValueError as exc:
        raise ValueError("Caminho inseguro para a revisão bíblica independente.") from exc
    if not path.is_file():
        raise FileNotFoundError(
            "Revisão bíblica independente ausente; execute o biblical_reviewer declarado."
        )
    review = read_model(path, BiblicalReview)
    if review.script_version != package.script.version:
        raise ValueError("A revisão bíblica independente não corresponde ao roteiro em staging.")
    if review.has_open_blockers:
        raise ValueError("A revisão bíblica independente ainda possui bloqueios abertos.")
    return review.model_copy(update={"approved": False, "approval_note": None})


def check_reviewed_shorts_script_staging(layout: ProjectLayout) -> ShortsScriptStagingCheck:
    """Validate author package plus independent review without mutating shared state."""

    package_check = check_shorts_script_package_staging(layout)
    if not package_check.valid:
        return package_check
    try:
        _, package = validate_shorts_script_package_staging(layout)
        _load_independent_biblical_review(layout, package)
    except (FileNotFoundError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return ShortsScriptStagingCheck(False, (str(exc),), package_check.normalizations)
    return package_check


def import_shorts_script_package(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> ShortsScriptPackage:
    if layout.load_manifest().stages["shorts_script"].status == StageStatus.COMPLETED and not force:
        return read_model(layout.shorts_script_package, ShortsScriptPackage)
    queue, package = validate_shorts_script_package_staging(layout)
    config = layout.load_manifest().config.shorts_script
    if config.require_independent_biblical_review:
        package = package.model_copy(
            update={"biblical_review": _load_independent_biblical_review(layout, package)}
        )
    script = package.script.model_copy(deep=True)
    script.version = int(queue["next_script_version"])
    script.word_count = len(tokenize_words(script.narration_text()))
    script.estimated_duration_seconds = round(
        script.word_count / config.estimated_narration_wpm * 60,
        2,
    )
    review = package.biblical_review.model_copy(
        update={
            "script_version": script.version,
            "approved": False,
            "approval_note": None,
        }
    )
    final_package = package.model_copy(
        update={
            "script": script,
            "biblical_review": review,
        }
    )

    deterministic_compact = config.generation_mode == "deterministic_compact_visual_plan_v1"
    save_script(
        layout,
        script,
        provider=(
            "deterministic-compact-script-plus-visual-plan"
            if deterministic_compact
            else "shorts-script-specialist"
        ),
        creative_strategy=final_package.strategy,
    )
    review_provider = (
        "independent-biblical-reviewer"
        if config.require_independent_biblical_review
        else "deterministic-verified-compact-v1"
        if deterministic_compact
        else "shorts-script-specialist"
    )
    save_biblical_review(layout, review, provider=review_provider)
    write_json(layout.shorts_script_package, final_package)
    write_json(layout.shorts_script_strategy, final_package.strategy)
    completed_queue = dict(queue)
    completed_queue["status"] = StageStatus.COMPLETED.value
    completed_queue["imported_at"] = utc_now()
    write_json(layout.shorts_script_queue, completed_queue)
    invalidate(
        layout,
        stages=("youtube_growth", "metadata", "render", "validation"),
        approvals=("metadata", "final_render"),
        note="O roteiro especializado de Short foi importado.",
    )
    complete_stage(
        layout,
        "shorts_script",
        str(queue["input_hash"]),
        [
            relative_posix(layout.shorts_script_queue, layout.root),
            relative_posix(layout.shorts_script_context, layout.root),
            relative_posix(layout.shorts_script_package, layout.root),
            relative_posix(layout.shorts_script_strategy, layout.root),
            relative_posix(layout.creative_contract, layout.root),
        ],
        provider="shorts-script-specialist",
        usage={
            "words": script.word_count,
            "retention_beats": len(final_package.strategy.retention_beats),
            "context_bytes": layout.shorts_script_context.stat().st_size,
            "schema_bytes": len(
                json.dumps(queue["schemas"]["package"], ensure_ascii=False).encode("utf-8")
            ),
        },
        note="Roteiro de Short importado com gancho, payoff, valor e CTA empático.",
    )
    layout.append_event(
        {
            "event": "shorts_script_package_imported",
            "job_id": package.job_id,
            "script_version": script.version,
            "words": script.word_count,
        }
    )
    return final_package
