from __future__ import annotations

import re
import unicodedata

from pipeline_videos.models import ScriptDocument
from pipeline_videos.utils import tokenize_words

AUTHORIAL_NARRATION_STANDARD = "human_authorial_voice_v1"

FORBIDDEN_AUTHORIAL_META_PATTERNS = (
    "o relato registra",
    "o relato não explica",
    "o relato mostra",
    "a sequência é compacta",
    "a sequência mostra",
    "o retrato começa",
    "o primeiro traço",
    "o segundo traço",
    "a passagem paralela acrescenta",
    "comparar é observar",
    "precisam ser examinados",
    "sem preencher os silêncios",
    "sem monólogo inventado",
    "não podemos afirmar",
)

_FORBIDDEN_NEGATIVE_CLICHES = ("não hesitou",)

_NEGATED_RHETORICAL_SETUP = re.compile(
    r"\bn[aã]o\s+(?:fez|deu|proferiu|pronunciou|preparou|apresentou)\b[^.!?]{0,100}"
    r"\b(?:discurso|explica[cç][aã]o|descri[cç][aã]o|coment[aá]rio|mon[oó]logo|"
    r"apresenta[cç][aã]o|aviso)\b",
    flags=re.IGNORECASE,
)
_META_NEGATIVE_LIST = re.compile(
    r"\b(?:a\s+)?(?:cena|passagem|sequ[eê]ncia|retrato|relato|texto)\b[^.!?]{0,160}"
    r"\bn[aã]o\s+(?:descreve|explica|informa|mostra|registra)\b[^.!?]{0,120}"
    r"\b(?:nem|tampouco)\b",
    flags=re.IGNORECASE,
)
_CONTRASTIVE_NEGATION_SCAFFOLD = re.compile(
    r"\bn[aã]o\b[^.!?]{0,160}\b(?:nem|tampouco)\b[^.!?]{0,160}"
    r"\b(?:mas|e\s+sim|pelo\s+contr[aá]rio)\b",
    flags=re.IGNORECASE,
)
_QUOTED_NARRATION = re.compile(r"“[^”]*”|\"[^\"]*\"", flags=re.DOTALL)
_FIRST_PERSON_JESUS_MARKER = re.compile(
    r"\b(?:eu|meu|minha|meus|minhas|mim|comigo|me)\b",
    flags=re.IGNORECASE,
)
_THIRD_PERSON_JESUS_MARKER = re.compile(r"\bjesus\b", flags=re.IGNORECASE)


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def _authorial_sentences(document: ScriptDocument) -> list[str]:
    """Return authorial narration while protecting literal biblical quotations."""

    sentences: list[str] = []
    for section in document.sections:
        narration = section.narration
        if "biblical_text" in section.claim_classification:
            narration = _QUOTED_NARRATION.sub("", narration)
        sentences.extend(
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", narration)
            if sentence.strip()
        )
    return sentences


def _negative_scaffolding(sentence: str) -> bool:
    """Detect prose that defines an event through invented absences or denials."""

    normalized_sentence = _normalized(sentence)
    exclusion_tail = re.search(r",\s*sem\s+(.+)$", sentence, flags=re.IGNORECASE)
    if exclusion_tail:
        tail = exclusion_tail.group(1)
        if len(tokenize_words(tail)) >= 5 and (
            "," in tail or re.search(r"\b(?:ou|nem|tampouco)\b", tail, flags=re.IGNORECASE)
        ):
            return True

    if _NEGATED_RHETORICAL_SETUP.search(sentence):
        return True
    if _META_NEGATIVE_LIST.search(sentence):
        return True
    if _CONTRASTIVE_NEGATION_SCAFFOLD.search(sentence):
        return True

    rhetorical_absence_terms = (
        "sem biografia",
        "sem discurso",
        "sem explicacao",
        "sem descricao",
        "sem preparacao",
        "sem monologo",
        "sem comentario",
        "sem analise",
    )
    return any(term in normalized_sentence for term in rhetorical_absence_terms)


def validate_authorial_narration_style(document: ScriptDocument) -> None:
    """Reject AI-like meta-analysis in every generated narration format."""

    authorial_sentences = _authorial_sentences(document)
    normalized_narration = _normalized(" ".join(authorial_sentences))
    for phrase in FORBIDDEN_AUTHORIAL_META_PATTERNS:
        if _normalized(phrase) in normalized_narration:
            raise ValueError(
                "A locução autoral não pode narrar metalinguagem de análise ou descrever o "
                f"próprio método de leitura: {phrase!r}. Diga diretamente o fato, a ação ou a "
                "explicação que entrega valor ao espectador."
            )

    for phrase in _FORBIDDEN_NEGATIVE_CLICHES:
        if _normalized(phrase) in normalized_narration:
            raise ValueError(
                "A locução autoral não pode valorizar uma ação por negação retórica. Diga "
                f"diretamente o que aconteceu, de forma positiva e concreta: {phrase!r}."
            )

    for sentence in authorial_sentences:
        if _negative_scaffolding(sentence):
            raise ValueError(
                "A locução autoral não pode valorizar uma ação por negação retórica nem definir "
                "uma cena por uma lista de ausências. Diga diretamente o que aconteceu, de "
                f"forma positiva e concreta: {sentence!r}."
            )


def validate_jesus_first_person_narration(document: ScriptDocument) -> None:
    """Keep Jesus as the explicit authorial narrator instead of a third-person subject."""

    authorial_sentences = _authorial_sentences(document)
    if not authorial_sentences:
        return
    opening = authorial_sentences[0]
    if not _FIRST_PERSON_JESUS_MARKER.search(opening):
        raise ValueError(
            "A locução autoral precisa começar com Jesus como eu lírico em primeira pessoa."
        )
    for sentence in authorial_sentences:
        if _THIRD_PERSON_JESUS_MARKER.search(sentence):
            raise ValueError(
                "A locução autoral não pode falar de Jesus em terceira pessoa. "
                f"Reescreva como fala de Jesus: {sentence!r}."
            )


def validate_biblical_explainer_narration(document: ScriptDocument) -> None:
    """Keep the cutout explainer in a third-person authorial voice."""

    for sentence in _authorial_sentences(document):
        if _FIRST_PERSON_JESUS_MARKER.search(sentence):
            raise ValueError(
                "O perfil cutout usa narrador bíblico explicativo em terceira pessoa; "
                "a locução autoral não pode falar como se fosse Jesus: "
                f"{sentence!r}."
            )
