from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CreativeModeSpec:
    label: str
    purpose: str
    suggested_structure: tuple[str, ...]
    interpretive_stance: str
    long_hook_policy: str
    application_required: bool = False


CREATIVE_MODE_SPECS: dict[str, CreativeModeSpec] = {
    "biblical_storytelling": CreativeModeSpec(
        label="Storytelling bíblico factual",
        purpose=(
            "Narrar ações, decisões e consequências registradas na Bíblia sem inventar diálogo, "
            "motivação ou detalhe histórico."
        ),
        suggested_structure=(
            "situação inicial",
            "progressão dos acontecimentos",
            "virada documentada",
            "consequência",
            "desfecho factual",
        ),
        interpretive_stance="factual",
        long_hook_policy="optional",
    ),
    "character_portrait": CreativeModeSpec(
        label="Retrato de personagem",
        purpose="Acompanhar escolhas, mudanças, limites e contradições de uma pessoa bíblica.",
        suggested_structure=(
            "traço inicial",
            "decisão sob pressão",
            "evidências em cena",
            "mudança ou contraste",
            "síntese do retrato",
        ),
        interpretive_stance="analytical",
        long_hook_policy="optional",
    ),
    "passage_explainer": CreativeModeSpec(
        label="Explicação de passagem",
        purpose="Explicar o que uma passagem afirma, como progride e quais limites ela possui.",
        suggested_structure=(
            "pergunta de leitura",
            "texto e contexto",
            "termos ou relações importantes",
            "limites da afirmação",
            "síntese clara",
        ),
        interpretive_stance="expository",
        long_hook_policy="optional",
    ),
    "biblical_curiosity": CreativeModeSpec(
        label="Curiosidade bíblica",
        purpose="Revelar um detalhe verificável que muda a leitura de uma cena ou passagem.",
        suggested_structure=(
            "detalhe intrigante",
            "evidência bíblica",
            "contexto mínimo",
            "o que muda na leitura",
            "conclusão",
        ),
        interpretive_stance="expository",
        long_hook_policy="required",
    ),
    "historical_context": CreativeModeSpec(
        label="Contexto histórico verificado",
        purpose=(
            "Reconstruir contexto cultural ou histórico somente com fontes verificadas e sem "
            "tratar hipótese como fato."
        ),
        suggested_structure=(
            "questão histórica",
            "fonte e cenário",
            "fato verificável",
            "efeito sobre a passagem",
            "limites do que sabemos",
        ),
        interpretive_stance="factual",
        long_hook_policy="optional",
    ),
    "philosophical_reflection": CreativeModeSpec(
        label="Reflexão filosófica",
        purpose=(
            "Construir uma reflexão autoral e argumentada, distinguindo claramente texto bíblico, "
            "inferência e opinião."
        ),
        suggested_structure=(
            "problema ou paradoxo",
            "base bíblica",
            "argumento",
            "objeção ou tensão",
            "síntese aberta ou posição defendida",
        ),
        interpretive_stance="exploratory",
        long_hook_policy="optional",
    ),
    "thematic_investigation": CreativeModeSpec(
        label="Investigação temática",
        purpose=(
            "Rastrear um tema por passagens distintas e mostrar continuidade, contraste e limite."
        ),
        suggested_structure=(
            "hipótese temática",
            "primeira evidência",
            "segunda perspectiva",
            "tensão entre passagens",
            "síntese sustentada",
        ),
        interpretive_stance="analytical",
        long_hook_policy="optional",
    ),
    "comparison_contrast": CreativeModeSpec(
        label="Comparação e contraste",
        purpose="Comparar personagens, decisões ou passagens sem apagar diferenças de contexto.",
        suggested_structure=(
            "critério de comparação",
            "primeiro caso",
            "segundo caso",
            "semelhança e diferença",
            "conclusão",
        ),
        interpretive_stance="analytical",
        long_hook_policy="optional",
    ),
    "ethical_dilemma": CreativeModeSpec(
        label="Dilema ético",
        purpose="Examinar uma decisão moral bíblica com tensão real, evidências e limites.",
        suggested_structure=(
            "dilema concreto",
            "alternativas e custos",
            "evidência bíblica",
            "tensão ética",
            "conclusão responsável",
        ),
        interpretive_stance="exploratory",
        long_hook_policy="required",
    ),
    "misconception_correction": CreativeModeSpec(
        label="Correção de leitura comum",
        purpose="Confrontar uma leitura popular com aquilo que a passagem realmente sustenta.",
        suggested_structure=(
            "leitura comum",
            "evidência que a tensiona",
            "contexto",
            "correção",
            "nova formulação precisa",
        ),
        interpretive_stance="analytical",
        long_hook_policy="required",
    ),
    "literary_exploration": CreativeModeSpec(
        label="Exploração literária",
        purpose=(
            "Observar imagem, repetição, contraste, estrutura ou voz presentes no texto bíblico."
        ),
        suggested_structure=(
            "elemento literário",
            "ocorrência no texto",
            "progressão",
            "efeito de sentido",
            "síntese",
        ),
        interpretive_stance="expository",
        long_hook_policy="none",
    ),
    "question_driven_essay": CreativeModeSpec(
        label="Ensaio guiado por pergunta",
        purpose=(
            "Investigar uma pergunta bíblica sem pressupor que a resposta seja uma dica prática."
        ),
        suggested_structure=(
            "pergunta delimitada",
            "primeira resposta possível",
            "evidência e objeção",
            "reformulação",
            "conclusão ou incerteza preservada",
        ),
        interpretive_stance="exploratory",
        long_hook_policy="optional",
    ),
    "chronological_reconstruction": CreativeModeSpec(
        label="Reconstrução cronológica",
        purpose="Organizar fatos e deslocamentos bíblicos em sequência temporal verificável.",
        suggested_structure=(
            "marco inicial",
            "sequência de eventos",
            "mudança de lugar ou condição",
            "ponto de virada",
            "estado final",
        ),
        interpretive_stance="factual",
        long_hook_policy="none",
    ),
    "cause_and_consequence": CreativeModeSpec(
        label="Causa e consequência",
        purpose=(
            "Acompanhar como uma escolha produz efeitos sucessivos dentro da narrativa bíblica."
        ),
        suggested_structure=(
            "decisão inicial",
            "efeito imediato",
            "efeito ampliado",
            "reversão ou confirmação",
            "consequência final",
        ),
        interpretive_stance="analytical",
        long_hook_policy="optional",
    ),
    "devotional_application": CreativeModeSpec(
        label="Devocional aplicado",
        purpose="Conectar a passagem a uma resposta prática específica, sem prometer resultado.",
        suggested_structure=(
            "tensão humana",
            "evidência bíblica",
            "mudança de entendimento",
            "aplicação delimitada",
            "encerramento",
        ),
        interpretive_stance="devotional",
        long_hook_policy="required",
        application_required=True,
    ),
    "pastoral_encouragement": CreativeModeSpec(
        label="Encorajamento pastoral",
        purpose="Acolher e encorajar com fidelidade bíblica, sem transformar dor em fórmula.",
        suggested_structure=(
            "situação reconhecível",
            "palavra bíblica",
            "acolhimento sem simplificação",
            "esperança responsável",
            "resposta possível",
        ),
        interpretive_stance="devotional",
        long_hook_policy="optional",
        application_required=True,
    ),
}


HOOK_TEMPLATE_EXAMPLES: dict[str, str] = {
    "if_you_want_do_this": "Se você quer [X], faça isso.",
    "what_happens_if_you_stop": "O que acontece se você parar de [X]?",
    "still_doing_losing": "Se você ainda faz [X], está perdendo [Y].",
    "nobody_talks_but": "Ninguém fala sobre [X], mas...",
    "want_to_learn_start": "Quer aprender [X]? Comece...",
    "never_do_this": "Nunca faça [X].",
    "start_in_scene": "Comece dentro de uma ação ou decisão já em andamento.",
    "surprising_fact": "Abra com um fato verificável que contrarie uma expectativa.",
    "unanswered_question": "Abra com uma pergunta específica que o vídeo realmente investigará.",
    "contradiction": "Coloque duas ideias aparentemente incompatíveis em tensão.",
    "consequence_first": "Mostre primeiro a consequência e depois reconstrua sua causa.",
    "quote_first": "Abra com uma citação curta e verificada que funcione como ponto de partida.",
}

USER_TEMPLATE_HOOK_FAMILIES = (
    "if_you_want_do_this",
    "what_happens_if_you_stop",
    "still_doing_losing",
    "nobody_talks_but",
    "want_to_learn_start",
    "never_do_this",
)

DEVELOPMENT_MODE_TO_CREATIVE_MODES: dict[str, tuple[str, ...]] = {
    "context": (
        "passage_explainer",
        "biblical_curiosity",
        "historical_context",
    ),
    "biblical_story": (
        "biblical_storytelling",
        "character_portrait",
        "chronological_reconstruction",
        "cause_and_consequence",
    ),
    "biblical_metaphor": (
        "literary_exploration",
        "philosophical_reflection",
        "question_driven_essay",
    ),
    "cross_reference": (
        "thematic_investigation",
        "comparison_contrast",
    ),
    "promise_limits": (
        "misconception_correction",
        "ethical_dilemma",
        "question_driven_essay",
    ),
}

SHORT_AUTOMATIC_EXCLUSIONS = frozenset(
    {
        "historical_context",
        "thematic_investigation",
        "comparison_contrast",
        "chronological_reconstruction",
    }
)

MULTI_SOURCE_CREATIVE_MODES = frozenset({"thematic_investigation", "comparison_contrast"})


def creative_mode_payload(mode: str) -> dict[str, Any]:
    spec = CREATIVE_MODE_SPECS[mode]
    return {"id": mode, **asdict(spec)}


def default_interpretive_stance(mode: str) -> str:
    return CREATIVE_MODE_SPECS[mode].interpretive_stance


def default_long_hook_policy(mode: str) -> str:
    return CREATIVE_MODE_SPECS[mode].long_hook_policy


def narration_tone_for_mode(mode: str) -> str:
    stance = default_interpretive_stance(mode)
    return {
        "factual": "narrativo, preciso, sóbrio, claro e cinematográfico",
        "expository": "claro, curioso, didático, natural e cinematográfico",
        "analytical": "investigativo, preciso, direto e intelectualmente honesto",
        "exploratory": "reflexivo, argumentativo, autoral e honesto sobre incertezas",
        "devotional": "pastoral, natural, próximo, sereno e concreto",
    }[stance]


def viewer_outcome_for_mode(mode: str, *, theme: str, reveal: str = "") -> str:
    subject = theme.strip().rstrip(".")
    if mode == "biblical_curiosity" and reveal.strip():
        return reveal.strip()
    if default_interpretive_stance(mode) == "factual":
        return f"Compreender com precisão os fatos bíblicos ligados a {subject}."
    if mode == "philosophical_reflection":
        return f"Examinar {subject} por uma reflexão autoral distinguida do texto bíblico."
    if mode in MULTI_SOURCE_CREATIVE_MODES:
        return f"Comparar evidências bíblicas e formar uma síntese responsável sobre {subject}."
    if CREATIVE_MODE_SPECS[mode].application_required:
        return f"Relacionar {subject} a uma resposta prática fiel e delimitada."
    return f"Compreender uma perspectiva bíblica mais precisa sobre {subject}."


def hook_families_for_mode(mode: str, *, short: bool) -> tuple[str, ...]:
    families: dict[str, tuple[str, ...]] = {
        "biblical_storytelling": ("start_in_scene", "consequence_first"),
        "character_portrait": ("start_in_scene", "contradiction", "consequence_first"),
        "passage_explainer": ("want_to_learn_start", "unanswered_question", "surprising_fact"),
        "biblical_curiosity": ("nobody_talks_but", "surprising_fact", "contradiction"),
        "historical_context": ("surprising_fact", "unanswered_question"),
        "philosophical_reflection": ("unanswered_question", "contradiction"),
        "thematic_investigation": ("unanswered_question", "contradiction"),
        "comparison_contrast": ("contradiction", "consequence_first"),
        "ethical_dilemma": ("never_do_this", "unanswered_question", "contradiction"),
        "misconception_correction": (
            "still_doing_losing",
            "never_do_this",
            "nobody_talks_but",
            "contradiction",
        ),
        "literary_exploration": ("surprising_fact", "unanswered_question"),
        "question_driven_essay": ("what_happens_if_you_stop", "unanswered_question"),
        "chronological_reconstruction": ("consequence_first", "start_in_scene"),
        "cause_and_consequence": ("consequence_first", "what_happens_if_you_stop"),
        "devotional_application": (
            "if_you_want_do_this",
            "what_happens_if_you_stop",
            "never_do_this",
        ),
        "pastoral_encouragement": (
            "if_you_want_do_this",
            "what_happens_if_you_stop",
            "start_in_scene",
        ),
    }
    selected = families[mode]
    if short:
        return tuple(family for family in selected if family != "quote_first")
    return selected


def _plain(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def hook_matches_family(hook: str, family: str) -> bool:
    normalized = _plain(hook)
    if family == "if_you_want_do_this":
        return normalized.startswith("se voce quer ")
    if family == "what_happens_if_you_stop":
        return normalized.startswith("o que acontece se voce parar de ")
    if family == "still_doing_losing":
        return normalized.startswith("se voce ainda ") and " perdendo " in f" {normalized} "
    if family == "nobody_talks_but":
        return normalized.startswith("ninguem fala sobre ") and " mas" in normalized
    if family == "want_to_learn_start":
        return normalized.startswith("quer aprender ") and " comece" in normalized
    if family == "never_do_this":
        return normalized.startswith("nunca ")
    return bool(normalized)


def creative_evidence_errors(
    document: Any,
    sources: Any,
    *,
    creative_mode: str,
    interpretive_stance: str,
) -> list[str]:
    errors: list[str] = []
    classifications = {
        classification
        for section in document.sections
        for classification in section.claim_classification
    }
    if interpretive_stance == "factual":
        unsupported = classifications.intersection({"interpretation", "creative_elaboration"})
        if unsupported:
            errors.append(
                "A postura factual não pode apresentar interpretação ou elaboração criativa como "
                "parte da narração."
            )
    if interpretive_stance == "exploratory" and "interpretation" not in classifications:
        errors.append(
            "A postura exploratória precisa identificar ao menos uma afirmação como interpretação."
        )
    entries = list(getattr(sources, "entries", ())) if sources is not None else []
    if creative_mode == "historical_context" and not any(
        entry.kind == "historical_context" and entry.verified for entry in entries
    ):
        errors.append("Contexto histórico exige ao menos uma fonte histórica verificada.")
    if creative_mode in MULTI_SOURCE_CREATIVE_MODES:
        verified_biblical = {
            entry.source_id
            for entry in entries
            if entry.kind == "biblical_text" and entry.verified and entry.excerpt
        }
        if len(verified_biblical) < 2:
            errors.append("Este modo comparativo exige ao menos duas fontes bíblicas verificadas.")
    return errors
