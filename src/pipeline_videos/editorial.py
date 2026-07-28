from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from pipeline_videos.models import EditorialPolicyConfig, ScriptDocument, SourceLibrary
from pipeline_videos.narration_style import (
    validate_authorial_narration_style,
    validate_biblical_explainer_narration,
)
from pipeline_videos.utils import tokenize_words

_ENGLISH_EDITORIAL_JARGON = (
    "open loop",
    "payoff",
    "storytelling",
    "plot twist",
    "call to action",
    "mindset",
    "insight",
)
_GENERIC_TEXT_REFERENCE_RE = re.compile(
    r"\b(?:o|esse|este)\s+texto(?:\s+bíblico)?\s+"
    r"(?:fala|diz|registra|mostra|ensina)\b",
    flags=re.IGNORECASE,
)
_STRAIGHT_QUOTED_TEXT_RE = re.compile(r'"(?P<quote>.+?)"', flags=re.DOTALL)
_COMMENT_RESPONSE_RE = re.compile(
    r"\b(?:escreva|escreve|digite|comente)\s+"
    r"(?:(?:a|esta)\s+(?:palavra|frase)\s+)?"
    r'["“](?P<response>[^"”]{1,80})["”]\s+(?:nos|nos seus|na seção de)\s+comentários\b',
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ScriptureCitation:
    section_id: str
    quote: str
    word_offset: int = 0
    estimated_start_seconds: float = 0.0
    reference_hint: str | None = None


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def _accent_fold(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold()
        if not unicodedata.combining(character)
    )


def _quoted_text_spans(value: str) -> list[tuple[int, str]]:
    spans: list[tuple[int, str]] = []
    depth = 0
    start = -1
    for index, character in enumerate(value):
        if character == "\u201c":
            if depth == 0:
                start = index
            depth += 1
        elif character == "\u201d" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, value[start + 1 : index]))
                start = -1
    spans.extend(
        (match.start(), match.group("quote")) for match in _STRAIGHT_QUOTED_TEXT_RE.finditer(value)
    )
    return sorted(spans, key=lambda item: item[0])


def quoted_comment_response(value: str) -> str | None:
    match = _COMMENT_RESPONSE_RE.search(value)
    return " ".join(match.group("response").split()) if match else None


def scripture_citations(
    document: ScriptDocument,
    *,
    words_per_minute: int = 145,
) -> list[ScriptureCitation]:
    citations: list[ScriptureCitation] = []
    word_cursor = 0
    for section_index, section in enumerate(document.sections):
        if "biblical_text" in section.claim_classification:
            for quote_start, quote in _quoted_text_spans(section.narration):
                prefix = section.narration[max(0, quote_start - 180) : quote_start]
                folded_prefix = _accent_fold(prefix)
                spoken_reference_matches = list(
                    re.finditer(
                        r"\bcapitulo\s+(?P<chapter>\d+)\s*,?\s*"
                        r"versiculos?\s+(?P<verse>\d+(?:\s*(?:a|-)\s*\d+)?)\b",
                        folded_prefix,
                    )
                )
                reference_before_quote = spoken_reference_matches and re.search(r":\s*$", prefix)
                next_section = (
                    document.sections[section_index + 1]
                    if section_index + 1 < len(document.sections)
                    else None
                )
                reference_after_quote = bool(
                    next_section is not None
                    and next_section.role == "closing"
                    and "biblical_text" in next_section.claim_classification
                    and set(section.biblical_basis).intersection(next_section.biblical_basis)
                    and re.search(
                        r"\bcapitulo\s+\d+\b",
                        _accent_fold(next_section.narration),
                    )
                    and re.search(
                        r"\bversiculos?\s+\d+\b",
                        _accent_fold(next_section.narration),
                    )
                )
                if reference_before_quote or reference_after_quote:
                    offset = word_cursor + len(tokenize_words(section.narration[:quote_start]))
                    reference_hint = None
                    if spoken_reference_matches:
                        reference_match = spoken_reference_matches[-1]
                        verse_hint = re.sub(
                            r"\s+",
                            "",
                            reference_match.group("verse"),
                        ).replace("a", "-")
                        reference_hint = f"{reference_match.group('chapter')}:{verse_hint}"
                    citations.append(
                        ScriptureCitation(
                            section_id=section.section_id,
                            quote=" ".join(quote.split()),
                            word_offset=offset,
                            estimated_start_seconds=round(
                                offset / words_per_minute * 60,
                                2,
                            ),
                            reference_hint=reference_hint,
                        )
                    )
        word_cursor += len(tokenize_words(section.narration))
    return citations


def citation_target_range(
    duration_seconds: int,
    policy: EditorialPolicyConfig,
) -> tuple[int, int]:
    minutes = max(1.0, duration_seconds / 60)
    minimum = max(1, math.floor(minutes * policy.minimum_scripture_citations_per_minute))
    maximum = max(minimum, math.ceil(minutes * policy.maximum_scripture_citations_per_minute))
    return minimum, maximum


def validate_script_editorial_policy(
    document: ScriptDocument,
    *,
    duration_seconds: int,
    policy: EditorialPolicyConfig,
    sources: SourceLibrary | None = None,
) -> None:
    validate_authorial_narration_style(document)
    if document.long_form_strategy is not None:
        if document.long_form_strategy.narrator_identity != policy.authorial_narrator:
            raise ValueError(
                "long_form_strategy.narrator_identity precisa copiar "
                "editorial_policy.authorial_narrator."
            )
        if policy.authorial_narrator == "biblical_explainer_third_person_v1":
            validate_biblical_explainer_narration(document)
    if policy.standard == "legacy":
        return

    narration = document.narration_text()
    folded = narration.casefold()
    if policy.forbid_english_editorial_jargon:
        found = sorted(term for term in _ENGLISH_EDITORIAL_JARGON if term in folded)
        if found:
            raise ValueError(
                "A locução deve permanecer em português e não pode narrar jargão editorial: "
                + ", ".join(found)
            )
    if policy.forbid_generic_text_reference:
        match = _GENERIC_TEXT_REFERENCE_RE.search(narration)
        if match:
            raise ValueError(
                f"Evite {match.group(0)!r} na locução; use 'a Bíblia diz' ou verbalize "
                "Livro, capítulo e versículo."
            )

    citations = scripture_citations(document)
    if policy.require_literal_scripture_citations:
        minimum, maximum = citation_target_range(duration_seconds, policy)
        if not minimum <= len(citations) <= maximum:
            raise ValueError(
                "A quantidade de citações bíblicas literais precisa ficar entre "
                f"{minimum} e {maximum} para este vídeo; foram encontradas {len(citations)}."
            )
        if sources is not None:
            verified_quotes = {
                _normalized(entry.excerpt)
                for entry in sources.entries
                if entry.kind == "biblical_text" and entry.verified and entry.excerpt
            }
            unverified = [
                item.quote for item in citations if _normalized(item.quote) not in verified_quotes
            ]
            if unverified:
                raise ValueError(
                    "Toda citação literal precisa coincidir com o excerpt de uma fonte bíblica "
                    f"verificada. Sem correspondência: {unverified[0]!r}"
                )

    if policy.require_quoted_comment_response:
        closing = " ".join(
            section.narration for section in document.sections if section.role == "closing"
        )
        if not quoted_comment_response(closing):
            raise ValueError(
                "O convite final deve colocar a palavra ou frase de comentário entre aspas, "
                "por exemplo: escreva “CORAGEM” nos comentários."
            )
