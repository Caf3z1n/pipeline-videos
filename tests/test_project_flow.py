from datetime import date
from pathlib import Path

import pytest

from pipeline_videos.models import ApprovalStatus, Briefing, StageStatus, StyleProfile
from pipeline_videos.project import create_project
from pipeline_videos.review import approve_briefing
from pipeline_videos.state import set_approval
from pipeline_videos.storage import read_json, read_model, resolve_project, write_json


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
    assert manifest.config.render.preview_crf == 16
    assert manifest.config.render.preview_preset == "fast"
    assert manifest.config.render.preview_motion_supersample == 2
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.production.worker_context_policy == "compact_only"
    assert manifest.config.youtube_growth.thumbnail_generation_mode == "integrated_imagegen_v1"
    assert manifest.config.youtube_growth.thumbnail_reference_mode == "opening_scene_edit_v1"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.youtube_growth.title_standard == "passage_pipe_topic_v1"
    assert manifest.config.youtube_growth.description_standard == "scripture_timeline_v1"
    assert manifest.config.youtube_growth.thumbnail_standard == "direct_conflict_red_v1"
    assert manifest.config.youtube_growth.title_thumbnail_pairing_standard == "complementary_v1"
    assert manifest.config.youtube_publication.allow_automated_private_review_upload is True
    assert manifest.config.editorial_policy.standard == "scripture_rich_ptbr_v1"
    assert manifest.config.editorial_policy.require_literal_scripture_citations is True
    assert manifest.config.editorial_policy.require_quoted_comment_response is True
    assert manifest.config.captions.vertical_position == "center"
    assert manifest.config.captions.isolate_quoted_comment_response is True
    assert manifest.config.captions.comment_response_hold_seconds == 1.0
    assert manifest.config.audio.comment_response_silence_seconds == 1.0
    assert manifest.config.render.transition_style == "cinematic_mix"
    assert manifest.config.render.motion_style == "smooth_pendulum"
    assert manifest.config.render.background_blur_profile == "functional_caption_focus_v1"
    assert manifest.config.render.background_music_path is None
    assert manifest.config.music.enabled is True
    assert manifest.config.music.selection_mode == "random"
    assert manifest.stages["music"].status == StageStatus.PENDING
    assert manifest.config.captions.font_family == "Montserrat ExtraBold"
    assert manifest.config.captions.variant == "active_card_v2"
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


def test_new_short_project_uses_vertical_render_and_safe_captions(tmp_path) -> None:
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
    assert (manifest.config.render.preview_width, manifest.config.render.preview_height) == (
        1080,
        1920,
    )
    assert manifest.config.render.final_crf == 12
    assert manifest.config.render.preview_crf == 16
    assert manifest.config.render.final_preset == "slow"
    assert manifest.config.render.preview_preset == "fast"
    assert manifest.config.render.transition_seconds == 0.38
    assert manifest.config.render.transition_style == "cinematic_mix"
    assert manifest.config.render.motion_style == "smooth_pendulum"
    assert manifest.config.render.motion_zoom_min == 1.02
    assert manifest.config.render.motion_zoom_max == 1.105
    assert manifest.config.render.motion_pan_strength == 0.32
    assert manifest.config.render.motion_supersample == 3
    assert manifest.config.render.preview_motion_supersample == 2
    assert manifest.config.render.background_music_path is None
    assert manifest.config.render.background_music_gain_db == -12.0
    assert manifest.config.render.short_cover_enabled is True
    assert manifest.config.render.short_cover_duration_seconds == pytest.approx(1 / 30)
    assert manifest.config.render.background_blur_profile == "functional_caption_focus_v1"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.youtube_growth.thumbnail_reference_mode == "opening_scene_edit_v1"
    assert manifest.config.images.height > manifest.config.images.width
    assert manifest.config.images.visual_standard_id == "cinematic-family-3d-v1"
    assert manifest.config.images.enforce_visual_standard is True
    style = read_model(layout.style_profile, StyleProfile)
    assert style.visual_standard_id == "cinematic-family-3d-v1"
    assert "Animação 3D Bíblica Cinematográfica" in style.name
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
    assert (layout.root / manifest.config.captions.font_file).is_file()
    assert manifest.config.production.mode == "draft_one_shot"
    assert manifest.config.production.use_subagents is True
    assert manifest.config.production.image_workers == 3
    assert manifest.config.production.approval_policy == "final_only"
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.production.worker_context_policy == "compact_only"
    assert manifest.config.shorts_script.enabled is True
    assert manifest.config.shorts_script.use_specialist_subagent is True
    assert manifest.config.shorts_script.narrative_standard == "human_hook_to_bible"
    assert manifest.config.shorts_script.require_verified_scripture is True
    assert "curta o vídeo" in manifest.config.shorts_script.final_subscribe_cta
    assert "compartilhe" in manifest.config.shorts_script.final_subscribe_cta
    assert manifest.config.shorts_script.verse_visual_change_seconds == 5.5
    assert manifest.stages["shorts_script"].status == StageStatus.PENDING

    legacy_manifest = read_json(layout.manifest)
    legacy_manifest["config"]["captions"].pop("vertical_position")
    write_json(layout.manifest, legacy_manifest)
    assert layout.load_manifest().config.captions.vertical_position == "center"
