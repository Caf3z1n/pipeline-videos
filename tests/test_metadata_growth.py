from __future__ import annotations

import json
from datetime import date

import pytest
from PIL import Image, ImageChops

from pipeline_videos.growth import (
    OFFICIAL_YOUTUBE_GUIDELINES,
    _normalize_thumbnail,
    _validate_package,
    compose_light_short_cover,
    import_growth_package,
    import_integrated_thumbnail_candidates,
    prepare_growth_queue,
    retouch_youtube_package,
)
from pipeline_videos.metadata import build_metadata
from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    ImageItem,
    ImageManifest,
    ImageVersion,
    PromptBlocks,
    Scene,
    SceneManifest,
    ScriptDocument,
    ScriptSection,
    StageStatus,
    YouTubeGrowthPackage,
    YouTubeGrowthStrategy,
    YouTubeMetadata,
    YouTubeThumbnailDesign,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import read_json, write_json
from pipeline_videos.utils import utc_now


def _project_with_editorial_inputs(tmp_path, *, video_format: str = "landscape"):
    layout = create_project(
        "davi-e-golias",
        "Davi e Golias",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format=video_format,
        project_date=date(2026, 7, 14),
    )
    briefing = Briefing(
        title="Davi e Golias",
        main_passage="1 Samuel 17",
        central_theme="Coragem e confiança em Deus",
    )
    write_json(layout.briefing, briefing)
    script = ScriptDocument(
        title="Davi e Golias",
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="O desafio",
                narration="Davi enfrentou Golias confiando em Deus.",
                biblical_basis=["1 Samuel 17"],
                claim_classification=["biblical_text"],
            )
        ],
    )
    write_json(layout.script, script)
    write_json(
        layout.biblical_review,
        BiblicalReview(
            script_version=1,
            reviewed_at=utc_now(),
            reviewer="teste",
            summary="Sem bloqueios.",
            approved=True,
        ),
    )
    scene = Scene(
        scene_id="scene-001",
        order=1,
        section_id="section-001",
        narration=script.sections[0].narration,
        estimated_duration_seconds=4.0,
        emotional_intent="coragem",
        environment="vale",
        shot="plano geral",
        narrative_function="curiosidade",
        prompt=PromptBlocks(
            style_profile_id="style",
            palette_id="palette",
            continuity=[],
            scene="Davi diante de Golias",
            composition="contraste de escala",
            emotion="coragem",
            negative=[],
            assembled_prompt="Davi diante de Golias",
        ),
        image_id="image-001",
    )
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=4.0,
            scenes=[scene],
        ),
    )
    return layout


def _package(queue: dict, *, format_name: str = "long_form") -> YouTubeGrowthPackage:
    scene_ids = queue["outputs"].get("thumbnail_scene_ids", [])
    design_specs = [
        YouTubeThumbnailDesign(
            source_scene_id=scene_ids[0] if scene_ids else None,
            text="FÉ CONTRA MEDO",
            accent_word="MEDO",
            text_lines=["FÉ CONTRA", "MEDO"],
            primary_color="white",
            accent_color="red",
            narrative_element="underline",
            narrative_element_target="MEDO",
            narrative_element_rationale="Sublinhar o conflito central da narrativa.",
        ),
        YouTubeThumbnailDesign(
            source_scene_id=scene_ids[1 % len(scene_ids)] if scene_ids else None,
            text="DAVI NÃO RECUOU",
            accent_word="RECUOU",
            text_lines=["DAVI", "NÃO RECUOU"],
            primary_color="white",
            accent_color="red",
            narrative_element="underline",
            narrative_element_target="RECUOU",
            narrative_element_rationale="Sublinhar a decisão narrada de Davi.",
        ),
        YouTubeThumbnailDesign(
            source_scene_id=scene_ids[2 % len(scene_ids)] if scene_ids else None,
            text="ELE PARECIA INVENCÍVEL",
            accent_word="INVENCÍVEL",
            text_lines=["ELE PARECIA", "INVENCÍVEL"],
            primary_color="white",
            accent_color="red",
            narrative_element="arrow",
            narrative_element_anchor="center_right",
            narrative_element_target="Golias",
            narrative_element_rationale="Apontar para o antagonista já presente na arte-base.",
        ),
    ]
    design_count = queue["outputs"].get(
        "thumbnail_design_count",
        queue["outputs"].get("thumbnail_count", len(queue["outputs"]["thumbnails"])),
    )
    return YouTubeGrowthPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        metadata=YouTubeMetadata(
            title="1 Samuel 17 | Davi e Golias: Coragem Diante do Medo",
            alternative_titles=[
                "1 Samuel 17 | Como Davi Enfrentou Golias?",
                "1 Samuel 17 | Davi Não Recuou Diante do Gigante",
            ],
            description=(
                "Entenda a história de Davi e Golias e o que ela ensina sobre coragem e fé."
            ),
            chapters=[],
            topics=["Davi e Golias", "1 Samuel 17"],
            pinned_comment="Qual parte dessa história mais falou com você?",
            thumbnail_concept="Davi pequeno diante da sombra de Golias",
            thumbnail_prompt=(
                "Visual standard: cinematic-family-3d-v1. Style/medium: animação 3D "
                "cinematográfica familiar premium. Contraste de escala, emoção clara e "
                "composição simples."
            ),
            thumbnail_alternatives=["Pedra no ar", "Sombra do gigante"],
        ),
        strategy=YouTubeGrowthStrategy(
            format=format_name,
            target_viewer="Pessoas interessadas em histórias bíblicas",
            search_intent="Entender Davi e Golias e aplicar a mensagem",
            primary_keyword="Davi e Golias",
            secondary_keywords=["1 Samuel 17", "coragem e fé"],
            content_promise="Explicar a narrativa e sua aplicação sem inventar fatos.",
            opening_hook="Um jovem diante de um guerreiro impossível de vencer.",
            title_rationale="Combina busca clara e curiosidade legítima.",
            thumbnail_rationale="O contraste de escala comunica o conflito rapidamente.",
            title_thumbnail_complement="O título explica; a imagem dramatiza a escala.",
            source_ids=[],
            section_ids=["section-001"],
            thumbnail_text="FÉ CONTRA MEDO",
            thumbnail_accent_word="MEDO",
            thumbnail_overlay_style="high_impact_editorial",
            thumbnail_designs=design_specs[:design_count],
            short_cover_timestamp_seconds=0.0 if format_name == "short" else None,
            experiment_plan=["Comparar três combinações por watch time no Studio."],
            compliance_checks=["Promessa fiel ao conteúdo", "Sem clickbait enganoso"],
            official_guidelines=list(OFFICIAL_YOUTUBE_GUIDELINES[:3]),
        ),
    )


def _install_opening_scene_image(layout, color: str = "#8A725E") -> None:
    source = layout.root / "images/assets/scene-001/v001.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1920), color).save(source)
    write_json(
        layout.images,
        ImageManifest(
            generated_at=utc_now(),
            style_profile_id="style",
            palette_id="palette",
            items=[
                ImageItem(
                    image_id="image-001",
                    scene_id="scene-001",
                    prompt="Davi diante de Golias",
                    prompt_hash="prompt-hash",
                    status=StageStatus.COMPLETED,
                    active_version=1,
                    versions=[
                        ImageVersion(
                            version=1,
                            path="images/assets/scene-001/v001.png",
                            provider="test",
                            prompt_hash="prompt-hash",
                            created_at=utc_now(),
                        )
                    ],
                )
            ],
        ),
    )


def _install_integrated_staging_thumbnails(layout, queue: dict) -> None:
    colors = ["#1E2850", "#35485E", "#5A332C"]
    for relative_path, color in zip(queue["outputs"]["thumbnails"], colors, strict=True):
        path = layout.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", tuple(queue["outputs"]["thumbnail_size"]), color).save(path)


def test_growth_queue_is_idempotent_and_imports_three_long_form_candidates(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)

    queue = prepare_growth_queue(layout)
    repeated = prepare_growth_queue(layout)

    assert repeated == queue
    assert queue["role"] == "youtube_growth_specialist"
    assert queue["orchestration"]["single_writer"] is True
    assert queue["orchestration"]["may_modify_project_state"] is False
    assert queue["orchestration"]["reasoning_effort"] == "medium"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-terra"
    assert queue["orchestration"]["fork_turns"] == "none"
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["inputs"] == {
        "compact_context": "youtube/growth-context.json",
        "thumbnail_visual_reference_scene_id": "scene-001",
    }
    assert queue["visual_standard"]["id"] == "cinematic-family-3d-v1"
    assert queue["visual_standard"]["thumbnail_must_match_scene_images"] is True
    assert queue["visual_standard"]["reference_policy"] == "opening_scene_image_when_available"
    context = read_json(layout.growth_context)
    assert context["visual_system"]["style"]["visual_standard_id"] == ("cinematic-family-3d-v1")
    assert context["opening_scene"]["assembled_prompt"] == "Davi diante de Golias"
    assert layout.growth_context.is_file()
    assert len(json.dumps(queue["schemas"]["package"])) < len(
        json.dumps(YouTubeGrowthPackage.model_json_schema())
    )
    assert len(queue["outputs"]["thumbnails"]) == 3
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_integrated_art"
    assert queue["outputs"]["external_image_generation_required"] is True
    assert queue["outputs"]["base_art_without_text"] is False
    assert queue["outputs"]["text_overlay_required"] is False
    assert queue["outputs"]["imagegen_text_authority"] is True
    assert queue["outputs"]["title_standard"] == "passage_pipe_topic_v1"
    assert queue["outputs"]["description_standard"] == "scripture_timeline_v1"
    assert queue["outputs"]["thumbnail_standard"] == "direct_conflict_red_v1"
    assert queue["outputs"]["title_thumbnail_pairing_standard"] == "complementary_v1"
    assert queue["outputs"]["design_contract"]["maximum_lines"] == 3
    assert queue["outputs"]["design_contract"]["allowed_text_colors"] == [
        "white",
        "yellow",
        "red",
    ]
    assert queue["outputs"]["design_contract"]["allowed_text_alignments"] == [
        "left",
        "center",
        "right",
    ]
    assert queue["outputs"]["design_contract"]["paired_variants_required"] is True
    assert queue["outputs"]["design_contract"]["title_thumbnail_repetition_allowed"] is False
    assert any("personagem ou objeto dominante" in item for item in queue["instructions"])
    assert any("bitmap completo" in item for item in queue["instructions"])
    assert any("não aplicará texto" in item for item in queue["instructions"])
    assert any("três pares editoriais completos" in item for item in queue["instructions"])
    assert any("É proibido repetir" in item for item in queue["instructions"])
    assert any("DAVI VS GOLIAS" in item for item in queue["instructions"])
    assert any("accent_color=red" in item for item in queue["instructions"])
    assert any("mesmo universo visual" in item for item in queue["instructions"])
    assert any("Style/medium: animação 3D" in item for item in queue["instructions"])
    assert layout.load_manifest().stages["youtube_growth"].attempts == 1

    write_json(layout.growth_package_staging, _package(queue))
    _install_integrated_staging_thumbnails(layout, queue)

    imported = import_growth_package(layout)

    assert imported.metadata.chapters == ["00:00 - Abertura"]
    assert layout.metadata.is_file()
    assert layout.growth_strategy.is_file()
    assert layout.growth_package.is_file()
    assert layout.thumbnail.is_file()
    assert Image.open(layout.thumbnail).size == (1280, 720)
    with Image.open(layout.thumbnail) as thumbnail:
        assert thumbnail.getpixel((640, 70)) == (30, 40, 80)
    assert len(list(layout.thumbnail.parent.glob("thumbnail-v*.png"))) == 3
    thumbnail_manifest = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")
    assert [item["design"]["text"] for item in thumbnail_manifest["candidates"]] == [
        "FÉ CONTRA MEDO",
        "DAVI NÃO RECUOU",
        "ELE PARECIA INVENCÍVEL",
    ]
    manifest = layout.load_manifest()
    assert manifest.stages["youtube_growth"].status == StageStatus.COMPLETED
    assert manifest.stages["metadata"].status == StageStatus.COMPLETED
    assert manifest.stages["thumbnail"].status == StageStatus.COMPLETED
    assert manifest.stages["thumbnail"].usage["external_image_generations"] == 3
    assert manifest.stages["thumbnail"].usage["integrated_image_generations"] == 3
    assert thumbnail_manifest["text_baked_into_bitmap"] is True


def test_remote_review_growth_queue_requests_one_integrated_title_thumbnail_pair(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    manifest = layout.load_manifest()
    manifest.config.production.mode = "remote_review"
    manifest.config.production.use_subagents = False
    manifest.config.youtube_growth.title_variants = 1
    manifest.config.youtube_growth.thumbnail_variants = 1
    layout.save_manifest(manifest)

    queue = prepare_growth_queue(layout)

    assert queue["orchestration"]["mode"] == "single_agent"
    assert queue["orchestration"]["worker_id"] == "main"
    assert queue["outputs"]["thumbnail_count"] == 1
    assert queue["outputs"]["thumbnail_design_count"] == 1
    assert len(queue["outputs"]["thumbnails"]) == 1
    assert queue["generation_retry_policy"]["same_prompt_attempts"] == 2
    assert queue["visual_standard"]["reference_policy"] == "required_opening_scene_image"
    assert "apparent_age" in queue["generation_retry_policy"]["immutable_on_alternative"]
    assert any("exatamente o mesmo prompt" in item for item in queue["instructions"])
    assert any("um par editorial completo" in item for item in queue["instructions"])
    assert not any("três pares editoriais completos" in item for item in queue["instructions"])


def test_retouch_updates_two_candidates_and_preserves_selected_third_thumbnail(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    write_json(layout.growth_package_staging, _package(queue))
    _install_integrated_staging_thumbnails(layout, queue)
    import_growth_package(layout)
    original_third = layout.thumbnail.parent / "thumbnail-v003.png"
    original_third_hash = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")[
        "candidates"
    ][2]["hash"]
    spec = layout.thumbnail.parent / "retouch-test.json"
    designs = _package(queue).strategy.thumbnail_designs
    write_json(
        spec,
        {
            "metadata": {
                "title": "1 Samuel 17 | Davi vs Golias",
                "alternative_titles": [
                    "1 Samuel 17 | Só Davi Avançou",
                    "1 Samuel 17 | Sem a Armadura",
                ],
                "description": "Resumo curto.",
                "chapters": ["00:00 - Abertura"],
            },
            "thumbnail_updates": [
                {"candidate_index": 1, "design": designs[0].model_dump(mode="json")},
                {"candidate_index": 2, "design": designs[1].model_dump(mode="json")},
            ],
            "active_candidate_index": 3,
        },
    )

    metadata = retouch_youtube_package(layout, spec)

    manifest = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")
    assert metadata.title == "1 Samuel 17 | Davi vs Golias"
    assert [item["path"] for item in manifest["candidates"]] == [
        "youtube/thumbnail-v004.png",
        "youtube/thumbnail-v005.png",
        "youtube/thumbnail-v003.png",
    ]
    assert manifest["active_path"] == "youtube/thumbnail-v003.png"
    assert manifest["candidates"][2]["hash"] == original_third_hash
    assert layout.thumbnail.read_bytes() == original_third.read_bytes()
    assert layout.load_manifest().approvals["metadata"].status.value == "pending"


def test_integrated_retouch_imports_new_versions_without_local_overlay_or_activation(
    tmp_path,
) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    write_json(layout.growth_package_staging, _package(queue))
    _install_integrated_staging_thumbnails(layout, queue)
    import_growth_package(layout)
    original_active = layout.thumbnail.read_bytes()
    original_active_path = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")[
        "active_path"
    ]

    source_dir = layout.growth_staging / "integrated-retouch"
    source_dir.mkdir(parents=True, exist_ok=True)
    sources = [source_dir / f"thumbnail-{index:03d}.png" for index in range(1, 4)]
    for source, color in zip(sources, ["#112233", "#445566", "#778899"], strict=True):
        Image.new("RGB", (1536, 1024), color).save(source)

    destinations = import_integrated_thumbnail_candidates(layout, sources)

    manifest = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")
    assert [path.name for path in destinations] == [
        "thumbnail-v004.png",
        "thumbnail-v005.png",
        "thumbnail-v006.png",
    ]
    assert manifest["active_path"] == original_active_path
    assert layout.thumbnail.read_bytes() == original_active
    assert manifest["composition_mode"] == "integrated_imagegen_v1"
    assert manifest["text_baked_into_bitmap"] is True
    assert all(candidate["text_baked_into_bitmap"] for candidate in manifest["candidates"])
    with Image.open(destinations[0]) as thumbnail:
        assert thumbnail.getpixel((640, 360)) == (17, 34, 51)
    usage = layout.load_manifest().stages["thumbnail"].usage
    assert usage["integrated_image_generations"] == 3
    assert usage["local_text_overlays"] == 0


def test_light_growth_specialist_uses_low_reasoning_with_same_contract(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    project = layout.load_manifest()
    project.config.production.mode = "light_automatic"
    project.config.youtube_growth.enabled = True
    project.config.youtube_growth.use_specialist_subagent = True
    layout.save_manifest(project)

    queue = prepare_growth_queue(layout)

    assert queue["role"] == "youtube_growth_specialist"
    assert queue["orchestration"]["reasoning_effort"] == "low"
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_integrated_art"
    assert queue["schemas"]["package"]


def test_light_cover_reuses_opening_scene_and_is_ready_for_frame_zero(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    project = layout.load_manifest()
    project.config.production.mode = "light_manual"
    project.config.youtube_growth.enabled = False
    project.config.youtube_growth.use_specialist_subagent = False
    project.stages["youtube_growth"] = project.stages["youtube_growth"].model_copy(
        update={"status": StageStatus.SKIPPED}
    )
    layout.save_manifest(project)
    _install_opening_scene_image(layout)
    metadata = build_metadata(layout)

    result = compose_light_short_cover(layout)

    assert result == layout.thumbnail
    with Image.open(result) as thumbnail:
        assert thumbnail.size == (1080, 1920)
        assert thumbnail.getpixel((540, 150)) != (138, 114, 94)
    thumbnail_manifest = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")
    assert thumbnail_manifest["provider"] == "light-opening-scene"
    assert thumbnail_manifest["candidates"][0]["source_path"].endswith(
        "images/assets/scene-001/v001.png"
    )
    current = layout.load_manifest()
    assert metadata.title == "Davi e Golias"
    assert current.config.render.short_cover_enabled is True
    assert current.stages["thumbnail"].status == StageStatus.COMPLETED
    assert current.stages["thumbnail"].usage["reused_opening_image"] == 1


def test_growth_import_rejects_a_stale_package_before_promoting_assets(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue).model_copy(update={"input_hash": "stale"})
    write_json(layout.growth_package_staging, package)
    for relative_path in queue["outputs"]["thumbnails"]:
        path = layout.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1280, 720), "#20304A").save(path)

    with pytest.raises(RuntimeError, match="fila antiga"):
        import_growth_package(layout)

    assert not layout.metadata.exists()
    assert not layout.thumbnail.exists()


def test_short_growth_queue_requests_composed_first_frame(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")

    queue = prepare_growth_queue(layout)

    assert queue["outputs"]["thumbnail_role"] == "short_first_frame"
    assert queue["outputs"]["thumbnail_size"] == [1080, 1920]
    assert queue["outputs"]["base_art_without_text"] is False
    assert queue["outputs"]["text_overlay_required"] is False
    assert queue["outputs"]["design_contract"]["text_word_count"] == [2, 5]
    assert queue["outputs"]["design_contract"]["pillow_is_text_authority"] is False
    assert queue["outputs"]["design_contract"]["text_must_be_baked_into_bitmap"] is True
    assert queue["outputs"]["thumbnail_count"] == 3
    assert queue["outputs"]["thumbnail_design_count"] == 3
    assert len(queue["outputs"]["thumbnails"]) == 3
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_integrated_art"
    assert queue["outputs"]["external_image_generation_required"] is True
    assert any("frame zero" in instruction for instruction in queue["instructions"])


def test_short_growth_import_preserves_integrated_text_bitmap(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    write_json(layout.growth_package_staging, _package(queue, format_name="short"))
    _install_integrated_staging_thumbnails(layout, queue)

    import_growth_package(layout)

    with Image.open(layout.thumbnail) as thumbnail:
        assert thumbnail.size == (1080, 1920)
        assert thumbnail.getpixel((540, 150)) == (30, 40, 80)
    thumbnail_manifest = layout.thumbnail.parent / "thumbnail-manifest.json"
    assert thumbnail_manifest.is_file()
    manifest = read_json(thumbnail_manifest)
    assert manifest["provider"] == "imagegen-integrated+youtube-growth-specialist"
    assert manifest["text_baked_into_bitmap"] is True
    assert read_json(layout.metadata)["chapters"] == []
    project = layout.load_manifest()
    assert project.stages["thumbnail"].usage["external_image_generations"] == 3
    assert project.stages["thumbnail"].usage["integrated_image_generations"] == 3
    assert project.stages["youtube_growth"].usage["context_bytes"] > 0


def test_short_growth_rejects_generic_title_without_primary_search_phrase(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    generic_metadata = package.metadata.model_copy(
        update={"title": "Quando tudo parece impossível"}
    )
    generic_package = package.model_copy(update={"metadata": generic_metadata})

    with pytest.raises(ValueError, match="conter primary_keyword"):
        _validate_package(layout, generic_package, queue)


def test_growth_rejects_photographic_thumbnail_prompt(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    photographic_metadata = package.metadata.model_copy(
        update={
            "thumbnail_prompt": (
                "Visual standard: cinematic-family-3d-v1. Style/medium: premium cinematic "
                "editorial photography, realistic."
            )
        }
    )

    with pytest.raises(ValueError, match="fotografia ou fotorrealismo"):
        _validate_package(
            layout,
            package.model_copy(update={"metadata": photographic_metadata}),
            queue,
        )


def test_growth_rejects_title_that_repeats_the_paired_thumbnail_text(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    repeated_metadata = package.metadata.model_copy(
        update={"title": "1 Samuel 17 | Davi Enfrenta Golias"}
    )
    repeated_design = package.strategy.thumbnail_designs[0].model_copy(
        update={
            "text": "DAVI VS GOLIAS",
            "accent_word": "VS",
            "text_lines": ["DAVI VS", "GOLIAS"],
        }
    )
    repeated_strategy = package.strategy.model_copy(
        update={
            "thumbnail_designs": [
                repeated_design,
                *package.strategy.thumbnail_designs[1:],
            ]
        }
    )
    repeated_package = package.model_copy(
        update={"metadata": repeated_metadata, "strategy": repeated_strategy}
    )

    with pytest.raises(ValueError, match="repete na thumbnail"):
        _validate_package(layout, repeated_package, queue)


def test_growth_rejects_reused_thumbnail_phrase_across_title_pairs(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    duplicate = package.strategy.thumbnail_designs[1].model_copy(
        update={
            "text": package.strategy.thumbnail_designs[0].text,
            "accent_word": package.strategy.thumbnail_designs[0].accent_word,
            "text_lines": package.strategy.thumbnail_designs[0].text_lines,
        }
    )
    strategy = package.strategy.model_copy(
        update={
            "thumbnail_designs": [
                package.strategy.thumbnail_designs[0],
                duplicate,
                package.strategy.thumbnail_designs[2],
            ]
        }
    )

    with pytest.raises(ValueError, match="frase de thumbnail distinta"):
        _validate_package(layout, package.model_copy(update={"strategy": strategy}), queue)


def test_thumbnail_design_contract_is_strict_but_legacy_package_remains_valid(tmp_path) -> None:
    with pytest.raises(ValueError, match="duas a cinco"):
        YouTubeThumbnailDesign(
            text="UMA FRASE COM SEIS PALAVRAS PARA CAPA",
            accent_word="CAPA",
        )
    with pytest.raises(ValueError, match="justificativa editorial"):
        YouTubeThumbnailDesign(
            text="ANTES DA PEDRA",
            accent_word="PEDRA",
            narrative_element="arrow",
            narrative_element_target="pedra",
        )

    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    legacy_queue = {**queue, "schema_version": "1.1"}
    package = _package(queue)
    legacy_strategy = package.strategy.model_copy(update={"thumbnail_designs": []})
    legacy_package = package.model_copy(update={"strategy": legacy_strategy})

    with pytest.raises(ValueError, match="thumbnail_design por candidata"):
        _validate_package(layout, legacy_package, queue)
    _validate_package(layout, legacy_package, legacy_queue)


def test_thumbnail_composition_is_deterministic_with_three_line_safe_layout(tmp_path) -> None:
    source = tmp_path / "source.png"
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (1280, 720), "#6C5848").save(source)
    design = YouTubeThumbnailDesign(
        text="ANTES DA PEDRA DAVI DECIDIU",
        accent_word="PEDRA",
        text_lines=["ANTES DA", "PEDRA", "DAVI DECIDIU"],
        text_position="top",
        text_alignment="left",
        primary_color="white",
        accent_color="yellow",
        safe_margin_x_ratio=0.08,
        safe_margin_y_ratio=0.08,
        narrative_element="brush",
        narrative_element_rationale="Destacar a palavra que resolve o open loop.",
    )

    _normalize_thumbnail(source, first, (1280, 720), design=design)
    _normalize_thumbnail(source, second, (1280, 720), design=design)

    assert first.read_bytes() == second.read_bytes()
    with Image.open(source) as original, Image.open(first) as composed:
        assert composed.size == (1280, 720)
        assert ImageChops.difference(original, composed).getbbox() is not None
