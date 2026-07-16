from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


type ProductionMode = Literal[
    "guided",
    "draft_one_shot",
    "light_manual",
    "light_automatic",
    "remote_review",
]


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
    preview_width: int = 1920
    preview_height: int = 1080
    preview_crf: int = 12
    preview_preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "slow"
    preview_motion_supersample: int = Field(default=3, ge=1, le=4)
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
    background_music_gain_db: float = Field(default=-12.0, ge=-60.0, le=0.0)
    background_music_fade_in_seconds: float = Field(default=2.0, ge=0.0, le=10.0)
    background_music_fade_out_seconds: float = Field(default=3.0, ge=0.0, le=10.0)
    short_cover_enabled: bool = True
    short_cover_duration_seconds: float = Field(default=1 / 30, ge=1 / 30, le=3.0)
    short_cover_transition_seconds: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_motion_zoom(self) -> RenderConfig:
        if self.motion_zoom_max < self.motion_zoom_min:
            raise ValueError("motion_zoom_max deve ser maior ou igual a motion_zoom_min")
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


class AudioConfig(PipelineModel):
    provider: Literal["mock", "elevenlabs", "local_manual"] = "elevenlabs"
    voice_id: str | None = None
    model_id: str = "eleven_flash_v2_5"
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


class MusicConfig(PipelineModel):
    enabled: bool = True
    selection_mode: Literal["random"] = "random"


class YouTubeGrowthConfig(PipelineModel):
    enabled: bool = True
    use_specialist_subagent: bool = True
    specialist_role: Literal["youtube_growth_specialist"] = "youtube_growth_specialist"
    title_variants: int = Field(default=3, ge=1, le=3)
    thumbnail_variants: int = Field(default=3, ge=1, le=3)
    reuse_scene_images_for_thumbnails: bool = False
    thumbnail_generation_mode: Literal["legacy_overlay", "integrated_imagegen_v1"] = (
        "legacy_overlay"
    )
    title_standard: Literal["legacy", "passage_pipe_topic_v1"] = "legacy"
    description_standard: Literal["legacy", "scripture_timeline_v1"] = "legacy"
    thumbnail_standard: Literal["legacy", "direct_conflict_red_v1"] = "legacy"
    title_thumbnail_pairing_standard: Literal["legacy", "complementary_v1"] = "legacy"


class EditorialPolicyConfig(PipelineModel):
    standard: Literal["legacy", "scripture_rich_ptbr_v1"] = "legacy"
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
    min_words: int = Field(default=95, ge=60, le=180)
    max_words: int = Field(default=125, ge=80, le=200)
    hook_max_words: int = Field(default=12, ge=4, le=24)
    hook_target_seconds: float = Field(default=2.5, ge=1.0, le=4.0)
    narrative_standard: Literal["human_hook_to_bible", "verse_first"] = "human_hook_to_bible"
    require_literal_verse_opening: bool = False
    require_verified_scripture: bool = True
    verse_opening_max_seconds: float = Field(default=12.0, ge=4.0, le=15.0)
    scripture_citation_max_end_seconds: float = Field(default=20.0, ge=6.0, le=30.0)
    verse_visual_change_seconds: float = Field(default=5.5, ge=3.5, le=8.0)
    minimum_retention_beats: int = Field(default=5, ge=3, le=10)
    require_comment_prompt: bool = True
    comment_response_max_words: int = Field(default=8, ge=1, le=12)
    require_subscribe_cta: bool = True
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
        return self


class ProductionConfig(PipelineModel):
    mode: ProductionMode = "draft_one_shot"
    use_subagents: bool = True
    image_workers: int = Field(default=3, ge=1, le=8)
    manual_image_batch_size: int = Field(default=10, ge=1, le=10)
    approval_policy: Literal["stage_gates", "final_only"] = "final_only"
    optimization_profile: Literal[
        "legacy_v4",
        "token_optimized_v1",
        "remote_single_variant_v1",
    ] = "legacy_v4"
    worker_context_policy: Literal["inherit", "compact_only"] = "inherit"


class ProjectConfig(PipelineModel):
    render: RenderConfig = Field(default_factory=RenderConfig)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    images: ImageConfig = Field(default_factory=ImageConfig)
    music: MusicConfig = Field(default_factory=MusicConfig)
    shorts_script: ShortsScriptConfig = Field(default_factory=ShortsScriptConfig)
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
    demo: bool = False
    video_format: Literal["landscape", "short"] = "landscape"
    config: ProjectConfig = Field(default_factory=ProjectConfig)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)


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


class SourceEntry(PipelineModel):
    source_id: str
    kind: Literal[
        "biblical_text", "historical_context", "commentary", "rights", "channel_reference"
    ]
    title: str
    passage: str | None = None
    translation: str | None = None
    author_or_publisher: str | None = None
    url: str | None = None
    accessed_at: str | None = None
    excerpt: str | None = None
    use_in_video: str
    claim_scope: str
    rights_note: str | None = None
    verified: bool = False


class SourceLibrary(PipelineModel):
    entries: list[SourceEntry] = Field(default_factory=list)


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
    selection_weight: float = 1.0
    familiarity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    clarity_score: float = Field(default=0.5, ge=0.0, le=1.0)


class VerseCatalog(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    catalog_id: str
    translation: str
    publisher: str
    source_url: str
    rights_note: str
    accessed_at: str
    candidates: list[VerseCandidate]


class VerseUsageRecord(PipelineModel):
    registry_id: str | None = None
    environment: str | None = None
    project_id: str
    project_path: str
    video_format: Literal["landscape", "short"]
    status: Literal["reserved", "used"]
    references: list[str] = Field(default_factory=list)
    normalized_references: list[str] = Field(default_factory=list)
    main_passage: str | None = None
    topic: str | None = None
    normalized_topic: str | None = None
    themes: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str | None = None


class VerseUsageIndex(PipelineModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: str
    records: list[VerseUsageRecord] = Field(default_factory=list)


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

    def narration_text(self) -> str:
        return "\n\n".join(section.narration.strip() for section in self.sections).strip()


class CreativePacingProfile(PipelineModel):
    profile_id: Literal["balanced_long_v1", "short_v1"]
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
            "progression",
            "reengagement",
            "contemplation",
            "payoff",
            "application",
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
    algorithm: Literal["creative-contract-v1"] = "creative-contract-v1"
    input_hash: str
    format: Literal["long_form", "short"]
    target_viewer: str
    content_promise: str
    opening_hook: str
    payoff: str
    practical_application: str
    main_passage: str
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str]
    claims_to_avoid: list[str] = Field(default_factory=list)
    beats: list[CreativeBeat]
    critical_component_ids: list[str]
    pacing: CreativePacingProfile

    @model_validator(mode="after")
    def validate_critical_components(self) -> CreativeContract:
        if not all(
            value.strip()
            for value in (self.target_viewer, self.content_promise, self.opening_hook, self.payoff)
        ):
            raise ValueError("Público, promessa, gancho e payoff não podem ficar vazios")
        beat_section_ids = [beat.section_id for beat in self.beats]
        if not self.section_ids or beat_section_ids != self.section_ids:
            raise ValueError("O contrato criativo precisa cobrir cada seção exatamente uma vez")
        if len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("section_id precisa ser único no contrato criativo")
        expected_critical = [beat.section_id for beat in self.beats if beat.critical]
        if self.critical_component_ids != expected_critical:
            raise ValueError("A lista de componentes críticos não corresponde aos beats")
        functions = {function for beat in self.beats for function in beat.functions}
        if not {"hook", "payoff"}.issubset(functions):
            raise ValueError("O contrato criativo precisa declarar gancho e payoff")
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
    source_scene_id: str | None = None
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


class YouTubeGrowthStrategy(PipelineModel):
    format: Literal["long_form", "short"]
    target_viewer: str
    search_intent: str
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    content_promise: str
    opening_hook: str
    title_rationale: str
    thumbnail_rationale: str
    title_thumbnail_complement: str
    source_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    thumbnail_text: str | None = None
    thumbnail_accent_word: str | None = None
    thumbnail_text_position: Literal["top", "center", "bottom"] = "top"
    thumbnail_overlay_style: Literal["high_contrast_editorial", "high_impact_editorial"] = (
        "high_contrast_editorial"
    )
    thumbnail_designs: list[YouTubeThumbnailDesign] = Field(default_factory=list, max_length=3)
    short_cover_timestamp_seconds: float | None = Field(default=None, ge=0.0)
    experiment_plan: list[str] = Field(default_factory=list)
    compliance_checks: list[str] = Field(default_factory=list)
    official_guidelines: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_short_cover(self) -> YouTubeGrowthStrategy:
        if self.format == "short" and self.short_cover_timestamp_seconds is None:
            raise ValueError("short_cover_timestamp_seconds é obrigatório para Shorts")
        return self


class YouTubeGrowthPackage(PipelineModel):
    job_id: str
    input_hash: str
    metadata: YouTubeMetadata
    strategy: YouTubeGrowthStrategy


class ShortsRetentionBeat(PipelineModel):
    section_id: str
    purpose: Literal[
        "hook", "open_loop", "stakes", "pattern_interrupt", "payoff", "application", "cta"
    ]
    viewer_question: str
    viewer_value: str
    target_start_seconds: float = Field(ge=0.0)
    target_end_seconds: float = Field(gt=0.0)
    pattern_interrupt: str | None = None
    opens_loop: str | None = None
    resolves_loop: str | None = None

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
    target_end_seconds: float = Field(gt=0.0, le=30.0)


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
    ]
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
    open_loop: str
    payoff: str
    practical_value: str
    comment_prompt: ShortsCommentPrompt | None = None
    cta: str
    reason_to_subscribe: str
    retention_beats: list[ShortsRetentionBeat]
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
