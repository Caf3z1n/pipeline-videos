from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from pipeline_videos.models import EditorialPolicyConfig, ScriptDocument, SourceLibrary

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
_QUOTED_TEXT_RE = re.compile(r'["“](?P<quote>.+?)["”]', flags=re.DOTALL)
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


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def quoted_comment_response(value: str) -> str | None:
    match = _COMMENT_RESPONSE_RE.search(value)
    return " ".join(match.group("response").split()) if match else None


def scripture_citations(document: ScriptDocument) -> list[ScriptureCitation]:
    citations: list[ScriptureCitation] = []
    for section in document.sections:
        if "biblical_text" not in section.claim_classification:
            continue
        for match in _QUOTED_TEXT_RE.finditer(section.narration):
            prefix = section.narration[max(0, match.start() - 180) : match.start()]
            if (
                re.search(r"\bcapítulo\s+\d+\b", prefix, flags=re.IGNORECASE)
                and re.search(r"\bversículos?\s+\d+\b", prefix, flags=re.IGNORECASE)
                and re.search(r"\b(?:lemos|diz|declara)\s*:\s*$", prefix, flags=re.IGNORECASE)
            ):
                citations.append(
                    ScriptureCitation(
                        section_id=section.section_id,
                        quote=" ".join(match.group("quote").split()),
                    )
                )
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
