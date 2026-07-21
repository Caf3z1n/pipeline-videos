from __future__ import annotations

import hashlib
import os
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type ElevenLabsVoiceVariant = Literal["jesus_1", "jesus_2"]

ELEVENLABS_VOICE_VARIANTS: dict[ElevenLabsVoiceVariant, str] = {
    "jesus_1": "qfOmqDJTQP75MDYJvxKc",
    "jesus_2": "rtrpEL7BeMd5aTru4HPq",
}
ELEVENLABS_VOICE_ENV_BY_VARIANT: dict[ElevenLabsVoiceVariant, str] = {
    "jesus_1": "ELEVENLABS_VOICE_JESUS_1_ID",
    "jesus_2": "ELEVENLABS_VOICE_JESUS_2_ID",
}
DEFAULT_ELEVENLABS_VOICE_VARIANT: ElevenLabsVoiceVariant = "jesus_1"
DEFAULT_ELEVENLABS_VOICE_ID = ELEVENLABS_VOICE_VARIANTS[DEFAULT_ELEVENLABS_VOICE_VARIANT]
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"


def normalize_elevenlabs_voice_variant(value: str) -> ElevenLabsVoiceVariant:
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized not in ELEVENLABS_VOICE_VARIANTS:
        choices = ", ".join(ELEVENLABS_VOICE_VARIANTS)
        raise ValueError(f"Variação de voz desconhecida: {value!r}. Use uma de: {choices}.")
    return cast(ElevenLabsVoiceVariant, normalized)


def elevenlabs_voice_id_for_variant(variant: ElevenLabsVoiceVariant) -> str:
    configured = os.getenv(ELEVENLABS_VOICE_ENV_BY_VARIANT[variant], "").strip()
    if configured:
        return configured
    if variant == DEFAULT_ELEVENLABS_VOICE_VARIANT:
        default_override = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        if default_override:
            return default_override
    return ELEVENLABS_VOICE_VARIANTS[variant]


def default_elevenlabs_voice_id() -> str:
    return elevenlabs_voice_id_for_variant(DEFAULT_ELEVENLABS_VOICE_VARIANT)


def default_elevenlabs_model_id() -> str:
    return os.getenv("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_ELEVENLABS_MODEL_ID


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


type ProductionMode = Literal["final_candidate_one_shot"]

type CreativeMode = Literal[
    "auto",
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
]

type InterpretiveStance = Literal[
    "factual",
    "expository",
    "analytical",
    "exploratory",
    "devotional",
]

type HookPolicy = Literal["auto", "required", "optional", "none"]

type HookContract = Literal[
    "mode_compatible_v1",
    "user_six_v1",
    "cinematic_interruption_v1",
]

type HookFamily = Literal[
    "auto",
    "none",
    "if_you_want_do_this",
    "what_happens_if_you_stop",
    "still_doing_losing",
    "nobody_talks_but",
    "want_to_learn_start",
    "never_do_this",
    "start_in_scene",
    "surprising_fact",
    "unanswered_question",
    "contradiction",
    "consequence_first",
    "quote_first",
    "emotional_interruption",
]

USER_SIX_HOOK_FAMILIES: tuple[HookFamily, ...] = (
    "if_you_want_do_this",
    "what_happens_if_you_stop",
    "still_doing_losing",
    "nobody_talks_but",
    "want_to_learn_start",
    "never_do_this",
)

MIN_SHORT_DURATION_SECONDS = 15
MIN_LONG_DURATION_SECONDS = 30
COMPACT_SHORT_THRESHOLD_SECONDS = 45

type ResolutionTiming = Literal["distributed", "late", "final_quarter"]

APPLICATION_REQUIRED_CREATIVE_MODES = frozenset(
    {"devotional_application", "pastoral_encouragement"}
)


class StageStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"
    SKIPPED = "skipped"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class StageRecord(PipelineModel):
    status: StageStatus = StageStatus.PENDING
    input_hash: str | None = None
    provider: str | None = None
    attempts: int = 0
    artifacts: list[str] = Field(default_factory=list)
    updated_at: str
    error: str | None = None
    usage: dict[str, int | float | str | None] = Field(default_factory=dict)
    note: str | None = None


class ApprovalRecord(PipelineModel):
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: str | None = None
    decided_by: str | None = None
    note: str | None = None
    artifact_hash: str | None = None


class RenderConfig(PipelineModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "libx264"
    final_crf: int = 12
    final_preset: str = "slow"
    audio_bitrate: str = "192k"
    transition_seconds: float = 0.38
    transition_style: Literal["fade_black", "cinematic_mix"] = "cinematic_mix"
    motion_strength: float = 0.00022
    motion_style: Literal["legacy", "smooth_pendulum"] = "smooth_pendulum"
    motion_zoom_min: float = Field(default=1.02, ge=1.0, le=1.2)
    motion_zoom_max: float = Field(default=1.105, ge=1.0, le=1.25)
    motion_pan_strength: float = Field(default=0.32, ge=0.0, le=1.0)
    motion_supersample: int = Field(default=3, ge=1, le=4)
    background_music_path: str | None = None
    audio_mix_standard: Literal[
        "legacy_gain_v1",
        "voice_music_loudness_v1",
    ] = "legacy_gain_v1"
    narration_target_lufs: float = Field(default=-18.0, ge=-30.0, le=-12.0)
    background_music_gap_lu: float = Field(default=13.0, ge=10.0, le=24.0)
    narration_true_peak_dbfs: float = Field(default=-2.0, ge=-9.0, le=-1.0)
    background_music_true_peak_dbfs: float = Field(default=-9.0, ge=-9.0, le=-1.0)
    audio_loudness_range_lu: float = Field(default=7.0, ge=1.0, le=20.0)
    narration_gain_db: float = Field(default=0.0, ge=-20.0, le=20.0)
    background_music_gain_db: float = Field(default=-12.0, ge=-60.0, le=0.0)
    background_music_fade_in_seconds: float = Field(default=2.0, ge=0.0, le=10.0)
    background_music_fade_out_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    short_cover_enabled: bool = True
    short_cover_duration_seconds: float = Field(default=1 / 30, ge=1 / 30, le=3.0)
    short_cover_transition_seconds: float = Field(default=0.0, ge=0.0, le=1.0)
    background_blur_profile: Literal["none", "functional_caption_focus_v1"] = "none"
    background_blur_opening_sigma: float = Field(default=8.0, ge=0.0, le=12.0)
    background_blur_scripture_sigma: float = Field(default=8.0, ge=0.0, le=12.0)
    background_blur_reflection_sigma: float = Field(default=8.0, ge=0.0, le=12.0)
    background_blur_payoff_sigma: float = Field(default=8.0, ge=0.0, le=12.0)
    background_blur_closing_sigma: float = Field(default=8.0, ge=0.0, le=12.0)
    caption_focus_blur_extra_sigma: float = Field(default=0.0, ge=0.0, le=6.0)
    caption_focus_top_ratio: float = Field(default=0.28, ge=0.0, le=0.8)
    caption_focus_height_ratio: float = Field(default=0.44, gt=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def migrate_removed_preview_settings(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for field in (
            "preview_width",
            "preview_height",
            "preview_crf",
            "preview_preset",
            "preview_motion_supersample",
        ):
            migrated.pop(field, None)
        return migrated

    @model_validator(mode="after")
    def validate_motion_zoom(self) -> RenderConfig:
        if self.motion_zoom_max < self.motion_zoom_min:
            raise ValueError("motion_zoom_max deve ser maior ou igual a motion_zoom_min")
        if self.caption_focus_top_ratio + self.caption_focus_height_ratio > 1.0:
            raise ValueError("A faixa de foco da legenda precisa caber no quadro.")
        return self


class CaptionStyle(PipelineModel):
    font_family: str = "Montserrat ExtraBold"
    font_file: str | None = "visual/fonts/files/Montserrat-ExtraBold.ttf"
    font_size: int = 70
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFFFFF"
    outline_color: str = "#101820"
    outline_width: int = 3
    shadow: int = 1
    margin_left: int = 150
    margin_right: int = 150
    margin_bottom: int = 95
    max_words: int = 5
    max_chars: int = 28
    max_lines: int = 2
    pause_break_seconds: float = 0.55
    uppercase: bool = True
    bold: bool = False
    font_scale_x: int = Field(default=88, ge=60, le=140)
    layout_width_scale: float = Field(default=0.68, ge=0.5, le=1.2)
    max_words_per_line: int | None = Field(default=3, ge=1, le=10)
    variant: Literal["classic", "active_card_v2"] = "active_card_v2"
    highlight_background_color: str | None = "#E5484D"
    highlight_card_padding_x: int = Field(default=16, ge=0, le=60)
    highlight_card_padding_y: int = Field(default=9, ge=0, le=40)
    highlight_card_radius: int = Field(default=24, ge=0, le=40)
    line_spacing: int = Field(default=8, ge=0, le=40)
    vertical_position: Literal["bottom", "center"] = "bottom"
    context_labels_enabled: bool = False
    scripture_context_label: str = "TEXTO BÍBLICO"
    reflection_context_label: str = "REFLEXÃO"
    context_label_margin_top: int = Field(default=210, ge=40, le=500)
    isolate_quoted_comment_response: bool = False
    comment_response_hold_seconds: float = Field(default=0.0, ge=0.0, le=3.0)
    word_arrival_animation: bool = True
    word_arrival_duration_ms: int = Field(default=120, ge=40, le=400)
    word_arrival_rise_px: int = Field(default=22, ge=0, le=100)


class AudioConfig(PipelineModel):
    provider: Literal["mock", "elevenlabs", "local_manual"] = "elevenlabs"
    synthesis_segmentation: Literal[
        "per_scene_v1",
        "continuous_section_v1",
        "continuous_narration_v1",
    ] = "per_scene_v1"
    voice_id: str | None = Field(default_factory=default_elevenlabs_voice_id)
    model_id: str = Field(default_factory=default_elevenlabs_model_id)
    output_format: str = "mp3_44100_128"
    language_code: str = "pt"
    stability: float = 0.55
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True
    sample_rate: int = 48000
    comment_response_silence_seconds: float = Field(default=0.0, ge=0.0, le=3.0)

    @field_validator("provider", mode="before")
    @classmethod
    def migrate_legacy_sapi_provider(cls, value: object) -> object:
        return "elevenlabs" if value == "sapi" else value


class ImageConfig(PipelineModel):
    provider: Literal["codex", "fixture"] = "codex"
    width: int = 1536
    height: int = 1024
    extension: str = ".png"
    visual_standard_id: str = "cinematic-family-3d-v1"
    enforce_visual_standard: bool = True
    reject_panel_like_images: bool = True
    scene_planning_standard: Literal[
        "section_visual_direction_v1",
        "narration_block_visual_v1",
    ] = "section_visual_direction_v1"
    composition_safety_mode: Literal[
        "caption_safe_area_v1",
        "unrestricted_scene_v1",
    ] = "caption_safe_area_v1"
    spoken_reference_image_mode: Literal[
        "generated",
        "fixed_profile_asset_v1",
    ] = "generated"
    spoken_reference_image_path: str | None = None

    @model_validator(mode="after")
    def validate_spoken_reference_image(self) -> ImageConfig:
        if self.spoken_reference_image_mode == "fixed_profile_asset_v1":
            path = (self.spoken_reference_image_path or "").strip().replace("\\", "/")
            if not path:
                raise ValueError("A imagem fixa da referência falada precisa declarar um caminho")
            if path.startswith("/") or ".." in path.split("/") or ":" in path.split("/", 1)[0]:
                raise ValueError("O caminho da imagem fixa precisa ser relativo e seguro")
        return self


class MusicConfig(PipelineModel):
    enabled: bool = True
    selection_mode: Literal["random"] = "random"
    library_profile: Literal[
        "default",
        "adaptive_compact_devotional_v1",
    ] = "default"


class YouTubeGrowthConfig(PipelineModel):
    enabled: bool = True
    use_specialist_subagent: bool = True
    specialist_role: Literal["youtube_growth_specialist"] = "youtube_growth_specialist"
    generation_mode: Literal[
        "specialist_full_package_v1",
        "compact_metadata_draft_v1",
    ] = "specialist_full_package_v1"
    validation_mode: Literal[
        "strict_v1",
        "advisory_seo_v1",
    ] = "strict_v1"
    title_variants: int = Field(default=1, ge=1, le=3)
    thumbnail_variants: int = Field(default=1, ge=0, le=3)
    thumbnail_generation_mode: Literal[
        "integrated_imagegen_v1",
        "theme_prompt_imagegen_v1",
        "youtube_auto_frame_v1",
    ] = "integrated_imagegen_v1"
    thumbnail_reference_mode: Literal[
        "opening_scene_edit_v1",
        "theme_only_v1",
        "none_v1",
    ] = "opening_scene_edit_v1"
    title_standard: Literal[
        "passage_pipe_topic_v1",
        "tension_pipe_passage_v2",
        "creative_pipe_passage_v3",
    ] = "passage_pipe_topic_v1"
    description_standard: Literal[
        "scripture_timeline_v1",
        "search_first_unique_v2",
    ] = "scripture_timeline_v1"
    thumbnail_standard: Literal[
        "direct_conflict_red_v1",
        "biblical_cinematic_landscape_v1",
        "youtube_auto_frame_v1",
    ] = "direct_conflict_red_v1"
    thumbnail_typography_standard: Literal[
        "integrated_extruded_3d_v1",
        "no_text_v1",
    ] = "integrated_extruded_3d_v1"
    title_thumbnail_pairing_standard: Literal["complementary_v1"] = "complementary_v1"


class EditorialPolicyConfig(PipelineModel):
    standard: Literal["scripture_rich_ptbr_v1"] = "scripture_rich_ptbr_v1"
    forbid_english_editorial_jargon: bool = False
    forbid_generic_text_reference: bool = False
    require_literal_scripture_citations: bool = False
    minimum_scripture_citations_per_minute: float = Field(default=0.8, ge=0.1, le=2.0)
    maximum_scripture_citations_per_minute: float = Field(default=1.2, ge=0.2, le=3.0)
    require_quoted_comment_response: bool = False

    @model_validator(mode="after")
    def validate_citation_range(self) -> EditorialPolicyConfig:
        if (
            self.maximum_scripture_citations_per_minute
            < self.minimum_scripture_citations_per_minute
        ):
            raise ValueError(
                "maximum_scripture_citations_per_minute deve ser maior ou igual ao mínimo"
            )
        return self


class LongFormScriptConfig(PipelineModel):
    enabled: bool = False
    narrative_standard: Literal[
        "legacy",
        "retention_story_long_v1",
        "creative_retention_long_v2",
    ] = "legacy"
    estimated_narration_wpm: int = Field(default=145, ge=120, le=180)
    minimum_duration_ratio: float = Field(default=0.85, ge=0.7, le=1.0)
    maximum_duration_ratio: float = Field(default=1.12, ge=1.0, le=1.35)
    opening_hook_max_words: int = Field(default=38, ge=12, le=60)
    opening_target_max_seconds: float = Field(default=20.0, ge=8.0, le=35.0)
    opening_value_max_seconds: float = Field(default=40.0, ge=20.0, le=75.0)
    first_scripture_citation_max_seconds: float = Field(default=75.0, ge=30.0, le=120.0)
    minimum_scripture_citation_gap_seconds: float = Field(default=25.0, ge=0.0, le=60.0)
    maximum_scripture_citation_gap_seconds: float = Field(default=105.0, ge=60.0, le=180.0)
    final_scripture_citation_max_distance_seconds: float = Field(default=120.0, ge=45.0, le=240.0)
    maximum_reengagement_gap_seconds: float = Field(default=120.0, ge=60.0, le=180.0)
    payoff_start_minimum_ratio: float = Field(default=0.75, ge=0.6, le=0.9)
    cta_max_words: int = Field(default=24, ge=8, le=40)
    cta_max_seconds: float = Field(default=10.0, ge=4.0, le=15.0)
    require_callback: bool = True
    require_visual_direction: bool = True

    @model_validator(mode="after")
    def validate_ranges(self) -> LongFormScriptConfig:
        if self.maximum_duration_ratio < self.minimum_duration_ratio:
            raise ValueError("maximum_duration_ratio deve ser maior ou igual ao mínimo")
        if (
            self.maximum_scripture_citation_gap_seconds
            <= self.minimum_scripture_citation_gap_seconds
        ):
            raise ValueError(
                "maximum_scripture_citation_gap_seconds deve ser maior que o intervalo mínimo"
            )
        return self


class YouTubePublicationConfig(PipelineModel):
    category_id: str = "22"
    default_language: str = "pt-BR"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    upload_captions: bool = True
    notify_subscribers: bool = False
    allow_automated_private_review_upload: bool = True
    allow_automated_scheduled_release: bool = False


class ShortsScriptConfig(PipelineModel):
    enabled: bool = True
    use_specialist_subagent: bool = True
    specialist_role: Literal["shorts_script_specialist"] = "shorts_script_specialist"
    require_independent_biblical_review: bool = False
    generation_mode: Literal[
        "specialist_full_package_v1",
        "deterministic_compact_visual_plan_v1",
    ] = "specialist_full_package_v1"
    biblical_review_mode: Literal[
        "independent_model_v1",
        "deterministic_verified_compact_v1",
    ] = "independent_model_v1"
    duration_profile: Literal["standard_45_55_v1", "adaptive_compact_v1"] = "standard_45_55_v1"
    compact_narrative_standard: Literal["cinematic_jesus_devotional_v1"] | None = None
    require_direct_jesus_speech: bool = False
    require_emotional_interruption_hook: bool = False
    require_reference_after_quote: bool = False
    require_no_cta: bool = False
    min_words: int = Field(default=105, ge=15, le=180)
    max_words: int = Field(default=130, ge=28, le=200)
    minimum_estimated_duration_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_audio_duration_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_narration_gap_seconds: float = Field(default=5.0, ge=0.25, le=10.0)
    maximum_terminal_silence_seconds: float = Field(default=3.0, ge=0.1, le=5.0)
    minimum_hook_strength_score: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_message_value_score: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_rewatch_value_score: float = Field(default=0.0, ge=0.0, le=1.0)
    hook_min_words: int = Field(default=2, ge=1, le=16)
    hook_max_words: int = Field(default=12, ge=4, le=24)
    hook_target_seconds: float = Field(default=2.0, ge=1.0, le=4.0)
    narrative_standard: Literal[
        "creative_retention_v3",
        "retention_story_v2",
        "human_hook_to_bible",
        "verse_first",
    ] = "retention_story_v2"
    require_literal_verse_opening: bool = False
    require_verified_scripture: bool = True
    verse_opening_max_seconds: float = Field(default=12.0, ge=4.0, le=15.0)
    scripture_citation_min_start_seconds: float = Field(default=8.0, ge=0.0, le=15.0)
    scripture_citation_max_end_seconds: float = Field(default=20.0, ge=6.0, le=30.0)
    verse_visual_change_seconds: float = Field(default=5.5, ge=2.5, le=8.0)
    max_literal_verse_words: int = Field(default=40, ge=8, le=80)
    allow_contiguous_multi_verse_citations: bool = True
    multi_verse_citation_policy: Literal["when_necessary_or_beneficial_for_continuity"] = (
        "when_necessary_or_beneficial_for_continuity"
    )
    minimum_retention_beats: int = Field(default=5, ge=3, le=10)
    minimum_progression_beats: int = Field(default=2, ge=1, le=5)
    minimum_sections: int = Field(default=6, ge=3, le=10)
    target_scene_min: int = Field(default=8, ge=3, le=12)
    target_scene_max: int = Field(default=10, ge=3, le=14)
    retention_target_min_seconds: float = Field(default=45.0, ge=10.0, le=60.0)
    retention_target_max_seconds: float = Field(default=55.0, ge=12.0, le=60.0)
    require_comment_prompt: bool = False
    comment_response_max_words: int = Field(default=8, ge=1, le=12)
    require_subscribe_cta: bool = False
    cta_max_words: int = Field(default=12, ge=4, le=16)
    cta_max_seconds: float = Field(default=4.0, ge=2.0, le=6.0)
    estimated_narration_wpm: int = Field(default=145, ge=120, le=180)
    duration_tolerance_seconds: float = Field(default=2.0, ge=0.0, le=5.0)
    final_subscribe_cta: Literal[
        "E se quiser fazer parte dessa comunidade, se inscreve no canal.",
        (
            "Se essa mensagem falou com você, curta o vídeo, se inscreva no canal e "
            "compartilhe com alguém que precisa ouvir isso."
        ),
    ] = (
        "Se essa mensagem falou com você, curta o vídeo, se inscreva no canal e "
        "compartilhe com alguém que precisa ouvir isso."
    )

    @model_validator(mode="after")
    def validate_word_range(self) -> ShortsScriptConfig:
        if self.max_words < self.min_words:
            raise ValueError("max_words deve ser maior ou igual a min_words")
        if self.hook_max_words < self.hook_min_words:
            raise ValueError("hook_max_words deve ser maior ou igual a hook_min_words")
        if self.retention_target_max_seconds < self.retention_target_min_seconds:
            raise ValueError("retention_target_max_seconds deve ser maior ou igual ao mínimo")
        if self.target_scene_max < self.target_scene_min:
            raise ValueError("target_scene_max deve ser maior ou igual a target_scene_min")
        if self.scripture_citation_max_end_seconds <= self.scripture_citation_min_start_seconds:
            raise ValueError(
                "scripture_citation_max_end_seconds deve ser posterior ao início mínimo"
            )
        return self


class ProductionConfig(PipelineModel):
    mode: ProductionMode = "final_candidate_one_shot"
    use_subagents: bool = True
    image_workers: int = Field(default=3, ge=1, le=8)
    approval_policy: Literal["final_only"] = "final_only"
    optimization_profile: Literal["token_optimized_v1"] = "token_optimized_v1"
    worker_context_policy: Literal["compact_only"] = "compact_only"
    coordinator_effective_token_budget: int = Field(default=120_000, ge=10_000)
    total_effective_token_budget: int = Field(default=550_000, ge=50_000)
    validation_policy: Literal["strict_v1", "completion_first_v1"] = "strict_v1"
    repair_policy: Literal["bounded_then_fallback_v1"] = "bounded_then_fallback_v1"
    token_budget_behavior: Literal["audit_only", "fallback_then_continue"] = "audit_only"
    max_model_repairs_per_stage: int = Field(default=1, ge=0, le=3)

    @model_validator(mode="before")
    @classmethod
    def migrate_draft_mode_name(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("mode") != "draft_one_shot":
            return value
        migrated = dict(value)
        migrated["mode"] = "final_candidate_one_shot"
        return migrated


class ProjectConfig(PipelineModel):
    render: RenderConfig = Field(default_factory=RenderConfig)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    images: ImageConfig = Field(default_factory=ImageConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    shorts_script: ShortsScriptConfig = Field(default_factory=ShortsScriptConfig)
    long_form_script: LongFormScriptConfig = Field(default_factory=LongFormScriptConfig)
    youtube_growth: YouTubeGrowthConfig = Field(default_factory=YouTubeGrowthConfig)
    youtube_publication: YouTubePublicationConfig = Field(default_factory=YouTubePublicationConfig)
    production: ProductionConfig = Field(default_factory=ProductionConfig)
    editorial_policy: EditorialPolicyConfig = Field(default_factory=EditorialPolicyConfig)


class ProjectManifest(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: str
    aliases: list[str] = Field(default_factory=list)
    title: str
    created_at: str
    updated_at: str
    root_hint: str = "."
    target_duration_seconds: int = 540
    duration_policy: Literal["exact_target_v1", "adaptive_range_v1"] = "exact_target_v1"
    requested_duration_min_seconds: float | None = Field(default=None, ge=1.0)
    requested_duration_max_seconds: float | None = Field(default=None, ge=1.0)
    effective_duration_seconds: float | None = Field(default=None, ge=0.0)
    demo: bool = False
    video_format: Literal["landscape", "short"] = "landscape"
    config: ProjectConfig = Field(default_factory=ProjectConfig)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_duration_contract(self) -> ProjectManifest:
        minimum = self.requested_duration_min_seconds
        maximum = self.requested_duration_max_seconds
        if self.duration_policy == "adaptive_range_v1":
            if minimum is None or maximum is None:
                raise ValueError("A duração adaptativa exige limites mínimo e máximo")
            if maximum < minimum:
                raise ValueError("O limite máximo de duração deve ser maior ou igual ao mínimo")
            if not minimum <= self.target_duration_seconds <= maximum:
                raise ValueError(
                    "A duração preferencial precisa permanecer dentro da faixa solicitada"
                )
        if (
            self.effective_duration_seconds is not None
            and self.duration_policy == "adaptive_range_v1"
        ):
            assert minimum is not None and maximum is not None
            if not minimum <= self.effective_duration_seconds <= maximum:
                raise ValueError("A duração efetiva precisa permanecer dentro da faixa solicitada")
        return self


class Briefing(PipelineModel):
    title: str
    main_passage: str = ""
    complementary_references: list[str] = Field(default_factory=list)
    central_theme: str = ""
    practical_application: str = ""
    audience: str = ""
    emotional_intent: str = ""
    narration_tone: str = "reflexivo, acolhedor e cinematográfico"
    target_duration_seconds: int = 540
    bible_translation: str | None = None
    literal_quote_policy: str = (
        "Citações literais devem informar tradução e fonte; preferir paráfrase quando os direitos "
        "não estiverem confirmados."
    )
    interpretation_limits: list[str] = Field(default_factory=list)
    claims_to_avoid: list[str] = Field(default_factory=list)
    visual_notes: list[str] = Field(default_factory=list)
    call_to_action: str = ""
    retention_archetype: (
        Literal[
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ]
        | None
    ) = None
    human_conflict: str = ""
    central_question: str = ""
    visual_progression: list[str] = Field(default_factory=list)
    possible_reveal: str = ""
    title_seed: str = ""
    creative_mode: CreativeMode = "auto"
    interpretive_stance: InterpretiveStance = "expository"
    hook_policy: HookPolicy = "auto"
    hook_family: HookFamily = "auto"
    hook_contract: HookContract = "mode_compatible_v1"
    viewer_outcome: str = ""
    creative_rationale: str = ""

    @model_validator(mode="after")
    def validate_hook_contract(self) -> Briefing:
        if self.hook_contract == "user_six_v1" and self.hook_family not in {
            "auto",
            "none",
            *USER_SIX_HOOK_FAMILIES,
        }:
            raise ValueError(
                "hook_contract=user_six_v1 aceita somente uma das seis famílias oficiais."
            )
        if self.hook_contract == "cinematic_interruption_v1" and self.hook_family not in {
            "auto",
            "emotional_interruption",
        }:
            raise ValueError(
                "hook_contract=cinematic_interruption_v1 exige emotional_interruption."
            )
        return self


class SourceEntry(PipelineModel):
    source_id: str
    kind: Literal[
        "biblical_text", "historical_context", "commentary", "rights", "channel_reference"
    ]
    title: str
    passage: str | None = None
    context_passage: str | None = None
    translation: str | None = None
    author_or_publisher: str | None = None
    url: str | None = None
    accessed_at: str | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    use_in_video: str
    claim_scope: str
    rights_note: str | None = None
    verified: bool = False
    spoken_by: Literal["jesus", "other", "unspecified"] = "unspecified"
    compact_hook: str = ""
    compact_hook_strength_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compact_message_value_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compact_rewatch_value_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def seal_excerpt_snapshot(self) -> SourceEntry:
        if self.excerpt is None:
            return self
        digest = hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()
        if self.content_hash is not None and self.content_hash != digest:
            raise ValueError("content_hash não corresponde ao excerpt bíblico armazenado.")
        if self.content_hash is None:
            object.__setattr__(self, "content_hash", digest)
        return self


class SourceLibrary(PipelineModel):
    entries: list[SourceEntry] = Field(default_factory=list)


class RelatedBiblicalPassage(PipelineModel):
    passage_id: str
    reference: str
    context_reference: str
    verse_text: str
    source_url: str
    role: Literal[
        "story_context",
        "parallel_account",
        "character_background",
        "character_development",
        "character_outcome",
        "direct_speech",
    ]
    spoken_by: Literal["jesus", "other", "unspecified"] = "unspecified"


class VerseCandidate(PipelineModel):
    candidate_id: str
    source_id: str
    reference: str
    context_reference: str
    book: str
    testament: Literal["old", "new"]
    translation: str
    verse_text: str
    source_url: str
    accessed_at: str
    rights_note: str
    title: str
    slug: str
    central_theme: str
    practical_application: str
    audience: str = "pessoas que buscam uma mensagem bíblica curta e aplicável"
    emotional_intent: str = "acolher, esclarecer e encorajar sem simplificar a dor"
    narration_tone: str = "pastoral, natural, próximo, sereno, direto e fácil de entender"
    themes: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    character_aliases: list[str] = Field(default_factory=list)
    related_passages: list[RelatedBiblicalPassage] = Field(default_factory=list)
    preferred_development_modes: list[
        Literal[
            "context",
            "biblical_story",
            "biblical_metaphor",
            "cross_reference",
            "promise_limits",
        ]
    ] = Field(default_factory=list)
    interpretation_limits: list[str] = Field(default_factory=list)
    claims_to_avoid: list[str] = Field(default_factory=list)
    visual_notes: list[str] = Field(default_factory=list)
    retention_archetypes: list[
        Literal[
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ]
    ] = Field(
        default_factory=lambda: [
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ]
    )
    human_conflict: str = ""
    central_question: str = ""
    visual_progression: list[str] = Field(default_factory=list)
    possible_reveal: str = ""
    title_seed: str = ""
    retention_potential_score: float = Field(default=0.5, ge=0.0, le=1.0)
    selection_weight: float = 1.0
    familiarity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    clarity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    catalog_segment: Literal[
        "legacy",
        "compact_jesus",
        "general",
        "long_form_anchor",
        "narrative_story",
    ] = "legacy"
    recommended_formats: list[Literal["short", "long"]] = Field(
        default_factory=lambda: ["short", "long"]
    )
    source_text_scope: Literal[
        "verse",
        "verse_range",
        "direct_speech_excerpt",
        "pericope_excerpt",
    ] = "verse"
    spoken_by: Literal["jesus", "other", "unspecified"] = "unspecified"
    compact_hook: str = ""
    compact_hook_strength_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compact_message_value_score: float = Field(default=0.0, ge=0.0, le=1.0)
    compact_rewatch_value_score: float = Field(default=0.0, ge=0.0, le=1.0)


class VerseCatalog(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    catalog_id: str
    translation: str
    publisher: str
    source_url: str
    rights_note: str
    accessed_at: str
    source_artifact_url: str = ""
    source_artifact_sha256: str = ""
    source_revision: str = ""
    candidates: list[VerseCandidate]


class VerseUsageRecord(PipelineModel):
    # The shared Git registry is read and written by independently deployed
    # desktop and VPS releases. Preserve additive fields from newer writers so
    # an older reader can merge the registry without failing or losing data.
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    registry_id: str | None = None
    environment: str | None = None
    project_id: str
    project_path: str
    video_format: Literal["landscape", "short"]
    status: Literal["reserved", "used"]
    references: list[str] = Field(default_factory=list)
    supporting_references: list[str] = Field(default_factory=list)
    normalized_references: list[str] = Field(default_factory=list)
    main_passage: str | None = None
    topic: str | None = None
    normalized_topic: str | None = None
    themes: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    retention_archetype: (
        Literal[
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ]
        | None
    ) = None
    hook_family: str | None = None
    creative_mode: CreativeMode | None = None
    engagement_mode: Literal["comment", "subscribe", "share"] | None = None
    created_at: str
    updated_at: str | None = None


class VerseUsageIndex(PipelineModel):
    # Keep the shared envelope forward-compatible for additive metadata too.
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    schema_version: Literal["1.0"] = "1.0"
    generated_at: str
    records: list[VerseUsageRecord] = Field(default_factory=list)


class LongFormRetentionBeat(PipelineModel):
    beat_id: str
    section_id: str
    functions: list[
        Literal[
            "hook",
            "orientation",
            "progression",
            "evidence",
            "context",
            "analysis",
            "comparison",
            "reengagement",
            "contemplation",
            "reveal",
            "payoff",
            "resolution",
            "synthesis",
            "application",
            "callback",
            "cta",
        ]
    ] = Field(min_length=1)
    viewer_value: str
    new_information: str
    target_start_seconds: float = Field(ge=0.0)
    target_end_seconds: float = Field(gt=0.0)
    visual_progression: str

    @model_validator(mode="after")
    def validate_timing_and_value(self) -> LongFormRetentionBeat:
        if self.target_end_seconds <= self.target_start_seconds:
            raise ValueError("O fim do beat longo deve ser posterior ao início")
        if not all(
            value.strip()
            for value in (self.beat_id, self.section_id, self.viewer_value, self.new_information)
        ):
            raise ValueError("Cada beat longo precisa acrescentar valor e informação")
        if not self.visual_progression.strip():
            raise ValueError("Cada beat longo precisa declarar progressão visual")
        return self


class LongFormScriptureAnchor(PipelineModel):
    source_id: str
    reference: str
    section_id: str
    target_start_seconds: float = Field(ge=0.0)
    narrative_purpose: str


class LongFormEngagementPlan(PipelineModel):
    action: Literal["comment", "subscribe", "share"]
    spoken_text: str
    payoff_connection: str


class LongFormScriptStrategy(PipelineModel):
    standard: Literal[
        "retention_story_long_v1",
        "creative_retention_long_v2",
    ] = "retention_story_long_v1"
    archetype: (
        Literal[
            "conflict_first",
            "counterintuitive_reveal",
            "stair_step_progression",
        ]
        | None
    ) = None
    target_viewer: str
    viewer_need: str
    content_promise: str
    central_question: str = ""
    opening_hook: str = ""
    opening_conflict: str = ""
    opening_line: str = ""
    creative_mode: CreativeMode = "auto"
    interpretive_stance: InterpretiveStance = "expository"
    hook_policy: HookPolicy = "auto"
    hook_family: HookFamily = "auto"
    hook_rationale: str = ""
    resolution_timing: ResolutionTiming = "late"
    progression: list[LongFormRetentionBeat] = Field(min_length=3)
    scripture_anchors: list[LongFormScriptureAnchor] = Field(min_length=1)
    payoff: str = ""
    resolution: str = ""
    viewer_takeaway: str = ""
    practical_application: str = ""
    callback: str = ""
    engagement: LongFormEngagementPlan
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_story_contract(self) -> LongFormScriptStrategy:
        common_required = (
            self.target_viewer,
            self.viewer_need,
            self.content_promise,
            self.engagement.spoken_text,
            self.engagement.payoff_connection,
        )
        if self.standard == "retention_story_long_v1":
            if self.archetype is None:
                raise ValueError("O contrato longo v1 precisa declarar um arquétipo de retenção")
            required = (
                *common_required,
                self.central_question,
                self.opening_hook,
                self.opening_conflict,
                self.payoff,
                self.practical_application,
                self.callback,
            )
        else:
            required = (
                *common_required,
                self.opening_line,
                self.resolution,
                self.viewer_takeaway,
                self.hook_rationale,
            )
        if not all(value.strip() for value in required):
            raise ValueError("A estratégia longa não pode ter movimentos narrativos vazios")
        if self.standard == "creative_retention_long_v2":
            if self.creative_mode == "auto":
                raise ValueError("A estratégia longa precisa resolver creative_mode")
            if self.hook_policy == "auto" or self.hook_family == "auto":
                raise ValueError("A estratégia longa precisa resolver política e família do hook")
            if self.hook_policy == "required" and self.hook_family == "none":
                raise ValueError("hook_policy=required exige uma família de hook")
            if self.hook_policy == "none" and self.hook_family != "none":
                raise ValueError("hook_policy=none exige hook_family=none")
            if self.hook_family == "none" and self.opening_hook.strip():
                raise ValueError("Uma abertura sem hook não deve preencher opening_hook")
            if (
                self.hook_family != "none"
                and self.opening_hook.strip() != self.opening_line.strip()
            ):
                raise ValueError("O hook usado precisa copiar opening_line")
            if (
                self.creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES
                and not self.practical_application.strip()
            ):
                raise ValueError("O modo devocional precisa declarar aplicação prática")
        beat_ids = [beat.beat_id for beat in self.progression]
        if len(set(beat_ids)) != len(beat_ids):
            raise ValueError("beat_id precisa ser único na estratégia longa")
        section_ids = [beat.section_id for beat in self.progression]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("Cada seção precisa ter exatamente um beat na estratégia longa")
        functions = {function for beat in self.progression for function in beat.functions}
        if self.standard == "retention_story_long_v1":
            required_functions = {"hook", "progression", "payoff", "application", "cta"}
            if not required_functions.issubset(functions):
                raise ValueError(
                    "A estratégia longa precisa declarar gancho, progressão, payoff, "
                    "aplicação e CTA"
                )
        else:
            development_functions = {
                "progression",
                "evidence",
                "context",
                "analysis",
                "comparison",
                "reveal",
            }
            if not functions.intersection(development_functions):
                raise ValueError("A estratégia criativa precisa desenvolver informação nova")
            if not {"resolution", "cta"}.issubset(functions):
                raise ValueError("A estratégia criativa precisa declarar resolução e CTA")
            if self.hook_family == "none" and "hook" in functions:
                raise ValueError("A abertura escolhida sem hook não pode marcar função hook")
            if self.hook_family != "none" and "hook" not in functions:
                raise ValueError("A estratégia declarou hook, mas nenhum beat cumpre essa função")
            if self.practical_application.strip() and "application" not in functions:
                raise ValueError("A aplicação declarada precisa de um beat de application")
        return self


class ScriptSection(PipelineModel):
    section_id: str
    role: Literal["opening", "biblical_narrative", "reflection", "application", "closing"]
    title: str
    narration: str
    biblical_basis: list[str] = Field(default_factory=list)
    claim_classification: list[
        Literal["biblical_text", "verified_context", "interpretation", "creative_elaboration"]
    ] = Field(default_factory=list)
    retention_goal: str | None = None
    visual_direction: str | None = None
    on_screen_emphasis: str | None = None


class ScriptDocument(PipelineModel):
    version: int = 1
    language: Literal["pt-BR"] = "pt-BR"
    title: str
    sections: list[ScriptSection]
    word_count: int = 0
    estimated_duration_seconds: float = 0
    source_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    long_form_strategy: LongFormScriptStrategy | None = None

    def narration_text(self) -> str:
        return "\n\n".join(section.narration.strip() for section in self.sections).strip()


class CreativePacingProfile(PipelineModel):
    profile_id: Literal[
        "balanced_long_v1",
        "long_retention_v1",
        "long_creative_v2",
        "short_v1",
        "short_retention_v2",
        "short_creative_v3",
        "short_compact_v1",
    ]
    default_scene_seconds: float = Field(gt=0.0)
    opening_scene_seconds: float = Field(gt=0.0)
    reengagement_scene_seconds: float = Field(gt=0.0)
    contemplative_scene_seconds: float = Field(gt=0.0)
    payoff_scene_seconds: float = Field(gt=0.0)


class CreativeBeat(PipelineModel):
    section_id: str
    functions: list[
        Literal[
            "hook",
            "orientation",
            "progression",
            "evidence",
            "context",
            "analysis",
            "comparison",
            "reengagement",
            "contemplation",
            "reveal",
            "payoff",
            "resolution",
            "synthesis",
            "application",
            "callback",
            "cta",
        ]
    ]
    viewer_value: str
    target_start_seconds: float = Field(ge=0.0)
    target_end_seconds: float = Field(gt=0.0)
    critical: bool = False

    @model_validator(mode="after")
    def validate_timing(self) -> CreativeBeat:
        if self.target_end_seconds <= self.target_start_seconds:
            raise ValueError("target_end_seconds deve ser posterior a target_start_seconds")
        if not self.functions:
            raise ValueError("Cada beat criativo precisa declarar ao menos uma função")
        if not self.viewer_value.strip():
            raise ValueError("Cada beat criativo precisa declarar valor para o público")
        return self


class CreativeContract(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    algorithm: Literal["creative-contract-v1", "creative-contract-v2"] = "creative-contract-v1"
    input_hash: str
    format: Literal["long_form", "short"]
    target_viewer: str
    content_promise: str
    opening_hook: str
    payoff: str
    practical_application: str
    main_passage: str
    narrative_standard: Literal[
        "legacy",
        "retention_story_long_v1",
        "creative_retention_long_v2",
        "creative_retention_v3",
        "retention_story_v2",
        "human_hook_to_bible",
        "verse_first",
    ] = "legacy"
    central_question: str = ""
    callback: str = ""
    creative_mode: CreativeMode = "auto"
    interpretive_stance: InterpretiveStance = "expository"
    hook_family: HookFamily = "auto"
    hook_used: bool = True
    resolution: str = ""
    viewer_takeaway: str = ""
    engagement_action: Literal["comment", "subscribe", "share"] | None = None
    scripture_citation_timestamps: list[float] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str]
    claims_to_avoid: list[str] = Field(default_factory=list)
    beats: list[CreativeBeat]
    critical_component_ids: list[str]
    pacing: CreativePacingProfile

    @model_validator(mode="after")
    def validate_critical_components(self) -> CreativeContract:
        if not all(
            value.strip() for value in (self.target_viewer, self.content_promise, self.payoff)
        ):
            raise ValueError("Público, promessa e entrega final não podem ficar vazios")
        if self.hook_used and not self.opening_hook.strip():
            raise ValueError("Um contrato com hook precisa preservar a fala de abertura")
        beat_section_ids = [beat.section_id for beat in self.beats]
        if not self.section_ids or beat_section_ids != self.section_ids:
            raise ValueError("O contrato criativo precisa cobrir cada seção exatamente uma vez")
        if len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("section_id precisa ser único no contrato criativo")
        expected_critical = [beat.section_id for beat in self.beats if beat.critical]
        if self.critical_component_ids != expected_critical:
            raise ValueError("A lista de componentes críticos não corresponde aos beats")
        functions = {function for beat in self.beats for function in beat.functions}
        if self.algorithm == "creative-contract-v1":
            if not {"hook", "payoff"}.issubset(functions):
                raise ValueError("O contrato criativo precisa declarar gancho e payoff")
        else:
            if "resolution" not in functions:
                raise ValueError("O contrato criativo precisa declarar resolução")
            if self.hook_used and "hook" not in functions:
                raise ValueError("O contrato criativo marcou hook sem beat correspondente")
        if self.format == "long_form" and len(self.beats) >= 5:
            reengagements = sum("reengagement" in beat.functions for beat in self.beats)
            if reengagements < 2:
                raise ValueError("O vídeo longo precisa de dois pontos de reengajamento")
        return self


class ReviewFinding(PipelineModel):
    finding_id: str
    severity: Literal["info", "warning", "blocking"]
    category: Literal[
        "citation", "context", "interpretation", "creative_elaboration", "rights", "reference"
    ]
    statement: str
    evidence: str
    recommendation: str | None = None
    resolved: bool = False


class BiblicalReview(PipelineModel):
    script_version: int
    reviewed_at: str
    reviewer: str
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    approved: bool = False
    approval_note: str | None = None

    @property
    def has_open_blockers(self) -> bool:
        return any(item.severity == "blocking" and not item.resolved for item in self.findings)


class StyleProfile(PipelineModel):
    style_id: str
    name: str
    medium: str
    principles: list[str]
    negative_constraints: list[str]
    visual_standard_id: str | None = None
    reference_note: str | None = None


class PaletteColor(PipelineModel):
    name: str
    hex: str
    role: str

    @field_validator("hex")
    @classmethod
    def validate_hex(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("A cor deve usar o formato #RRGGBB")
        int(value[1:], 16)
        return value.upper()


class Palette(PipelineModel):
    palette_id: str
    name: str
    mood: str
    colors: list[PaletteColor]


class CharacterSheet(PipelineModel):
    character_id: str
    name: str
    visual_description: str
    clothing: str
    invariants: list[str] = Field(default_factory=list)
    anchor_images: list[str] = Field(default_factory=list)


class ContinuityBible(PipelineModel):
    characters: list[CharacterSheet] = Field(default_factory=list)
    environments: dict[str, str] = Field(default_factory=dict)
    global_rules: list[str] = Field(default_factory=list)


class PromptBlocks(PipelineModel):
    style_profile_id: str
    palette_id: str
    continuity: list[str]
    scene: str
    composition: str
    emotion: str
    negative: list[str]
    assembled_prompt: str


class Scene(PipelineModel):
    scene_id: str
    order: int
    section_id: str
    narration: str
    estimated_duration_seconds: float
    emotional_intent: str
    characters: list[str] = Field(default_factory=list)
    environment: str
    shot: str
    narrative_function: str
    prompt: PromptBlocks
    image_id: str
    image_source_mode: Literal["generated", "fixed_profile_asset_v1"] = "generated"
    image_source_path: str | None = None
    narration_block_id: str | None = None
    visual_relation: Literal["literal", "contextual", "symbolic"] = "contextual"
    visual_brief: str = ""
    generation_status: StageStatus = StageStatus.PENDING
    approval: ApprovalStatus = ApprovalStatus.PENDING


class SceneManifest(PipelineModel):
    script_version: int
    style_profile_id: str
    palette_id: str
    total_estimated_duration_seconds: float
    scenes: list[Scene]


class ImageVersion(PipelineModel):
    version: int
    path: str
    provider: str
    prompt_hash: str
    created_at: str
    status: Literal["candidate", "approved", "rejected"] = "candidate"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImageItem(PipelineModel):
    image_id: str
    scene_id: str
    prompt: str
    prompt_hash: str
    source_mode: Literal["generated", "fixed_profile_asset_v1"] = "generated"
    source_path: str | None = None
    status: StageStatus = StageStatus.PENDING
    active_version: int | None = None
    approved_version: int | None = None
    versions: list[ImageVersion] = Field(default_factory=list)
    last_error: str | None = None


class ImageManifest(PipelineModel):
    generated_at: str
    style_profile_id: str
    palette_id: str
    items: list[ImageItem]


class WordTiming(PipelineModel):
    index: int
    word: str
    start: float
    end: float
    scene_id: str


class AudioSegment(PipelineModel):
    scene_id: str
    narration_block_id: str | None = None
    version: int
    text: str
    input_hash: str
    provider: str
    model_id: str | None = None
    voice_id: str | None = None
    path: str
    start: float
    end: float
    duration: float
    alignment_source: Literal[
        "provider",
        "sapi_events",
        "simulated",
        "local_forced_alignment",
    ]
    words: list[WordTiming]
    usage: dict[str, int | float | str | None] = Field(default_factory=dict)


class AudioManifest(PipelineModel):
    generated_at: str
    provider: str
    sample_rate: int
    narration_path: str
    duration: float
    segments: list[AudioSegment]
    words: list[WordTiming]


class MusicManifest(PipelineModel):
    selected_at: str
    selection_mode: Literal["random", "existing", "disabled"]
    source_name: str | None = None
    source_hash: str | None = None
    path: str | None = None
    library_track_count: int = 0
    library_profile: Literal["default", "adaptive_compact_devotional_v1"] = "default"
    mix_standard: Literal["legacy_gain_v1", "voice_music_loudness_v1"] = "legacy_gain_v1"
    narration_target_lufs: float | None = None
    background_music_target_lufs: float | None = None
    background_music_gap_lu: float | None = None


class CaptionBlock(PipelineModel):
    block_id: str
    word_indexes: list[int]
    text: str
    start: float
    end: float
    hold_seconds: float = Field(default=0.0, ge=0.0, le=3.0)


class CaptionManifest(PipelineModel):
    generated_at: str
    word_count: int
    blocks: list[CaptionBlock]
    ass_path: str
    srt_path: str
    vtt_path: str
    validation: dict[str, bool | int | str]
    context_labels: dict[str, str] = Field(default_factory=dict)
    italic_word_indexes: list[int] = Field(default_factory=list)


class YouTubeMetadata(PipelineModel):
    status: Literal["draft", "approved"] = "draft"
    title: str
    alternative_titles: list[str]
    description: str
    chapters: list[str]
    topics: list[str]
    pinned_comment: str
    thumbnail_concept: str
    thumbnail_prompt: str
    thumbnail_alternatives: list[str]
    publication_visibility: Literal["private", "unlisted"] = "private"


class YouTubePublicationAsset(PipelineModel):
    status: Literal["pending", "uploaded", "skipped", "failed"] = "pending"
    source_path: str | None = None
    source_hash: str | None = None
    youtube_id: str | None = None
    updated_at: str | None = None
    error: str | None = None


class YouTubePublicationRecord(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: str
    video_id: str
    youtube_url: str
    channel_id: str
    channel_title: str
    title: str
    privacy_status: Literal["private", "unlisted", "public"]
    publish_at: str | None = None
    published_at: str | None = None
    category_id: str = "22"
    default_language: str = "pt-BR"
    made_for_kids: bool = False
    contains_synthetic_media: bool = True
    notify_subscribers: bool = False
    video: YouTubePublicationAsset
    metadata: YouTubePublicationAsset
    thumbnail: YouTubePublicationAsset
    captions: YouTubePublicationAsset
    created_at: str
    updated_at: str


class YouTubeThumbnailDesign(PipelineModel):
    text: str
    accent_word: str
    text_lines: list[str] = Field(default_factory=list, max_length=3)
    text_position: Literal["top", "center", "bottom"] = "top"
    text_alignment: Literal["left", "center", "right"] = "center"
    primary_color: Literal["white", "yellow", "red"] = "white"
    accent_color: Literal["white", "yellow", "red"] = "yellow"
    max_lines: int = Field(default=3, ge=1, le=3)
    safe_margin_x_ratio: float = Field(default=0.06, ge=0.04, le=0.18)
    safe_margin_y_ratio: float = Field(default=0.06, ge=0.04, le=0.18)
    texture: Literal["none", "subtle_grain"] = "subtle_grain"
    typography_treatment: Literal["integrated_extruded_3d", "none"] = "integrated_extruded_3d"
    typography_hierarchy: Literal["dominant_keyword_scale", "none"] = "dominant_keyword_scale"
    typography_material: Literal["scene_lit_text_material", "none"] = "scene_lit_text_material"
    narrative_element: Literal["none", "arrow", "x", "underline", "brush"] = "none"
    narrative_element_anchor: Literal[
        "top_left",
        "top_right",
        "center_left",
        "center_right",
        "bottom_left",
        "bottom_right",
    ] = "center_right"
    narrative_element_target: str | None = None
    narrative_element_rationale: str | None = None

    @model_validator(mode="after")
    def validate_editorial_design(self) -> YouTubeThumbnailDesign:
        if self.typography_treatment == "none":
            if self.text.strip() or self.accent_word.strip() or self.text_lines:
                raise ValueError("A thumbnail sem tipografia não pode declarar texto")
            if self.typography_hierarchy != "none" or self.typography_material != "none":
                raise ValueError("A thumbnail sem tipografia precisa neutralizar a hierarquia")
            if self.texture != "none":
                raise ValueError("A thumbnail sem tipografia não pode declarar textura de texto")
            if self.narrative_element != "none":
                raise ValueError("A thumbnail limpa não pode declarar marcação editorial")
            return self
        words = self.text.split()
        if not 2 <= len(words) <= 5:
            raise ValueError("O texto da thumbnail precisa ter de duas a cinco palavras")
        accent_words = self.accent_word.split()
        if len(accent_words) != 1:
            raise ValueError("A thumbnail precisa de uma única palavra de destaque")
        normalized_words = {word.strip(".,!?:;").casefold() for word in words}
        normalized_accent = accent_words[0].strip(".,!?:;").casefold()
        if normalized_accent not in normalized_words:
            raise ValueError("A palavra de destaque precisa existir no texto da thumbnail")
        if self.text_lines:
            if len(self.text_lines) > self.max_lines:
                raise ValueError("A hierarquia da thumbnail excede o máximo de linhas")
            if any(not line.strip() for line in self.text_lines):
                raise ValueError("As linhas da thumbnail não podem estar vazias")
            flattened = " ".join(" ".join(self.text_lines).split()).casefold()
            normalized_text = " ".join(self.text.split()).casefold()
            if flattened != normalized_text:
                raise ValueError("text_lines precisa preservar exatamente thumbnail text")
        if self.narrative_element != "none":
            if not (self.narrative_element_rationale or "").strip():
                raise ValueError("Elemento narrativo precisa de justificativa editorial")
            if (
                self.narrative_element in {"arrow", "x"}
                and not (self.narrative_element_target or "").strip()
            ):
                raise ValueError("Seta ou X precisa identificar o alvo narrativo")
        return self


class YouTubePromiseMatch(PipelineModel):
    content_promise: str
    opening_line: str
    opening_scene_id: str
    title_delivery: str
    thumbnail_delivery: str


class YouTubeGrowthStrategy(PipelineModel):
    format: Literal["long_form", "short"]
    target_viewer: str
    search_intent: str
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    content_promise: str
    opening_hook: str
    title_rationale: str
    description_rationale: str = ""
    tag_strategy: str = ""
    discovery_hypothesis: str = ""
    thumbnail_rationale: str
    title_thumbnail_complement: str
    promise_match: YouTubePromiseMatch | None = None
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    thumbnail_designs: list[YouTubeThumbnailDesign] = Field(default_factory=list, max_length=3)
    short_cover_timestamp_seconds: float | None = Field(default=None, ge=0.0)
    experiment_plan: list[str] = Field(default_factory=list)
    compliance_checks: list[str] = Field(default_factory=list)
    official_guidelines: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_short_cover(self) -> YouTubeGrowthStrategy:
        return self


class YouTubeGrowthPackage(PipelineModel):
    job_id: str
    input_hash: str
    metadata: YouTubeMetadata
    strategy: YouTubeGrowthStrategy


class CompactGrowthDraft(PipelineModel):
    """Small creative hand-off; deterministic code owns the full YouTube package."""

    job_id: str
    input_hash: str
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    primary_keyword: str = Field(min_length=1)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=8)
    topics: list[str] = Field(default_factory=list, max_length=15)
    pinned_comment: str = ""
    title_rationale: str = ""
    description_rationale: str = ""
    tag_strategy: str = ""
    discovery_hypothesis: str = ""


class ShortsRetentionBeat(PipelineModel):
    section_id: str
    purpose: Literal[
        "hook",
        "orientation",
        "open_loop",
        "stakes",
        "progression",
        "evidence",
        "context",
        "analysis",
        "comparison",
        "pattern_interrupt",
        "reveal",
        "payoff",
        "resolution",
        "synthesis",
        "application",
        "callback",
        "cta",
        "reference",
    ]
    viewer_question: str
    viewer_value: str
    target_start_seconds: float = Field(ge=0.0)
    target_end_seconds: float = Field(gt=0.0)
    pattern_interrupt: str | None = None
    opens_loop: str | None = None
    resolves_loop: str | None = None
    new_information: str | None = None
    visual_change: str | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> ShortsRetentionBeat:
        if self.target_end_seconds <= self.target_start_seconds:
            raise ValueError("target_end_seconds deve ser posterior a target_start_seconds")
        return self


class ShortsVerseOpening(PipelineModel):
    source_id: str
    reference: str
    translation: str
    quote_mode: Literal["literal", "paraphrase"] = "literal"
    lead_in: str
    verse_text: str
    target_start_seconds: float = Field(default=0.0, ge=0.0, le=30.0)
    target_end_seconds: float = Field(gt=0.0, le=30.0)

    @model_validator(mode="after")
    def validate_timing(self) -> ShortsVerseOpening:
        if self.target_end_seconds <= self.target_start_seconds:
            raise ValueError("target_end_seconds deve ser posterior a target_start_seconds")
        return self


class ShortsNarrativeSpine(PipelineModel):
    pattern: Literal["human_hook_to_bible", "verse_first"] = "verse_first"
    development_mode: Literal[
        "contextual_reflection",
        "biblical_story_bridge",
        "biblical_image_bridge",
        "cross_reference_bridge",
        "promise_boundary",
    ]
    verse_section_id: str
    tension_section_id: str
    bridge_section_id: str
    meaning_section_id: str
    application_section_id: str
    tension_question: str
    bridge_statement: str
    central_truth: str
    practical_response: str
    bridge_source_ids: list[str] = Field(default_factory=list)


class ShortsCommentPrompt(PipelineModel):
    mode: Literal[
        "keyword_reaffirmation",
        "next_step_commitment",
        "sentence_completion",
        "message_declaration",
        "community_encouragement",
    ]
    prompt_text: str
    response_cue: str
    objective: str
    fit_reason: str


class ShortsEngagementPlan(PipelineModel):
    mode: Literal["comment", "subscribe", "share"]
    spoken_text: str
    objective: str
    fit_reason: str
    target_start_seconds: float = Field(ge=0.0)
    target_end_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_timing(self) -> ShortsEngagementPlan:
        if self.target_end_seconds <= self.target_start_seconds:
            raise ValueError("target_end_seconds deve ser posterior a target_start_seconds")
        return self


class ShortsReplayBridge(PipelineModel):
    mode: Literal["verbal_callback", "visual_callback", "verbal_and_visual"]
    opening_element: str
    closing_callback: str
    added_meaning: str


class ShortsRetentionStory(PipelineModel):
    pattern: Literal["retention_story_v2"] = "retention_story_v2"
    archetype: Literal[
        "conflict_first",
        "counterintuitive_reveal",
        "stair_step_progression",
    ]
    opening_section_id: str
    scripture_section_id: str
    progression_section_ids: list[str] = Field(min_length=2, max_length=5)
    reengagement_section_id: str
    payoff_section_id: str
    cta_section_id: str
    opening_promise: str
    central_question: str
    progression: list[str] = Field(min_length=2, max_length=5)
    reengagement: str
    scripture_anchor: str
    payoff: str
    practical_response: str
    engagement: ShortsEngagementPlan
    replay_bridge: ShortsReplayBridge
    source_ids: list[str] = Field(default_factory=list)


class ShortsCreativeDirection(PipelineModel):
    pattern: Literal["creative_retention_v3"] = "creative_retention_v3"
    creative_mode: CreativeMode
    interpretive_stance: InterpretiveStance
    hook_family: HookFamily
    opening_section_id: str
    scripture_section_ids: list[str] = Field(min_length=1, max_length=2)
    development_section_ids: list[str] = Field(min_length=1, max_length=6)
    reengagement_section_ids: list[str] = Field(min_length=1, max_length=2)
    resolution_section_id: str
    cta_section_id: str
    content_promise: str
    organizing_question: str = ""
    resolution: str
    viewer_takeaway: str
    practical_application: str = ""
    callback: str = ""
    engagement: ShortsEngagementPlan
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_creative_direction(self) -> ShortsCreativeDirection:
        if self.creative_mode == "auto":
            raise ValueError("creative_mode precisa ser resolvido antes do roteiro")
        if self.hook_family in {"auto", "none"}:
            raise ValueError("Shorts exigem uma família de hook concreta")
        required = (self.content_promise, self.resolution, self.viewer_takeaway)
        if not all(value.strip() for value in required):
            raise ValueError("Promessa, resolução e valor final não podem ficar vazios")
        if (
            self.creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES
            and not self.practical_application.strip()
        ):
            raise ValueError("O modo devocional precisa declarar aplicação prática")
        return self


class ShortsCinematicDevotionalDirection(PipelineModel):
    pattern: Literal["cinematic_jesus_devotional_v1"] = "cinematic_jesus_devotional_v1"
    creative_mode: Literal["devotional_application", "pastoral_encouragement"]
    interpretive_stance: Literal["devotional"] = "devotional"
    hook_family: Literal["emotional_interruption"] = "emotional_interruption"
    opening_section_id: str
    scripture_section_id: str
    reference_section_id: str
    emotional_hook: str
    content_promise: str
    viewer_takeaway: str
    spoken_reference: str
    source_id: str
    visual_emotional_arc: list[str] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def validate_compact_direction(self) -> ShortsCinematicDevotionalDirection:
        section_ids = (
            self.opening_section_id,
            self.scripture_section_id,
            self.reference_section_id,
        )
        if len(set(section_ids)) != 3:
            raise ValueError("O momento cinematográfico exige três seções distintas")
        required = (
            self.emotional_hook,
            self.content_promise,
            self.viewer_takeaway,
            self.spoken_reference,
            self.source_id,
        )
        if not all(value.strip() for value in required):
            raise ValueError("O contrato cinematográfico não pode ter campos essenciais vazios")
        return self


class ShortsVisualScene(PipelineModel):
    section_id: str
    narration: str
    scene_description: str
    visual_relation: Literal["literal", "contextual", "symbolic"]

    @model_validator(mode="after")
    def validate_visual_scene(self) -> ShortsVisualScene:
        if not self.narration.strip():
            raise ValueError("O bloco de narração da cena não pode ficar vazio")
        if not self.scene_description.strip():
            raise ValueError("A descrição específica da cena não pode ficar vazia")
        return self


class CompactVisualConcept(PipelineModel):
    block_id: str
    scene_description: str = Field(min_length=1)
    visual_relation: Literal["literal", "contextual", "symbolic"]


class CompactVisualPlan(PipelineModel):
    """Creative scene concepts for narration blocks sealed by deterministic code."""

    job_id: str
    input_hash: str
    concepts: list[CompactVisualConcept] = Field(min_length=1, max_length=11)


class ShortsScriptStrategy(PipelineModel):
    target_viewer: str
    viewer_need: str
    core_value: str
    content_promise: str
    hook: str
    hook_type: Literal[
        "scripture_opening",
        "curiosity_gap",
        "contrast",
        "direct_tension",
        "surprising_detail",
        "emotional_interruption",
    ]
    hook_family: HookFamily = "auto"
    hook_techniques: list[
        Literal[
            "self_relevance",
            "curiosity_gap",
            "contrast",
            "specificity",
            "pattern_interrupt",
            "open_loop",
            "emotional_mirroring",
        ]
    ] = Field(default_factory=list)
    hook_target_end_seconds: float = Field(gt=0.0, le=4.0)
    verse_opening: ShortsVerseOpening | None = None
    additional_scriptures: list[ShortsVerseOpening] = Field(default_factory=list)
    narrative_spine: ShortsNarrativeSpine | None = None
    retention_story: ShortsRetentionStory | None = None
    creative_direction: ShortsCreativeDirection | None = None
    cinematic_devotional: ShortsCinematicDevotionalDirection | None = None
    open_loop: str
    payoff: str
    practical_value: str
    comment_prompt: ShortsCommentPrompt | None = None
    cta: str
    reason_to_subscribe: str
    retention_beats: list[ShortsRetentionBeat]
    visual_scenes: list[ShortsVisualScene] = Field(default_factory=list, max_length=60)
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    biblical_safeguards: list[str] = Field(default_factory=list)
    compliance_checks: list[str] = Field(default_factory=list)


class ShortsScriptPackage(PipelineModel):
    job_id: str
    input_hash: str
    script: ScriptDocument
    biblical_review: BiblicalReview
    strategy: ShortsScriptStrategy


class Experiment(PipelineModel):
    experiment_id: str
    hypothesis: str
    change: str
    project_ids: list[str]
    period: str | None = None
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    conclusion: str | None = None
    confidence_note: str = "Não inferir causalidade a partir de poucos dados."


class ExperimentMemory(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    experiments: list[Experiment] = Field(default_factory=list)
