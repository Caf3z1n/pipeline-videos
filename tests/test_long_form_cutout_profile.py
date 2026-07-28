from __future__ import annotations

from datetime import date

import pytest
from PIL import Image

from pipeline_videos.autopilot import prepare_local_coordinator_job
from pipeline_videos.long_form_profile import (
    LongFormCutoutEditPlan,
    build_asset_inventory,
    compile_project_edit_plan,
    official_profile_config,
    preflight_edit_plan_data,
    stage_profile_asset,
    validate_conversational_script,
)
from pipeline_videos.models import (
    AudioManifest,
    Briefing,
    CreativeBeat,
    CreativeContract,
    CreativePacingProfile,
    CutoutAssetCue,
    CutoutFullscreenShot,
    CutoutYouTubePackageSeed,
    LongFormEngagementPlan,
    LongFormRetentionBeat,
    LongFormScriptStrategy,
    LongFormScriptureAnchor,
    NarrationVisualScene,
    ScriptDocument,
    ScriptSection,
    StyleProfile,
    WordTiming,
)
from pipeline_videos.project import create_project
from pipeline_videos.scenes import build_scenes
from pipeline_videos.storage import read_json, read_model, write_json
from pipeline_videos.thumbnail import compose_cutout_thumbnail


def _asset(asset_id: str, *, kind: str = "cutout") -> dict:
    return {
        "asset_id": asset_id,
        "kind": kind,
        "description": f"Asset concreto {asset_id}",
        "generation_prompt": f"Gerar {asset_id} no estilo massinha 3D.",
        "semantic_tags": [f"cue-{asset_id}"],
        "max_uses": 3,
    }


def _text_page(
    block_id: str,
    start: float,
    end: float,
    text: str,
    *,
    quote_open: bool = False,
    quote_close: bool = False,
) -> dict:
    return {
        "page_id": f"page-{block_id}",
        "start": start,
        "end": end,
        "text": text,
        "first_word_index": 0,
        "last_word_index_exclusive": 1,
        "quote_open": quote_open,
        "quote_close": quote_close,
        "emphasis_word_indexes": [],
    }


def _event(
    block_id: str,
    asset_id: str,
    start: float,
    end: float,
    narration: str,
    *,
    fullscreen: bool = False,
) -> dict:
    return {
        "event_id": f"event-{block_id}",
        "asset_id": asset_id,
        "start": start if fullscreen else start + 0.2,
        "end": end,
        "cue_word_index": 0,
        "semantic_key": f"cue-{asset_id}",
        "semantic_basis": f"O asset representa literalmente {narration}.",
        "matched_narration_phrase": narration,
        "zone": (
            {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
            if fullscreen
            else {"x": 0.04, "y": 0.12, "width": 0.38, "height": 0.76}
        ),
    }


def _valid_plan() -> dict:
    blocks: list[dict] = []
    assets: list[dict] = []
    for index in range(10):
        block_id = f"block-{index:02d}"
        asset_id = f"asset-{index:02d}"
        start = index * 3.0
        end = start + 3.0
        narration = f"Ação concreta número {index}"
        if index in {3, 7}:
            block_type = "center_statement_v1"
            block = {
                "block_id": block_id,
                "section_id": "opening",
                "block_type": block_type,
                "role": "reengagement",
                "start": start,
                "end": end,
                "narration": narration,
                "citation_id": None,
                "use_standard_background": True,
                "text_zone": {"x": 0.1, "y": 0.15, "width": 0.8, "height": 0.7},
                "text_pages": [_text_page(block_id, start + 0.1, end, narration)],
                "asset_events": [],
            }
        elif index in {5, 9}:
            block_type = "fullscreen_image_sequence_v1"
            assets.append(_asset(asset_id, kind="fullscreen"))
            block = {
                "block_id": block_id,
                "section_id": "opening",
                "block_type": block_type,
                "role": "explanation",
                "start": start,
                "end": end,
                "narration": narration,
                "citation_id": None,
                "use_standard_background": False,
                "text_zone": None,
                "text_pages": [],
                "asset_events": [
                    _event(
                        block_id,
                        asset_id,
                        start,
                        end,
                        narration,
                        fullscreen=True,
                    )
                ],
            }
        else:
            role = "opening" if index == 0 else "explanation"
            citation_id = None
            quote_open = False
            quote_close = False
            if index == 1:
                role = "biblical_quote"
                citation_id = "genesis-1"
                quote_open = True
                quote_close = True
            assets.append(_asset(asset_id))
            block = {
                "block_id": block_id,
                "section_id": "opening",
                "block_type": "assets_with_full_text_v1",
                "role": role,
                "start": start,
                "end": end,
                "narration": narration,
                "citation_id": citation_id,
                "use_standard_background": True,
                "text_zone": {"x": 0.54, "y": 0.12, "width": 0.4, "height": 0.76},
                "text_pages": [
                    _text_page(
                        block_id,
                        start + 0.1,
                        end,
                        narration,
                        quote_open=quote_open,
                        quote_close=quote_close,
                    )
                ],
                "asset_events": [_event(block_id, asset_id, start, end, narration)],
            }
        blocks.append(block)
    return {
        "schema_version": "1.0",
        "profile_id": "biblical_cutout_explainer_7m_v1",
        "generated_at": "2026-07-28T12:00:00Z",
        "script_hash": "a" * 64,
        "audio_hash": "b" * 64,
        "duration_seconds": 30.0,
        "background_path": "visual/backgrounds/warm-textured-parchment.png",
        "blocks": blocks,
        "assets": assets,
        "sfx_events": [
            {
                "kind": "whoosh",
                "at": 0.0,
                "reason": "opening",
                "block_id": "block-00",
            },
            {
                "kind": "whoosh",
                "at": 3.0,
                "reason": "biblical_citation",
                "block_id": "block-01",
            },
        ],
    }


def test_new_cutout_project_persists_the_official_opt_in_profile(tmp_path) -> None:
    layout = create_project(
        "cutout-oficial",
        "Perfil cutout oficial",
        projects_dir=tmp_path,
        target_duration_seconds=420,
        long_form_profile="biblical_cutout_explainer_7m_v1",
        project_date=date(2026, 7, 28),
    )

    manifest = layout.load_manifest()
    style = read_model(layout.style_profile, StyleProfile)

    assert manifest.config.long_form_profile.enabled is True
    assert manifest.config.long_form_profile.profile_id == "biblical_cutout_explainer_7m_v1"
    assert manifest.config.long_form_profile.planner_model_passes == 0
    assert manifest.config.long_form_profile.aggregate_model_repairs == 0
    assert (
        manifest.config.long_form_profile.planning_standard
        == "deterministic_script_blueprint_compiler_v2"
    )
    assert manifest.config.production.image_workers == 2
    assert manifest.config.images.visual_standard_id == "biblical-cutout-clay-v1"
    assert manifest.config.images.scene_planning_standard == "script_visual_blueprint_compiler_v2"
    assert manifest.config.images.composition_safety_mode == "reserved_non_overlapping_zones_v1"
    assert manifest.config.audio.voice_id == "36rVQA1AOIPwpA3Hg1tC"
    assert manifest.config.audio.model_id == "eleven_v3"
    assert manifest.config.audio.prompting_profile == "eleven_v3_strategic_tags_v1"
    assert manifest.config.audio.stability == 0.5
    assert (
        manifest.config.editorial_policy.authorial_narrator == "biblical_explainer_third_person_v1"
    )
    assert "terceira pessoa" in read_model(layout.briefing, Briefing).narration_tone
    assert style.visual_standard_id == "biblical-cutout-clay-v1"
    assert (layout.root / "visual/fonts/files/Knewave-Regular.ttf").is_file()
    assert (layout.root / "visual/backgrounds/warm-textured-parchment.png").is_file()
    assert (layout.root / "audio/sfx/whoosh-suave.wav").is_file()
    assert (layout.root / "audio/sfx/double-click.wav").is_file()

    coordinator = prepare_local_coordinator_job(layout)
    assert coordinator["external_roles"]["image_workers"]["jobs_directory"] == "visual/assets/jobs"
    assert coordinator["external_roles"]["image_workers"]["maximum_parallel"] == 2
    assert "long_form_visual_editor" not in coordinator["external_roles"]
    assert "youtube_growth_specialist" not in coordinator["external_roles"]


def test_valid_plan_passes_one_aggregate_preflight_and_builds_inventory() -> None:
    raw = _valid_plan()
    report = preflight_edit_plan_data(raw)

    assert report.valid is True
    assert report.diagnostics == []
    assert report.metrics["first_30_block_count"] == 10
    assert report.metrics["first_30_visual_event_count"] >= 12

    plan = LongFormCutoutEditPlan.model_validate(raw)
    inventory = build_asset_inventory(plan, report=report)
    assert inventory["generation_count"] == 8
    assert len(inventory["items"]) == 8


def test_preflight_blocks_early_duplicate_quote_and_text_over_asset() -> None:
    raw = _valid_plan()
    quoted = raw["blocks"][1]
    quoted["text_pages"][0]["text"] = f"“{quoted['narration']}”"
    quoted["text_zone"] = {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.6}

    report = preflight_edit_plan_data(raw)
    codes = {item.code for item in report.diagnostics}

    assert report.valid is False
    assert "schema_error" in codes

    raw = _valid_plan()
    raw["blocks"][0]["text_zone"] = {
        "x": 0.2,
        "y": 0.2,
        "width": 0.5,
        "height": 0.6,
    }
    report = preflight_edit_plan_data(raw)
    codes = {item.code for item in report.diagnostics}
    assert "text_asset_overlap" in codes


def test_preflight_blocks_flashing_or_semantically_unrelated_assets() -> None:
    raw = _valid_plan()
    event = raw["blocks"][0]["asset_events"][0]
    event["end"] = event["start"] + 0.4
    event["matched_narration_phrase"] = "frase inexistente"

    report = preflight_edit_plan_data(raw)
    codes = {item.code for item in report.diagnostics}

    assert "asset_flash" in codes
    assert "asset_without_literal_cue" in codes


def test_conversational_script_rejects_abstract_generic_language() -> None:
    document = ScriptDocument(
        title="Teste",
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="Abertura",
                narration="A ordem foi deslocada; a consequência foi contradita.",
            )
        ],
    )

    with pytest.raises(ValueError, match="conversational_concrete_ptbr_v1"):
        validate_conversational_script(document)


def test_official_profile_has_exactly_three_block_types() -> None:
    profile = official_profile_config()
    assert profile.allowed_block_types == [
        "assets_with_full_text_v1",
        "center_statement_v1",
        "fullscreen_image_sequence_v1",
    ]
    assert profile.collision_policy == "reserved_non_overlapping_zones_v1"
    assert profile.asset_selection_policy == "semantic_blueprint_dedup_v2"
    assert profile.validation_mode == "compiled_invariants_once_v2"


def test_cutout_staging_removes_chroma_and_trims_unused_area(tmp_path) -> None:
    layout = create_project(
        "cutout-stage",
        "Trim de cutout",
        projects_dir=tmp_path,
        target_duration_seconds=420,
        long_form_profile="biblical_cutout_explainer_7m_v1",
        project_date=date(2026, 7, 28),
    )
    source = tmp_path / "generated.png"
    generated = Image.new("RGB", (200, 160), (0, 255, 0))
    for x in range(70, 130):
        for y in range(45, 125):
            generated.putpixel((x, y), (210, 55, 35))
    generated.save(source)
    write_json(
        layout.long_form_asset_queue,
        {
            "items": [
                {
                    "job_id": "cutout-character",
                    "kind": "cutout",
                    "staging_path": "visual/assets/staging/character.png",
                }
            ]
        },
    )

    destination = stage_profile_asset(
        layout,
        job_id="cutout-character",
        source=source,
    )

    with Image.open(destination) as staged:
        assert staged.mode == "RGBA"
        assert staged.width < generated.width
        assert staged.height < generated.height
        assert staged.getpixel((0, 0))[3] == 0


def test_cutout_builds_audio_markers_without_requiring_a_legacy_visual_plan(
    tmp_path,
    monkeypatch,
) -> None:
    layout = create_project(
        "cutout-audio-markers",
        "Marcadores de áudio",
        projects_dir=tmp_path,
        target_duration_seconds=420,
        long_form_profile="biblical_cutout_explainer_7m_v1",
        project_date=date(2026, 7, 28),
    )
    document = ScriptDocument(
        title="Marcadores",
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="Abertura",
                narration="Eu quero mostrar uma escolha concreta.",
            ),
            ScriptSection(
                section_id="closing",
                role="closing",
                title="Conclusão",
                narration="Essa escolha deixou uma consequência visível.",
            ),
        ],
    )
    contract = CreativeContract(
        input_hash="contract",
        format="long_form",
        target_viewer="público geral",
        content_promise="explicar uma escolha",
        opening_hook=document.sections[0].narration,
        payoff=document.sections[1].narration,
        practical_application="",
        main_passage="Gênesis 3",
        section_ids=["opening", "closing"],
        beats=[
            CreativeBeat(
                section_id="opening",
                functions=["hook"],
                viewer_value="entender a pergunta",
                target_start_seconds=0,
                target_end_seconds=10,
            ),
            CreativeBeat(
                section_id="closing",
                functions=["payoff"],
                viewer_value="receber a resposta",
                target_start_seconds=10,
                target_end_seconds=20,
            ),
        ],
        critical_component_ids=[],
        pacing=CreativePacingProfile(
            profile_id="long_creative_v2",
            default_scene_seconds=10,
            opening_scene_seconds=8,
            reengagement_scene_seconds=8,
            contemplative_scene_seconds=12,
            payoff_scene_seconds=10,
        ),
    )
    write_json(layout.script, document)
    monkeypatch.setattr(
        "pipeline_videos.scenes.ensure_creative_contract",
        lambda _layout, _script: contract,
    )

    manifest = build_scenes(layout, skip_approvals=True)

    assert [scene.narration for scene in manifest.scenes] == [
        section.narration for section in document.sections
    ]
    assert all(
        scene.visual_brief.startswith("Marcador técnico exclusivo") for scene in manifest.scenes
    )


def _direct_compiler_fixture(tmp_path):
    opening = "As estrelas parecem cair, mas a pergunta começa no modo como Jesus descreveu o céu."
    citation = (
        '"O sol escurecerá, e a lua não dará a sua luz." Em Mateus, capítulo 24, versículo 29.'
    )
    closing = "A imagem aponta para uma virada visível, e essa resposta fecha a pergunta."
    sections = [
        ScriptSection(
            section_id="opening",
            role="opening",
            title="A pergunta",
            narration=opening,
            biblical_basis=["Mateus 24:29"],
            claim_classification=["verified_context"],
        ),
        ScriptSection(
            section_id="citation",
            role="biblical_narrative",
            title="A fala de Jesus",
            narration=citation,
            biblical_basis=["Mateus 24:29"],
            claim_classification=["biblical_text"],
        ),
        ScriptSection(
            section_id="closing",
            role="closing",
            title="A resposta",
            narration=closing,
            biblical_basis=["Mateus 24:29"],
            claim_classification=["verified_context"],
        ),
    ]
    engagement = LongFormEngagementPlan(
        action="comment",
        spoken_text="Compartilhe nos comentários como você entendia essa imagem.",
        payoff_connection="A pergunta retoma a interpretação central.",
    )
    strategy = LongFormScriptStrategy(
        standard="creative_retention_long_v2",
        target_viewer="pessoas curiosas sobre imagens proféticas",
        viewer_need="entender Mateus 24:29 sem leitura automática",
        content_promise="explicar o sentido da imagem usada por Jesus",
        opening_line=opening,
        creative_mode="passage_explainer",
        interpretive_stance="expository",
        hook_policy="none",
        hook_family="none",
        hook_rationale="A ação visual e a pergunta orientam a abertura.",
        progression=[
            LongFormRetentionBeat(
                beat_id="opening",
                section_id="opening",
                functions=["orientation", "evidence"],
                viewer_value="formular a pergunta",
                new_information="A imagem pode ser lida no vocabulário profético.",
                target_start_seconds=0,
                target_end_seconds=6,
                visual_progression="céu em movimento e estrelas em queda",
            ),
            LongFormRetentionBeat(
                beat_id="citation",
                section_id="citation",
                functions=["evidence", "reengagement"],
                viewer_value="ouvir a fala central",
                new_information="O versículo reúne sol, lua e estrelas.",
                target_start_seconds=6,
                target_end_seconds=14,
                visual_progression="sol e lua perdem a luz",
            ),
            LongFormRetentionBeat(
                beat_id="closing",
                section_id="closing",
                functions=["resolution", "cta"],
                viewer_value="receber a resposta",
                new_information="A imagem comunica uma virada reconhecível.",
                target_start_seconds=14,
                target_end_seconds=21,
                visual_progression="duas ações fullscreen concluem a explicação",
            ),
        ],
        scripture_anchors=[
            LongFormScriptureAnchor(
                source_id="mateus-24-29",
                reference="Mateus 24:29",
                section_id="citation",
                target_start_seconds=6,
                narrative_purpose="Apresentar a fala central de Jesus.",
            )
        ],
        resolution="Jesus usou uma imagem de virada reconhecível para seus ouvintes.",
        viewer_takeaway="A linguagem profética precisa ser lida dentro do seu contexto bíblico.",
        engagement=engagement,
        visual_scenes=[
            NarrationVisualScene(
                section_id="opening",
                narration=opening,
                scene_description="Céu escuro com estrelas atravessando o quadro.",
                visual_relation="literal",
                presentation="assets_with_full_text_v1",
                asset_cues=[
                    CutoutAssetCue(
                        semantic_key="falling-stars",
                        cue_phrase="estrelas",
                        description="Grupo de estrelas brilhantes em movimento descendente.",
                    )
                ],
                emphasis_terms=["estrelas", "Jesus"],
            ),
            NarrationVisualScene(
                section_id="citation",
                narration=citation,
                scene_description="Sol escuro e lua sem brilho como símbolos bíblicos.",
                visual_relation="literal",
                presentation="assets_with_full_text_v1",
                asset_cues=[
                    CutoutAssetCue(
                        semantic_key="dark-sun",
                        cue_phrase="sol",
                        description="Sol escurecido em estilo bíblico educacional.",
                    ),
                    CutoutAssetCue(
                        semantic_key="dim-moon",
                        cue_phrase="lua",
                        description="Lua fosca sem emitir luz.",
                    ),
                ],
                emphasis_terms=["sol", "lua"],
            ),
            NarrationVisualScene(
                section_id="closing",
                narration=closing,
                scene_description="Sequência visual da virada descrita por Jesus.",
                visual_relation="contextual",
                presentation="fullscreen_image_sequence_v1",
                fullscreen_shots=[
                    CutoutFullscreenShot(
                        cue_phrase="virada",
                        description="Estrelas cruzam o céu acima da terra.",
                    ),
                    CutoutFullscreenShot(
                        cue_phrase="resposta",
                        description="Uma figueira é sacudida por uma forte ventania.",
                    ),
                ],
            ),
        ],
        youtube_package=CutoutYouTubePackageSeed(
            title="A Bíblia diz que estrelas cairão? | Mateus 24:29",
            description=(
                "Mateus 24:29 reúne imagens do sol, da lua e das estrelas para anunciar uma "
                "virada. A explicação acompanha o contexto bíblico e a fala de Jesus.\n\n"
                "00:00 - Abertura\n00:06 - Mateus 24:29"
            ),
            primary_keyword="Mateus 24:29 explicado",
            secondary_keywords=["estrelas cairão do céu", "profecia de Jesus"],
            topics=[
                "Mateus 24:29",
                "Bíblia",
                "Jesus",
                "estrelas cairão do céu",
                "profecia bíblica",
                "fim dos tempos",
                "Evangelho de Mateus",
                "linguagem profética",
            ],
            pinned_comment="Como você entendia a imagem das estrelas antes desta explicação?",
            thumbnail_text_lines=["ESTRELAS", "VÃO CAIR?"],
            thumbnail_emphasis_line=1,
            thumbnail_visual_description=(
                "Jesus olha para um céu com estrelas em queda; composição bíblica à direita."
            ),
        ),
        source_ids=["mateus-24-29"],
    )
    document = ScriptDocument(
        title="Mateus 24:29 explicado",
        sections=sections,
        source_ids=["mateus-24-29"],
        long_form_strategy=strategy,
    )
    words = []
    cursor = 0.0
    for index, token in enumerate(document.narration_text().split()):
        words.append(
            WordTiming(
                index=index,
                word=token,
                start=cursor,
                end=cursor + 0.38,
                scene_id="audio",
            )
        )
        cursor += 0.42
    audio = AudioManifest(
        generated_at="2026-07-28T12:00:00Z",
        provider="fixture",
        sample_rate=44100,
        narration_path="audio/narration.wav",
        duration=cursor,
        segments=[],
        words=words,
    )
    layout = create_project(
        "direct-compiler",
        "Compilador direto",
        projects_dir=tmp_path,
        target_duration_seconds=180,
        long_form_profile="biblical_cutout_explainer_7m_v1",
        project_date=date(2026, 7, 28),
    )
    write_json(layout.script, document)
    write_json(layout.audio, audio)
    return layout


def test_direct_compiler_resolves_quotes_layouts_and_fullscreen_without_model(tmp_path) -> None:
    layout = _direct_compiler_fixture(tmp_path)

    plan, report, _ = compile_project_edit_plan(layout)

    assert report.valid is True
    assert plan.blocks[0].block_type == "assets_with_full_text_v1"
    reference = next(block for block in plan.blocks if block.reference_label == "Mateus 24:29")
    assert reference.block_type == "center_statement_v1"
    assert reference.asset_events == []
    assert reference.text_pages == []
    quote = next(block for block in plan.blocks if block.role == "biblical_quote")
    assert quote.block_type == "assets_with_full_text_v1"
    assert quote.citation_id == reference.citation_id
    assert quote.text_pages[0].quote_open is True
    assert quote.text_pages[-1].quote_close is True
    assert all('"' not in page.text for page in quote.text_pages)
    fullscreen = next(
        block for block in plan.blocks if block.block_type == "fullscreen_image_sequence_v1"
    )
    assert len(fullscreen.asset_events) == 2
    assert fullscreen.text_pages == []
    assert {block.layout_variant for block in plan.blocks if block.text_zone} >= {
        "text_left_visual_right",
        "visual_left_text_right",
    }
    queue = read_json(layout.long_form_edit_plan_queue)
    assert queue["model_calls"] == 0
    assert queue["repairs"] == 0


def test_cutout_thumbnail_compositor_uses_dedicated_base_and_writes_both_formats(
    tmp_path,
) -> None:
    layout = _direct_compiler_fixture(tmp_path)
    plan, _, _ = compile_project_edit_plan(layout)
    assert plan.thumbnail is not None
    base = layout.root / "visual" / "assets" / "files" / "thumbnail-base.png"
    base.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1280, 720), (235, 204, 148))
    for x in range(760, 1160):
        for y in range(140, 620):
            image.putpixel((x, y), (111, 139, 60))
    image.save(base)
    inventory = read_json(layout.long_form_asset_inventory)
    for item in inventory["items"]:
        if item["asset_id"] == plan.thumbnail.source_asset_id:
            item.update(
                {
                    "status": "completed",
                    "path": "visual/assets/files/thumbnail-base.png",
                }
            )
    write_json(layout.long_form_asset_inventory, inventory)

    artifact = compose_cutout_thumbnail(layout)

    assert layout.thumbnail_png.is_file()
    assert layout.thumbnail_jpg.is_file()
    with Image.open(layout.thumbnail_png) as thumbnail:
        assert thumbnail.size == (1280, 720)
    assert artifact["source_asset_id"] == plan.thumbnail.source_asset_id
