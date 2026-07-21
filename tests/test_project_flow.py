from datetime import date
from pathlib import Path

import pytest

from pipeline_videos.models import (
    DEFAULT_ELEVENLABS_MODEL_ID,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ApprovalStatus,
    Briefing,
    ProjectConfig,
    SourceEntry,
    StageStatus,
    StyleProfile,
)
from pipeline_videos.project import DEFAULT_SHORT_SPOKEN_REFERENCE_IMAGE_PATH, create_project
from pipeline_videos.review import approve_briefing
from pipeline_videos.state import set_approval
from pipeline_videos.storage import read_json, read_model, resolve_project, write_json


def test_biblical_source_excerpt_is_sealed_by_content_hash() -> None:
    source = SourceEntry(
        source_id="bpm-mateus-26-16",
        kind="biblical_text",
        title="Mateus 26:16",
        excerpt="Daquele momento em diante, ele buscava uma oportunidade para traí-lo.",
        use_in_video="Citação literal.",
        claim_scope="Mateus 26:16.",
        verified=True,
    )

    assert source.content_hash is not None
    tampered = source.model_dump(mode="json")
    tampered["excerpt"] = "Texto alterado depois da verificação."
    with pytest.raises(ValueError, match="content_hash"):
        SourceEntry.model_validate(tampered)


def test_new_project_is_portable_and_briefing_approval_updates_state(tmp_path) -> None:
    layout = create_project(
        "salmo-121",
        "De onde vem o meu socorro?",
        projects_dir=tmp_path,
        target_duration_seconds=540,
        project_date=date(2026, 7, 14),
    )
    briefing = read_model(layout.briefing, Briefing)
    briefing.main_passage = "Salmo 121"
    briefing.central_theme = "socorro e confiança"
    briefing.practical_application = "Reconhecer limites e perseverar com esperança."
    briefing.audience = "adultos em momentos de incerteza"
    briefing.emotional_intent = "acolher e fortalecer"
    briefing.viewer_outcome = "Compreender como o Salmo apresenta socorro e confiança."
    briefing.creative_mode = "passage_explainer"
    briefing.interpretive_stance = "expository"
    briefing.hook_policy = "optional"
    briefing.hook_family = "unanswered_question"
    briefing.interpretation_limits = ["Não prometer ausência de sofrimento."]
    briefing.claims_to_avoid = ["Não inventar contexto histórico."]
    write_json(layout.briefing, briefing)

    approve_briefing(layout, decided_by="teste")

    manifest = layout.load_manifest()
    assert layout.root == tmp_path / "videos-longos" / "14-07-2026_salmo-121"
    assert manifest.project_id == "14-07-2026_salmo-121"
    assert manifest.aliases == ["salmo-121"]
    assert resolve_project("salmo-121", tmp_path).root == layout.root
    assert manifest.config.render.final_crf == 12
    assert manifest.config.render.final_preset == "slow"
    assert all(not field.startswith("preview_") for field in manifest.config.render.model_dump())
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.production.worker_context_policy == "compact_only"
    assert manifest.config.youtube_growth.thumbnail_generation_mode == "theme_prompt_imagegen_v1"
    assert manifest.config.youtube_growth.thumbnail_reference_mode == "theme_only_v1"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.youtube_growth.title_standard == "creative_pipe_passage_v3"
    assert manifest.config.youtube_growth.description_standard == "search_first_unique_v2"
    assert manifest.config.youtube_growth.thumbnail_standard == "biblical_cinematic_landscape_v1"
    assert manifest.config.youtube_growth.thumbnail_typography_standard == "no_text_v1"
    assert manifest.config.youtube_growth.title_thumbnail_pairing_standard == "complementary_v1"
    assert manifest.config.youtube_publication.allow_automated_private_review_upload is True
    assert manifest.config.youtube_publication.category_id == "27"
    assert manifest.config.editorial_policy.standard == "scripture_rich_ptbr_v1"
    assert manifest.config.editorial_policy.require_literal_scripture_citations is True
    assert manifest.config.editorial_policy.require_quoted_comment_response is False
    assert manifest.config.long_form_script.enabled is True
    assert manifest.config.long_form_script.narrative_standard == "creative_retention_long_v2"
    assert manifest.config.long_form_script.opening_value_max_seconds == 40.0
    assert manifest.config.long_form_script.require_callback is False
    assert manifest.config.long_form_script.first_scripture_citation_max_seconds == 75.0
    assert manifest.config.long_form_script.maximum_scripture_citation_gap_seconds == 105.0
    assert manifest.config.long_form_script.maximum_reengagement_gap_seconds == 120.0
    assert manifest.config.captions.vertical_position == "center"
    assert manifest.config.captions.isolate_quoted_comment_response is False
    assert manifest.config.captions.comment_response_hold_seconds == 0.0
    assert manifest.config.audio.comment_response_silence_seconds == 0.0
    assert manifest.config.render.transition_style == "cinematic_mix"
    assert manifest.config.render.motion_style == "smooth_pendulum"
    assert manifest.config.render.background_blur_profile == "functional_caption_focus_v1"
    assert manifest.config.render.background_blur_opening_sigma == 8.0
    assert manifest.config.render.background_blur_scripture_sigma == 8.0
    assert manifest.config.render.background_blur_reflection_sigma == 8.0
    assert manifest.config.render.background_blur_payoff_sigma == 8.0
    assert manifest.config.render.background_blur_closing_sigma == 8.0
    assert manifest.config.render.caption_focus_blur_extra_sigma == 0.0
    assert manifest.config.render.background_music_path is None
    assert manifest.config.music.enabled is True
    assert manifest.config.music.selection_mode == "random"
    assert manifest.stages["music"].status == StageStatus.PENDING
    assert manifest.config.captions.font_family == "Montserrat ExtraBold"
    assert manifest.config.captions.variant == "active_card_v2"
    assert manifest.config.captions.word_arrival_animation is True
    assert manifest.config.audio.voice_id == DEFAULT_ELEVENLABS_VOICE_ID
    assert manifest.config.audio.model_id == DEFAULT_ELEVENLABS_MODEL_ID
    assert (layout.root / "visual/fonts/files/Montserrat-ExtraBold.ttf").is_file()
    assert (layout.root / "visual/fonts/OFL-Montserrat.txt").is_file()
    assert manifest.stages["briefing"].status == StageStatus.COMPLETED
    assert manifest.approvals["briefing"].status == ApprovalStatus.APPROVED
    assert all(not Path(path).is_absolute() for path in manifest.stages["briefing"].artifacts)

    set_approval(
        layout,
        "metadata",
        ApprovalStatus.APPROVED,
        decided_by="teste",
    )
    briefing.central_theme = "socorro, presença e confiança"
    write_json(layout.briefing, briefing)
    approve_briefing(layout, decided_by="teste")

    changed = layout.load_manifest()
    assert changed.stages["script"].status == StageStatus.STALE
    assert changed.approvals["metadata"].status == ApprovalStatus.PENDING


def test_existing_preview_manifest_is_migrated_on_load(tmp_path) -> None:
    layout = create_project(
        "manifesto-legado",
        "Manifesto legado",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        project_date=date(2026, 7, 19),
    )
    payload = read_json(layout.manifest)
    payload["config"]["production"]["mode"] = "draft_one_shot"
    payload["config"]["render"].update(
        {
            "preview_width": 640,
            "preview_height": 360,
            "preview_crf": 16,
            "preview_preset": "fast",
            "preview_motion_supersample": 2,
        }
    )
    write_json(layout.manifest, payload)

    manifest = layout.load_manifest()

    assert manifest.config.production.mode == "final_candidate_one_shot"
    assert all(not field.startswith("preview_") for field in manifest.config.render.model_dump())


def test_missing_versioned_config_keeps_legacy_script_contracts() -> None:
    config = ProjectConfig.model_validate({})

    assert config.long_form_script.narrative_standard == "legacy"
    assert config.shorts_script.narrative_standard == "retention_story_v2"
    assert config.youtube_growth.title_standard == "passage_pipe_topic_v1"


def test_new_short_project_uses_painterly_block_profile_without_custom_cover(tmp_path) -> None:
    layout = create_project(
        "davi-golias-short",
        "Davi e Golias em 1 minuto",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )

    manifest = layout.load_manifest()

    assert manifest.video_format == "short"
    assert layout.root == tmp_path / "shorts" / "14-07-2026_davi-golias-short"
    assert (manifest.config.render.width, manifest.config.render.height) == (1080, 1920)
    assert manifest.config.render.final_crf == 12
    assert manifest.config.render.final_preset == "slow"
    assert manifest.config.render.transition_seconds == 0.38
    assert manifest.config.render.transition_style == "cinematic_mix"
    assert manifest.config.render.motion_style == "smooth_pendulum"
    assert manifest.config.render.motion_zoom_min == 1.02
    assert manifest.config.render.motion_zoom_max == 1.105
    assert manifest.config.render.motion_pan_strength == 0.32
    assert manifest.config.render.motion_supersample == 3
    assert manifest.config.render.background_music_path is None
    assert manifest.config.render.audio_mix_standard == "voice_music_loudness_v1"
    assert manifest.config.render.narration_target_lufs == -18.0
    assert manifest.config.render.background_music_gap_lu == 13.0
    assert manifest.config.render.narration_true_peak_dbfs == -2.0
    assert manifest.config.render.background_music_true_peak_dbfs == -9.0
    assert manifest.config.render.narration_gain_db == 0.0
    assert manifest.config.render.background_music_gain_db == 0.0
    assert manifest.config.render.short_cover_enabled is False
    assert manifest.config.render.short_cover_duration_seconds == pytest.approx(1 / 30)
    assert manifest.config.render.background_blur_profile == "functional_caption_focus_v1"
    assert manifest.config.render.background_blur_opening_sigma == 8.0
    assert manifest.config.render.background_blur_scripture_sigma == 8.0
    assert manifest.config.render.background_blur_reflection_sigma == 8.0
    assert manifest.config.render.background_blur_payoff_sigma == 8.0
    assert manifest.config.render.background_blur_closing_sigma == 8.0
    assert manifest.config.render.caption_focus_blur_extra_sigma == 0.0
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 0
    assert manifest.config.youtube_growth.description_standard == "search_first_unique_v2"
    assert manifest.config.youtube_growth.thumbnail_generation_mode == "youtube_auto_frame_v1"
    assert manifest.config.youtube_growth.thumbnail_reference_mode == "none_v1"
    assert manifest.config.youtube_growth.thumbnail_standard == "youtube_auto_frame_v1"
    assert manifest.config.youtube_growth.title_standard == "creative_pipe_passage_v3"
    assert manifest.config.youtube_growth.thumbnail_typography_standard == "no_text_v1"
    assert manifest.config.youtube_publication.category_id == "22"
    assert manifest.config.images.height > manifest.config.images.width
    assert manifest.config.images.visual_standard_id == "biblical-midcentury-gouache-v1"
    assert manifest.config.images.enforce_visual_standard is True
    assert manifest.config.images.scene_planning_standard == "narration_block_visual_v1"
    assert manifest.config.images.composition_safety_mode == "unrestricted_scene_v1"
    assert manifest.config.images.spoken_reference_image_mode == "fixed_profile_asset_v1"
    assert (
        manifest.config.images.spoken_reference_image_path
        == DEFAULT_SHORT_SPOKEN_REFERENCE_IMAGE_PATH
    )
    assert (layout.root / DEFAULT_SHORT_SPOKEN_REFERENCE_IMAGE_PATH).is_file()
    style = read_model(layout.style_profile, StyleProfile)
    assert style.visual_standard_id == "biblical-midcentury-gouache-v1"
    assert "guache" in style.name.casefold()
    assert manifest.config.captions.font_file == ("visual/fonts/files/Montserrat-ExtraBold.ttf")
    assert manifest.config.captions.margin_bottom == 310
    assert manifest.config.captions.vertical_position == "center"
    assert manifest.config.captions.context_labels_enabled is False
    assert manifest.config.captions.scripture_context_label == "TEXTO BÍBLICO"
    assert manifest.config.captions.reflection_context_label == "REFLEXÃO"
    assert manifest.config.captions.max_words == 5
    assert manifest.config.captions.layout_width_scale == 0.68
    assert manifest.config.captions.max_words_per_line == 3
    assert manifest.config.captions.highlight_card_radius == 24
    assert manifest.config.captions.word_arrival_animation is True


def test_short_can_explicitly_use_the_legacy_3d_thumbnail_profile(tmp_path) -> None:
    layout = create_project(
        "short-3d-regressao",
        "Short 3D",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        style_id="cinematic-family-3d-animation",
        project_date=date(2026, 7, 14),
    )

    manifest = layout.load_manifest()
    assert manifest.config.images.visual_standard_id == "cinematic-family-3d-v1"
    assert manifest.config.images.scene_planning_standard == "section_visual_direction_v1"
    assert manifest.config.images.composition_safety_mode == "caption_safe_area_v1"
    assert manifest.config.images.spoken_reference_image_mode == "generated"
    assert manifest.config.images.spoken_reference_image_path is None
    assert manifest.config.render.short_cover_enabled is True
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.youtube_growth.thumbnail_generation_mode == "integrated_imagegen_v1"
    assert manifest.config.youtube_growth.thumbnail_reference_mode == "opening_scene_edit_v1"
    assert manifest.config.audio.voice_id == DEFAULT_ELEVENLABS_VOICE_ID
    assert manifest.config.audio.model_id == DEFAULT_ELEVENLABS_MODEL_ID
    assert (layout.root / manifest.config.captions.font_file).is_file()
    assert manifest.config.production.mode == "final_candidate_one_shot"
    assert manifest.config.production.use_subagents is True
    assert manifest.config.production.image_workers == 3
    assert manifest.config.production.approval_policy == "final_only"
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.production.worker_context_policy == "compact_only"
    assert manifest.config.shorts_script.enabled is True
    assert manifest.config.shorts_script.use_specialist_subagent is True
    assert manifest.config.shorts_script.narrative_standard == "creative_retention_v3"
    assert manifest.config.shorts_script.require_verified_scripture is True
    assert manifest.config.shorts_script.min_words == 105
    assert manifest.config.shorts_script.max_words == 130
    assert manifest.config.shorts_script.require_comment_prompt is False
    assert manifest.config.shorts_script.require_subscribe_cta is False
    assert manifest.config.shorts_script.cta_max_words == 12
    assert manifest.config.shorts_script.cta_max_seconds == 4.0
    assert manifest.config.editorial_policy.require_quoted_comment_response is False
    assert manifest.config.captions.isolate_quoted_comment_response is False
    assert manifest.config.captions.comment_response_hold_seconds == 0.0
    assert manifest.config.audio.comment_response_silence_seconds == 0.0
    assert manifest.config.shorts_script.verse_visual_change_seconds == 5.5
    assert manifest.stages["shorts_script"].status == StageStatus.PENDING

    legacy_manifest = read_json(layout.manifest)
    legacy_manifest["config"]["captions"].pop("vertical_position")
    write_json(layout.manifest, legacy_manifest)
    assert layout.load_manifest().config.captions.vertical_position == "center"


def test_fifteen_second_short_uses_compact_duration_profile(tmp_path) -> None:
    layout = create_project(
        "micro-short",
        "Short de quinze segundos",
        projects_dir=tmp_path,
        target_duration_seconds=15,
        video_format="short",
        project_date=date(2026, 7, 20),
    )

    manifest = layout.load_manifest()
    config = manifest.config.shorts_script

    assert manifest.target_duration_seconds == 15
    assert config.duration_profile == "adaptive_compact_v1"
    assert config.compact_narrative_standard == "cinematic_jesus_devotional_v1"
    assert config.require_direct_jesus_speech is True
    assert config.require_emotional_interruption_hook is True
    assert config.require_reference_after_quote is True
    assert config.require_no_cta is True
    assert (config.min_words, config.max_words) == (41, 46)
    assert (config.retention_target_min_seconds, config.retention_target_max_seconds) == (14.1, 15)
    assert (
        config.scripture_citation_min_start_seconds,
        config.scripture_citation_max_end_seconds,
    ) == (3.5, 12.3)
    assert (config.hook_min_words, config.hook_max_words) == (10, 12)
    assert config.hook_target_seconds == 3.5
    assert config.estimated_narration_wpm == 170
    assert config.minimum_estimated_duration_ratio == 0.92
    assert config.minimum_audio_duration_ratio == 0.94
    assert config.maximum_narration_gap_seconds == 1.0
    assert config.minimum_hook_strength_score == 0.9
    assert config.minimum_message_value_score == 0.9
    assert config.minimum_rewatch_value_score == 0.88
    assert config.max_literal_verse_words == 28
    assert config.allow_contiguous_multi_verse_citations is True
    assert config.multi_verse_citation_policy == "when_necessary_or_beneficial_for_continuity"
    assert (config.minimum_sections, config.minimum_retention_beats) == (3, 3)
    assert (config.target_scene_min, config.target_scene_max) == (5, 6)
    assert manifest.config.music.library_profile == "adaptive_compact_devotional_v1"
    assert manifest.config.production.image_workers == 2
    assert config.generation_mode == "deterministic_compact_visual_plan_v1"
    assert config.biblical_review_mode == "deterministic_verified_compact_v1"
    assert config.require_independent_biblical_review is False
    assert manifest.config.youtube_growth.generation_mode == "compact_metadata_draft_v1"
    assert manifest.config.youtube_growth.validation_mode == "advisory_seo_v1"
    assert manifest.config.production.validation_policy == "completion_first_v1"
    assert manifest.config.production.token_budget_behavior == "fallback_then_continue"
    assert manifest.config.production.max_model_repairs_per_stage == 1
    briefing = read_model(layout.briefing, Briefing)
    assert briefing.creative_mode == "pastoral_encouragement"
    assert briefing.interpretive_stance == "devotional"
    assert briefing.hook_contract == "cinematic_interruption_v1"
    assert briefing.hook_family == "emotional_interruption"
    assert briefing.call_to_action == ""

    with pytest.raises(ValueError, match="pelo menos 30 segundos"):
        create_project(
            "longo-curto-demais",
            "Longo inválido",
            projects_dir=tmp_path,
            target_duration_seconds=15,
            video_format="landscape",
        )


def test_twenty_second_short_scales_literal_quote_limit_without_padding(tmp_path) -> None:
    layout = create_project(
        "micro-short-vinte",
        "Short de vinte segundos",
        projects_dir=tmp_path,
        target_duration_seconds=20,
        video_format="short",
        project_date=date(2026, 7, 20),
    )

    config = layout.load_manifest().config.shorts_script
    assert (config.min_words, config.max_words) == (55, 62)
    assert config.max_literal_verse_words == 47
    assert config.minimum_audio_duration_ratio == 0.94


def test_compact_short_can_persist_an_adaptive_duration_range(tmp_path) -> None:
    layout = create_project(
        "micro-short-faixa",
        "Short entre quinze e vinte segundos",
        projects_dir=tmp_path,
        target_duration_seconds=18,
        duration_min_seconds=15,
        duration_max_seconds=20,
        video_format="short",
        project_date=date(2026, 7, 21),
    )

    manifest = layout.load_manifest()

    assert manifest.duration_policy == "adaptive_range_v1"
    assert manifest.requested_duration_min_seconds == 15
    assert manifest.requested_duration_max_seconds == 20
    assert manifest.target_duration_seconds == 18
    assert manifest.effective_duration_seconds is None
