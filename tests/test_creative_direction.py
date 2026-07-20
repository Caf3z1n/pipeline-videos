from __future__ import annotations

from pipeline_videos.creative_direction import (
    CREATIVE_MODE_SPECS,
    HOOK_TEMPLATE_EXAMPLES,
    creative_evidence_errors,
    hook_matches_family,
)
from pipeline_videos.models import ScriptDocument, ScriptSection, SourceLibrary


def _document(*classifications: str) -> ScriptDocument:
    return ScriptDocument(
        title="Direção criativa",
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="Abertura",
                narration="Uma afirmação editorial claramente classificada.",
                claim_classification=list(classifications),
            )
        ],
    )


def test_catalog_exposes_sixteen_distinct_creative_modes() -> None:
    assert set(CREATIVE_MODE_SPECS) == {
        "biblical_storytelling",
        "character_portrait",
        "passage_explainer",
        "biblical_curiosity",
        "historical_context",
        "philosophical_reflection",
        "thematic_investigation",
        "comparison_contrast",
        "ethical_dilemma",
        "misconception_correction",
        "literary_exploration",
        "question_driven_essay",
        "chronological_reconstruction",
        "cause_and_consequence",
        "devotional_application",
        "pastoral_encouragement",
    }
    assert {mode for mode, spec in CREATIVE_MODE_SPECS.items() if spec.application_required} == {
        "devotional_application",
        "pastoral_encouragement",
    }


def test_requested_hook_templates_are_available_and_recognized() -> None:
    concrete_hooks = {
        "if_you_want_do_this": "Se você quer entender esta passagem, faça isso.",
        "what_happens_if_you_stop": "O que acontece se você parar de ignorar o contexto?",
        "still_doing_losing": "Se você ainda lê sem contexto, está perdendo a virada.",
        "nobody_talks_but": "Ninguém fala sobre esta palavra, mas ela muda a cena.",
        "want_to_learn_start": "Quer aprender a ler este salmo? Comece pela primeira palavra.",
        "never_do_this": "Nunca leia esta promessa fora do contexto.",
    }

    assert list(HOOK_TEMPLATE_EXAMPLES)[:6] == list(concrete_hooks)
    assert all(hook_matches_family(hook, family) for family, hook in concrete_hooks.items())


def test_evidence_rules_separate_factual_narration_from_interpretation() -> None:
    factual_errors = creative_evidence_errors(
        _document("biblical_text", "interpretation"),
        SourceLibrary(),
        creative_mode="biblical_storytelling",
        interpretive_stance="factual",
    )
    exploratory_errors = creative_evidence_errors(
        _document("biblical_text"),
        SourceLibrary(),
        creative_mode="philosophical_reflection",
        interpretive_stance="exploratory",
    )

    assert any("postura factual" in error for error in factual_errors)
    assert any("postura exploratória" in error for error in exploratory_errors)
