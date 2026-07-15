import pytest

from pipeline_videos.editorial import (
    citation_target_range,
    scripture_citations,
    validate_script_editorial_policy,
)
from pipeline_videos.models import (
    EditorialPolicyConfig,
    ScriptDocument,
    ScriptSection,
    SourceEntry,
    SourceLibrary,
)


def _policy() -> EditorialPolicyConfig:
    return EditorialPolicyConfig(
        standard="scripture_rich_ptbr_v1",
        forbid_english_editorial_jargon=True,
        forbid_generic_text_reference=True,
        require_literal_scripture_citations=True,
        require_quoted_comment_response=True,
    )


def _script_and_sources(count: int = 8) -> tuple[ScriptDocument, SourceLibrary]:
    sections = []
    entries = []
    for index in range(1, count + 1):
        quote = f"Citação verificada número {index}."
        narration = f"Em 1 Samuel, capítulo 17, versículo {index}, lemos: “{quote}”"
        if index == count:
            narration += " Escreva “CORAGEM” nos comentários."
        sections.append(
            ScriptSection(
                section_id=f"section-{index:03d}",
                role="closing" if index == count else "biblical_narrative",
                title=f"Citação {index}",
                narration=narration,
                biblical_basis=[f"1 Samuel 17:{index}"],
                claim_classification=["biblical_text"],
            )
        )
        entries.append(
            SourceEntry(
                source_id=f"source-{index}",
                kind="biblical_text",
                title=f"1 Samuel 17:{index}",
                passage=f"1 Samuel 17:{index}",
                excerpt=quote,
                use_in_video="Citação literal",
                claim_scope="Versículo citado",
                verified=True,
            )
        )
    return ScriptDocument(title="Davi e Golias", sections=sections), SourceLibrary(entries=entries)


def test_ten_minute_video_requires_eight_to_twelve_verified_citations() -> None:
    policy = _policy()
    script, sources = _script_and_sources(8)

    assert citation_target_range(600, policy) == (8, 12)
    assert len(scripture_citations(script)) == 8
    validate_script_editorial_policy(
        script,
        duration_seconds=600,
        policy=policy,
        sources=sources,
    )

    short_script, short_sources = _script_and_sources(7)
    with pytest.raises(ValueError, match="entre 8 e 12"):
        validate_script_editorial_policy(
            short_script,
            duration_seconds=600,
            policy=policy,
            sources=short_sources,
        )


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("Agora o open loop se fecha.", "jargão editorial"),
        ("O texto fala sobre coragem.", "a Bíblia diz"),
        ("Escreva CORAGEM nos comentários.", "entre aspas"),
    ],
)
def test_narration_rejects_english_jargon_generic_text_and_unquoted_response(
    replacement: str,
    message: str,
) -> None:
    script, sources = _script_and_sources(8)
    closing = script.sections[-1]
    prefix = closing.narration.split(" Escreva", 1)[0]
    sections = list(script.sections)
    sections[-1] = closing.model_copy(update={"narration": f"{prefix} {replacement}"})

    with pytest.raises(ValueError, match=message):
        validate_script_editorial_policy(
            script.model_copy(update={"sections": sections}),
            duration_seconds=600,
            policy=_policy(),
            sources=sources,
        )
