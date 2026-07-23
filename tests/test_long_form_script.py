from __future__ import annotations

import re
from datetime import date

import pytest

from pipeline_videos.editorial import scripture_citations, validate_script_editorial_policy
from pipeline_videos.long_form_script import (
    minimum_long_form_reengagements,
    validate_biblical_storytelling_voice,
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
    NarrationVisualScene,
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


def _sync_visual_scenes(document: ScriptDocument) -> None:
    strategy = document.long_form_strategy
    assert strategy is not None
    document.long_form_strategy = strategy.model_copy(
        update={
            "visual_scenes": [
                NarrationVisualScene(
                    section_id=section.section_id,
                    narration=section.narration,
                    scene_description=f"Ação concreta atualizada para {section.section_id}.",
                    visual_relation="contextual",
                )
                for section in document.sections
            ]
        }
    )


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
        visual_scenes=[
            NarrationVisualScene(
                section_id=section.section_id,
                narration=section.narration,
                scene_description=f"Ação concreta do movimento {index}.",
                visual_relation="contextual",
            )
            for index, section in enumerate(sections, start=1)
        ],
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


def _creative_long_fixture() -> tuple[
    ScriptDocument,
    SourceLibrary,
    Briefing,
    LongFormScriptConfig,
]:
    document, sources, legacy_briefing, _, _ = _long_script_fixture()
    legacy = document.long_form_strategy
    assert legacy is not None
    opening_line = "José recebeu no Egito os irmãos que anos antes o haviam vendido como escravo."
    viewer_outcome = (
        "Compreender como Gênesis 45 progride da revelação da identidade ao reencontro."
    )
    sections = [section.model_copy(deep=True) for section in document.sections]
    sections[0] = sections[0].model_copy(
        update={
            "narration": sections[0].narration.replace(legacy.opening_hook, opening_line, 1),
        }
    )
    sections[4] = sections[4].model_copy(
        update={
            "role": "reflection",
            "narration": sections[4].narration.replace(
                legacy.practical_application,
                viewer_outcome,
                1,
            ),
        }
    )
    functions = [
        ["orientation", "evidence"],
        ["context", "progression"],
        ["evidence", "reengagement"],
        ["analysis", "reengagement"],
        ["resolution", "synthesis"],
        ["cta"],
    ]
    beats = [
        beat.model_copy(update={"functions": beat_functions})
        for beat, beat_functions in zip(legacy.progression, functions, strict=True)
    ]
    briefing = legacy_briefing.model_copy(
        update={
            "practical_application": "",
            "creative_mode": "passage_explainer",
            "interpretive_stance": "expository",
            "hook_policy": "none",
            "hook_family": "none",
            "viewer_outcome": viewer_outcome,
            "creative_rationale": (
                "Explicar a progressão da passagem sem converter a conclusão em conselho."
            ),
        }
    )
    placeholder_anchors = [
        anchor.model_copy(update={"target_start_seconds": 0}) for anchor in legacy.scripture_anchors
    ]
    strategy = LongFormScriptStrategy(
        standard="creative_retention_long_v2",
        target_viewer=briefing.audience,
        viewer_need="acompanhar com clareza a progressão factual e literária da passagem",
        content_promise=legacy.content_promise,
        central_question=legacy.central_question,
        opening_line=opening_line,
        creative_mode="passage_explainer",
        interpretive_stance="expository",
        hook_policy="none",
        hook_family="none",
        hook_rationale="A abertura orienta a cena sem criar urgência artificial.",
        resolution_timing="final_quarter",
        progression=beats,
        scripture_anchors=placeholder_anchors,
        resolution=legacy.payoff,
        viewer_takeaway=viewer_outcome,
        engagement=legacy.engagement,
        visual_scenes=[
            NarrationVisualScene(
                section_id=section.section_id,
                narration=section.narration,
                scene_description=f"Cena concreta do movimento {index}.",
                visual_relation="contextual",
            )
            for index, section in enumerate(sections, start=1)
        ],
        source_ids=legacy.source_ids,
    )
    creative_document = document.model_copy(
        update={"sections": sections, "long_form_strategy": strategy}
    )
    citations = scripture_citations(creative_document)
    creative_document.long_form_strategy = strategy.model_copy(
        update={
            "scripture_anchors": [
                anchor.model_copy(update={"target_start_seconds": citation.estimated_start_seconds})
                for anchor, citation in zip(placeholder_anchors, citations, strict=True)
            ]
        }
    )
    config = LongFormScriptConfig(
        enabled=True,
        narrative_standard="creative_retention_long_v2",
        require_callback=False,
    )
    return creative_document, sources, briefing, config


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
    _sync_visual_scenes(document)

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
    _sync_visual_scenes(multiple)
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
    assert "plano médio ou close de alto impacto" in scenes.scenes[0].shot
    assert any(
        scene.section_id == "section-003" and "reengajamento visual" in scene.narrative_function
        for scene in scenes.scenes
    )


def test_legacy_long_project_import_enforces_and_persists_v1_contract(tmp_path) -> None:
    document, sources, briefing, _, _ = _long_script_fixture()
    layout = create_project(
        "jose-importacao",
        "José diante dos irmãos",
        projects_dir=tmp_path / "projects",
        project_date=date(2026, 7, 16),
        target_duration_seconds=180,
    )
    manifest = layout.load_manifest()
    manifest.config.long_form_script.narrative_standard = "retention_story_long_v1"
    manifest.config.long_form_script.require_callback = True
    layout.save_manifest(manifest)
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


def test_creative_long_accepts_hookless_explainer_without_application(tmp_path) -> None:
    document, sources, briefing, config = _creative_long_fixture()

    diagnostics = validate_long_form_retention_script(
        document,
        target_duration_seconds=180,
        config=config,
        sources=sources,
        briefing=briefing,
    )

    assert diagnostics["hook_used"] is False
    assert diagnostics["creative_mode"] == "passage_explainer"
    assert len(diagnostics["scripture_citation_timestamps"]) == 3
    assert document.long_form_strategy is not None
    assert document.long_form_strategy.archetype is None
    assert document.long_form_strategy.practical_application == ""
    assert all(section.role != "application" for section in document.sections)

    layout = create_project(
        "genesis-45-explicado",
        "Como Gênesis 45 constrói o reencontro",
        projects_dir=tmp_path / "projects",
        project_date=date(2026, 7, 19),
        target_duration_seconds=180,
    )
    write_json(layout.briefing, briefing)
    write_json(layout.sources, sources)
    source = tmp_path / "creative-long-script.json"
    write_json(source, document)

    imported = import_script(layout, source)
    contract = ensure_creative_contract(layout, imported)

    assert contract.algorithm == "creative-contract-v2"
    assert contract.hook_used is False
    assert contract.hook_family == "none"
    assert contract.pacing.profile_id == "long_creative_v2"
    assert "resolution" in {function for beat in contract.beats for function in beat.functions}


def _as_biblical_storytelling(document: ScriptDocument) -> ScriptDocument:
    converted = document.model_copy(deep=True)
    assert converted.long_form_strategy is not None
    sections = []
    for index, section in enumerate(converted.sections):
        role = section.role if index in {0, len(converted.sections) - 1} else "biblical_narrative"
        sections.append(
            section.model_copy(
                update={
                    "role": role,
                    "title": f"Acontecimento {index + 1}",
                    "narration": (
                        "José atravessou um novo acontecimento, tomou uma decisão concreta e "
                        f"seguiu adiante no movimento {index + 1}."
                    ),
                }
            )
        )
    beats = [
        beat.model_copy(
            update={
                "functions": [
                    "progression" if function in {"analysis", "comparison"} else function
                    for function in beat.functions
                ]
            }
        )
        for beat in converted.long_form_strategy.progression
    ]
    converted.sections = sections
    converted.long_form_strategy = converted.long_form_strategy.model_copy(
        update={
            "creative_mode": "biblical_storytelling",
            "interpretive_stance": "factual",
            "progression": beats,
        }
    )
    return converted


def test_storytelling_profile_rejects_ai_like_meta_analysis() -> None:
    document, _, _, _ = _creative_long_fixture()
    storytelling = _as_biblical_storytelling(document)
    storytelling.sections[
        1
    ].narration = "O retrato começa no lugar de trabalho. O relato não explica suas emoções."

    with pytest.raises(ValueError, match="metalinguagem de análise"):
        validate_biblical_storytelling_voice(storytelling)


def test_storytelling_profile_accepts_action_driven_human_narration() -> None:
    document, _, _, _ = _creative_long_fixture()
    storytelling = _as_biblical_storytelling(document)
    storytelling.sections[1].narration = (
        "José entrou na sala onde os irmãos esperavam. Eles ainda não o reconheceram, mas ele "
        "já sabia exatamente quem estava diante dele."
    )

    validate_biblical_storytelling_voice(storytelling)


@pytest.mark.parametrize(
    "narration",
    [
        (
            "Mateus estava na coletoria, sem preparação anterior, explicação emocional ou "
            "qualquer aviso."
        ),
        "A cena não descreve seus pensamentos, nem explica seus medos.",
        "Não fez um discurso longo. Jesus apenas disse: Siga-me.",
        "Jesus não fez um discurso longo, mas o chamou imediatamente.",
    ],
)
def test_storytelling_profile_rejects_negative_scaffolding(narration: str) -> None:
    document, _, _, _ = _creative_long_fixture()
    storytelling = _as_biblical_storytelling(document)
    storytelling.sections[1].narration = narration

    with pytest.raises(ValueError, match="negação retórica|lista de ausências"):
        validate_biblical_storytelling_voice(storytelling)


def test_storytelling_profile_ignores_negation_inside_literal_scripture() -> None:
    document, _, _, _ = _creative_long_fixture()
    storytelling = _as_biblical_storytelling(document)
    storytelling.sections[1].narration = (
        "Jesus chamou Mateus diante da coletoria. Em Mateus, capítulo 9, versículo 13, lemos: "
        "“Eu não vim para chamar os justos, mas os pecadores.” Depois, Jesus entrou na casa e "
        "se sentou à mesa."
    )

    validate_biblical_storytelling_voice(storytelling)


def test_storytelling_profile_accepts_positive_concrete_action() -> None:
    document, _, _, _ = _creative_long_fixture()
    storytelling = _as_biblical_storytelling(document)
    storytelling.sections[1].narration = (
        "Mateus se levantou, deixou a coletoria para trás e seguiu Jesus. Pouco depois, recebeu "
        "Jesus e outros convidados à mesa."
    )

    validate_biblical_storytelling_voice(storytelling)
