from __future__ import annotations

import json
from datetime import date

import pytest

from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    CreativeContract,
    ScriptDocument,
    ScriptSection,
    ShortsCinematicDevotionalDirection,
    ShortsCommentPrompt,
    ShortsCreativeDirection,
    ShortsEngagementPlan,
    ShortsNarrativeSpine,
    ShortsReplayBridge,
    ShortsRetentionBeat,
    ShortsRetentionStory,
    ShortsScriptPackage,
    ShortsScriptStrategy,
    ShortsVerseOpening,
    SourceEntry,
    SourceLibrary,
    StageStatus,
)
from pipeline_videos.project import create_project
from pipeline_videos.scenes import build_scenes
from pipeline_videos.shorts_script import (
    LEGACY_SCRIPTURE_BOUNDARY_PHRASE,
    ShortsScriptPackageValidationError,
    check_reviewed_shorts_script_staging,
    check_shorts_script_package_staging,
    import_shorts_script_package,
    prepare_shorts_biblical_review_queue,
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
    manifest.config.shorts_script.narrative_standard = "human_hook_to_bible"
    manifest.config.shorts_script.require_independent_biblical_review = False
    manifest.config.shorts_script.min_words = 60
    manifest.config.shorts_script.max_words = 140
    manifest.config.shorts_script.minimum_retention_beats = 3
    manifest.config.shorts_script.require_comment_prompt = True
    manifest.config.shorts_script.require_subscribe_cta = True
    manifest.config.editorial_policy.require_quoted_comment_response = True
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


def _retention_project(tmp_path):
    layout = create_project(
        "salmos-56-retencao",
        "Davi não esperou o medo desaparecer",
        projects_dir=tmp_path,
        target_duration_seconds=50,
        video_format="short",
        project_date=date(2026, 7, 16),
    )
    manifest = layout.load_manifest()
    manifest.config.shorts_script.narrative_standard = "retention_story_v2"
    manifest.config.shorts_script.require_independent_biblical_review = False
    layout.save_manifest(manifest)
    write_json(
        layout.briefing,
        Briefing(
            title="Davi não esperou o medo desaparecer",
            main_passage="Salmos 56:3",
            central_theme="Confiar em Deus enquanto o medo ainda está presente.",
            practical_application="Dizer a Deus o que assusta e dar o próximo passo responsável.",
            audience="Pessoas paralisadas pelo medo.",
            emotional_intent="Criar tensão, clareza e coragem responsável.",
            bible_translation="BPM",
            target_duration_seconds=50,
            retention_archetype="counterintuitive_reveal",
            human_conflict="Davi sente medo diante de um perigo real.",
            central_question="Sentir medo significava perder a fé?",
            visual_progression=[
                "Davi ouve passos no escuro.",
                "O perigo se aproxima.",
                "Davi escolhe o próximo passo.",
            ],
            possible_reveal="Davi diz quando tiver medo, não se tiver medo.",
            title_seed="Davi não esperou o medo desaparecer",
        ),
    )
    write_json(
        layout.sources,
        SourceLibrary(
            entries=[
                SourceEntry(
                    source_id="bpm-salmos-56-3",
                    kind="biblical_text",
                    title="Salmos 56:3 — BPM",
                    passage="Salmos 56:1-4",
                    translation="BPM",
                    author_or_publisher="eBible.org",
                    url="https://example.invalid/salmos-56",
                    accessed_at="2026-07-16",
                    excerpt="Quando eu tiver medo, colocarei a minha confiança em ti.",
                    use_in_video="Citação literal obrigatória.",
                    claim_scope="Somente Salmos 56:3.",
                    rights_note="Domínio público; fixture de teste.",
                    verified=True,
                )
            ]
        ),
    )
    return layout


def _retention_package(queue: dict) -> ShortsScriptPackage:
    verse = "Quando eu tiver medo, colocarei a minha confiança em ti."
    lead_in = "Em Salmos, capítulo 56, versículo 3, lemos:"
    hook = "Davi ouviu passos e congelou."
    central_question = "Sentir medo significava perder a fé?"
    payoff = "Coragem bíblica não esconde o medo."
    practical_response = (
        "Hoje, diga a Deus exatamente o que assusta você, escolha a próxima atitude "
        "responsável e faça apenas esse passo."
    )
    cta = "Inscreva-se: os passos do medo não decidem o seu caminho."
    sections = [
        ScriptSection(
            section_id="section-001",
            role="opening",
            title="Passos no escuro",
            narration=hook,
            biblical_basis=["Salmos 56:3"],
            claim_classification=["creative_elaboration"],
            retention_goal="Abrir dentro da ação e tornar o perigo imediatamente legível.",
            visual_direction="Davi para no escuro ao ouvir passos próximos atrás dele.",
        ),
        ScriptSection(
            section_id="section-002",
            role="biblical_narrative",
            title="O medo real",
            narration=(f"O perigo era real, e ele não escondeu o que sentia. {central_question}"),
            biblical_basis=["Salmos 56:1-4"],
            claim_classification=["interpretation"],
            retention_goal="Aumentar a tensão sem fingir que fé elimina emoções.",
            visual_direction="Davi encosta numa parede enquanto uma sombra se aproxima.",
        ),
        ScriptSection(
            section_id="section-003",
            role="biblical_narrative",
            title="A evidência bíblica",
            narration=f"{lead_in} “{verse}”",
            biblical_basis=["Salmos 56:3"],
            claim_classification=["biblical_text"],
            retention_goal="Usar a citação para mudar a leitura do medo.",
            visual_direction="Davi mantém os olhos abertos enquanto decide em quem confiar.",
        ),
        ScriptSection(
            section_id="section-004",
            role="reflection",
            title="A primeira virada",
            narration=(
                "Repara na primeira palavra: quando. "
                "Davi não esperou uma vida sem medo para confiar."
            ),
            biblical_basis=["Salmos 56:3"],
            claim_classification=["interpretation"],
            retention_goal="Revelar o detalhe que contraria a expectativa comum.",
            visual_direction="Davi abre a mão que estava fechada e volta a respirar.",
        ),
        ScriptSection(
            section_id="section-005",
            role="reflection",
            title="O reengajamento",
            narration=(
                "Só que confiar não apagou o perigo. "
                "Mudou quem decidia o próximo passo: o medo ou Deus."
            ),
            biblical_basis=["Salmos 56:3"],
            claim_classification=["interpretation"],
            retention_goal="Trocar alívio instantâneo por uma decisão concreta.",
            visual_direction="Davi vê a saída e move um pé apesar da sombra próxima.",
        ),
        ScriptSection(
            section_id="section-006",
            role="application",
            title="O payoff",
            narration=f"Esse é o ponto: {payoff} {practical_response}",
            biblical_basis=["Salmos 56:3"],
            claim_classification=["interpretation"],
            retention_goal="Resolver a pergunta e entregar uma ação praticável.",
            visual_direction="Uma pessoa anota uma atitude e se levanta para executá-la.",
        ),
        ScriptSection(
            section_id="section-007",
            role="closing",
            title="Callback",
            narration=cta,
            biblical_basis=["Salmos 56:3"],
            claim_classification=["interpretation"],
            retention_goal="Encerrar no callback sem despedida.",
            visual_direction="Davi avança enquanto os mesmos passos ficam para trás.",
        ),
    ]
    strategy = ShortsScriptStrategy(
        target_viewer="Pessoas paralisadas por medo real.",
        viewer_need="Entender que confiança não exige negar o medo.",
        core_value=payoff,
        content_promise=hook,
        hook=hook,
        hook_type="surprising_detail",
        hook_techniques=["specificity", "open_loop"],
        hook_target_end_seconds=1.9,
        verse_opening=ShortsVerseOpening(
            source_id="bpm-salmos-56-3",
            reference="Salmos 56:3",
            translation="BPM",
            quote_mode="literal",
            lead_in=lead_in,
            verse_text=verse,
            target_start_seconds=8.0,
            target_end_seconds=16.0,
        ),
        retention_story=ShortsRetentionStory(
            archetype="counterintuitive_reveal",
            opening_section_id="section-001",
            scripture_section_id="section-003",
            progression_section_ids=[
                "section-002",
                "section-003",
                "section-004",
                "section-005",
            ],
            reengagement_section_id="section-005",
            payoff_section_id="section-006",
            cta_section_id="section-007",
            opening_promise=hook,
            central_question=central_question,
            progression=[
                "O perigo era real",
                verse,
                "Davi não esperou uma vida sem medo para confiar.",
                "Confiar não apagou o perigo.",
            ],
            reengagement="Mudou quem decidia o próximo passo: o medo ou Deus.",
            scripture_anchor=verse,
            payoff=payoff,
            practical_response=practical_response,
            engagement=ShortsEngagementPlan(
                mode="subscribe",
                spoken_text=cta,
                objective="Convidar a acompanhar novas mensagens com o mesmo rigor.",
                fit_reason="O callback transforma os passos ameaçadores em caminho escolhido.",
                target_start_seconds=46.0,
                target_end_seconds=50.0,
            ),
            replay_bridge=ShortsReplayBridge(
                mode="verbal_and_visual",
                opening_element="passos",
                closing_callback="os passos do medo não decidem o seu caminho",
                added_meaning="Os passos deixam de representar ameaça e passam a representar ação.",
            ),
            source_ids=["bpm-salmos-56-3"],
        ),
        open_loop=central_question,
        payoff=payoff,
        practical_value=practical_response,
        cta=cta,
        reason_to_subscribe="",
        retention_beats=[
            ShortsRetentionBeat(
                section_id="section-001",
                purpose="hook",
                viewer_question="Quem está se aproximando?",
                viewer_value="Entrar diretamente no perigo percebido por Davi.",
                target_start_seconds=0,
                target_end_seconds=2,
                opens_loop=central_question,
                new_information="Davi congela ao ouvir passos.",
                visual_change="O movimento para abruptamente no escuro.",
            ),
            ShortsRetentionBeat(
                section_id="section-002",
                purpose="open_loop",
                viewer_question=central_question,
                viewer_value="Reconhecer que o medo descrito é real.",
                target_start_seconds=2,
                target_end_seconds=8,
                new_information="O perigo não é imaginário.",
                visual_change="A sombra se aproxima da parede.",
            ),
            ShortsRetentionBeat(
                section_id="section-003",
                purpose="progression",
                viewer_question="O que Davi faz com esse medo?",
                viewer_value="Ouvir a resposta literal do salmo.",
                target_start_seconds=8,
                target_end_seconds=16,
                new_information="Davi escolhe confiar quando sente medo.",
                visual_change="A mão de Davi deixa de procurar uma fuga.",
            ),
            ShortsRetentionBeat(
                section_id="section-004",
                purpose="progression",
                viewer_question="Qual detalhe muda a leitura?",
                viewer_value="Perceber a força da palavra quando.",
                target_start_seconds=16,
                target_end_seconds=23,
                new_information="A confiança começa antes do medo desaparecer.",
                visual_change="A mão fechada se abre.",
            ),
            ShortsRetentionBeat(
                section_id="section-005",
                purpose="reveal",
                viewer_question="Então o perigo desapareceu?",
                viewer_value="Distinguir confiança de alívio instantâneo.",
                target_start_seconds=23,
                target_end_seconds=32,
                pattern_interrupt="O perigo permanece visível depois da citação.",
                new_information="Confiar muda quem decide, não apaga o perigo.",
                visual_change="Davi move um pé apesar da sombra.",
            ),
            ShortsRetentionBeat(
                section_id="section-006",
                purpose="payoff",
                viewer_question="Como agir hoje?",
                viewer_value="Receber uma resposta prática e limitada.",
                target_start_seconds=32,
                target_end_seconds=46,
                resolves_loop=central_question,
                new_information="Coragem permite agir sem esconder o medo.",
                visual_change="Uma atitude concreta é anotada e iniciada.",
            ),
            ShortsRetentionBeat(
                section_id="section-007",
                purpose="cta",
                viewer_question="Como continuar lembrando disso?",
                viewer_value="Acompanhar novas mensagens bíblicas.",
                target_start_seconds=46,
                target_end_seconds=50,
                new_information="Os passos agora representam o caminho escolhido.",
                visual_change="Davi avança e deixa os passos ameaçadores para trás.",
            ),
        ],
        source_ids=["bpm-salmos-56-3"],
        section_ids=[section.section_id for section in sections],
        biblical_safeguards=["Não tratar medo como pecado automático."],
        compliance_checks=["Citação literal entre 8 e 20 segundos", "CTA de uma ação"],
    )
    script = ScriptDocument(
        version=queue["next_script_version"],
        title="Davi não esperou o medo desaparecer",
        sections=sections,
        source_ids=["bpm-salmos-56-3"],
    )
    return ShortsScriptPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        script=script,
        biblical_review=BiblicalReview(
            script_version=script.version,
            reviewed_at=utc_now(),
            reviewer="shorts_script_specialist",
            summary="Citação literal conferida e aplicação distinguida do texto bíblico.",
        ),
        strategy=strategy,
    )


def _creative_project(tmp_path):
    layout = _retention_project(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.shorts_script.narrative_standard = "creative_retention_v3"
    layout.save_manifest(manifest)
    briefing = read_model(layout.briefing, Briefing)
    write_json(
        layout.briefing,
        briefing.model_copy(
            update={
                "creative_mode": "biblical_curiosity",
                "interpretive_stance": "expository",
                "hook_policy": "required",
                "hook_family": "nobody_talks_but",
                "hook_contract": "user_six_v1",
                "viewer_outcome": "O detalhe gramatical muda a leitura do medo.",
                "practical_application": "",
            }
        ),
    )
    return layout


def _creative_package(queue: dict) -> ShortsScriptPackage:
    legacy = _retention_package(queue)
    strategy = legacy.strategy
    assert strategy.retention_story is not None
    hook = "Ninguém fala sobre esta palavra, mas ela muda toda a cena."
    content_promise = "A primeira palavra muda a leitura do medo neste salmo."
    resolution = "A palavra quando reconhece o medo antes de apresentar a confiança."
    viewer_takeaway = "O detalhe gramatical muda a leitura do medo."
    sections = [section.model_copy(deep=True) for section in legacy.script.sections]
    sections[0] = sections[0].model_copy(update={"narration": f"{hook} {content_promise}"})
    sections[5] = sections[5].model_copy(
        update={
            "role": "reflection",
            "title": "A conclusão do detalhe",
            "narration": f"{resolution} {viewer_takeaway}",
            "retention_goal": "Concluir a curiosidade sem transformar o vídeo em conselho.",
            "visual_direction": (
                "A palavra quando permanece em destaque enquanto Davi encara a sombra."
            ),
        }
    )
    purposes = ["hook", "orientation", "evidence", "analysis", "reveal", "resolution", "cta"]
    beats = [
        beat.model_copy(
            update={
                "purpose": purpose,
                "new_information": (
                    f"Movimento {index} acrescenta uma evidência distinta sobre a palavra quando."
                ),
            }
        )
        for index, (beat, purpose) in enumerate(
            zip(strategy.retention_beats, purposes, strict=True),
            start=1,
        )
    ]
    direction = ShortsCreativeDirection(
        creative_mode="biblical_curiosity",
        interpretive_stance="expository",
        hook_family="nobody_talks_but",
        opening_section_id="section-001",
        scripture_section_ids=["section-003"],
        development_section_ids=[
            "section-002",
            "section-003",
            "section-004",
            "section-005",
        ],
        reengagement_section_ids=["section-005"],
        resolution_section_id="section-006",
        cta_section_id="section-007",
        content_promise=content_promise,
        organizing_question="Sentir medo significava perder a fé?",
        resolution=resolution,
        viewer_takeaway=viewer_takeaway,
        engagement=strategy.retention_story.engagement,
        source_ids=["bpm-salmos-56-3"],
    )
    creative_strategy = strategy.model_copy(
        update={
            "content_promise": content_promise,
            "hook": hook,
            "hook_family": "nobody_talks_but",
            "creative_direction": direction,
            "retention_story": None,
            "open_loop": direction.organizing_question,
            "payoff": resolution,
            "practical_value": viewer_takeaway,
            "retention_beats": beats,
        }
    )
    script = legacy.script.model_copy(update={"sections": sections})
    return legacy.model_copy(update={"script": script, "strategy": creative_strategy})


def _micro_creative_project(tmp_path):
    layout = create_project(
        "momento-com-jesus",
        "Jesus conhece o seu cansaço",
        projects_dir=tmp_path,
        target_duration_seconds=15,
        video_format="short",
        project_date=date(2026, 7, 20),
    )
    manifest = layout.load_manifest()
    manifest.config.shorts_script.require_independent_biblical_review = False
    layout.save_manifest(manifest)
    write_json(
        layout.briefing,
        Briefing(
            title="Jesus conhece o seu cansaço",
            main_passage="Mateus 11:28",
            central_theme="O convite de Jesus aos cansados.",
            practical_application="Receber o convite de descanso em Jesus.",
            audience="pessoas cansadas e sobrecarregadas",
            emotional_intent="acolher sem explicar excessivamente",
            narration_tone="íntimo, emocional e cinematográfico",
            target_duration_seconds=15,
            bible_translation="BPM",
            creative_mode="pastoral_encouragement",
            interpretive_stance="devotional",
            hook_policy="required",
            hook_family="emotional_interruption",
            hook_contract="cinematic_interruption_v1",
            viewer_outcome="Sentir o convite direto de Jesus ao descanso.",
        ),
    )
    write_json(
        layout.sources,
        SourceLibrary(
            entries=[
                SourceEntry(
                    source_id="bpm-mateus-11-28",
                    kind="biblical_text",
                    title="Mateus 11:28 — BPM",
                    passage="Mateus 11:28-30",
                    translation="BPM",
                    author_or_publisher="eBible.org",
                    url="https://example.invalid/mateus-11",
                    accessed_at="2026-07-20",
                    excerpt=(
                        "Venham a mim, todos vocês que estão cansados e sobrecarregados, "
                        "e eu lhes darei descanso."
                    ),
                    use_in_video="Fala direta de Jesus.",
                    claim_scope="Citação literal.",
                    rights_note="Domínio público.",
                    verified=True,
                    spoken_by="jesus",
                )
            ]
        ),
    )
    return layout


def _micro_creative_package(queue: dict) -> ShortsScriptPackage:
    hook = "Eu sei que você está cansado."
    verse = (
        "Venham a mim, todos vocês que estão cansados e sobrecarregados, e eu lhes darei descanso."
    )
    spoken_reference = "Mateus, capítulo 11, versículo 28"
    promise = "Jesus interrompe o cansaço com um convite de descanso."
    takeaway = "O descanso é oferecido pela própria voz de Jesus."
    sections = [
        ScriptSection(
            section_id="section-001",
            role="opening",
            title="Interrupção",
            narration=hook,
            biblical_basis=["Mateus 11:28"],
            claim_classification=["creative_elaboration"],
            retention_goal="Interromper o pensamento de desistência.",
            visual_direction="Jesus interrompe o passo e olha diretamente para o espectador.",
        ),
        ScriptSection(
            section_id="section-002",
            role="biblical_narrative",
            title="Fala de Jesus",
            narration=f"“{verse}”",
            biblical_basis=["Mateus 11:28"],
            claim_classification=["biblical_text"],
            retention_goal="Ouvir a fala direta de Jesus.",
            visual_direction="Jesus estende a mão em uma paisagem épica iluminada pelo amanhecer.",
        ),
        ScriptSection(
            section_id="section-003",
            role="closing",
            title="Referência",
            narration=f"{spoken_reference}.",
            biblical_basis=["Mateus 11:28"],
            claim_classification=["biblical_text"],
            retention_goal="Selar a origem bíblica da fala.",
            visual_direction="O olhar de Jesus permanece firme enquanto a luz envolve a cena.",
        ),
    ]
    direction = ShortsCinematicDevotionalDirection(
        creative_mode="pastoral_encouragement",
        opening_section_id="section-001",
        scripture_section_id="section-002",
        reference_section_id="section-003",
        emotional_hook=hook,
        content_promise=promise,
        viewer_takeaway=takeaway,
        spoken_reference=spoken_reference,
        source_id="bpm-mateus-11-28",
        visual_emotional_arc=[
            "Jesus interrompe o movimento.",
            "Jesus oferece descanso diretamente.",
            "A presença permanece depois da referência.",
        ],
    )
    beats = [
        ShortsRetentionBeat(
            section_id="section-001",
            purpose="hook",
            viewer_question="Jesus percebeu o meu cansaço?",
            viewer_value="Sentir uma interrupção direta e acolhedora.",
            target_start_seconds=0,
            target_end_seconds=2,
            opens_loop=promise,
            new_information="Jesus interrompe o pensamento do espectador.",
            visual_change="Jesus para e encontra o olhar do espectador.",
        ),
        ShortsRetentionBeat(
            section_id="section-002",
            purpose="evidence",
            viewer_question="O que Jesus diz aos cansados?",
            viewer_value="Ouvir a fala literal de Jesus.",
            target_start_seconds=2,
            target_end_seconds=12.3,
            resolves_loop=promise,
            new_information="Jesus convida os sobrecarregados ao descanso.",
            visual_change="A mão de Jesus se estende em silêncio.",
        ),
        ShortsRetentionBeat(
            section_id="section-003",
            purpose="reference",
            viewer_question="Onde essa fala está registrada?",
            viewer_value="Receber a referência depois da citação.",
            target_start_seconds=12.3,
            target_end_seconds=15,
            new_information="A referência confirma a origem bíblica da fala.",
            visual_change="A cena sustenta o olhar de Jesus até o fim.",
        ),
    ]
    strategy = ShortsScriptStrategy(
        target_viewer="pessoas cansadas e sobrecarregadas",
        viewer_need="sentir-se vistas por Jesus",
        core_value="acolhimento bíblico direto",
        content_promise=promise,
        hook=hook,
        hook_type="emotional_interruption",
        hook_family="emotional_interruption",
        hook_techniques=["pattern_interrupt", "emotional_mirroring"],
        hook_target_end_seconds=2,
        verse_opening=ShortsVerseOpening(
            source_id="bpm-mateus-11-28",
            reference="Mateus 11:28",
            translation="BPM",
            lead_in="",
            verse_text=verse,
            target_start_seconds=2,
            target_end_seconds=12.3,
        ),
        cinematic_devotional=direction,
        open_loop=promise,
        payoff=verse,
        practical_value=takeaway,
        cta="",
        reason_to_subscribe="",
        retention_beats=beats,
        source_ids=["bpm-mateus-11-28"],
        section_ids=[section.section_id for section in sections],
        biblical_safeguards=["Usar somente a fala direta verificada de Jesus."],
        compliance_checks=["Sem CTA e sem lemos."],
    )
    return ShortsScriptPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        script=ScriptDocument(
            title="Jesus conhece o seu cansaço",
            sections=sections,
            source_ids=["bpm-mateus-11-28"],
        ),
        biblical_review=BiblicalReview(
            script_version=1,
            reviewed_at=utc_now(),
            reviewer="fixture-reviewer",
            summary="Fala direta de Jesus conferida em Mateus 11:28.",
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
    assert queue["preflight"] == {
        "read_only": True,
        "command": (
            'uv run pipeline-videos script shorts-check-staging "14-07-2026_davi-golias-short"'
        ),
        "success_condition": "valid=true",
        "returns_all_detectable_errors": True,
    }
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
    assert any("biblical_basis" in instruction for instruction in queue["instructions"])
    assert any("preflight.command" in instruction for instruction in queue["instructions"])

    write_json(layout.shorts_script_package_staging, _package(queue))
    imported = import_shorts_script_package(layout)
    repeated = import_shorts_script_package(layout)

    assert repeated == imported
    assert imported.script.word_count >= 60
    assert read_model(layout.script, ScriptDocument).version == 1
    assert layout.shorts_script_strategy.is_file()
    contract = read_model(layout.creative_contract, CreativeContract)
    assert contract.content_promise == imported.strategy.content_promise
    assert contract.opening_hook == imported.strategy.hook
    assert contract.payoff == imported.strategy.payoff
    assert contract.pacing.profile_id == "short_v1"
    manifest = layout.load_manifest()
    assert manifest.stages["shorts_script"].status == StageStatus.COMPLETED
    assert manifest.stages["shorts_script"].usage["context_bytes"] > 0
    assert manifest.stages["shorts_script"].usage["schema_bytes"] > 0
    assert manifest.stages["script"].status == StageStatus.COMPLETED
    assert manifest.stages["biblical_review"].status == StageStatus.COMPLETED
    assert manifest.approvals["script"].status.value == "pending"


def test_retention_story_v2_imports_and_builds_eight_to_ten_scenes(tmp_path) -> None:
    layout = _retention_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)

    assert queue["retention_contract"] == {
        "standard": "retention_story_v2",
        "selected_archetype": "counterintuitive_reveal",
        "allowed_archetypes": [
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ],
        "timeline_seconds": {
            "cold_open": [0, 2],
            "conflict_or_action": [2, 8],
            "scripture": [8, 20],
            "progression_and_reengagement": [20, 32],
            "payoff_and_application": [32, 45],
            "single_action_cta": [45, 55],
        },
        "cta_max_words": 12,
        "cta_max_seconds": 4.0,
        "target_scene_count": [8, 10],
    }
    assert any("retention_story_v2" in item for item in queue["instructions"])
    assert any("exatamente uma ação" in item for item in queue["instructions"])
    assert any("oito a dez cenas" in item for item in queue["instructions"])

    write_json(layout.shorts_script_package_staging, _retention_package(queue))
    imported = import_shorts_script_package(layout)
    scenes = build_scenes(layout, skip_approvals=True)

    assert imported.strategy.retention_story is not None
    assert imported.strategy.narrative_spine is None
    assert imported.strategy.verse_opening.target_start_seconds == 8.0
    assert imported.strategy.retention_story.engagement.mode == "subscribe"
    assert 8 <= len(scenes.scenes) <= 10
    contract = read_model(layout.creative_contract, CreativeContract)
    assert contract.pacing.profile_id == "short_retention_v2"
    assert sum("reengagement" in beat.functions for beat in contract.beats) == 1


def test_retention_story_v2_rejects_stacked_cta(tmp_path) -> None:
    layout = _retention_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    payload = _retention_package(queue).model_dump(mode="json")
    stacked = "Inscreva-se e compartilhe: os passos do medo não decidem seu caminho."
    payload["strategy"]["retention_story"]["engagement"]["spoken_text"] = stacked
    payload["script"]["sections"][-1]["narration"] = stacked
    write_json(layout.shorts_script_package_staging, payload)

    result = check_shorts_script_package_staging(layout)

    assert result.valid is False
    assert any("exatamente a única ação" in error for error in result.errors)


def test_creative_retention_v3_accepts_curiosity_without_practical_application(tmp_path) -> None:
    layout = _creative_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)

    assert queue["schema_version"] == "5.0"
    assert queue["retention_contract"]["standard"] == "creative_retention_v3"
    assert queue["retention_contract"]["creative_mode"] == "biblical_curiosity"
    assert queue["retention_contract"]["mode_guidance"]["id"] == "biblical_curiosity"
    assert queue["retention_contract"]["hook"]["required"] is True
    assert queue["retention_contract"]["hook"]["selected_family"] == "nobody_talks_but"
    assert queue["retention_contract"]["application_required"] is False
    assert queue["scripture_candidates"] == ["bpm-salmos-56-3"]

    write_json(layout.shorts_script_package_staging, _creative_package(queue))
    imported = import_shorts_script_package(layout)
    contract = read_model(layout.creative_contract, CreativeContract)

    assert imported.strategy.creative_direction is not None
    assert imported.strategy.creative_direction.practical_application == ""
    assert imported.strategy.verse_opening is not None
    assert contract.algorithm == "creative-contract-v2"
    assert contract.creative_mode == "biblical_curiosity"
    assert contract.hook_used is True
    assert contract.pacing.profile_id == "short_creative_v3"
    assert all(section.role != "application" for section in imported.script.sections)


def test_fifteen_second_profile_builds_cinematic_jesus_moment_without_cta(tmp_path) -> None:
    layout = _micro_creative_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)

    assert queue["retention_contract"]["duration_profile"] == "adaptive_compact_v1"
    assert queue["retention_contract"]["standard"] == "cinematic_jesus_devotional_v1"
    assert queue["retention_contract"]["timeline_seconds"] == {
        "hook": [0, 2.0],
        "direct_jesus_quote": [2.0, 12.3],
        "spoken_reference": [12.3, 15],
        "cta": None,
    }
    assert queue["retention_contract"]["target_scene_count"] == [3, 4]
    assert queue["retention_contract"]["cta_allowed"] is False
    assert queue["scripture_candidates"] == ["bpm-mateus-11-28"]
    assert queue["spoken_reference_standard"]["placement"] == "after_closing_quote"
    assert any("filme épico sobre Jesus" in instruction for instruction in queue["instructions"])
    assert any("Não crie CTA" in instruction for instruction in queue["instructions"])

    package = _micro_creative_package(queue)
    write_json(layout.shorts_script_package_staging, package)
    check = check_shorts_script_package_staging(layout)

    assert check.valid is True, check.errors
    imported = import_shorts_script_package(layout)
    scenes = build_scenes(layout, skip_approvals=True)
    contract = read_model(layout.creative_contract, CreativeContract)

    assert 21 <= imported.script.word_count <= 42
    assert imported.script.estimated_duration_seconds <= 15
    assert imported.strategy.cinematic_devotional is not None
    assert imported.strategy.creative_direction is None
    assert imported.strategy.cta == ""
    assert "lemos" not in imported.script.narration_text().casefold()
    assert imported.script.sections[1].narration.startswith("“")
    assert imported.script.sections[1].narration.endswith("”")
    assert imported.script.sections[-1].narration == "Mateus, capítulo 11, versículo 28."
    assert contract.pacing.profile_id == "short_compact_v1"
    assert contract.engagement_action is None
    assert 3 <= len(scenes.scenes) <= 4


def test_compact_profile_rejects_lemos_and_cta(tmp_path) -> None:
    layout = _micro_creative_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _micro_creative_package(queue).model_dump(mode="json")
    package["strategy"]["cta"] = "Inscreva-se."
    package["script"]["sections"][1]["narration"] = (
        "Em Mateus, capítulo 11, versículo 28, lemos: "
        + package["script"]["sections"][1]["narration"]
    )
    write_json(layout.shorts_script_package_staging, package)

    check = check_shorts_script_package_staging(layout)

    assert check.valid is False
    assert any("não permite CTA" in error for error in check.errors)
    assert any("palavra 'lemos'" in error for error in check.errors)


def test_compact_profile_refuses_source_not_marked_as_direct_jesus_speech(tmp_path) -> None:
    layout = _micro_creative_project(tmp_path)
    sources = read_model(layout.sources, SourceLibrary)
    sources.entries[0].spoken_by = "other"
    write_json(layout.sources, sources)

    with pytest.raises(ValueError, match="fala direta de Jesus"):
        prepare_shorts_script_queue(layout, force=True)


def test_new_short_import_waits_for_one_independent_biblical_review(tmp_path) -> None:
    layout = _creative_project(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.shorts_script.require_independent_biblical_review = True
    layout.save_manifest(manifest)
    queue = prepare_shorts_script_queue(layout)
    package = _creative_package(queue)
    write_json(layout.shorts_script_package_staging, package)

    review_queue = prepare_shorts_biblical_review_queue(layout)
    missing = check_reviewed_shorts_script_staging(layout)

    assert missing.valid is False
    assert "Revisão bíblica independente ausente" in missing.errors[0]
    assert review_queue["role"] == "biblical_reviewer"
    assert review_queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["preflight"]["independent_biblical_review_required"] is True

    independent = package.biblical_review.model_copy(
        update={"reviewer": "biblical_reviewer", "summary": "Revisão independente sem bloqueios."}
    )
    write_json(layout.shorts_biblical_review_staging_file, independent)

    reviewed = check_reviewed_shorts_script_staging(layout)
    imported = import_shorts_script_package(layout)

    assert reviewed.valid is True
    assert imported.biblical_review.reviewer == "biblical_reviewer"
    assert read_model(layout.biblical_review, BiblicalReview).reviewer == "biblical_reviewer"


def test_new_short_preflight_rejects_opening_outside_the_six_hook_contract(tmp_path) -> None:
    layout = _creative_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _creative_package(queue)
    invalid_hook = "As moedas voltaram ao templo, mas a decisão veio antes."
    sections = [section.model_copy(deep=True) for section in package.script.sections]
    sections[0] = sections[0].model_copy(
        update={
            "narration": f"{invalid_hook} {package.strategy.content_promise}",
        }
    )
    invalid = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": sections}),
            "strategy": package.strategy.model_copy(update={"hook": invalid_hook}),
        }
    )
    write_json(layout.shorts_script_package_staging, invalid)

    result = check_shorts_script_package_staging(layout)

    assert result.valid is False
    assert "O hook não corresponde à família declarada." in result.errors


def test_shorts_staging_preflight_aggregates_errors_and_is_read_only(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    payload = _package(queue).model_dump(mode="json")
    payload["strategy"]["narrative_spine"]["tension_question"] = "Pergunta ausente."
    payload["strategy"]["narrative_spine"]["bridge_statement"] = "Ponte ausente."
    payload["strategy"]["open_loop"] = "Formulação concorrente."
    payload["script"]["sections"][0]["biblical_basis"] = []
    payload["script"]["sections"][1]["claim_classification"] = []
    write_json(layout.shorts_script_package_staging, payload)
    before = layout.shorts_script_package_staging.read_bytes()

    result = check_shorts_script_package_staging(layout)

    assert result.valid is False
    assert result.normalizations == (
        "strategy.open_loop sincronizado com narrative_spine.tension_question.",
    )
    assert "narrative_spine.tension_question precisa aparecer na seção mapeada." in result.errors
    assert "narrative_spine.bridge_statement precisa aparecer na seção mapeada." in result.errors
    assert "A seção section-001 não registra base bíblica." in result.errors
    assert "A seção section-002 não classifica suas afirmações." in result.errors
    assert layout.shorts_script_package_staging.read_bytes() == before

    with pytest.raises(ShortsScriptPackageValidationError) as exc_info:
        import_shorts_script_package(layout)
    assert set(result.errors).issubset(exc_info.value.errors)


def test_shorts_staging_normalizes_redundant_open_loop_without_writing_staging(
    tmp_path,
) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    payload = _package(queue).model_dump(mode="json")
    payload["strategy"]["open_loop"] = "Uma formulação que seria rejeitada antes."
    write_json(layout.shorts_script_package_staging, payload)
    before = layout.shorts_script_package_staging.read_bytes()

    result = check_shorts_script_package_staging(layout)
    imported = import_shorts_script_package(layout)

    assert result.valid is True
    assert result.normalizations == (
        "strategy.open_loop sincronizado com narrative_spine.tension_question.",
    )
    assert imported.strategy.open_loop == imported.strategy.narrative_spine.tension_question
    assert layout.shorts_script_package_staging.read_bytes() == before


def test_single_agent_script_queue_uses_sol_medium(tmp_path) -> None:
    layout = _short_project(tmp_path)
    project = layout.load_manifest()
    project.config.production.use_subagents = False
    project.config.shorts_script.use_specialist_subagent = False
    project.config.youtube_growth.enabled = False
    project.config.youtube_growth.use_specialist_subagent = False
    layout.save_manifest(project)

    queue = prepare_shorts_script_queue(layout)

    assert queue["orchestration"]["mode"] == "main_agent"
    assert queue["orchestration"]["worker_id"] == "main"
    assert queue["orchestration"]["reasoning_effort"] == "medium"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-sol"
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


def test_shorts_import_rejects_weak_comment_keyword(tmp_path) -> None:
    layout = _short_project(tmp_path)
    queue = prepare_shorts_script_queue(layout)
    package = _package(queue)
    weak_text = 'Escreve "BEM" nos comentários como lembrete desta mensagem.'
    weak_prompt = package.strategy.comment_prompt.model_copy(
        update={
            "prompt_text": weak_text,
            "response_cue": "BEM",
            "fit_reason": "Bem é uma palavra simples ligada ao tema.",
        }
    )
    altered_sections = list(package.script.sections)
    altered_sections[-1] = altered_sections[-1].model_copy(
        update={"narration": f"{weak_text} {package.strategy.cta}"}
    )
    altered_package = package.model_copy(
        update={
            "script": package.script.model_copy(update={"sections": altered_sections}),
            "strategy": package.strategy.model_copy(update={"comment_prompt": weak_prompt}),
        }
    )
    write_json(layout.shorts_script_package_staging, altered_package)

    with pytest.raises(ValueError, match="palavra-síntese expressiva e contextual"):
        import_shorts_script_package(layout)


def test_shorts_queue_requests_contextual_comment_keyword(tmp_path) -> None:
    layout = _short_project(tmp_path)

    queue = prepare_shorts_script_queue(layout)

    assert any("REFÚGIO, PERSISTÊNCIA, CONFIANÇA" in item for item in queue["instructions"])
    assert any("Nunca use como resposta isolada" in item for item in queue["instructions"])


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
