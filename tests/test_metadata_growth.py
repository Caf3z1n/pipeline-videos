from __future__ import annotations

import json
from datetime import date

import pytest
from PIL import Image

from pipeline_videos.growth import (
    OFFICIAL_YOUTUBE_GUIDELINES,
    _validate_package,
    import_growth_package,
    import_integrated_thumbnail_candidates,
    prepare_growth_queue,
)
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
    YouTubePromiseMatch,
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
    _install_opening_scene_image(layout)
    return layout


def _set_growth_variants(layout, count: int) -> None:
    manifest = layout.load_manifest()
    manifest.config.youtube_growth.title_variants = count
    manifest.config.youtube_growth.thumbnail_variants = count
    layout.save_manifest(manifest)


def _package(queue: dict, *, format_name: str = "long_form") -> YouTubeGrowthPackage:
    clean_theme_art = queue["outputs"]["thumbnail_source_mode"] == "generated_theme_art"
    design_specs = (
        [
            YouTubeThumbnailDesign(
                text="",
                accent_word="",
                texture="none",
                typography_treatment="none",
                typography_hierarchy="none",
                typography_material="none",
            )
        ]
        if clean_theme_art
        else [
            YouTubeThumbnailDesign(
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
    )
    design_count = queue["outputs"].get(
        "thumbnail_design_count",
        queue["outputs"].get("thumbnail_count", len(queue["outputs"]["thumbnails"])),
    )
    title_standard = queue["outputs"]["title_standard"]
    if title_standard == "tension_pipe_passage_v2":
        titles = [
            "O detalhe escondido em Davi e Golias | 1 Samuel 17",
            "Por que Davi e Golias começou antes da pedra | 1 Samuel 17",
            "O medo que muda a história de Davi e Golias | 1 Samuel 17",
        ][:design_count]
        promise_match = YouTubePromiseMatch(
            content_promise=queue["contract_guard"]["content_promise"],
            opening_line=queue["contract_guard"]["opening_hook"],
            opening_scene_id=queue["contract_guard"]["opening_scene_id"],
            title_delivery=titles[0].split("|", 1)[0].strip(),
            thumbnail_delivery=(
                "Davi pequeno diante da paisagem do vale"
                if clean_theme_art
                else design_specs[0].text
            ),
        )
    else:
        titles = [
            "1 Samuel 17 | Davi e Golias: Coragem Diante do Medo",
            "1 Samuel 17 | Como Davi Enfrentou Golias?",
            "1 Samuel 17 | Davi Não Recuou Diante do Gigante",
        ][:design_count]
        promise_match = None
    return YouTubeGrowthPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        metadata=YouTubeMetadata(
            title=titles[0],
            alternative_titles=titles[1:],
            description=(
                "Entenda a história de Davi e Golias e o que ela ensina sobre coragem e fé."
            ),
            chapters=[],
            topics=["Davi e Golias", "1 Samuel 17"],
            pinned_comment="Qual parte dessa história mais falou com você?",
            thumbnail_concept=(
                "Davi pequeno diante da paisagem do vale"
                if clean_theme_art
                else "Davi pequeno diante da sombra de Golias"
            ),
            thumbnail_prompt=(
                queue["inputs"]["thumbnail_prompt"]
                if clean_theme_art
                else (
                    "Visual standard: cinematic-family-3d-v1. Style/medium: animação 3D "
                    "cinematográfica familiar premium. Typography standard: "
                    "integrated_extruded_3d_v1. Tipografia 3D extrudada integrada, com espessura "
                    "visível, bordas chanfradas, sombras de contato e oclusão, perspectiva "
                    "coerente e material coerente com a cena. Contraste de escala, emoção clara "
                    "e composição simples."
                )
            ),
            thumbnail_alternatives=(
                ["Pedra no ar", "Sombra do gigante"] if design_count > 1 else []
            ),
        ),
        strategy=YouTubeGrowthStrategy(
            format=format_name,
            target_viewer="Pessoas interessadas em histórias bíblicas",
            search_intent="Entender Davi e Golias e aplicar a mensagem",
            primary_keyword="Davi e Golias",
            secondary_keywords=["1 Samuel 17", "coragem e fé"],
            content_promise=queue["contract_guard"]["content_promise"],
            opening_hook=queue["contract_guard"]["opening_hook"],
            title_rationale="Combina busca clara e curiosidade legítima.",
            thumbnail_rationale="O contraste de escala comunica o conflito rapidamente.",
            title_thumbnail_complement="O título explica; a imagem dramatiza a escala.",
            promise_match=promise_match,
            source_ids=[],
            section_ids=["section-001"],
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
    thumbnails = queue["outputs"]["thumbnails"]
    for relative_path, color in zip(thumbnails, colors[: len(thumbnails)], strict=True):
        path = layout.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", tuple(queue["outputs"]["thumbnail_size"]), color).save(path)


def test_growth_queue_is_idempotent_and_imports_one_long_form_candidate(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    layout.images.unlink()

    queue = prepare_growth_queue(layout)
    repeated = prepare_growth_queue(layout)

    assert repeated == queue
    assert queue["role"] == "youtube_growth_specialist"
    assert queue["orchestration"]["single_writer"] is True
    assert queue["orchestration"]["may_modify_project_state"] is False
    assert queue["orchestration"]["reasoning_effort"] == "medium"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-sol"
    assert queue["orchestration"]["fork_turns"] == "none"
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["inputs"] == {
        "compact_context": "youtube/growth-context.json",
        "thumbnail_visual_reference_scene_id": None,
        "thumbnail_visual_reference_path": None,
        "thumbnail_theme": "Coragem e confiança em Deus",
        "thumbnail_prompt": queue["inputs"]["thumbnail_prompt"],
    }
    assert "TEMA DO VÍDEO:\nCoragem e confiança em Deus" in queue["inputs"]["thumbnail_prompt"]
    assert "texto, letras, logotipos" in queue["inputs"]["thumbnail_prompt"]
    assert queue["visual_standard"]["id"] == "cinematic-family-3d-v1"
    assert queue["visual_standard"]["thumbnail_must_match_scene_images"] is False
    assert queue["visual_standard"]["reference_policy"] == "theme_only_no_reference"
    context = read_json(layout.growth_context)
    assert context["visual_system"]["style"]["visual_standard_id"] == ("cinematic-family-3d-v1")
    assert context["opening_scene"]["visual_subject"] == "Davi diante de Golias"
    assert context["creative_contract"]["content_promise"] == "Coragem e confiança em Deus"
    assert "narration" not in context["script_outline"]["sections"][0]
    assert layout.growth_context.is_file()
    assert len(json.dumps(queue["schemas"]["package"])) < len(
        json.dumps(YouTubeGrowthPackage.model_json_schema())
    )
    assert len(queue["outputs"]["thumbnails"]) == 1
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_theme_art"
    assert queue["outputs"]["external_image_generation_required"] is True
    assert queue["outputs"]["base_art_without_text"] is True
    assert queue["outputs"]["text_overlay_required"] is False
    assert queue["outputs"]["imagegen_text_authority"] is False
    assert queue["outputs"]["text_forbidden"] is True
    assert queue["outputs"]["title_standard"] == "tension_pipe_passage_v2"
    assert queue["outputs"]["description_standard"] == "scripture_timeline_v1"
    assert queue["outputs"]["thumbnail_standard"] == "biblical_cinematic_landscape_v1"
    assert queue["outputs"]["thumbnail_typography_standard"] == "no_text_v1"
    assert queue["outputs"]["title_thumbnail_pairing_standard"] == "complementary_v1"
    assert queue["outputs"]["design_contract"]["maximum_lines"] == 0
    assert queue["outputs"]["design_contract"]["allowed_text_colors"] == []
    assert queue["outputs"]["design_contract"]["allowed_text_alignments"] == []
    assert queue["outputs"]["design_contract"]["paired_variants_required"] is True
    assert queue["outputs"]["design_contract"]["typography_treatment"] == "none"
    assert queue["outputs"]["design_contract"]["clean_negative_space_required"] is True
    assert queue["outputs"]["design_contract"]["text_forbidden"] is True
    assert "top_text_safe_margin_ratio" not in queue["outputs"]["design_contract"]
    assert any("única base narrativa" in item for item in queue["instructions"])
    assert any("bitmap completo" in item for item in queue["instructions"])
    assert any("texto, letras, números" in item for item in queue["instructions"])
    assert any("um par editorial completo" in item for item in queue["instructions"])
    assert any("Não passe referenced_image_paths" in item for item in queue["instructions"])
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
    assert len(list(layout.thumbnail.parent.glob("thumbnail-v*.png"))) == 1
    thumbnail_manifest = read_json(layout.thumbnail.parent / "thumbnail-manifest.json")
    assert [item["design"]["text"] for item in thumbnail_manifest["candidates"]] == [""]
    manifest = layout.load_manifest()
    assert manifest.stages["youtube_growth"].status == StageStatus.COMPLETED
    assert manifest.stages["metadata"].status == StageStatus.COMPLETED
    assert manifest.stages["thumbnail"].status == StageStatus.COMPLETED
    assert manifest.stages["thumbnail"].usage["external_image_generations"] == 1
    assert manifest.stages["thumbnail"].usage["integrated_image_generations"] == 0
    assert manifest.stages["thumbnail"].usage["clean_image_generations"] == 1
    assert thumbnail_manifest["text_baked_into_bitmap"] is False
    assert thumbnail_manifest["text_forbidden"] is True


def test_single_agent_growth_queue_requests_one_integrated_title_thumbnail_pair(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    manifest = layout.load_manifest()
    manifest.config.production.use_subagents = False
    manifest.config.youtube_growth.use_specialist_subagent = False
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
    assert queue["outputs"]["design_contract"]["top_text_safe_margin_ratio"] == 0.10
    assert any(
        "top 10% of the canvas completely free of text" in item for item in queue["instructions"]
    )
    assert any(
        "não se aplica a thumbnails de vídeos longos" in item for item in queue["instructions"]
    )
    assert "apparent_age" in queue["generation_retry_policy"]["immutable_on_alternative"]
    assert any("exatamente o mesmo prompt" in item for item in queue["instructions"])
    assert any("um par editorial completo" in item for item in queue["instructions"])
    assert not any("três pares editoriais completos" in item for item in queue["instructions"])


def test_integrated_retouch_imports_new_versions_without_local_overlay_or_activation(
    tmp_path,
) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    _set_growth_variants(layout, 3)
    queue = prepare_growth_queue(layout)
    write_json(layout.growth_package_staging, _package(queue, format_name="short"))
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


def test_single_agent_growth_uses_sol_medium_with_same_contract(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    project = layout.load_manifest()
    project.config.production.use_subagents = False
    project.config.youtube_growth.enabled = True
    project.config.youtube_growth.use_specialist_subagent = False
    layout.save_manifest(project)

    queue = prepare_growth_queue(layout)

    assert queue["orchestration"]["mode"] == "single_agent"
    assert queue["orchestration"]["worker_id"] == "main"
    assert queue["orchestration"]["reasoning_effort"] == "medium"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-sol"
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_integrated_art"
    assert queue["schemas"]["package"]


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
    assert queue["outputs"]["thumbnail_count"] == 1
    assert queue["outputs"]["thumbnail_design_count"] == 1
    assert len(queue["outputs"]["thumbnails"]) == 1
    assert queue["outputs"]["thumbnail_source_mode"] == "generated_integrated_art"
    assert queue["outputs"]["external_image_generation_required"] is True
    assert queue["outputs"]["title_standard"] == "tension_pipe_passage_v2"
    assert any("Tensão ou promessa concreta" in item for item in queue["instructions"])
    assert any("strategy.promise_match" in item for item in queue["instructions"])
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
    assert project.stages["thumbnail"].usage["external_image_generations"] == 1
    assert project.stages["thumbnail"].usage["integrated_image_generations"] == 1
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


def test_short_growth_rejects_promise_match_that_changes_opening_scene(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    assert package.strategy.promise_match is not None
    invalid_match = package.strategy.promise_match.model_copy(
        update={"opening_scene_id": "scene-inexistente"}
    )
    invalid_strategy = package.strategy.model_copy(update={"promise_match": invalid_match})

    with pytest.raises(ValueError, match="cena de abertura ativa"):
        _validate_package(
            layout,
            package.model_copy(update={"strategy": invalid_strategy}),
            queue,
        )


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


def test_long_growth_rejects_any_change_to_the_theme_prompt(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    changed_metadata = package.metadata.model_copy(
        update={"thumbnail_prompt": f"{package.metadata.thumbnail_prompt}\nTexto extra."}
    )

    with pytest.raises(ValueError, match="copiar integralmente o template temático"):
        _validate_package(
            layout,
            package.model_copy(update={"metadata": changed_metadata}),
            queue,
        )


def test_long_growth_rejects_text_or_editorial_marks(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    invalid_design = package.strategy.thumbnail_designs[0].model_copy(
        update={
            "text": "FÉ CONTRA MEDO",
            "accent_word": "MEDO",
            "text_lines": ["FÉ CONTRA MEDO"],
        }
    )
    invalid_strategy = package.strategy.model_copy(update={"thumbnail_designs": [invalid_design]})

    with pytest.raises(ValueError, match="sem texto ou marcações"):
        _validate_package(
            layout,
            package.model_copy(update={"strategy": invalid_strategy}),
            queue,
        )


def test_long_growth_rejects_thumbnail_alternatives(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    metadata_with_alternative = package.metadata.model_copy(
        update={"thumbnail_alternatives": ["Outra composição"]}
    )

    with pytest.raises(ValueError, match="não pode declarar alternativas"):
        _validate_package(
            layout,
            package.model_copy(update={"metadata": metadata_with_alternative}),
            queue,
        )


def test_growth_rejects_prompt_without_integrated_extruded_3d_typography(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    flat_metadata = package.metadata.model_copy(
        update={
            "thumbnail_prompt": (
                "Visual standard: cinematic-family-3d-v1. Style/medium: animação 3D "
                "cinematográfica familiar premium. Texto grande e legível sobre a cena."
            )
        }
    )

    with pytest.raises(ValueError, match="tipografia 3D integrada"):
        _validate_package(
            layout,
            package.model_copy(update={"metadata": flat_metadata}),
            queue,
        )


def test_growth_accepts_photographic_markers_in_negative_constraints(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    constrained_metadata = package.metadata.model_copy(
        update={
            "thumbnail_prompt": (
                f"{package.metadata.thumbnail_prompt} Sem fotografia editorial, sem live-action, "
                "sem retrato fotorrealista."
            )
        }
    )

    _validate_package(
        layout,
        package.model_copy(update={"metadata": constrained_metadata}),
        queue,
    )


def test_growth_ignores_long_avoid_section_with_photographic_markers(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    constrained_metadata = package.metadata.model_copy(
        update={
            "thumbnail_prompt": (
                f"{package.metadata.thumbnail_prompt}\n"
                "Avoid: qualquer mudança na arte-base além da composição editorial; texto "
                "extra ou duplicado; fotorrealismo, fotografia, live action, editorial "
                "photography, realistic photography e photorealistic portrait."
            )
        }
    )

    _validate_package(
        layout,
        package.model_copy(update={"metadata": constrained_metadata}),
        queue,
    )


def test_growth_rejects_title_that_repeats_the_paired_thumbnail_text(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
    repeated_metadata = package.metadata.model_copy(
        update={"title": "Davi enfrenta Golias | 1 Samuel 17"}
    )
    repeated_design = package.strategy.thumbnail_designs[0].model_copy(
        update={
            "text": "DAVI ENFRENTA GOLIAS",
            "accent_word": "GOLIAS",
            "text_lines": ["DAVI ENFRENTA", "GOLIAS"],
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
    layout = _project_with_editorial_inputs(tmp_path, video_format="short")
    _set_growth_variants(layout, 3)
    queue = prepare_growth_queue(layout)
    package = _package(queue, format_name="short")
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


def test_growth_rejects_semantic_drift_from_the_creative_contract(tmp_path) -> None:
    layout = _project_with_editorial_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(queue)
    drifted = package.strategy.model_copy(
        update={"content_promise": "Uma promessa mais dramática que o roteiro não fez."}
    )

    with pytest.raises(ValueError, match="promessa do contrato criativo"):
        _validate_package(layout, package.model_copy(update={"strategy": drifted}), queue)


def test_thumbnail_design_contract_is_strict_and_old_queue_is_rejected(tmp_path) -> None:
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
    old_queue = {**queue, "schema_version": "1.1"}
    package = _package(queue)
    legacy_strategy = package.strategy.model_copy(update={"thumbnail_designs": []})
    legacy_package = package.model_copy(update={"strategy": legacy_strategy})

    with pytest.raises(ValueError, match="thumbnail_design por candidata"):
        _validate_package(layout, legacy_package, queue)
    with pytest.raises(ValueError, match="versão da fila"):
        _validate_package(layout, legacy_package, old_queue)
