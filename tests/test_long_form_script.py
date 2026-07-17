from __future__ import annotations

import re
from datetime import date

import pytest

from pipeline_videos.editorial import scripture_citations, validate_script_editorial_policy
from pipeline_videos.long_form_script import (
    minimum_long_form_reengagements,
    validate_long_form_retention_script,
)
from pipeline_videos.models import (
    Briefing,
    EditorialPolicyConfig,
    LongFormEngagementPlan,
    LongFormRetentionBeat,
    LongFormScriptConfig,
    LongFormScriptStrategy,
    LongFormScriptureAnchor,
    ProjectConfig,
    ScriptDocument,
    ScriptSection,
    SourceEntry,
    SourceLibrary,
)
from pipeline_videos.project import create_project
from pipeline_videos.scenes import build_scenes
from pipeline_videos.script import ensure_creative_contract, import_script
from pipeline_videos.storage import write_json
from pipeline_videos.utils import tokenize_words


def _pad(text: str, target_words: int, *, ending: str = "") -> str:
    body = text.strip()
    while len(tokenize_words(f"{body} {ending}")) < target_words:
        body += " A narrativa avança com clareza e propósito."
    return f"{body} {ending}".strip()


def _long_script_fixture() -> tuple[
    ScriptDocument,
    SourceLibrary,
    Briefing,
    LongFormScriptConfig,
    EditorialPolicyConfig,
]:
    quotes = (
        "Chegai-vos a mim, peço-vos.",
        "Não vos entristeçais, nem vos pese aos vossos olhos.",
        "E beijou a todos os seus irmãos, e chorou sobre eles.",
    )
    source_ids = ["genesis-45-4", "genesis-45-5", "genesis-45-15"]
    references = ["Gênesis 45:4", "Gênesis 45:5", "Gênesis 45:15"]
    sources = SourceLibrary(
        entries=[
            SourceEntry(
                source_id=source_id,
                kind="biblical_text",
                title=reference,
                passage=reference,
                excerpt=quote,
                use_in_video="Citação literal",
                claim_scope="Versículo citado",
                verified=True,
            )
            for source_id, reference, quote in zip(
                source_ids,
                references,
                quotes,
                strict=True,
            )
        ]
    )
    briefing = Briefing(
        title="José diante dos irmãos",
        main_passage="Gênesis 45",
        central_theme="O perdão interrompe o ciclo da vingança",
        practical_application="Responder à dor com verdade, limites e perdão.",
        audience="pessoas feridas por conflitos familiares",
        emotional_intent="conduzir da tensão à possibilidade de reconciliação",
        target_duration_seconds=180,
    )
    hook = "José tinha poder para se vingar, mas seus irmãos não sabiam o que ele faria."
    promise = "O perdão interrompe o ciclo da vingança."
    question = "O que transforma poder em misericórdia?"
    payoff = "José não negou a ferida; ele escolheu não devolver a dor."
    application = briefing.practical_application
    callback = "O homem que podia se vingar terminou abraçando quem o feriu."
    cta = "Se esta leitura ajudou você, inscreva-se no canal."

    narrations = [
        _pad(
            f"{hook} {promise} {question} Em Gênesis, capítulo 45, versículo 4, "
            f"lemos: “{quotes[0]}”",
            48,
        ),
        _pad(
            "Os irmãos estavam diante do governador do Egito sem reconhecer nele o jovem que "
            "haviam vendido. Agora a antiga violência estava diante de quem possuía poder.",
            97,
        ),
        _pad(
            f"Em Gênesis, capítulo 45, versículo 5, lemos: “{quotes[1]}” José mudou a direção "
            "da conversa: em vez de usar o passado para esmagá-los, revelou quem era.",
            97,
        ),
        _pad(
            "Essa revelação não apagou o que aconteceu. Ela elevou o risco da cena, porque os "
            "irmãos finalmente perceberam que estavam nas mãos daquele que haviam ferido.",
            85,
        ),
        _pad(
            f"Em Gênesis, capítulo 45, versículo 15, lemos: “{quotes[2]}” {payoff} {application}",
            73,
        ),
        _pad(
            "A história começou com poder suficiente para vingança.",
            36,
            ending=f"{callback} {cta}",
        ),
    ]
    roles = [
        "opening",
        "biblical_narrative",
        "biblical_narrative",
        "reflection",
        "application",
        "closing",
    ]
    bases = [[references[0]], [], [references[1]], [], [references[2]], []]
    sections = [
        ScriptSection(
            section_id=f"section-{index:03d}",
            role=role,
            title=f"Movimento {index}",
            narration=narration,
            biblical_basis=basis,
            claim_classification=(
                ["biblical_text", "interpretation"] if basis else ["interpretation"]
            ),
            retention_goal=f"Valor novo do movimento {index}.",
            visual_direction=f"Progressão visual concreta do movimento {index}.",
        )
        for index, (role, narration, basis) in enumerate(
            zip(roles, narrations, bases, strict=True),
            start=1,
        )
    ]
    timings = [(0, 20), (20, 60), (60, 100), (100, 135), (135, 165), (165, 180)]
    functions = [
        ["hook", "progression"],
        ["progression"],
        ["progression", "reengagement"],
        ["progression", "reengagement"],
        ["payoff", "application"],
        ["cta"],
    ]
    beats = [
        LongFormRetentionBeat(
            beat_id=f"beat-{index:03d}",
            section_id=section.section_id,
            functions=beat_functions,
            viewer_value=section.retention_goal or "",
            new_information=f"Informação inédita do movimento {index}.",
            target_start_seconds=start,
            target_end_seconds=end,
            visual_progression=section.visual_direction or "",
        )
        for index, (section, beat_functions, (start, end)) in enumerate(
            zip(sections, functions, timings, strict=True),
            start=1,
        )
    ]
    placeholder_anchors = [
        LongFormScriptureAnchor(
            source_id=source_id,
            reference=reference,
            section_id=section_id,
            target_start_seconds=0,
            narrative_purpose="Mudar o movimento da narrativa com evidência bíblica.",
        )
        for source_id, reference, section_id in zip(
            source_ids,
            references,
            ("section-001", "section-003", "section-005"),
            strict=True,
        )
    ]
    strategy = LongFormScriptStrategy(
        archetype="conflict_first",
        target_viewer=briefing.audience,
        viewer_need="entender como lidar com uma ferida sem repetir a violência",
        content_promise=promise,
        central_question=question,
        opening_hook=hook,
        opening_conflict="José pode se vingar dos irmãos que o venderam.",
        progression=beats,
        scripture_anchors=placeholder_anchors,
        payoff=payoff,
        practical_application=application,
        callback=callback,
        engagement=LongFormEngagementPlan(
            action="subscribe",
            spoken_text=cta,
            payoff_connection="Continuar acompanhando leituras bíblicas narradas com clareza.",
        ),
        source_ids=source_ids,
    )
    document = ScriptDocument(
        title="José diante dos irmãos",
        sections=sections,
        source_ids=source_ids,
        long_form_strategy=strategy,
    )
    actual = scripture_citations(document)
    document.long_form_strategy = strategy.model_copy(
        update={
            "scripture_anchors": [
                anchor.model_copy(update={"target_start_seconds": citation.estimated_start_seconds})
                for anchor, citation in zip(placeholder_anchors, actual, strict=True)
            ]
        }
    )
    config = LongFormScriptConfig(enabled=True, narrative_standard="retention_story_long_v1")
    editorial_policy = EditorialPolicyConfig(
        standard="scripture_rich_ptbr_v1",
        forbid_english_editorial_jargon=True,
        forbid_generic_text_reference=True,
        require_literal_scripture_citations=True,
        require_quoted_comment_response=False,
    )
    return document, sources, briefing, config, editorial_policy


def test_long_form_v1_validates_retention_citation_cadence_and_single_cta() -> None:
    document, sources, briefing, config, editorial_policy = _long_script_fixture()

    validate_script_editorial_policy(
        document,
        duration_seconds=180,
        policy=editorial_policy,
        sources=sources,
    )
    diagnostics = validate_long_form_retention_script(
        document,
        target_duration_seconds=180,
        config=config,
        sources=sources,
        briefing=briefing,
    )

    assert 153 <= diagnostics["estimated_duration_seconds"] <= 201.6
    assert diagnostics["reengagement_count"] == 2
    assert len(diagnostics["scripture_citation_timestamps"]) == 3
    assert diagnostics["scripture_citation_timestamps"][0] <= 75


def test_long_form_rejects_clustered_scripture_citations() -> None:
    document, sources, briefing, config, _ = _long_script_fixture()
    document = document.model_copy(deep=True)
    second_quote = sources.entries[1].excerpt
    assert second_quote is not None
    section_one = document.sections[0]
    section_three = document.sections[2]
    section_one.narration += f" Em Gênesis, capítulo 45, versículo 5, lemos: “{second_quote}”"
    section_one.biblical_basis.append("Gênesis 45:5")
    section_three.narration = re.sub(
        r"Em Gênesis, capítulo 45, versículo 5, lemos: “.+?”\s*",
        "",
        section_three.narration,
        count=1,
    )
    section_three.biblical_basis = []
    section_three.claim_classification = ["interpretation"]
    citations = scripture_citations(document)
    assert document.long_form_strategy is not None
    anchors = document.long_form_strategy.scripture_anchors
    document.long_form_strategy = document.long_form_strategy.model_copy(
        update={
            "scripture_anchors": [
                anchors[0].model_copy(
                    update={
                        "section_id": citations[0].section_id,
                        "target_start_seconds": citations[0].estimated_start_seconds,
                    }
                ),
                anchors[1].model_copy(
                    update={
                        "section_id": citations[1].section_id,
                        "target_start_seconds": citations[1].estimated_start_seconds,
                    }
                ),
                anchors[2].model_copy(
                    update={"target_start_seconds": citations[2].estimated_start_seconds}
                ),
            ]
        }
    )

    with pytest.raises(ValueError, match="agrupadas"):
        validate_long_form_retention_script(
            document,
            target_duration_seconds=180,
            config=config,
            sources=sources,
            briefing=briefing,
        )


def test_long_form_rejects_sparse_reengagement_and_multiple_cta_actions() -> None:
    document, sources, briefing, config, _ = _long_script_fixture()
    assert document.long_form_strategy is not None
    sparse = document.model_copy(deep=True)
    assert sparse.long_form_strategy is not None
    beats = list(sparse.long_form_strategy.progression)
    beats[3] = beats[3].model_copy(update={"functions": ["progression"]})
    sparse.long_form_strategy = sparse.long_form_strategy.model_copy(update={"progression": beats})
    with pytest.raises(ValueError, match="ao menos 2 reengajamentos"):
        validate_long_form_retention_script(
            sparse,
            target_duration_seconds=180,
            config=config,
            sources=sources,
            briefing=briefing,
        )

    multiple = document.model_copy(deep=True)
    assert multiple.long_form_strategy is not None
    old_cta = multiple.long_form_strategy.engagement.spoken_text
    new_cta = "Inscreva-se no canal e compartilhe este vídeo."
    multiple.sections[-1].narration = (
        multiple.sections[-1].narration.removesuffix(old_cta) + new_cta
    )
    multiple.long_form_strategy = multiple.long_form_strategy.model_copy(
        update={
            "engagement": multiple.long_form_strategy.engagement.model_copy(
                update={"spoken_text": new_cta}
            )
        }
    )
    with pytest.raises(ValueError, match="exatamente uma ação"):
        validate_long_form_retention_script(
            multiple,
            target_duration_seconds=180,
            config=config,
            sources=sources,
            briefing=briefing,
        )


def test_long_form_defaults_preserve_legacy_manifests() -> None:
    config = ProjectConfig.model_validate({})

    assert config.long_form_script.enabled is False
    assert config.long_form_script.narrative_standard == "legacy"
    active = config.long_form_script.model_copy(
        update={"enabled": True, "narrative_standard": "retention_story_long_v1"}
    )
    assert minimum_long_form_reengagements(540, active) == 4


def test_long_form_strategy_drives_creative_contract_and_visual_resets(tmp_path) -> None:
    document, _, briefing, _, _ = _long_script_fixture()
    layout = create_project(
        "jose-retencao",
        "José diante dos irmãos",
        projects_dir=tmp_path,
        project_date=date(2026, 7, 16),
        target_duration_seconds=180,
    )
    write_json(layout.briefing, briefing)
    write_json(layout.script, document)

    contract = ensure_creative_contract(layout, document)
    scenes = build_scenes(layout, skip_approvals=True)

    assert contract.narrative_standard == "retention_story_long_v1"
    assert contract.pacing.profile_id == "long_retention_v1"
    assert contract.central_question == document.long_form_strategy.central_question
    assert contract.engagement_action == "subscribe"
    assert sum("reengagement" in beat.functions for beat in contract.beats) == 2
    assert len(contract.scripture_citation_timestamps) == 3
    assert "composição horizontal de alto impacto" in scenes.scenes[0].shot
    assert any(
        scene.section_id == "section-003" and "reengajamento visual" in scene.narrative_function
        for scene in scenes.scenes
    )


def test_new_long_project_import_enforces_and_persists_v1_contract(tmp_path) -> None:
    document, sources, briefing, _, _ = _long_script_fixture()
    layout = create_project(
        "jose-importacao",
        "José diante dos irmãos",
        projects_dir=tmp_path / "projects",
        project_date=date(2026, 7, 16),
        target_duration_seconds=180,
    )
    write_json(layout.briefing, briefing)
    write_json(layout.sources, sources)
    source = tmp_path / "long-script.json"
    write_json(source, document)

    imported = import_script(layout, source)

    assert imported.long_form_strategy is not None
    assert imported.word_count > 0
    assert 153 <= imported.estimated_duration_seconds <= 201.6
    assert layout.creative_contract.is_file()
    assert layout.load_manifest().stages["script"].status.value == "completed"
