from __future__ import annotations

import json
from datetime import date

import pytest

from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    ScriptDocument,
    ScriptSection,
    ShortsCommentPrompt,
    ShortsNarrativeSpine,
    ShortsRetentionBeat,
    ShortsScriptPackage,
    ShortsScriptStrategy,
    ShortsVerseOpening,
    SourceEntry,
    SourceLibrary,
    StageStatus,
)
from pipeline_videos.project import create_project
from pipeline_videos.shorts_script import (
    LEGACY_SCRIPTURE_BOUNDARY_PHRASE,
    import_shorts_script_package,
    prepare_shorts_script_queue,
    spoken_biblical_reference,
)
from pipeline_videos.storage import read_json, read_model, write_json
from pipeline_videos.utils import utc_now


def _short_project(tmp_path):
    layout = create_project(
        "davi-golias-short",
        "Davi e Golias em um minuto",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )
    manifest = layout.load_manifest()
    manifest.config.shorts_script.min_words = 60
    manifest.config.shorts_script.max_words = 140
    manifest.config.shorts_script.minimum_retention_beats = 3
    layout.save_manifest(manifest)
    write_json(
        layout.briefing,
        Briefing(
            title="Coragem em Josué 1:9",
            main_passage="Josué 1:9",
            central_theme="A presença de Deus sustenta a coragem obediente.",
            practical_application="Dar o próximo passo responsável mesmo com medo.",
            bible_translation="NVI",
            target_duration_seconds=60,
        ),
    )
    write_json(
        layout.sources,
        SourceLibrary(
            entries=[
                SourceEntry(
                    source_id="biblia-josue-1-9",
                    kind="biblical_text",
                    title="Josué 1:9 — NVI",
                    passage="Josué 1:9",
                    translation="NVI",
                    author_or_publisher="Biblica / fonte de teste",
                    url="https://example.invalid/josue-1-9",
                    accessed_at="2026-07-14",
                    excerpt=(
                        "Seja forte e corajoso! Não se apavore nem desanime, pois o Senhor, "
                        "o seu Deus, estará com você por onde andar."
                    ),
                    use_in_video="Versículo literal de abertura.",
                    claim_scope="Somente Josué 1:9.",
                    rights_note="Trecho fornecido pelo usuário; fixture de teste, não publicável.",
                    verified=True,
                )
            ]
        ),
    )
    return layout


def _package(queue: dict) -> ShortsScriptPackage:
    verse = (
        "Seja forte e corajoso! Não se apavore nem desanime, pois o Senhor, o seu Deus, "
        "estará com você por onde andar."
    )
    lead_in = "Em Josué, capítulo 1, versículo 9, lemos:"
    hook = "Você já soube o que fazer e mesmo assim travou de medo?"
    tension_question = hook
    bridge = (
        "Josué também tinha motivos para ter medo. Moisés tinha morrido, e agora era ele "
        "quem precisava conduzir o povo."
    )
    central_truth = "Deus não disse que seria fácil. Disse que estaria com Josué."
    practical_response = (
        "Se o medo travou você hoje, pense só no que precisa fazer agora e dê esse passo."
    )
    comment_prompt_text = (
        'Se você quer lembrar dessa mensagem durante a semana, escreve "CORAGEM" nos comentários.'
    )
    cta = (
        "Se essa mensagem falou com você, curta o vídeo, se inscreva no canal e "
        "compartilhe com alguém que precisa ouvir isso."
    )
    closing = f"{comment_prompt_text} {cta}"
    script = ScriptDocument(
        version=queue["next_script_version"],
        title="Coragem sustentada pela presença de Deus",
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="A tensão",
                narration=hook,
                biblical_basis=["Josué 1:9"],
                claim_classification=["interpretation"],
                retention_goal="Criar identificação imediata com uma tensão humana real.",
                visual_direction="Uma pessoa parada diante de uma decisão difícil.",
            ),
            ScriptSection(
                section_id="section-002",
                role="biblical_narrative",
                title="O versículo",
                narration=f"{lead_in} “{verse}”",
                biblical_basis=["Josué 1:9"],
                claim_classification=["biblical_text"],
                retention_goal="Levar a tensão imediatamente ao texto bíblico verificado.",
                visual_direction="Close nas mãos tensas de Josué antes de avançar.",
            ),
            ScriptSection(
                section_id="section-003",
                role="biblical_narrative",
                title="O contexto",
                narration=bridge,
                biblical_basis=["Josué 1:1-9"],
                claim_classification=["biblical_text", "verified_context"],
                retention_goal="Mostrar que a ordem foi dada dentro de uma transição difícil.",
                visual_direction="Manto de Moisés deixado para trás e o povo esperando Josué.",
            ),
            ScriptSection(
                section_id="section-004",
                role="reflection",
                title="O sentido",
                narration=central_truth,
                biblical_basis=["Josué 1:9"],
                claim_classification=["interpretation"],
                retention_goal="Resolver a pergunta sem transformar o texto em fórmula.",
                visual_direction="Josué ergue o olhar enquanto a luz alcança o caminho.",
            ),
            ScriptSection(
                section_id="section-005",
                role="application",
                title="O próximo passo",
                narration=practical_response,
                biblical_basis=["Josué 1:9"],
                claim_classification=["interpretation"],
                retention_goal="Entregar uma ação simples, responsável e praticável.",
                visual_direction="Pessoa contemporânea anotando um único próximo passo.",
            ),
            ScriptSection(
                section_id="section-006",
                role="closing",
                title="Convite",
                narration=closing,
                biblical_basis=["Josué 1:9"],
                claim_classification=["interpretation"],
                retention_goal="Convidar sem pressão com benefício explícito.",
                visual_direction="Caminho aberto ao amanhecer com espaço para a chamada final.",
            ),
        ],
        source_ids=["biblia-josue-1-9"],
    )
    strategy = ShortsScriptStrategy(
        target_viewer="Pessoas que precisam agir com responsabilidade apesar do medo.",
        viewer_need="Entender a coragem de Josué 1:9 sem receber uma promessa simplista.",
        core_value=central_truth,
        content_promise="Explicar de onde nasce a coragem apresentada em Josué 1:9.",
        hook=hook,
        hook_type="direct_tension",
        hook_techniques=["self_relevance", "open_loop"],
        hook_target_end_seconds=2.4,
        verse_opening=ShortsVerseOpening(
            source_id="biblia-josue-1-9",
            reference="Josué 1:9",
            translation="NVI",
            quote_mode="literal",
            lead_in=lead_in,
            verse_text=verse,
            target_end_seconds=10.5,
        ),
        narrative_spine=ShortsNarrativeSpine(
            pattern="human_hook_to_bible",
            development_mode="contextual_reflection",
            verse_section_id="section-002",
            tension_section_id="section-001",
            bridge_section_id="section-003",
            meaning_section_id="section-004",
            application_section_id="section-005",
            tension_question=tension_question,
            bridge_statement=bridge,
            central_truth=central_truth,
            practical_response=practical_response,
        ),
        open_loop=tension_question,
        payoff=central_truth,
        practical_value=practical_response,
        comment_prompt=ShortsCommentPrompt(
            mode="keyword_reaffirmation",
            prompt_text=comment_prompt_text,
            response_cue="CORAGEM",
            objective="Ajudar o espectador a reafirmar a mensagem em uma palavra.",
            fit_reason="Coragem é a palavra central do versículo e do vídeo.",
        ),
        cta=cta,
        reason_to_subscribe="Fazer parte da comunidade do canal.",
        retention_beats=[
            ShortsRetentionBeat(
                section_id="section-001",
                purpose="hook",
                viewer_question=tension_question,
                viewer_value="Reconhecer uma situação comum de medo e paralisia.",
                target_start_seconds=0,
                target_end_seconds=3,
                opens_loop=tension_question,
            ),
            ShortsRetentionBeat(
                section_id="section-002",
                purpose="stakes",
                viewer_question="O que a Bíblia diz diante desse medo?",
                viewer_value="Ouvir um versículo conhecido e verificado.",
                target_start_seconds=3,
                target_end_seconds=14,
            ),
            ShortsRetentionBeat(
                section_id="section-003",
                purpose="stakes",
                viewer_question="Em que situação Josué ouviu essa ordem?",
                viewer_value="O contexto que impede uma leitura rasa.",
                target_start_seconds=14,
                target_end_seconds=23,
            ),
            ShortsRetentionBeat(
                section_id="section-004",
                purpose="payoff",
                viewer_question="De onde nasce essa coragem?",
                viewer_value=central_truth,
                target_start_seconds=23,
                target_end_seconds=31,
                resolves_loop=tension_question,
            ),
            ShortsRetentionBeat(
                section_id="section-005",
                purpose="application",
                viewer_question="O que posso fazer hoje?",
                viewer_value=practical_response,
                target_start_seconds=31,
                target_end_seconds=42,
            ),
            ShortsRetentionBeat(
                section_id="section-006",
                purpose="cta",
                viewer_question="Por que acompanhar o canal?",
                viewer_value="Receber mais reflexões bíblicas fiéis e práticas.",
                target_start_seconds=42,
                target_end_seconds=48,
            ),
        ],
        source_ids=["biblia-josue-1-9"],
        section_ids=[
            "section-001",
            "section-002",
            "section-003",
            "section-004",
            "section-005",
            "section-006",
        ],
        biblical_safeguards=["Não transformar a presença de Deus em garantia de facilidade."],
        compliance_checks=["Versículo verificado na abertura", "CTA sem pressão"],
    )
    return ShortsScriptPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        script=script,
        biblical_review=BiblicalReview(
            script_version=script.version,
            reviewed_at=utc_now(),
            reviewer="shorts_script_specialist",
            summary="Roteiro fiel ao núcleo de 1 Samuel 17 e sem bloqueios.",
        ),
        strategy=strategy,
    )


def test_shorts_specialist_queue_imports_script_review_and_retention_strategy(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)

    assert queue["role"] == "shorts_script_specialist"
    assert queue["orchestration"]["single_writer"] is True
    assert queue["orchestration"]["may_modify_project_state"] is False
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["orchestration"]["fork_turns"] == "none"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-sol"
    assert queue["orchestration"]["reasoning_effort"] == "high"
    assert queue["inputs"] == {"compact_context": "script/shorts-context.json"}
    assert layout.shorts_script_context.is_file()
    context = read_json(layout.shorts_script_context)
    assert context["scripture_candidates"] == ["biblia-josue-1-9"]
    assert context["verse_opening_candidates"] == ["biblia-josue-1-9"]
    assert context["sources"][0]["excerpt"].startswith("Seja forte e corajoso")
    assert len(json.dumps(queue["schemas"]["package"])) < len(
        json.dumps(ShortsScriptPackage.model_json_schema())
    )
    assert queue["next_script_version"] == 1
    assert queue["scripture_candidates"] == ["biblia-josue-1-9"]
    assert queue["verse_opening_candidates"] == ["biblia-josue-1-9"]
    assert queue["spoken_reference_standard"] == {
        "compact_reference": "Josué 1:9",
        "spoken_reference": "Josué, capítulo 1, versículo 9",
        "lead_in_example": "Em Josué, capítulo 1, versículo 9, lemos:",
    }
    assert queue["scripture_boundary_standard"] == {
        "spoken_phrase": None,
        "boundary_mode": "quotation_sections_and_claim_classification",
        "direct_quote_caption_style": "italic",
        "context_labels_enabled": False,
    }
    assert queue["scripture_quote_standard"] == {
        "scope": "verse_text_only",
        "opening_quote": "“",
        "closing_quote": "”",
        "opening_example": ("Em Josué, capítulo 1, versículo 9, lemos: “texto do versículo.”"),
    }
    assert any("human_hook_to_bible" in instruction for instruction in queue["instructions"])
    assert any("capítulo X, versículo Y" in instruction for instruction in queue["instructions"])
    assert any(
        "Não fale nem escreva 'Até aqui" in instruction for instruction in queue["instructions"]
    )
    assert any(
        "somente verse_text entre aspas" in instruction for instruction in queue["instructions"]
    )
    assert any("recebem itálico" in instruction for instruction in queue["instructions"])
    assert any("português brasileiro" in instruction for instruction in queue["instructions"])
    assert any("voz pastoral simples" in instruction for instruction in queue["instructions"])
    assert any("um único momento" in instruction for instruction in queue["instructions"])
    assert any("imagem-âncora" in instruction for instruction in queue["instructions"])
    assert any("curta o vídeo" in instruction for instruction in queue["instructions"])

    write_json(layout.shorts_script_package_staging, _package(queue))
    imported = import_shorts_script_package(layout)
    repeated = import_shorts_script_package(layout)

    assert repeated == imported
    assert imported.script.word_count >= 60
    assert read_model(layout.script, ScriptDocument).version == 1
    assert layout.shorts_script_strategy.is_file()
    manifest = layout.load_manifest()
    assert manifest.stages["shorts_script"].status == StageStatus.COMPLETED
    assert manifest.stages["shorts_script"].usage["context_bytes"] > 0
    assert manifest.stages["shorts_script"].usage["schema_bytes"] > 0
    assert manifest.stages["script"].status == StageStatus.COMPLETED
    assert manifest.stages["biblical_review"].status == StageStatus.COMPLETED
    assert manifest.approvals["script"].status.value == "pending"


def test_light_script_queue_is_owned_by_main_agent_with_low_reasoning(tmp_path) -> None:
    layout = _short_project(tmp_path)
    project = layout.load_manifest()
    project.config.production.mode = "light_manual"
    project.config.production.use_subagents = False
    project.config.shorts_script.use_specialist_subagent = False
    project.config.youtube_growth.enabled = False
    project.config.youtube_growth.use_specialist_subagent = False
    layout.save_manifest(project)

    queue = prepare_shorts_script_queue(layout)

    assert queue["orchestration"]["mode"] == "main_agent"
    assert queue["orchestration"]["worker_id"] == "main"
    assert queue["orchestration"]["reasoning_effort"] == "low"
    assert any("agente principal" in instruction for instruction in queue["instructions"])


def test_spoken_biblical_reference_formats_single_verse_and_range() -> None:
    assert spoken_biblical_reference("1 Pedro 5:7") == ("1 Pedro, capítulo 5, versículo 7")
    assert spoken_biblical_reference("Salmo 23:1-4") == ("Salmo, capítulo 23, versículos 1 a 4")


def test_shorts_import_rejects_compact_numeric_reference_in_narration(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    compact_lead_in = "Em Josué 1:9, lemos:"
    altered_opening = package.strategy.verse_opening.model_copy(update={"lead_in": compact_lead_in})
    altered_sections = list(package.script.sections)
    altered_sections[1] = altered_sections[1].model_copy(
        update={"narration": f"{compact_lead_in} “{altered_opening.verse_text}”"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy.model_copy(
                update={
                    "verse_opening": altered_opening,
                }
            ),
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="capítulo X, versículo Y"):
        import_shorts_script_package(layout)


def test_shorts_import_rejects_legacy_scripture_boundary_phrase(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    altered_sections = list(package.script.sections)
    altered_sections[2] = altered_sections[2].model_copy(
        update={
            "narration": (
                f"{LEGACY_SCRIPTURE_BOUNDARY_PHRASE} "
                f"{package.strategy.narrative_spine.bridge_statement}"
            )
        }
    )
    altered_package = package.model_copy(
        update={"script": package.script.model_copy(update={"sections": altered_sections})}
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="não pode narrar o rótulo"):
        import_shorts_script_package(layout)


def test_shorts_import_requires_quotes_around_only_the_literal_verse(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    opening = package.strategy.verse_opening
    altered_sections = list(package.script.sections)
    altered_sections[1] = altered_sections[1].model_copy(
        update={"narration": f"“{opening.lead_in} {opening.verse_text}”"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy,
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="referência fora das aspas"):
        import_shorts_script_package(layout)


def test_shorts_prepare_requires_a_verified_literal_verse_source(tmp_path) -> None:
    layout = create_project(
        "sem-fonte-short",
        "Short sem fonte",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )

    with pytest.raises(ValueError, match="roteiro de Short exige"):
        prepare_shorts_script_queue(layout)


def test_shorts_import_rejects_literal_text_that_differs_from_source(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    altered_verse = "Seja forte, porque tudo será fácil."
    altered_opening = package.strategy.verse_opening.model_copy(
        update={"verse_text": altered_verse}
    )
    altered_sections = list(package.script.sections)
    altered_sections[1] = altered_sections[1].model_copy(
        update={"narration": f"{altered_opening.lead_in} “{altered_verse}”"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy.model_copy(
                update={
                    "verse_opening": altered_opening,
                }
            ),
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="diverge do excerpt verificado"):
        import_shorts_script_package(layout)


def test_shorts_import_rejects_a_different_final_cta(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    altered_cta = "Se gostou, se inscreva no canal."
    altered_sections = list(package.script.sections)
    altered_sections[-1] = altered_sections[-1].model_copy(
        update={"narration": f"{package.strategy.comment_prompt.prompt_text} {altered_cta}"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy.model_copy(update={"cta": altered_cta}),
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="CTA final diverge"):
        import_shorts_script_package(layout)


def test_shorts_import_rejects_spiritual_engagement_bait(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    bait_text = 'Comente "AMÉM" para receber uma bênção.'
    bait_prompt = package.strategy.comment_prompt.model_copy(
        update={"prompt_text": bait_text, "response_cue": "AMÉM"}
    )
    altered_sections = list(package.script.sections)
    altered_sections[-1] = altered_sections[-1].model_copy(
        update={"narration": f"{bait_text} {package.strategy.cta}"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy.model_copy(update={"comment_prompt": bait_prompt}),
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="promessa espiritual indevida"):
        import_shorts_script_package(layout)


def test_shorts_import_rejects_multiple_scenes_in_one_visual_direction(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    altered_sections = list(package.script.sections)
    altered_sections[3] = altered_sections[3].model_copy(
        update={"visual_direction": "Três vinhetas com momentos diferentes da história."}
    )
    altered_package = package.model_copy(
        update={"script": package.script.model_copy(update={"sections": altered_sections})}
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="múltiplas cenas na mesma imagem"):
        import_shorts_script_package(layout)


def test_human_hook_requires_declared_attention_technique(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    altered_package = package.model_copy(
        update={"strategy": package.strategy.model_copy(update={"hook_techniques": []})}
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="técnicas naturais de atenção"):
        import_shorts_script_package(layout)
