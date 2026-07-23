from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from pipeline_videos.creative_direction import (
    CREATIVE_MODE_SPECS,
    HOOK_TEMPLATE_EXAMPLES,
    creative_mode_payload,
    default_interpretive_stance,
    default_long_hook_policy,
    narration_tone_for_mode,
    viewer_outcome_for_mode,
)
from pipeline_videos.imagegen_retry import imagegen_retry_instructions
from pipeline_videos.models import (
    APPLICATION_REQUIRED_CREATIVE_MODES,
    COMPACT_SHORT_THRESHOLD_SECONDS,
    HIGH_STAKES_HOOK_FAMILIES,
    MIN_LONG_DURATION_SECONDS,
    MIN_SHORT_DURATION_SECONDS,
    USER_SIX_HOOK_FAMILIES,
    BiblicalReview,
    Briefing,
    CreativeMode,
    ElevenLabsVoiceVariant,
    HookFamily,
    HookPolicy,
    PipelineModel,
    ScriptDocument,
    ShortsEditorialProfile,
    SourceEntry,
    SourceLibrary,
    StageStatus,
    VerseCandidate,
    VerseUsageIndex,
    default_elevenlabs_model_id,
    elevenlabs_voice_id_for_variant,
)
from pipeline_videos.music import import_music
from pipeline_videos.project import create_project
from pipeline_videos.publication import (
    authenticated_client,
    publish_project,
    upload_project,
)
from pipeline_videos.storage import (
    DEFAULT_PROJECTS_DIR,
    REPO_ROOT,
    ProjectLayout,
    read_json,
    write_json,
)
from pipeline_videos.utils import compact_json_schema, slugify, utc_now
from pipeline_videos.validation import validate_project
from pipeline_videos.verse_selection import create_auto_video

REMOTE_JOB_SCHEMA_VERSION = "1.1"
REMOTE_JOB_STATE_SCHEMA_VERSION = "1.4"
DEFAULT_REMOTE_JOBS_DIR = REPO_ROOT / "remote-jobs"
REMOTE_TIMEZONE = ZoneInfo("America/Sao_Paulo")
DEFAULT_REMOTE_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_REMOTE_REASONING_EFFORT = "medium"
JOB_ID_PATTERN = re.compile(r"^video-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$")
CODEX_THREAD_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
REMOTE_DISABLED_CODEX_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "goals",
    "in_app_browser",
    "memories",
    "plugins",
    "remote_plugin",
    "tool_suggest",
    "workspace_dependencies",
)

RemoteJobStatus = Literal[
    "queued",
    "running",
    "waiting_youtube",
    "completed",
    "failed",
    "cancelled",
]
RemoteReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
RemoteMusicTrack = Literal["leaning", "aloboi"]

REMOTE_MUSIC_TRACK_PATHS: dict[RemoteMusicTrack, Path] = {
    "leaning": REPO_ROOT / "music-library" / "adaptive-compact-v1" / "Leaning.mp3",
    "aloboi": REPO_ROOT / "music-library" / "adaptive-compact-v1" / "Aloboi .mp3",
}


class RemoteCodexRun(PipelineModel):
    phase: str
    item_id: str | None = None
    model: str
    reasoning_effort: RemoteReasoningEffort
    job_attempt: int = 0
    started_at: str
    finished_at: str
    exit_code: int
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CodexPhaseSpec:
    phase: str
    item_id: str | None
    model: str
    reasoning_effort: RemoteReasoningEffort
    payload: dict[str, Any]
    expected_outputs: tuple[Path, ...]

    def __post_init__(self) -> None:
        if self.model != DEFAULT_REMOTE_CODEX_MODEL:
            raise ValueError(f"O executor remoto exige {DEFAULT_REMOTE_CODEX_MODEL}.")
        if self.reasoning_effort != DEFAULT_REMOTE_REASONING_EFFORT:
            raise ValueError(
                f"O executor remoto exige raciocínio {DEFAULT_REMOTE_REASONING_EFFORT}."
            )


class RemoteVideoRequest(PipelineModel):
    schema_version: Literal["1.0", "1.1"] = REMOTE_JOB_SCHEMA_VERSION
    video_format: Literal["short", "long"]
    production_profile: Literal["standard_v1", "approved_fast_v1"] = "standard_v1"
    approved_script_id: str | None = None
    approved_script_hash: str | None = None
    topic: str | None = Field(default=None, max_length=240)
    title: str | None = Field(default=None, max_length=240)
    character: str | None = Field(default=None, max_length=120)
    creative_mode: CreativeMode = "auto"
    hook_policy: HookPolicy = "auto"
    hook_family: HookFamily = "auto"
    editorial_profile: ShortsEditorialProfile = "verse_led_v1"
    music_track: RemoteMusicTrack | None = None
    duration_seconds: int | None = Field(default=None, ge=MIN_SHORT_DURATION_SECONDS, le=3600)
    exact_duration: bool = False
    start_policy: Literal["immediate", "overnight"] = "immediate"
    not_before: datetime | None = None
    publication_mode: Literal["private_review", "scheduled"] = "private_review"
    publish_at: datetime | None = None
    audio_provider: Literal["elevenlabs"] = "elevenlabs"
    voice_variant: ElevenLabsVoiceVariant = "jesus_1"
    external_services_authorized: bool = False

    @model_validator(mode="after")
    def validate_request(self) -> RemoteVideoRequest:
        object.__setattr__(self, "topic", self.topic.strip() if self.topic else None)
        object.__setattr__(self, "title", self.title.strip() if self.title else None)
        object.__setattr__(self, "character", self.character.strip() if self.character else None)
        if self.duration_seconds is None:
            object.__setattr__(
                self,
                "duration_seconds",
                50 if self.video_format == "short" else 540,
            )
        if self.video_format == "short" and self.duration_seconds > 180:
            raise ValueError(
                f"Shorts precisam ter duração entre {MIN_SHORT_DURATION_SECONDS} e 180 segundos."
            )
        if self.video_format == "long" and self.duration_seconds < MIN_LONG_DURATION_SECONDS:
            raise ValueError(
                f"Vídeos longos precisam ter ao menos {MIN_LONG_DURATION_SECONDS} segundos."
            )
        compact = (
            self.video_format == "short" and self.duration_seconds < COMPACT_SHORT_THRESHOLD_SECONDS
        )
        high_stakes = self.editorial_profile == "high_stakes_biblical_question_v1"
        approved_fast = self.production_profile == "approved_fast_v1"
        if approved_fast:
            if self.video_format != "short":
                raise ValueError("O fluxo pré-aprovado é exclusivo de Shorts.")
            if not self.approved_script_id or not self.approved_script_hash:
                raise ValueError("O fluxo pré-aprovado exige ID e hash do roteiro.")
            if self.topic or self.character:
                raise ValueError("O fluxo pré-aprovado não aceita tema ou personagem adicionais.")
        elif self.approved_script_id is not None or self.approved_script_hash is not None:
            raise ValueError("ID/hash de roteiro só podem ser usados em approved_fast_v1.")
        if self.video_format == "long" and high_stakes:
            raise ValueError("O perfil high-stakes é exclusivo de Shorts.")
        if (
            high_stakes
            and not approved_fast
            and not COMPACT_SHORT_THRESHOLD_SECONDS <= self.duration_seconds <= 60
        ):
            raise ValueError("O perfil high-stakes exige duração entre 45 e 60 segundos.")
        if high_stakes and (self.topic or self.character):
            raise ValueError(
                "O perfil high-stakes seleciona uma pergunta curada automaticamente; "
                "não informe topic ou character."
            )
        if self.video_format == "long" and self.music_track is not None:
            raise ValueError("As faixas Leaning e Aloboi deste contrato são exclusivas de Shorts.")
        if compact and self.character:
            raise ValueError(
                "Histórias de personagens exigem Short de ao menos "
                f"{COMPACT_SHORT_THRESHOLD_SECONDS} segundos ou vídeo longo."
            )
        if compact and self.creative_mode not in {
            "auto",
            "devotional_application",
            "pastoral_encouragement",
        }:
            raise ValueError(
                "Shorts compactos aceitam somente os modos devocional aplicado ou pastoral."
            )
        if self.video_format == "short" and (
            self.hook_policy == "none" or self.hook_family == "none"
        ):
            raise ValueError("Shorts exigem hook inicial; use auto ou uma família concreta.")
        if compact and self.hook_family not in {"auto", "emotional_interruption"}:
            raise ValueError("Shorts compactos exigem hook emocional de interrupção.")
        if high_stakes and self.hook_family not in {"auto", *HIGH_STAKES_HOOK_FAMILIES}:
            raise ValueError("O perfil high-stakes exige uma família investigativa de hook.")
        if (
            self.video_format == "short"
            and not compact
            and not high_stakes
            and self.hook_family
            not in {
                "auto",
                *USER_SIX_HOOK_FAMILIES,
            }
        ):
            raise ValueError("Shorts novos aceitam somente uma das seis famílias oficiais de hook.")
        if self.hook_policy == "required" and self.hook_family == "none":
            raise ValueError("hook_policy=required não aceita hook_family=none.")
        if self.hook_policy == "none" and self.hook_family not in {"auto", "none"}:
            raise ValueError("hook_policy=none não aceita uma família de hook.")
        for field_name in ("not_before", "publish_at"):
            value = getattr(self, field_name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} precisa incluir fuso horário.")
        if self.publication_mode == "scheduled" and self.publish_at is None:
            raise ValueError("publish_at é obrigatório para publicação agendada.")
        if self.publication_mode == "private_review" and self.publish_at is not None:
            raise ValueError("publish_at deve ficar vazio no modo private_review.")
        return self


class RemoteVideoJob(PipelineModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4"] = REMOTE_JOB_STATE_SCHEMA_VERSION
    job_id: str
    status: RemoteJobStatus
    request: RemoteVideoRequest
    created_at: str
    updated_at: str
    attempts: int = 0
    project_path: str | None = None
    video_id: str | None = None
    youtube_url: str | None = None
    publish_at: str | None = None
    current_phase: str | None = None
    codex_session_id: str | None = None
    codex_runs: list[RemoteCodexRun] = Field(default_factory=list)
    codex_exit_code: int | None = None
    automatic_recovery_count: int = 0
    next_retry_at: datetime | None = None
    schedule_occurrence_id: str | None = None
    error: str | None = None


def remote_jobs_dir(path: Path | None = None) -> Path:
    configured = os.getenv("PIPELINE_VIDEOS_REMOTE_JOBS_DIR")
    return (
        (path or (Path(configured) if configured else DEFAULT_REMOTE_JOBS_DIR))
        .expanduser()
        .resolve()
    )


class RemoteJobStore:
    statuses: tuple[RemoteJobStatus, ...] = (
        "queued",
        "running",
        "waiting_youtube",
        "completed",
        "failed",
        "cancelled",
    )

    def __init__(self, root: Path | None = None) -> None:
        self.root = remote_jobs_dir(root)
        for status in self.statuses:
            (self.root / status).mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str, status: RemoteJobStatus) -> Path:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("ID de job inválido.")
        return self.root / status / f"{job_id}.json"

    def log_path(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise ValueError("ID de job inválido.")
        return self.root / "logs" / f"{job_id}.log"

    def find(self, job_id: str) -> tuple[RemoteVideoJob, Path]:
        for status in self.statuses:
            path = self.path(job_id, status)
            if path.is_file():
                return RemoteVideoJob.model_validate_json(path.read_text(encoding="utf-8")), path
        raise FileNotFoundError(f"Job remoto não encontrado: {job_id}")

    def save(self, job: RemoteVideoJob) -> Path:
        object.__setattr__(job, "updated_at", utc_now())
        path = self.path(job.job_id, job.status)
        write_json(path, job)
        with suppress(OSError):
            path.chmod(0o600)
        return path

    def enqueue(
        self,
        request: RemoteVideoRequest,
        *,
        job_id: str | None = None,
        schedule_occurrence_id: str | None = None,
        now: datetime | None = None,
    ) -> RemoteVideoJob:
        if not request.external_services_authorized:
            raise ValueError(
                "O pedido precisa autorizar explicitamente Imagegen, ElevenLabs e upload privado."
            )
        current = now or datetime.now(UTC)
        if request.publish_at is not None and request.publish_at.astimezone(UTC) <= current:
            raise ValueError("publish_at precisa estar no futuro.")
        resolved_job_id = job_id or f"video-{current:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        if not JOB_ID_PATTERN.fullmatch(resolved_job_id):
            raise ValueError("ID de job inválido.")
        with suppress(FileNotFoundError):
            existing, _ = self.find(resolved_job_id)
            if (
                existing.request != request
                or existing.schedule_occurrence_id != schedule_occurrence_id
            ):
                raise RuntimeError("Colisão no identificador determinístico do job remoto.")
            return existing
        job = RemoteVideoJob(
            job_id=resolved_job_id,
            status="queued",
            request=request,
            created_at=utc_now(),
            updated_at=utc_now(),
            schedule_occurrence_id=schedule_occurrence_id,
        )
        self.save(job)
        return job

    def transition(
        self,
        job: RemoteVideoJob,
        status: RemoteJobStatus,
        **updates: Any,
    ) -> RemoteVideoJob:
        _, old_path = self.find(job.job_id)
        updated = job.model_copy(update={"status": status, "updated_at": utc_now(), **updates})
        new_path = self.path(job.job_id, status)
        write_json(new_path, updated)
        with suppress(OSError):
            new_path.chmod(0o600)
        if old_path != new_path:
            old_path.unlink(missing_ok=True)
        return updated

    def list(self, *, status: RemoteJobStatus | None = None) -> list[RemoteVideoJob]:
        statuses = (status,) if status else self.statuses
        jobs: list[RemoteVideoJob] = []
        for current in statuses:
            for path in (self.root / current).glob("video-*.json"):
                jobs.append(RemoteVideoJob.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def cancel(self, job_id: str) -> RemoteVideoJob:
        job, _ = self.find(job_id)
        if job.status not in {"queued", "waiting_youtube"}:
            raise RuntimeError("Somente jobs na fila ou aguardando o YouTube podem ser cancelados.")
        return self.transition(job, "cancelled", error=None)

    def retry(self, job_id: str) -> RemoteVideoJob:
        job, _ = self.find(job_id)
        if job.status != "failed":
            raise RuntimeError("Somente jobs com falha podem voltar para a fila.")
        return self.transition(
            job,
            "queued",
            error=None,
            codex_exit_code=None,
            next_retry_at=None,
        )

    def due_jobs(self, *, now: datetime | None = None) -> list[RemoteVideoJob]:
        current = now or datetime.now(UTC)
        return [job for job in reversed(self.list(status="queued")) if job_is_due(job, current)]


def job_is_due(job: RemoteVideoJob, now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if job.next_retry_at and current < job.next_retry_at.astimezone(UTC):
        return False
    request = job.request
    if request.not_before and current < request.not_before.astimezone(UTC):
        return False
    if job.automatic_recovery_count > 0:
        return True
    if request.start_policy == "overnight":
        local = current.astimezone(REMOTE_TIMEZONE)
        return 1 <= local.hour < 5
    return True


def _assert_project_path(path: Path, projects_root: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(projects_root.expanduser().resolve())
    except ValueError as exc:
        raise ValueError("O projeto do job está fora da raiz permitida.") from exc
    return resolved


def prepare_job_project(
    job: RemoteVideoJob,
    *,
    projects_root: Path | None = None,
    approved_store: Any | None = None,
) -> ProjectLayout:
    root = (projects_root or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    if job.project_path:
        return ProjectLayout(_assert_project_path(Path(job.project_path), root))
    request = job.request
    if request.production_profile == "approved_fast_v1":
        from pipeline_videos.approved_scripts import (
            ApprovedScriptStore,
            materialize_approved_project,
        )

        script_store = approved_store or ApprovedScriptStore(remote_jobs_dir())
        assert request.approved_script_id is not None
        approved, _ = script_store.find(request.approved_script_id)
        if approved.status != "reserved" or approved.reserved_by_job_id != job.job_id:
            raise RuntimeError("O roteiro pré-aprovado não está reservado por este job.")
        if approved.content_hash != request.approved_script_hash:
            raise RuntimeError("O hash do roteiro reservado diverge do pedido remoto.")
        layout = materialize_approved_project(
            approved,
            job_id=job.job_id,
            projects_root=root,
            publication_mode=request.publication_mode,
            voice_id=elevenlabs_voice_id_for_variant(request.voice_variant),
            voice_model_id=default_elevenlabs_model_id(),
        )
        if request.music_track is not None:
            import_music(layout, REMOTE_MUSIC_TRACK_PATHS[request.music_track])
        return layout
    video_format: Literal["landscape", "short"] = (
        "short" if request.video_format == "short" else "landscape"
    )
    assert request.duration_seconds is not None
    if request.character:
        layout, _, _ = create_auto_video(
            projects_dir=root,
            target_duration_seconds=request.duration_seconds,
            exact_duration=request.exact_duration,
            video_format=video_format,
            production_mode="final_candidate_one_shot",
            creative_mode_override=(
                request.creative_mode
                if request.creative_mode != "auto"
                else "biblical_storytelling"
            ),
            hook_family_override=(request.hook_family if request.hook_family != "auto" else None),
            requested_character=request.character,
            editorial_profile=request.editorial_profile,
        )
    elif request.topic:
        layout = create_project(
            slugify(request.topic),
            request.title or request.topic,
            projects_dir=root,
            target_duration_seconds=request.duration_seconds,
            exact_duration=request.exact_duration,
            video_format=video_format,
            production_mode="final_candidate_one_shot",
        )
    else:
        layout, _, _ = create_auto_video(
            projects_dir=root,
            target_duration_seconds=request.duration_seconds,
            exact_duration=request.exact_duration,
            video_format=video_format,
            production_mode="final_candidate_one_shot",
            creative_mode_override=(
                request.creative_mode if request.creative_mode != "auto" else None
            ),
            hook_family_override=(request.hook_family if request.hook_family != "auto" else None),
            editorial_profile=request.editorial_profile,
        )
    briefing = Briefing.model_validate(read_json(layout.briefing))
    briefing_updates: dict[str, Any] = {}
    compact = (
        request.video_format == "short"
        and request.duration_seconds < COMPACT_SHORT_THRESHOLD_SECONDS
    )
    if request.creative_mode != "auto":
        briefing_updates["creative_mode"] = request.creative_mode
        briefing_updates["interpretive_stance"] = default_interpretive_stance(request.creative_mode)
    elif compact and briefing.creative_mode == "auto":
        briefing_updates["creative_mode"] = "pastoral_encouragement"
        briefing_updates["interpretive_stance"] = "devotional"
    if request.hook_policy != "auto":
        briefing_updates["hook_policy"] = request.hook_policy
    if request.hook_family != "auto":
        briefing_updates["hook_family"] = request.hook_family
    if compact:
        briefing_updates.update(
            {
                "hook_policy": "required",
                "hook_family": "emotional_interruption",
                "hook_contract": "cinematic_interruption_v1",
                "call_to_action": "",
                "narration_tone": (
                    "íntimo, emocional e cinematográfico, como uma fala de Jesus em um filme épico"
                ),
            }
        )
    if briefing_updates:
        write_json(layout.briefing, briefing.model_copy(update=briefing_updates))
    manifest = layout.load_manifest()
    manifest.config.production.use_subagents = True
    if manifest.config.shorts_script.duration_profile != "adaptive_compact_v1":
        manifest.config.production.image_workers = 2
    manifest.config.shorts_script.use_specialist_subagent = True
    manifest.config.youtube_growth.use_specialist_subagent = True
    manifest.config.audio.provider = request.audio_provider
    manifest.config.audio.voice_id = elevenlabs_voice_id_for_variant(request.voice_variant)
    manifest.config.audio.model_id = default_elevenlabs_model_id()
    manifest.config.youtube_growth.title_variants = 1
    manifest.config.youtube_publication.allow_automated_private_review_upload = True
    manifest.config.youtube_publication.allow_automated_scheduled_release = (
        request.publication_mode == "scheduled"
    )
    layout.save_manifest(manifest)
    if request.music_track is not None:
        import_music(layout, REMOTE_MUSIC_TRACK_PATHS[request.music_track])
        manifest = layout.load_manifest()
    layout.append_decision(
        {
            "type": "remote_local_parity_job",
            "job_id": job.job_id,
            "start_policy": request.start_policy,
            "publication_mode": request.publication_mode,
            "publish_at": request.publish_at.isoformat() if request.publish_at else None,
            "title_variants": 1,
            "short_duration_profile": manifest.config.shorts_script.duration_profile,
            "creative_mode": request.creative_mode,
            "hook_policy": request.hook_policy,
            "hook_family": request.hook_family,
            "editorial_profile": request.editorial_profile,
            "music_track": request.music_track,
            "voice_variant": request.voice_variant,
            "voice_id": manifest.config.audio.voice_id,
            "voice_model_id": manifest.config.audio.model_id,
            "model": DEFAULT_REMOTE_CODEX_MODEL,
            "coordinator_reasoning_effort": DEFAULT_REMOTE_REASONING_EFFORT,
            "specialist_reasoning_effort": {
                "shorts_visual_planner": (
                    "medium"
                    if manifest.config.shorts_script.generation_mode
                    == "deterministic_compact_visual_plan_v1"
                    else None
                ),
                "shorts_script_specialist": (
                    "high"
                    if manifest.config.shorts_script.generation_mode == "specialist_full_package_v1"
                    else None
                ),
                "biblical_reviewer": (
                    "high"
                    if manifest.config.shorts_script.require_independent_biblical_review
                    else None
                ),
                "image_worker": "low",
                "youtube_growth_specialist": "medium",
            },
            "subagents": True,
            "human_approval_recorded": False,
        }
    )
    return layout


def _is_nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _is_png_file(path: Path) -> bool:
    if not _is_nonempty_file(path):
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _project_file(layout: ProjectLayout, value: str) -> Path:
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else layout.root / candidate).resolve()
    try:
        resolved.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho da fila fora do projeto: {value}") from exc
    return resolved


def codex_checkpoint_prompt(request_path: Path) -> str:
    return f"""Continue a produção deste vídeo pelo checkpoint descrito em `{request_path}`.

Na primeira execução, leia uma única vez AGENTS.md, README.md, docs/optimized-production.md e o
project.json indicado. Nas retomadas, preserve todas as decisões e o contexto desta mesma sessão.
Leia o JSON do checkpoint e os caminhos listados em allowed_inputs. Quando delegation.required for
true, crie exatamente os subagentes declarados, sempre com fork_turns=none, entregue a cada um
somente o input compacto atribuído e aguarde todos terminarem. O coordenador não deve substituir o
trabalho de um especialista. Escreva apenas os arquivos em required_outputs e use os comandos de
preflight declarados até a validação ficar válida. Não leia segredos, não aprove em nome do usuário
e não envie, publique ou agende no YouTube. Use somente o imagegen embutido quando solicitado;
nunca troque para API paga. Preserve moderation_blocked se as tentativas permitidas se esgotarem.
Responda de forma breve com o estado do checkpoint e as saídas produzidas.
"""


def _phase_payload(job: RemoteVideoJob, spec: CodexPhaseSpec) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "job_id": job.job_id,
        "phase": spec.phase,
        "item_id": spec.item_id,
        "contract": {
            "persistent_coordinator": True,
            "shared_state_writer": "deterministic_remote_executor",
            "may_create_subagents": True,
            "may_read_secrets": False,
            "may_import_or_approve": False,
            "may_upload_publish_or_schedule": False,
            "final_response_max_lines": 5,
        },
        **spec.payload,
        "required_outputs": [str(path) for path in spec.expected_outputs],
    }


def _available_verse_candidates(
    layout: ProjectLayout,
    *,
    projects_root: Path,
    requested_character: str | None = None,
) -> tuple[list[VerseCandidate], str | None, set[str], VerseUsageIndex]:
    from pipeline_videos.verse_selection import (
        eligible_main_candidate_ids,
        load_verse_catalog,
        sync_shared_usage,
    )

    catalog = load_verse_catalog()
    raw_sources = read_json(layout.sources).get("entries", []) if layout.sources.is_file() else []
    current_source_ids = {
        str(item.get("source_id")) for item in raw_sources if isinstance(item, dict)
    }
    current_main = next(
        (
            candidate.candidate_id
            for candidate in catalog.candidates
            if candidate.source_id in current_source_ids
        ),
        None,
    )
    usage = sync_shared_usage(projects_root, catalog=catalog)
    project = layout.load_manifest()
    main_eligible = eligible_main_candidate_ids(
        catalog,
        usage,
        video_format=project.video_format,
        max_verse_words=(
            project.config.shorts_script.max_literal_verse_words
            if project.video_format == "short"
            and project.config.shorts_script.duration_profile == "adaptive_compact_v1"
            else None
        ),
        required_spoken_by=(
            "jesus"
            if project.video_format == "short"
            and project.config.shorts_script.require_direct_jesus_speech
            else None
        ),
        minimum_package_words=(
            project.config.shorts_script.min_words
            if project.video_format == "short"
            and project.config.shorts_script.compact_narrative_standard
            == "cinematic_jesus_devotional_v1"
            else None
        ),
        maximum_package_words=(
            project.config.shorts_script.max_words
            if project.video_format == "short"
            and project.config.shorts_script.compact_narrative_standard
            == "cinematic_jesus_devotional_v1"
            else None
        ),
        minimum_hook_strength_score=project.config.shorts_script.minimum_hook_strength_score,
        minimum_message_value_score=project.config.shorts_script.minimum_message_value_score,
        minimum_rewatch_value_score=project.config.shorts_script.minimum_rewatch_value_score,
        requested_character=requested_character,
    )
    if current_main:
        main_eligible.add(current_main)
    if requested_character:
        available = [
            candidate
            for candidate in catalog.candidates
            if candidate.candidate_id in main_eligible
            or candidate.catalog_segment != "narrative_story"
        ]
    elif project.video_format == "short":
        available = [
            candidate for candidate in catalog.candidates if candidate.candidate_id in main_eligible
        ]
    else:
        available = [
            candidate
            for candidate in catalog.candidates
            if candidate.catalog_segment != "narrative_story"
        ]
    return available, current_main, main_eligible, usage


def _editorial_context_ready(layout: ProjectLayout) -> bool:
    if not layout.briefing.is_file() or not layout.sources.is_file():
        return False
    briefing = Briefing.model_validate(read_json(layout.briefing))
    sources = SourceLibrary.model_validate(read_json(layout.sources))
    verified_count = sum(
        1
        for source in sources.entries
        if source.kind == "biblical_text" and source.verified and source.excerpt
    )
    project = layout.load_manifest()
    creative_standard = (
        project.config.shorts_script.narrative_standard == "creative_retention_v3"
        if project.video_format == "short"
        else project.config.long_form_script.narrative_standard == "creative_retention_long_v2"
    )
    required = [
        briefing.main_passage,
        briefing.central_theme,
        briefing.audience,
        briefing.emotional_intent,
    ]
    if creative_standard:
        required.append(briefing.viewer_outcome)
        if briefing.creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES:
            required.append(briefing.practical_application)
    else:
        required.append(briefing.practical_application)
    if project.video_format == "short":
        target = 1
    else:
        from pipeline_videos.editorial import citation_target_range

        target, _ = citation_target_range(
            project.target_duration_seconds,
            project.config.editorial_policy,
        )
    creative_ready = not creative_standard or (
        briefing.creative_mode != "auto"
        and briefing.hook_policy != "auto"
        and briefing.hook_family != "auto"
        and (project.video_format != "short" or briefing.hook_family != "none")
    )
    return creative_ready and all(value.strip() for value in required) and verified_count >= target


def _editorial_sources_phase_spec(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    *,
    projects_root: Path,
) -> CodexPhaseSpec:
    from pipeline_videos.editorial import citation_target_range
    from pipeline_videos.verse_selection import (
        compact_hook_editorial_features,
        recent_compact_hook_count,
    )

    project = layout.load_manifest()
    candidates, current_main, main_eligible, usage = _available_verse_candidates(
        layout,
        projects_root=projects_root,
        requested_character=job.request.character,
    )
    desired_count = 1
    if project.video_format != "short":
        desired_count, _ = citation_target_range(
            project.target_duration_seconds,
            project.config.editorial_policy,
        )
    if len(candidates) < desired_count:
        raise RuntimeError("O catálogo não possui passagens inéditas suficientes para o job.")
    output = layout.root / "editorial" / "staging" / "remote-sources-selection.json"
    topic = job.request.topic or read_json(layout.briefing).get("central_theme") or project.title
    payload = {
        "allowed_inputs": [],
        "task": {
            "kind": "select_verified_biblical_sources",
            "topic": topic,
            "video_format": job.request.video_format,
            "requested_creative_mode": job.request.creative_mode,
            "desired_candidate_count": desired_count,
            "required_main_candidate_id": current_main,
            "main_eligible_candidate_ids": sorted(main_eligible),
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "reference": candidate.reference,
                    "context_reference": candidate.context_reference,
                    "verse_text": candidate.verse_text,
                    "title": candidate.title,
                    "title_seed": candidate.title_seed,
                    "central_theme": candidate.central_theme,
                    "practical_application": candidate.practical_application,
                    "human_conflict": candidate.human_conflict,
                    "central_question": candidate.central_question,
                    "visual_progression": candidate.visual_progression,
                    "possible_reveal": candidate.possible_reveal,
                    "retention_archetypes": candidate.retention_archetypes,
                    "preferred_development_modes": candidate.preferred_development_modes,
                    "retention_potential_score": candidate.retention_potential_score,
                    "themes": candidate.themes,
                    "characters": candidate.characters,
                    "character_aliases": candidate.character_aliases,
                    "related_passages": [
                        {
                            "reference": passage.reference,
                            "role": passage.role,
                        }
                        for passage in candidate.related_passages
                    ],
                    "interpretation_limits": candidate.interpretation_limits,
                    "spoken_by": candidate.spoken_by,
                    "catalog_segment": candidate.catalog_segment,
                    "recommended_formats": candidate.recommended_formats,
                    "source_text_scope": candidate.source_text_scope,
                    "compact_hook": candidate.compact_hook,
                    "compact_hook_strength_score": candidate.compact_hook_strength_score,
                    "compact_message_value_score": candidate.compact_message_value_score,
                    "compact_rewatch_value_score": candidate.compact_rewatch_value_score,
                    "compact_hook_editorial_features": (
                        compact_hook_editorial_features(candidate.compact_hook)
                        if candidate.compact_hook
                        else None
                    ),
                    "compact_hook_prior": (
                        "concrete_scroll_stop_v1"
                        if project.video_format == "short"
                        and project.config.shorts_script.duration_profile == "adaptive_compact_v1"
                        else None
                    ),
                    "recent_exact_hook_count": (
                        recent_compact_hook_count(usage, candidate.compact_hook)
                        if candidate.compact_hook
                        else 0
                    ),
                    "recent_exact_hook_penalty": (
                        -28 * recent_compact_hook_count(usage, candidate.compact_hook)
                        if candidate.compact_hook
                        else 0
                    ),
                }
                for candidate in candidates
            ],
            "output_schema": {
                "main_candidate_id": "string",
                "supporting_candidate_ids": ["string"],
                "rationale": "string curta em pt-BR",
            },
            "instructions": [
                "Escolha somente candidate_id fornecido; nunca invente passagem ou texto.",
                "Mantenha required_main_candidate_id como principal quando ele estiver preenchido.",
                "Escolha o principal somente entre main_eligible_candidate_ids.",
                "Selecione exatamente desired_candidate_count IDs distintos contando o principal.",
                "Prefira passagens complementares coerentes, sem transformar promessa em garantia.",
                "No perfil compacto, trate compact_hook_editorial_features como prior suave e "
                "rebaixe recent_exact_hook_count acima de zero; não reescreva o hook nem a "
                "citação.",
                f"Grave apenas JSON válido em {output}.",
            ],
        },
    }
    return CodexPhaseSpec(
        phase="editorial_sources",
        item_id=None,
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload=payload,
        expected_outputs=(output,),
    )


def _source_entry(candidate: VerseCandidate, *, long_form: bool) -> SourceEntry:
    from pipeline_videos.verse_selection import load_verse_catalog, source_entries_for_candidate

    catalog = load_verse_catalog()
    return source_entries_for_candidate(
        candidate,
        publisher=catalog.publisher,
        long_form=long_form,
    )[0]


def _import_editorial_source_selection(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    selection_path: Path,
    *,
    projects_root: Path,
) -> None:
    from pipeline_videos.state import complete_stage
    from pipeline_videos.utils import relative_posix, sha256_value
    from pipeline_videos.verse_selection import (
        load_verse_catalog,
        reserve_project_candidates,
        select_compact_devotional_mode,
        select_creative_mode,
        select_hook_family,
        select_retention_archetype,
        sync_shared_usage,
    )

    selection = read_json(selection_path)
    candidates, current_main, main_eligible, _usage = _available_verse_candidates(
        layout,
        projects_root=projects_root,
        requested_character=job.request.character,
    )
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    main_id = str(selection.get("main_candidate_id", ""))
    supporting = [str(item) for item in selection.get("supporting_candidate_ids", [])]
    if current_main and main_id != current_main:
        raise ValueError("A seleção remota tentou trocar a passagem principal já reservada.")
    if main_id not in main_eligible:
        raise ValueError("A passagem principal não está elegível no ciclo atual deste formato.")
    selected_ids = list(dict.fromkeys([main_id, *supporting]))
    project = layout.load_manifest()
    desired_count = 1
    if project.video_format != "short":
        from pipeline_videos.editorial import citation_target_range

        desired_count, _ = citation_target_range(
            project.target_duration_seconds,
            project.config.editorial_policy,
        )
    if len(selected_ids) != desired_count or any(item not in by_id for item in selected_ids):
        raise ValueError("A seleção remota de passagens não corresponde ao catálogo permitido.")
    selected = [by_id[item] for item in selected_ids]
    main = selected[0]
    usage = sync_shared_usage(projects_root, catalog=load_verse_catalog())
    retention_archetype = (
        select_retention_archetype(main, usage) if project.video_format == "short" else None
    )
    current = Briefing.model_validate(read_json(layout.briefing))
    compact = (
        project.video_format == "short"
        and project.config.shorts_script.duration_profile == "adaptive_compact_v1"
    )
    creative_mode = (
        job.request.creative_mode
        if job.request.creative_mode != "auto"
        else current.creative_mode
        if current.creative_mode != "auto"
        else select_compact_devotional_mode(usage)
        if compact
        else "biblical_storytelling"
        if job.request.character
        else select_creative_mode(main, usage, video_format=project.video_format)
    )
    interpretive_stance = default_interpretive_stance(creative_mode)
    hook_policy = (
        "required"
        if project.video_format == "short"
        else job.request.hook_policy
        if job.request.hook_policy != "auto"
        else current.hook_policy
        if current.hook_policy != "auto"
        else default_long_hook_policy(creative_mode)
    )
    requested_hook_family = (
        job.request.hook_family
        if job.request.hook_family != "auto"
        else current.hook_family
        if current.hook_family != "auto"
        else None
    )
    hook_family = (
        "emotional_interruption"
        if compact
        else "none"
        if hook_policy == "none"
        else requested_hook_family
        or select_hook_family(
            creative_mode,
            usage,
            video_format=project.video_format,
            hook_policy=hook_policy,
            hook_contract=current.hook_contract,
        )
    )
    if current.hook_contract == "user_six_v1" and hook_family not in USER_SIX_HOOK_FAMILIES:
        raise ValueError("Novos Shorts aceitam somente uma das seis famílias oficiais de hook.")
    reserve_project_candidates(
        projects_root,
        project_id=project.project_id,
        video_format=project.video_format,
        candidates=selected,
        catalog=load_verse_catalog(),
        retention_archetype=retention_archetype,
        creative_mode=creative_mode,
        hook_family=hook_family,
        duration_profile=(
            project.config.shorts_script.duration_profile
            if project.video_format == "short"
            else None
        ),
    )
    suggested_title = main.title_seed.strip() or main.title
    title = job.request.title or job.request.topic or current.title or suggested_title
    central_theme = current.central_theme.strip() or job.request.topic or main.central_theme
    call_to_action = "" if compact else current.call_to_action.strip()
    if not compact and not call_to_action:
        call_to_action = (
            "Escolher exatamente uma ação natural — comentar, inscrever-se ou compartilhar — "
            "conectada ao valor entregue, em até doze palavras e quatro segundos."
            if project.video_format == "short"
            else "Encerrar após a conclusão com exatamente um convite comunitário natural."
        )
    practical_application = current.practical_application.strip()
    if not practical_application and creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES:
        practical_application = main.practical_application
    viewer_outcome = current.viewer_outcome.strip() or viewer_outcome_for_mode(
        creative_mode,
        theme=central_theme,
        reveal=main.possible_reveal,
    )
    creative_spec = CREATIVE_MODE_SPECS[creative_mode]
    briefing = Briefing(
        title=title,
        main_passage=main.reference,
        complementary_references=list(
            dict.fromkeys(
                [
                    *[passage.reference for passage in main.related_passages],
                    *[candidate.reference for candidate in selected[1:]],
                ]
            )
        ),
        central_theme=central_theme,
        practical_application=practical_application,
        audience=current.audience.strip() or main.audience,
        emotional_intent=current.emotional_intent.strip() or main.emotional_intent,
        narration_tone=(
            "íntimo, emocional e cinematográfico, como uma fala de Jesus em um filme épico"
            if compact
            else narration_tone_for_mode(creative_mode)
        ),
        target_duration_seconds=project.target_duration_seconds,
        bible_translation=main.translation,
        literal_quote_policy=(
            "Citar literalmente somente excerpts verificados destas fontes em domínio público."
        ),
        interpretation_limits=list(
            dict.fromkeys(current.interpretation_limits or main.interpretation_limits)
        ),
        claims_to_avoid=list(dict.fromkeys(current.claims_to_avoid or main.claims_to_avoid)),
        visual_notes=list(
            dict.fromkeys(
                [
                    *(current.visual_notes or main.visual_notes),
                    *(
                        [
                            (
                                "Jesus é o centro emocional e visual da cena, em linguagem "
                                "de filme épico."
                            ),
                            (
                                "A câmera aproxima Jesus do espectador; não usar pregador "
                                "ou explicação visual."
                            ),
                        ]
                        if compact
                        else []
                    ),
                ]
            )
        ),
        call_to_action=call_to_action,
        retention_archetype=retention_archetype,
        human_conflict=current.human_conflict.strip() or main.human_conflict,
        central_question=current.central_question.strip() or main.central_question,
        visual_progression=current.visual_progression or main.visual_progression,
        possible_reveal=current.possible_reveal.strip() or main.possible_reveal,
        title_seed=current.title_seed.strip() or main.title_seed,
        creative_mode=creative_mode,
        interpretive_stance=interpretive_stance,
        hook_policy=hook_policy,
        hook_family=hook_family,
        hook_contract=current.hook_contract,
        viewer_outcome=viewer_outcome,
        creative_rationale=f"{creative_spec.label}: {creative_spec.purpose}",
    )
    current_sources = SourceLibrary.model_validate(read_json(layout.sources))
    non_biblical = [source for source in current_sources.entries if source.kind != "biblical_text"]
    from pipeline_videos.verse_selection import source_entries_for_candidate

    biblical_sources = [
        source
        for candidate in selected
        for source in source_entries_for_candidate(
            candidate,
            publisher=load_verse_catalog().publisher,
            long_form=project.video_format != "short",
        )
    ]
    sources = SourceLibrary(entries=[*biblical_sources, *non_biblical])
    write_json(layout.briefing, briefing)
    write_json(layout.sources, sources)
    digest = sha256_value({"briefing": briefing, "sources": sources})
    complete_stage(
        layout,
        "briefing",
        digest,
        [
            relative_posix(layout.briefing, layout.root),
            relative_posix(layout.sources, layout.root),
        ],
        provider="remote-catalog-source-selector",
        note="Fontes verificadas selecionadas; aprovação humana permanece pendente.",
    )
    layout.append_decision(
        {
            "type": "remote_verified_source_selection",
            "job_id": job.job_id,
            "candidate_ids": selected_ids,
            "references": [candidate.reference for candidate in selected],
            "rationale": str(selection.get("rationale", ""))[:500],
            "retention_archetype": retention_archetype,
            "creative_mode": creative_mode,
            "interpretive_stance": interpretive_stance,
            "hook_policy": hook_policy,
            "hook_family": hook_family,
            "human_approval_recorded": False,
        }
    )
    sync_shared_usage(projects_root, catalog=load_verse_catalog())


def _shorts_script_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    queue = read_json(layout.shorts_script_queue)
    context = _project_file(layout, queue["inputs"]["compact_context"])
    package = _project_file(layout, queue["outputs"]["package"])
    visual_plan_only = queue.get("generation_mode") == "deterministic_compact_visual_plan_v1"
    specialist_effort = str(
        queue.get("orchestration", {}).get(
            "reasoning_effort", "medium" if visual_plan_only else "high"
        )
    )
    return CodexPhaseSpec(
        phase="shorts_script",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "delegation": {
                "required": True,
                "role": (
                    "shorts_visual_planner" if visual_plan_only else "shorts_script_specialist"
                ),
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": specialist_effort,
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": [str(layout.shorts_script_queue), str(context)],
            "task": {
                "kind": (
                    "produce_compact_visual_plan"
                    if visual_plan_only
                    else "produce_shorts_script_package"
                ),
                "queue_path": str(layout.shorts_script_queue),
                "compact_context_path": str(context),
                "instructions": [
                    "Siga integralmente instructions e schemas da fila.",
                    ("Escreva somente outputs.package; o executor fará a importação e validação."),
                    "Não registre aprovação humana e preserve qualquer finding blocking real.",
                ],
            },
        },
        expected_outputs=(package,),
    )


def _biblical_review_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    queue = read_json(layout.shorts_biblical_review_queue)
    context = _project_file(layout, queue["inputs"]["compact_context"])
    review = _project_file(layout, queue["outputs"]["review"])
    return CodexPhaseSpec(
        phase="biblical_review",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "delegation": {
                "required": True,
                "role": "biblical_reviewer",
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": "high",
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": [str(layout.shorts_biblical_review_queue), str(context)],
            "task": {
                "kind": "review_shorts_script_biblical_accuracy",
                "queue_path": str(layout.shorts_biblical_review_queue),
                "compact_context_path": str(context),
                "instructions": [
                    "Siga integralmente instructions e o schema da fila.",
                    "Escreva somente outputs.review; o executor fará o preflight e o import.",
                    "Não reescreva o roteiro e não registre aprovação humana.",
                ],
            },
        },
        expected_outputs=(review,),
    )


_SHORTS_HOOK_TYPES = {
    "scripture_opening",
    "curiosity_gap",
    "contrast",
    "direct_tension",
    "surprising_detail",
}
_SHORTS_TECHNIQUE_ONLY_HOOK_TYPES = {
    "self_relevance",
    "specificity",
    "pattern_interrupt",
    "open_loop",
    "emotional_mirroring",
}


def _normalize_shorts_script_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    changes: list[str] = []
    strategy = payload.get("strategy")
    if not isinstance(strategy, dict):
        return payload, None
    hook_type = strategy.get("hook_type")
    if hook_type not in _SHORTS_HOOK_TYPES and hook_type in _SHORTS_TECHNIQUE_ONLY_HOOK_TYPES:
        techniques = strategy.get("hook_techniques")
        technique_values = set(techniques) if isinstance(techniques, list) else set()
        if "contrast" in technique_values:
            replacement = "contrast"
        elif technique_values.intersection({"curiosity_gap", "open_loop"}):
            replacement = "curiosity_gap"
        elif hook_type == "pattern_interrupt":
            replacement = "surprising_detail"
        else:
            replacement = "direct_tension"
        strategy["hook_type"] = replacement
        changes.append(f"strategy.hook_type: {hook_type} -> {replacement}")

    script = payload.get("script")
    sections = script.get("sections") if isinstance(script, dict) else None
    creative = strategy.get("creative_direction")
    if isinstance(creative, dict) and isinstance(sections, list):
        organizing_question = creative.get("organizing_question")
        content_promise = creative.get("content_promise")
        expected_loop = (
            organizing_question
            if isinstance(organizing_question, str) and organizing_question.strip()
            else content_promise
        )
        synchronization = (
            ("content_promise", "content_promise"),
            ("payoff", "resolution"),
            ("practical_value", "viewer_takeaway"),
            ("hook_family", "hook_family"),
        )
        for strategy_field, creative_field in synchronization:
            value = creative.get(creative_field)
            if isinstance(value, str) and value.strip() and strategy.get(strategy_field) != value:
                strategy[strategy_field] = value
                changes.append(f"strategy.{strategy_field}: creative_direction.{creative_field}")
        if isinstance(expected_loop, str) and strategy.get("open_loop") != expected_loop:
            strategy["open_loop"] = expected_loop
            changes.append("strategy.open_loop: creative_direction")
        engagement = creative.get("engagement")
        spoken_text = engagement.get("spoken_text") if isinstance(engagement, dict) else None
        if (
            isinstance(spoken_text, str)
            and spoken_text.strip()
            and strategy.get("cta") != spoken_text
        ):
            strategy["cta"] = spoken_text
            changes.append("strategy.cta: creative_direction.engagement.spoken_text")

        section_ids = [
            section.get("section_id")
            for section in sections
            if isinstance(section, dict) and isinstance(section.get("section_id"), str)
        ]
        if strategy.get("section_ids") != section_ids:
            strategy["section_ids"] = section_ids
            changes.append("strategy.section_ids: seções do roteiro")

    story = strategy.get("retention_story")
    if isinstance(story, dict) and isinstance(sections, list):
        synchronization = (
            ("content_promise", "opening_promise"),
            ("open_loop", "central_question"),
            ("payoff", "payoff"),
            ("practical_value", "practical_response"),
        )
        for strategy_field, story_field in synchronization:
            value = story.get(story_field)
            if isinstance(value, str) and value.strip() and strategy.get(strategy_field) != value:
                strategy[strategy_field] = value
                changes.append(f"strategy.{strategy_field}: retention_story.{story_field}")
        engagement = story.get("engagement")
        spoken_text = engagement.get("spoken_text") if isinstance(engagement, dict) else None
        if (
            isinstance(spoken_text, str)
            and spoken_text.strip()
            and strategy.get("cta") != spoken_text
        ):
            strategy["cta"] = spoken_text
            changes.append("strategy.cta: retention_story.engagement.spoken_text")

        section_ids = [
            section.get("section_id")
            for section in sections
            if isinstance(section, dict) and isinstance(section.get("section_id"), str)
        ]
        if strategy.get("section_ids") != section_ids:
            strategy["section_ids"] = section_ids
            changes.append("strategy.section_ids: seções do roteiro")
        verse_opening = strategy.get("verse_opening")
        primary_source_id = (
            verse_opening.get("source_id") if isinstance(verse_opening, dict) else None
        )
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id", "desconhecida")
            if primary_source_id and not section.get("biblical_basis"):
                section["biblical_basis"] = [primary_source_id]
                changes.append(f"script.sections.{section_id}.biblical_basis: fonte principal")
            if not section.get("claim_classification"):
                section["claim_classification"] = ["interpretation"]
                changes.append(f"script.sections.{section_id}.claim_classification: interpretação")

    spine = strategy.get("narrative_spine")
    if isinstance(spine, dict) and isinstance(sections, list):
        section_by_id = {
            section.get("section_id"): section
            for section in sections
            if isinstance(section, dict) and isinstance(section.get("section_id"), str)
        }
        mapped_fields = (
            ("tension_question", "tension_section_id"),
            ("bridge_statement", "bridge_section_id"),
            ("central_truth", "meaning_section_id"),
            ("practical_response", "application_section_id"),
        )
        for field_name, section_field in mapped_fields:
            section = section_by_id.get(spine.get(section_field))
            narration = section.get("narration") if isinstance(section, dict) else None
            current = spine.get(field_name)
            if (
                isinstance(narration, str)
                and narration.strip()
                and (
                    not isinstance(current, str)
                    or re.sub(r"\W+", " ", current.casefold()).strip()
                    not in re.sub(r"\W+", " ", narration.casefold()).strip()
                )
            ):
                spine[field_name] = narration.strip()
                changes.append(f"strategy.narrative_spine.{field_name}: seção mapeada")
        tension = spine.get("tension_question")
        if isinstance(tension, str) and strategy.get("open_loop") != tension:
            strategy["open_loop"] = tension
            changes.append("strategy.open_loop: tensão central")

        section_ids = [
            section.get("section_id")
            for section in sections
            if isinstance(section, dict) and isinstance(section.get("section_id"), str)
        ]
        if strategy.get("section_ids") != section_ids:
            strategy["section_ids"] = section_ids
            changes.append("strategy.section_ids: seções do roteiro")

        verse_opening = strategy.get("verse_opening")
        primary_source_id = (
            verse_opening.get("source_id") if isinstance(verse_opening, dict) else None
        )
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = section.get("section_id", "desconhecida")
            if primary_source_id and not section.get("biblical_basis"):
                section["biblical_basis"] = [primary_source_id]
                changes.append(f"script.sections.{section_id}.biblical_basis: fonte principal")
            if not section.get("claim_classification"):
                section["claim_classification"] = ["interpretation"]
                changes.append(f"script.sections.{section_id}.claim_classification: interpretação")

    return payload, "; ".join(changes) or None


def _shorts_script_correction_checkpoint(
    layout: ProjectLayout,
    validation_error: Exception,
) -> CodexPhaseSpec:
    queue = read_json(layout.shorts_script_queue)
    package = _project_file(layout, queue["outputs"]["package"])
    context_value = queue.get("inputs", {}).get("compact_context")
    context = _project_file(layout, context_value) if context_value else None
    visual_plan_only = queue.get("generation_mode") == "deterministic_compact_visual_plan_v1"
    return CodexPhaseSpec(
        phase="shorts_script_correction",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "delegation": {
                "required": True,
                "role": (
                    "shorts_visual_planner" if visual_plan_only else "shorts_script_specialist"
                ),
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": "medium" if visual_plan_only else "high",
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": [
                str(layout.shorts_script_queue),
                *([str(context)] if context is not None else []),
                str(package),
            ],
            "task": {
                "kind": (
                    "correct_compact_visual_plan"
                    if visual_plan_only
                    else "correct_shorts_script_package"
                ),
                "queue_path": str(layout.shorts_script_queue),
                "compact_context_path": str(context) if context is not None else None,
                "package_path": str(package),
                "validation_error": str(validation_error)[:4000],
                "instructions": [
                    (
                        "Corrija somente os campos necessários para satisfazer o schema e as "
                        "regras da fila."
                    ),
                    "Preserve roteiro, citações, revisão bíblica e direção visual válidos.",
                    (
                        "Não crie conteúdo novo quando bastar corrigir enum, tipo, campo ausente "
                        "ou vínculo."
                    ),
                    (
                        "Sobrescreva apenas package_path e execute o preflight da fila. Corrija "
                        "todos os erros retornados até obter valid=true."
                    ),
                    (
                        "validation_error tambÃ©m pode vir de uma etapa determinÃ­stica posterior "
                        "ao preflight. Nesse caso, corrija o pacote para satisfazer explicitamente "
                        "essa restriÃ§Ã£o antes de encerrar."
                    ),
                    (
                        "Copie job_id e input_hash da fila atual, mesmo quando package_path ainda "
                        "contiver uma versÃ£o anterior do pacote."
                    ),
                ],
            },
        },
        expected_outputs=(package,),
    )


def _shorts_script_correction_is_available(
    job: RemoteVideoJob,
    layout: ProjectLayout,
) -> bool:
    correction_runs = sum(
        run.phase == "shorts_script_correction" and run.job_attempt == job.attempts
        for run in job.codex_runs
    )
    maximum = layout.load_manifest().config.production.max_model_repairs_per_stage
    return correction_runs < maximum


def _shorts_pipeline_repair_is_applicable(layout: ProjectLayout) -> bool:
    project = layout.load_manifest()
    return (
        project.video_format == "short"
        and project.config.shorts_script.enabled
        and project.stages["scenes"].status == StageStatus.FAILED
    )


def _repair_shorts_pipeline_failure(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    error: Exception,
    *,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    from pipeline_videos.shorts_script import (
        check_reviewed_shorts_script_staging,
        import_shorts_script_package,
        materialize_compact_visual_fallback,
        prepare_shorts_biblical_review_queue,
        prepare_shorts_script_queue,
        validate_shorts_script_package_staging,
    )

    latest, _ = store.find(job.job_id)
    if not _shorts_script_correction_is_available(latest, layout):
        project = layout.load_manifest()
        if project.config.shorts_script.generation_mode == "deterministic_compact_visual_plan_v1":
            prepare_shorts_script_queue(layout, force=True)
            materialize_compact_visual_fallback(layout)
            import_shorts_script_package(layout, force=True)
            return latest
        raise RuntimeError(
            "A tentativa atual esgotou dois checkpoints de correÃ§Ã£o do roteiro de Short. "
            "O job serÃ¡ retomado automaticamente em uma nova tentativa."
        ) from error
    prepare_shorts_script_queue(layout, force=True)
    latest = _execute_agent_checkpoint(
        latest,
        _shorts_script_correction_checkpoint(layout, error),
        store,
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
    )
    validate_shorts_script_package_staging(layout)
    project = layout.load_manifest()
    if project.config.shorts_script.require_independent_biblical_review:
        prepare_shorts_biblical_review_queue(layout, force=True)
        latest = _execute_agent_checkpoint(
            latest,
            _biblical_review_phase_spec(layout),
            store,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
        reviewed = check_reviewed_shorts_script_staging(layout)
        if not reviewed.valid:
            raise RuntimeError(
                "A revisão bíblica independente bloqueou o pacote corrigido: "
                + "; ".join(reviewed.errors)
            )
    import_shorts_script_package(layout, force=True)
    layout.append_event(
        {
            "event": "remote_pipeline_automatic_repair",
            "job_id": job.job_id,
            "failed_stage": "scenes",
            "error": str(error)[:2000],
        }
    )
    return latest


def _ensure_valid_shorts_script_staging(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    *,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    project = layout.load_manifest()
    if (
        project.video_format != "short"
        or project.stages["shorts_script"].status == StageStatus.COMPLETED
        or not _is_nonempty_file(layout.shorts_script_package_staging)
    ):
        return job

    payload = read_json(layout.shorts_script_package_staging)
    if isinstance(payload, dict):
        normalized, change = _normalize_shorts_script_payload(payload)
        if change:
            write_json(layout.shorts_script_package_staging, normalized)
            layout.append_event(
                {
                    "event": "shorts_script_staging_normalized",
                    "job_id": job.job_id,
                    "change": change,
                    "codex_tokens_used": 0,
                }
            )

    from pipeline_videos.shorts_script import (
        materialize_compact_visual_fallback,
        validate_shorts_script_package_staging,
    )

    try:
        validate_shorts_script_package_staging(layout)
        return job
    except (ValueError, TypeError, KeyError) as exc:
        latest, _ = store.find(job.job_id)
        if not _shorts_script_correction_is_available(latest, layout):
            if (
                project.config.shorts_script.generation_mode
                == "deterministic_compact_visual_plan_v1"
            ):
                materialize_compact_visual_fallback(layout)
                validate_shorts_script_package_staging(layout)
                return latest
            raise RuntimeError(
                "O pacote de roteiro continuou inválido após as correções da sessão principal. "
                f"Último erro: {exc}"
            ) from exc
        job = _execute_agent_checkpoint(
            latest,
            _shorts_script_correction_checkpoint(layout, exc),
            store,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
        try:
            validate_shorts_script_package_staging(layout)
        except (ValueError, TypeError, KeyError) as repair_exc:
            if (
                project.config.shorts_script.generation_mode
                == "deterministic_compact_visual_plan_v1"
            ):
                materialize_compact_visual_fallback(layout)
                validate_shorts_script_package_staging(layout)
                return job
            raise RuntimeError(
                "O pacote de roteiro continuou inválido após o checkpoint de correção. "
                f"Último erro: {repair_exc}"
            ) from repair_exc
        return job


def _image_phase_specs(layout: ProjectLayout) -> list[CodexPhaseSpec]:
    queue = read_json(layout.image_queue)
    pending_items = [
        item
        for item in queue.get("items", [])
        if isinstance(item, dict)
        and item.get("status") == StageStatus.WAITING_EXTERNAL.value
        and not _is_nonempty_file(_project_file(layout, str(item["staging_path"])))
    ]
    if not pending_items:
        return []

    groups: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(pending_items):
        worker_id = str(item.get("worker_id") or f"image-worker-{(index % 2) + 1}")
        groups.setdefault(worker_id, []).append(item)
    if len(groups) > 2:
        redistributed: dict[str, list[dict[str, Any]]] = {
            "image-worker-1": [],
            "image-worker-2": [],
        }
        for index, item in enumerate(pending_items):
            redistributed[f"image-worker-{(index % 2) + 1}"].append(item)
        groups = redistributed

    declared = queue.get("orchestration", {}).get("worker_job_files", {})
    declared = declared if isinstance(declared, dict) else {}
    assignments: list[dict[str, Any]] = []
    expected_outputs: list[Path] = []
    allowed_inputs: list[str] = []
    for worker_id, items in groups.items():
        relative_job_path = declared.get(worker_id)
        if isinstance(relative_job_path, str):
            job_path = _project_file(layout, relative_job_path)
        else:
            job_path = layout.image_jobs / f"remote-{worker_id}.json"
            write_json(
                job_path,
                {
                    "schema_version": "1.0",
                    "project_id": queue.get("project_id"),
                    "batch_id": queue.get("batch_id"),
                    "worker_id": worker_id,
                    "role": "image_worker",
                    "context_policy": "compact_only",
                    "fork_turns": "none",
                    "recommended_model": DEFAULT_REMOTE_CODEX_MODEL,
                    "reasoning_effort": "low",
                    "generation_retry_policy": queue.get("generation_retry_policy", {}),
                    "visual_standard": queue.get("visual_standard", {}),
                    "jobs": items,
                },
            )
        outputs = [_project_file(layout, str(item["staging_path"])) for item in items]
        expected_outputs.extend(outputs)
        allowed_inputs.append(str(job_path))
        assignments.append(
            {
                "role": "image_worker",
                "worker_id": worker_id,
                "job_path": str(job_path),
                "required_outputs": [str(path) for path in outputs],
            }
        )

    return [
        CodexPhaseSpec(
            phase="image_workers",
            item_id=str(queue.get("batch_id") or "batch"),
            model=DEFAULT_REMOTE_CODEX_MODEL,
            reasoning_effort=DEFAULT_REMOTE_REASONING_EFFORT,
            payload={
                "delegation": {
                    "required": True,
                    "role": "image_worker",
                    "count": len(assignments),
                    "max_parallel": 2,
                    "fork_turns": "none",
                    "reasoning_effort": "low",
                    "model": DEFAULT_REMOTE_CODEX_MODEL,
                    "assignments": assignments,
                },
                "allowed_inputs": allowed_inputs,
                "task": {
                    "kind": "delegate_image_worker_batches",
                    "tool": "$imagegen",
                    "visual_standard": queue.get("visual_standard", {}),
                    "generation_retry_policy": queue.get("generation_retry_policy", {}),
                    "instructions": [
                        "Crie um image_worker por assignment e inicie os dois em paralelo.",
                        (
                            "Cada trabalhador deve ler somente seu job_path e gravar seus "
                            "staging_path."
                        ),
                        *imagegen_retry_instructions(asset_label="job de imagem"),
                        (
                            "Nenhum trabalhador pode importar, aprovar ou alterar estado "
                            "compartilhado."
                        ),
                    ],
                },
            },
            expected_outputs=tuple(expected_outputs),
        )
    ]


def _growth_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    queue = read_json(layout.growth_queue)
    context = _project_file(layout, queue["inputs"]["context"])
    package = _project_file(layout, queue["outputs"]["package"])
    allowed_inputs = [str(layout.growth_queue), str(context)]
    return CodexPhaseSpec(
        phase="youtube_growth",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort=DEFAULT_REMOTE_REASONING_EFFORT,
        payload={
            "delegation": {
                "required": True,
                "role": "youtube_growth_specialist",
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": "medium",
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": allowed_inputs,
            "task": {
                "kind": "produce_youtube_metadata",
                "tool": None,
                "queue_path": str(layout.growth_queue),
                "context_path": str(context),
                "instructions": [
                    "Siga integralmente instructions e schemas da fila.",
                    "Gere somente o pacote de título, descrição, tags e comentário fixado.",
                    "Escreva apenas outputs.package.",
                    "O executor importará o pacote; não altere metadados compartilhados.",
                ],
            },
        },
        expected_outputs=(package,),
    )


def _growth_correction_checkpoint(
    layout: ProjectLayout,
    validation_error: Exception,
) -> CodexPhaseSpec:
    base_spec = _growth_phase_spec(layout)
    package = base_spec.expected_outputs[0]
    payload = dict(base_spec.payload)
    allowed_inputs = list(payload["allowed_inputs"])
    if package.is_file():
        allowed_inputs.append(str(package))
    task = dict(payload["task"])
    task.update(
        {
            "kind": "correct_youtube_metadata",
            "validation_error": str(validation_error)[:4000],
            "instructions": [
                "Corrija somente o pacote de metadados que falhou na validação.",
                ("Preserve título, promessa, passagem e todos os campos já válidos."),
                (
                    "Nesta correção, ignore a regra de pular staging já existente: sobrescreva "
                    "explicitamente cada saída que precisar ser corrigida."
                ),
                (
                    "Sobrescreva somente outputs.package. Não altere project.json, fila, "
                    "aprovações ou outros ativos."
                ),
            ],
        }
    )
    payload["allowed_inputs"] = allowed_inputs
    payload["task"] = task
    return CodexPhaseSpec(
        phase="youtube_growth_correction",
        item_id=base_spec.item_id,
        model=base_spec.model,
        reasoning_effort=base_spec.reasoning_effort,
        payload=payload,
        expected_outputs=base_spec.expected_outputs,
    )


def _growth_correction_is_available(job: RemoteVideoJob) -> bool:
    correction_runs = sum(
        run.phase == "youtube_growth_correction" and run.job_attempt == job.attempts
        for run in job.codex_runs
    )
    return correction_runs < 1


def _growth_package_repair_is_applicable(layout: ProjectLayout) -> bool:
    project = layout.load_manifest()
    return (
        project.config.youtube_growth.enabled
        and project.stages["youtube_growth"].status == StageStatus.WAITING_EXTERNAL
        and _is_nonempty_file(layout.growth_package_staging)
    )


def _repair_growth_package_failure(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    error: Exception,
    *,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    latest, _ = store.find(job.job_id)
    if not _growth_correction_is_available(latest):
        project = layout.load_manifest()
        if project.config.youtube_growth.generation_mode == "compact_metadata_draft_v1":
            from pipeline_videos.growth import materialize_compact_growth_fallback

            materialize_compact_growth_fallback(layout)
            return latest
        raise RuntimeError(
            "A tentativa atual já usou o checkpoint único de correção do crescimento. "
            "O job será retomado automaticamente em uma nova tentativa."
        ) from error
    corrected = _execute_agent_checkpoint(
        latest,
        _growth_correction_checkpoint(layout, error),
        store,
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
    )
    layout.append_event(
        {
            "event": "remote_growth_automatic_repair",
            "job_id": job.job_id,
            "error": str(error)[:2000],
        }
    )
    return corrected


def _long_script_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    from pipeline_videos.editorial import citation_target_range
    from pipeline_videos.long_form_script import minimum_long_form_reengagements

    project = layout.load_manifest()
    long_config = project.config.long_form_script
    minimum, maximum = citation_target_range(
        project.target_duration_seconds,
        project.config.editorial_policy,
    )
    required_reengagements = minimum_long_form_reengagements(
        project.target_duration_seconds,
        long_config,
    )
    script_output = layout.root / "script" / "staging" / "remote-long-script" / "script.json"
    review_output = (
        layout.root / "reviews" / "staging" / "remote-long-script" / "biblical-review.json"
    )
    briefing = Briefing.model_validate(read_json(layout.briefing))
    is_creative = long_config.narrative_standard == "creative_retention_long_v2"
    creative_contract = {
        "narrative_standard": long_config.narrative_standard,
        "content_promise_source": "briefing.central_theme",
        "resolution_source": ("writer_synthesis_supported_by_sources" if is_creative else None),
        "viewer_takeaway_source": "briefing.viewer_outcome" if is_creative else None,
        "payoff_source": "briefing.practical_application" if not is_creative else None,
        "pacing_profile": "long_creative_v2" if is_creative else "long_retention_v1",
        "creative_mode": briefing.creative_mode if is_creative else None,
        "interpretive_stance": briefing.interpretive_stance if is_creative else None,
        "mode_guidance": (creative_mode_payload(briefing.creative_mode) if is_creative else None),
        "hook_policy": briefing.hook_policy if is_creative else "required",
        "hook_family": briefing.hook_family if is_creative else None,
        "hook_templates": HOOK_TEMPLATE_EXAMPLES if is_creative else None,
        "opening_target_max_seconds": long_config.opening_target_max_seconds,
        "opening_value_max_seconds": long_config.opening_value_max_seconds,
        "required_reengagements": required_reengagements,
        "maximum_reengagement_gap_seconds": long_config.maximum_reengagement_gap_seconds,
        "resolution_timing_options": (
            ["distributed", "late", "final_quarter"] if is_creative else None
        ),
        "payoff_start_minimum_ratio": (
            long_config.payoff_start_minimum_ratio if not is_creative else None
        ),
        "application_required": (
            briefing.creative_mode in APPLICATION_REQUIRED_CREATIVE_MODES if is_creative else True
        ),
        "callback_required": long_config.require_callback if not is_creative else False,
        "cta_max_words": long_config.cta_max_words,
        "cta_max_seconds": long_config.cta_max_seconds,
        "cta_actions": ["comment", "subscribe", "share"],
        "storytelling_profile": (
            "biblical_character_storytelling_v1"
            if is_creative and briefing.creative_mode == "biblical_storytelling"
            else None
        ),
        "authorial_narration_standard": "human_authorial_voice_v1",
        "narrator_identity": "jesus_first_person_v1",
        "visual_scene_standard": "narration_block_visual_v1",
        "visual_style": "biblical-midcentury-gouache-v1",
    }
    authorial_style_instructions = [
        (
            "Toda a voz autoral é Jesus narrando em primeira pessoa. Escreva como Jesus contando "
            "o que viveu, ensinou, viu, fez e sentiu, sem transformar a narração em uma imitação "
            "de versículo."
        ),
        (
            "Separe com rigor a dramatização autoral das citações: apenas excerpts verificados "
            "entre aspas são palavras bíblicas literais. As demais frases em primeira pessoa são "
            "narração dramatizada sustentada pelas fontes."
        ),
        (
            "Aplique human_authorial_voice_v1 a toda locução autoral, independentemente do modo. "
            "Narre diretamente o fato, a ação, a dúvida ou a explicação e nunca descreva o próprio "
            "método de leitura."
        ),
        (
            "São proibidas fórmulas metalinguísticas como 'o relato registra', 'o relato não "
            "explica', 'a sequência é compacta', 'o retrato começa', 'a passagem paralela "
            "acrescenta', 'comparar é observar', 'não podemos afirmar' e equivalentes."
        ),
        (
            "Não valorize uma ação dizendo antes o que alguém não fez e não defina cenas por "
            "listas de ausência como 'afirmação, sem A, B ou C'. Também não use a fórmula "
            "'não A nem B, mas C'; declare C diretamente. Citações bíblicas literais permanecem "
            "intactas."
        ),
    ]
    storytelling_instructions = (
        [
            (
                "Aplique biblical_character_storytelling_v1: conte a história em voz alta como "
                "uma sequência humana de cenas, escolhas e consequências. A precisão bíblica "
                "deve controlar o que é escrito sem virar assunto da locução."
            ),
            (
                "Use todas as passagens relacionadas para avançar a linha do tempo disponível "
                "do personagem. Não permaneça analisando a passagem-âncora nem a repita para "
                "preencher duração."
            ),
            (
                "A maior parte das seções entre opening e closing deve usar role="
                "biblical_narrative. Nos beats, use progression, context, evidence, reveal e "
                "reengagement; não use analysis nem comparison."
            ),
            (
                "Não narre o método de leitura. São proibidas formulações como 'o relato "
                "registra', 'o relato não explica', 'a sequência é compacta', 'o retrato "
                "começa', 'o primeiro traço', 'a passagem paralela acrescenta', 'comparar é "
                "observar', 'sem preencher os silêncios' e equivalentes."
            ),
            (
                "Não use negação retórica para valorizar a ação seguinte. São inválidas tanto "
                "listas de ausência ('a cena começa, sem biografia, preparação ou explicação') "
                "quanto frases isoladas como 'não fez um discurso longo' seguidas do que o "
                "personagem fez. Não acumule 'sem', 'não' ou 'nem'. Narre diretamente a ação "
                "positiva e concreta: 'Jesus encontrou Mateus na coletoria e disse: Siga-me'."
            ),
            (
                "Quando um detalhe não estiver sustentado, simplesmente não o inclua. Mencione "
                "uma dúvida ou tradição somente se ela for indispensável para compreender a "
                "história e houver fonte apropriada; faça isso em uma frase natural, sem aula "
                "sobre interpretação."
            ),
            (
                "Depois de cada citação, retome imediatamente quem fez o quê, onde a situação "
                "mudou ou qual consequência veio a seguir. Evite duas passagens paralelas que "
                "apenas recontam o mesmo momento sem acrescentar uma etapa da vida."
            ),
        ]
        if is_creative and briefing.creative_mode == "biblical_storytelling"
        else []
    )
    if is_creative:
        instructions = [
            (
                "Escreva locução integralmente em pt-BR e siga briefing.narration_tone. O tom "
                "pode ser factual, didático, investigativo, filosófico ou pastoral conforme "
                "creative_mode; não converta automaticamente o conteúdo em aconselhamento."
            ),
            *authorial_style_instructions,
            (
                "Use somente excerpts verificados de sources em citações literais e distribua "
                "entre "
                f"{minimum} e {maximum} citações ao longo do vídeo."
            ),
            "Verbalize Livro, capítulo e versículo; deixe somente o excerpt entre aspas.",
            (
                "Classifique cada afirmação como biblical_text, verified_context, interpretation "
                "ou creative_elaboration. Em postura factual, não use interpretação nem "
                "elaboração criativa; em postura exploratória, deixe opinião e inferência "
                "explicitamente distinguíveis da Bíblia."
            ),
            (
                "Preencha long_form_strategy.standard=creative_retention_long_v2 e copie do "
                "briefing creative_mode, interpretive_stance, hook_policy e hook_family. Não "
                "preencha archetype: a estrutura vem do modo criativo, não dos três arquétipos "
                "legados."
            ),
            (
                "Faça opening_line ser o prefixo literal da locução. Se hook_family=none, deixe "
                "opening_hook vazio, use orientation no primeiro beat e entregue valor até "
                f"{long_config.opening_value_max_seconds:.0f}s. Caso contrário, copie opening_line "
                "em opening_hook, marque hook no primeiro beat, siga a família escolhida e conclua "
                f"o hook até {long_config.opening_target_max_seconds:.0f}s."
            ),
            (
                "Não use saudação, apresentação do canal ou 'no vídeo de hoje'. Um longo sem hook "
                "continua precisando de uma abertura específica e útil; não simule urgência."
            ),
            (
                "Copie briefing.audience em target_viewer, briefing.central_theme em "
                "content_promise e briefing.viewer_outcome em viewer_takeaway. central_question, "
                "practical_application e callback são opcionais, salvo quando mode_guidance "
                "exigir aplicação."
            ),
            (
                "Construa a estrutura sugerida por mode_guidance. Cada seção acrescenta fato, "
                "evidência, contexto, comparação, argumento, consequência, revelação ou síntese; "
                "não reformule a mesma mensagem motivacional."
            ),
            *storytelling_instructions,
            (
                "Cada beat mapeia exatamente uma seção em ordem; copie viewer_value em "
                "retention_goal e visual_progression em visual_direction. Use ao menos "
                f"{required_reengagements} reengajamentos e nunca passe de "
                f"{long_config.maximum_reengagement_gap_seconds:.0f}s sem valor novo."
            ),
            (
                "Divida integralmente a locução em long_form_strategy.visual_scenes. Cada item "
                "copia um bloco contíguo da narração, preserva a ordem e descreve uma cena "
                "concreta, distinta e criativa em guache editorial cristão de meados do século. "
                "Use relações literal, contextual ou symbolic; varie ação, ambiente, objeto, "
                "escala e simbolismo, sem colagem, painéis, texto legível ou visual 3D."
            ),
            (
                "Mapeie cada citação em scripture_anchors na mesma ordem da locução. Insira a "
                "primeira até "
                f"{long_config.first_scripture_citation_max_seconds:.0f}s, respeite os intervalos "
                "e faça cada citação cumprir função narrativa ou argumentativa real."
            ),
            (
                "Declare resolution_timing conforme o modo. A resolução pode ser conclusão "
                "factual, síntese analítica, posição filosófica, incerteza preservada ou "
                "aplicação. "
                "Copie resolution e viewer_takeaway no beat de resolution; não force uma lição "
                "para a vida."
            ),
            (
                "A closing termina imediatamente após exatamente uma ação de CTA: comentar, "
                "inscrever-se ou compartilhar. Conecte-a ao valor entregue, não a promessa de "
                "bênção ou resultado."
            ),
            "A revisão deve manter approved=false e registrar blockers reais sem ocultá-los.",
            f"Grave ScriptDocument em {script_output}.",
            f"Grave BiblicalReview com script_version=1 em {review_output}.",
        ]
    else:
        instructions = [
            "Escreva locução integralmente em pt-BR, natural, pastoral e sem jargão editorial.",
            *authorial_style_instructions,
            "Use somente excerpts verificados de sources em citações literais e distribua-os.",
            "Verbalize Livro, capítulo e versículo; deixe somente o excerpt entre aspas.",
            "Classifique afirmações e preserve limites de interpretação do briefing.",
            (
                "Comece dentro de um conflito concreto, sem saudação, apresentação do canal ou "
                "'no vídeo de hoje'. Faça opening_hook ser o prefixo literal da locução, "
                "verbalize content_promise e central_question na abertura e conclua o cold open "
                f"até {long_config.opening_target_max_seconds:.0f}s."
            ),
            (
                "Copie briefing.audience em target_viewer, briefing.central_theme em "
                "content_promise e briefing.practical_application em practical_application."
            ),
            (
                "Construa progressão com valor novo, use ao menos "
                f"{required_reengagements} reengajamentos e nunca passe de "
                f"{long_config.maximum_reengagement_gap_seconds:.0f}s sem nova informação."
            ),
            "Preencha long_form_strategy por completo e mapeie cada seção em ordem.",
            (
                "Divida integralmente a locução em long_form_strategy.visual_scenes. Cada bloco "
                "é contíguo, reconstrói a seção sem lacunas e recebe uma cena concreta e distinta "
                "no padrão biblical-midcentury-gouache-v1, sem 3D, colagem ou texto legível."
            ),
            "Mapeie cada citação em scripture_anchors na mesma ordem da locução.",
            "Reserve payoff e aplicação para o quarto final e retome a abertura no callback.",
            "A closing termina imediatamente após exatamente uma ação de CTA.",
            "A revisão deve manter approved=false e registrar blockers reais sem ocultá-los.",
            f"Grave ScriptDocument em {script_output}.",
            f"Grave BiblicalReview com script_version=1 em {review_output}.",
        ]
    payload = {
        "allowed_inputs": [],
        "task": {
            "kind": "produce_long_script_and_biblical_review",
            "project": {
                "title": project.title,
                "target_duration_seconds": project.target_duration_seconds,
                "video_format": project.video_format,
            },
            "briefing": briefing.model_dump(mode="json"),
            "sources": read_json(layout.sources),
            "literal_citation_target": {"minimum": minimum, "maximum": maximum},
            "creative_contract": creative_contract,
            "scripture_cadence": {
                "first_citation_max_seconds": (long_config.first_scripture_citation_max_seconds),
                "minimum_gap_seconds": long_config.minimum_scripture_citation_gap_seconds,
                "maximum_gap_seconds": long_config.maximum_scripture_citation_gap_seconds,
                "final_citation_max_distance_seconds": (
                    long_config.final_scripture_citation_max_distance_seconds
                ),
            },
            "schemas": {
                "script": compact_json_schema(ScriptDocument.model_json_schema()),
                "biblical_review": compact_json_schema(BiblicalReview.model_json_schema()),
            },
            "instructions": instructions,
        },
    }
    return CodexPhaseSpec(
        phase="long_script",
        item_id=None,
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload=payload,
        expected_outputs=(script_output, review_output),
    )


def _failure_with_codex_context(
    job: RemoteVideoJob,
    store: RemoteJobStore,
    exc: Exception,
) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    output_message = store.root / "logs" / f"{job.job_id}-last-message.txt"
    if not output_message.is_file():
        return message[:2000]
    relevant_markers = ("moderation", "bloque", "falh", "erro", "interromp")
    relevant = [
        line.strip(" -\t")
        for line in output_message.read_text(encoding="utf-8", errors="replace").splitlines()
        if any(marker in line.casefold() for marker in relevant_markers)
    ]
    context = " ".join(dict.fromkeys(line for line in relevant if line))
    if not context:
        return message[:2000]
    return f"{context} Consequência técnica: {message}"[:2000]


def _hook_config_arguments(hook_script: Path) -> list[str]:
    command = f'python3 "{hook_script}"'
    command_value = json.dumps(command)
    post_tool = (
        'hooks.PostToolUse=[{matcher="*",hooks=[{type="command",command='
        f"{command_value},timeout=5}}]}}]"
    )
    pre_compact = (
        'hooks.PreCompact=[{matcher="manual|auto",hooks=[{type="command",command='
        f"{command_value},timeout=5}}]}}]"
    )
    stop = f'hooks.Stop=[{{hooks=[{{type="command",command={command_value},timeout=5}}]}}]'
    return ["-c", post_tool, "-c", pre_compact, "-c", stop]


def _codex_agent_command(
    *,
    codex_binary: str,
    spec: CodexPhaseSpec,
    repo_root: Path,
    output_message: Path,
    hook_script: Path,
    session_id: str | None,
) -> list[str]:
    command = [
        codex_binary,
        "exec",
    ]
    if session_id is not None:
        command.append("resume")
    command.extend(
        [
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--skip-git-repo-check",
            "--enable",
            "image_generation",
            "--enable",
            "multi_agent",
            "--enable",
            "hooks",
            *[
                value
                for feature in REMOTE_DISABLED_CODEX_FEATURES
                for value in ("--disable", feature)
            ],
            "--model",
            spec.model,
            "-c",
            f'model_reasoning_effort="{spec.reasoning_effort}"',
            "-c",
            'model_reasoning_summary="none"',
            "-c",
            'model_verbosity="low"',
            "-c",
            'web_search="disabled"',
            *_hook_config_arguments(hook_script),
            "--json",
            "--output-last-message",
            str(output_message),
        ]
    )
    if session_id is None:
        command.extend(["--cd", str(repo_root)])
    else:
        command.append(session_id)
    command.append("-")
    return command


def _usage_from_codex_jsonl(path: Path) -> dict[str, int]:
    usage = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    if not path.is_file():
        return usage
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        with suppress(json.JSONDecodeError):
            event = json.loads(line)
            if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
                continue
            for key in usage:
                value = event["usage"].get(key, 0)
                usage[key] = int(value) if isinstance(value, int | float) else 0
    return usage


def _codex_thread_id_from_jsonl(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        with suppress(json.JSONDecodeError):
            event = json.loads(line)
            if event.get("type") != "thread.started":
                continue
            thread_id = str(event.get("thread_id", "")).strip().casefold()
            if CODEX_THREAD_ID_PATTERN.fullmatch(thread_id):
                return thread_id
    return None


def _recover_generated_image_outputs(
    spec: CodexPhaseSpec,
    phase_log: Path,
    *,
    codex_home: Path,
    session_id: str | None = None,
    generated_after_ns: int = 0,
) -> list[tuple[Path, Path]]:
    if spec.phase not in {"image", "image_workers", "youtube_growth"}:
        return []
    missing_pngs = [
        path
        for path in spec.expected_outputs
        if path.suffix.casefold() == ".png" and not _is_nonempty_file(path)
    ]
    if not missing_pngs:
        return []
    thread_id = _codex_thread_id_from_jsonl(phase_log) or session_id
    if thread_id is None:
        return []
    generated_root = (codex_home.expanduser().resolve() / "generated_images").resolve()
    session_dir = (generated_root / thread_id).resolve()
    try:
        session_dir.relative_to(generated_root)
    except ValueError:
        return []
    if not session_dir.is_dir():
        return []
    candidates = sorted(
        (
            path
            for path in session_dir.glob("exec-*.png")
            if _is_png_file(path) and path.stat().st_mtime_ns >= generated_after_ns
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    if len(candidates) < len(missing_pngs):
        return []
    selected = candidates[-len(missing_pngs) :]
    recovered: list[tuple[Path, Path]] = []
    for source, destination in zip(selected, missing_pngs, strict=True):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if not _is_png_file(destination):
            with suppress(OSError):
                destination.unlink()
            continue
        recovered.append((source, destination))
    return recovered


def _phase_slug(spec: CodexPhaseSpec) -> str:
    value = f"{spec.phase}-{spec.item_id or 'main'}".casefold()
    return re.sub(r"[^a-z0-9._-]+", "-", value).strip("-")[:100]


def _execute_agent_checkpoint(
    job: RemoteVideoJob,
    spec: CodexPhaseSpec,
    store: RemoteJobStore,
    *,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    codex_binary = os.getenv("CODEX_BINARY") or shutil.which("codex")
    if not codex_binary:
        raise FileNotFoundError("Codex CLI não encontrado no PATH do worker.")
    hook_script = repo_root.resolve() / "scripts" / "remote_codex_hook.py"
    if not hook_script.is_file():
        raise FileNotFoundError(f"Hook remoto não encontrado: {hook_script}")
    latest, _ = store.find(job.job_id)
    run_number = len(latest.codex_runs) + 1
    slug = _phase_slug(spec)
    context_dir = store.root / "contexts" / job.job_id / "main-agent"
    context_dir.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        context_dir.chmod(0o700)
    requests_dir = context_dir / "checkpoints"
    requests_dir.mkdir(parents=True, exist_ok=True)
    request_path = requests_dir / f"run-{run_number:03d}-{slug}.json"
    output_message = context_dir / f"run-{run_number:03d}-{slug}-last-message.txt"
    phase_log = store.root / "logs" / f"{job.job_id}-run-{run_number:03d}-{slug}.jsonl"
    hook_log = store.root / "logs" / f"{job.job_id}-run-{run_number:03d}-{slug}.hooks.jsonl"
    write_json(request_path, _phase_payload(latest, spec))
    with suppress(OSError):
        request_path.chmod(0o600)
    for output in spec.expected_outputs:
        output.parent.mkdir(parents=True, exist_ok=True)

    phase_label = f"{spec.phase}:{spec.item_id}" if spec.item_id else spec.phase
    latest = store.transition(latest, "running", current_phase=phase_label)
    command = _codex_agent_command(
        codex_binary=codex_binary,
        spec=spec,
        repo_root=repo_root.resolve(),
        output_message=output_message,
        hook_script=hook_script,
        session_id=latest.codex_session_id,
    )
    env = os.environ.copy()
    env["PIPELINE_VIDEOS_CODEX_HOOK_LOG"] = str(hook_log)
    env["PIPELINE_VIDEOS_REMOTE_PHASE"] = phase_label
    started_at = utc_now()
    generated_after_ns = time.time_ns()
    master_log = store.log_path(job.job_id)
    with master_log.open("a", encoding="utf-8") as log:
        log.write(
            f"[{started_at}] codex_checkpoint_start phase={phase_label} "
            f"session={latest.codex_session_id or 'new'} "
            f"model={spec.model} reasoning={spec.reasoning_effort}\n"
        )
    exit_code = 0
    error: str | None = None
    try:
        with phase_log.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                input=codex_checkpoint_prompt(request_path),
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=repo_root,
                env=env,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
        error = f"Fase {phase_label} excedeu {timeout_seconds} segundos."
    recovered_outputs: list[tuple[Path, Path]] = []
    observed_session_id = _codex_thread_id_from_jsonl(phase_log)
    session_id = latest.codex_session_id or observed_session_id
    if session_id is None:
        exit_code = 2
        error = "O Codex não informou o UUID da sessão persistente."
    elif observed_session_id is not None and observed_session_id != session_id:
        exit_code = 2
        error = "A retomada do Codex respondeu com um UUID de sessão diferente do job."
    if exit_code == 0:
        recovered_outputs = _recover_generated_image_outputs(
            spec,
            phase_log,
            codex_home=Path(env.get("CODEX_HOME") or Path.home() / ".codex"),
            session_id=session_id,
            generated_after_ns=generated_after_ns,
        )
        if recovered_outputs and output_message.is_file():
            with output_message.open("a", encoding="utf-8") as message:
                message.write(
                    "\nRecuperacao deterministica: o PNG gerado pelo imagegen foi "
                    "materializado no staging exigido.\n"
                )
    missing = [str(path) for path in spec.expected_outputs if not _is_nonempty_file(path)]
    if exit_code == 0 and missing:
        exit_code = 2
        error = "Saídas obrigatórias ausentes: " + ", ".join(missing)
    if exit_code != 0 and error is None:
        error = f"Codex encerrou a fase {phase_label} com código {exit_code}."
    latest_message = store.root / "logs" / f"{job.job_id}-last-message.txt"
    if output_message.is_file():
        shutil.copy2(
            output_message,
            latest_message,
        )
    usage = _usage_from_codex_jsonl(phase_log)
    finished_at = utc_now()
    record = RemoteCodexRun(
        phase=spec.phase,
        item_id=spec.item_id,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
        job_attempt=latest.attempts,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        error=error,
        **usage,
    )
    current, _ = store.find(job.job_id)
    current = store.transition(
        current,
        "running",
        current_phase=None,
        codex_session_id=session_id,
        codex_exit_code=exit_code,
        codex_runs=[*current.codex_runs, record],
    )
    with master_log.open("a", encoding="utf-8") as log:
        log.write(
            f"[{finished_at}] codex_checkpoint_exit phase={phase_label} code={exit_code} "
            f"session={session_id or 'missing'} "
            f"input={record.input_tokens} cached={record.cached_input_tokens} "
            f"output={record.output_tokens}\n"
        )
        for source, destination in recovered_outputs:
            log.write(
                f"[{finished_at}] imagegen_artifact_recovered phase={phase_label} "
                f"source={source} destination={destination}\n"
            )
    for path in (phase_log, hook_log, output_message, latest_message, master_log):
        if path.exists():
            with suppress(OSError):
                path.chmod(0o600)
    if exit_code != 0:
        raise RuntimeError(error or f"Falha na fase {phase_label}.")
    return current


def _run_pipeline_diagnostics(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    *,
    repo_root: Path,
) -> None:
    uv_binary = os.getenv("UV_BINARY") or shutil.which("uv")
    if not uv_binary:
        raise FileNotFoundError("uv não encontrado no PATH do worker.")
    commands = (
        [
            uv_binary,
            "run",
            "--project",
            str(repo_root.resolve()),
            "pipeline-videos",
            "status",
            str(layout.root),
        ],
        [
            uv_binary,
            "run",
            "--project",
            str(repo_root.resolve()),
            "pipeline-videos",
            "run",
            str(layout.root),
            "--dry-run",
        ],
    )
    log_path = store.log_path(job.job_id)
    with log_path.open("a", encoding="utf-8") as log:
        for command in commands:
            completed = subprocess.run(
                command,
                text=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=repo_root,
                timeout=120,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Diagnóstico determinístico falhou com código {completed.returncode}."
                )
    with suppress(OSError):
        log_path.chmod(0o600)


def _archive_invalid_staging(paths: tuple[Path, ...]) -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    for path in paths:
        if not path.exists():
            continue
        archived = path.with_name(f"{path.stem}.invalid-{timestamp}{path.suffix}")
        path.replace(archived)


def _ensure_editorial_context(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    *,
    projects_root: Path,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    if _editorial_context_ready(layout):
        return job
    spec = _editorial_sources_phase_spec(job, layout, projects_root=projects_root)
    selection = spec.expected_outputs[0]
    if _is_nonempty_file(selection):
        try:
            _import_editorial_source_selection(
                job,
                layout,
                selection,
                projects_root=projects_root,
            )
            return job
        except (ValueError, TypeError, KeyError):
            _archive_invalid_staging(spec.expected_outputs)
    job = _execute_agent_checkpoint(
        job,
        spec,
        store,
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
    )
    _import_editorial_source_selection(
        job,
        layout,
        selection,
        projects_root=projects_root,
    )
    return job


def _validate_long_script_staging(
    layout: ProjectLayout,
    script_path: Path,
    review_path: Path,
) -> tuple[ScriptDocument, BiblicalReview]:
    from pipeline_videos.editorial import validate_script_editorial_policy
    from pipeline_videos.long_form_script import validate_long_form_retention_script

    document = ScriptDocument.model_validate_json(script_path.read_text(encoding="utf-8"))
    review = BiblicalReview.model_validate_json(review_path.read_text(encoding="utf-8"))
    project = layout.load_manifest()
    sources = SourceLibrary.model_validate(read_json(layout.sources))
    validate_script_editorial_policy(
        document,
        duration_seconds=project.target_duration_seconds,
        policy=project.config.editorial_policy,
        sources=sources,
    )
    validate_long_form_retention_script(
        document,
        target_duration_seconds=project.target_duration_seconds,
        config=project.config.long_form_script,
        sources=sources,
        briefing=Briefing.model_validate(read_json(layout.briefing)),
    )
    if review.script_version != document.version:
        raise ValueError("A revisão em staging não corresponde ao roteiro longo.")
    return document, review


def _long_review_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    project = layout.load_manifest()
    output = layout.root / "reviews" / "staging" / "remote-long-script" / "biblical-review.json"
    script = read_json(layout.script)
    return CodexPhaseSpec(
        phase="long_biblical_review",
        item_id=None,
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "allowed_inputs": [],
            "task": {
                "kind": "review_existing_long_script",
                "project_title": project.title,
                "briefing": read_json(layout.briefing),
                "sources": read_json(layout.sources),
                "script": script,
                "schema": compact_json_schema(BiblicalReview.model_json_schema()),
                "instructions": [
                    "Confira citações, contexto, interpretações, direitos e referências.",
                    "Não oculte finding blocking real e mantenha approved=false.",
                    f"Use script_version={script['version']} e grave apenas JSON em {output}.",
                ],
            },
        },
        expected_outputs=(output,),
    )


def _ensure_long_script(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    *,
    repo_root: Path,
    timeout_seconds: int,
) -> RemoteVideoJob:
    from pipeline_videos.review import import_biblical_review
    from pipeline_videos.script import import_script

    project = layout.load_manifest()
    if project.video_format == "short":
        return job
    if (
        project.stages["script"].status == StageStatus.COMPLETED
        and project.stages["biblical_review"].status == StageStatus.COMPLETED
    ):
        return job
    if project.stages["script"].status != StageStatus.COMPLETED:
        spec = _long_script_phase_spec(layout)
        if all(_is_nonempty_file(path) for path in spec.expected_outputs):
            try:
                _validate_long_script_staging(layout, *spec.expected_outputs)
            except (ValueError, TypeError, KeyError):
                _archive_invalid_staging(spec.expected_outputs)
        if not all(_is_nonempty_file(path) for path in spec.expected_outputs):
            job = _execute_agent_checkpoint(
                job,
                spec,
                store,
                repo_root=repo_root,
                timeout_seconds=timeout_seconds,
            )
        _validate_long_script_staging(layout, *spec.expected_outputs)
        document = import_script(layout, spec.expected_outputs[0])
        review = BiblicalReview.model_validate_json(
            spec.expected_outputs[1].read_text(encoding="utf-8")
        )
        if review.script_version != document.version:
            review = review.model_copy(update={"script_version": document.version})
            write_json(spec.expected_outputs[1], review)
        import_biblical_review(layout, spec.expected_outputs[1])
        return job

    review_spec = _long_review_phase_spec(layout)
    review_path = review_spec.expected_outputs[0]
    active_script = ScriptDocument.model_validate(read_json(layout.script))
    valid_review = False
    if _is_nonempty_file(review_path):
        try:
            review = BiblicalReview.model_validate_json(review_path.read_text(encoding="utf-8"))
            valid_review = review.script_version == active_script.version
        except (ValueError, TypeError, KeyError):
            valid_review = False
        if not valid_review:
            _archive_invalid_staging(review_spec.expected_outputs)
    if not valid_review:
        job = _execute_agent_checkpoint(
            job,
            review_spec,
            store,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
    import_biblical_review(layout, review_path)
    return job


def _produce_remote_candidate(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    store: RemoteJobStore,
    *,
    projects_root: Path,
    repo_root: Path,
    timeout_seconds: int = 21_600,
) -> RemoteVideoJob:
    from pipeline_videos.autopilot import run_autopilot_once

    _run_pipeline_diagnostics(job, layout, store, repo_root=repo_root)
    if job.request.production_profile != "approved_fast_v1":
        job = _ensure_editorial_context(
            job,
            layout,
            store,
            projects_root=projects_root,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
        job = _ensure_long_script(
            job,
            layout,
            store,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
    for _ in range(8):
        job = _ensure_valid_shorts_script_staging(
            job,
            layout,
            store,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
        )
        try:
            if job.request.production_profile == "approved_fast_v1":
                from pipeline_videos.approved_scripts import (
                    verify_project_approved_script_lock,
                )

                verify_project_approved_script_lock(layout)
            result = run_autopilot_once(layout, allow_external=True)
        except Exception as exc:
            if _growth_package_repair_is_applicable(layout):
                job = _repair_growth_package_failure(
                    job,
                    layout,
                    store,
                    exc,
                    repo_root=repo_root,
                    timeout_seconds=timeout_seconds,
                )
                continue
            if job.request.production_profile == "approved_fast_v1":
                raise
            if not _shorts_pipeline_repair_is_applicable(layout):
                raise
            job = _repair_shorts_pipeline_failure(
                job,
                layout,
                store,
                exc,
                repo_root=repo_root,
                timeout_seconds=timeout_seconds,
            )
            continue
        if result.status == "completed":
            if result.validation_ok is not True:
                raise RuntimeError("A validação técnica do candidato remoto falhou.")
            return job
        progressed = False
        for wait in result.waits:
            if wait.get("ready"):
                continue
            kind = wait.get("kind")
            if kind == "shorts_script":
                job = _execute_agent_checkpoint(
                    job,
                    _shorts_script_phase_spec(layout),
                    store,
                    repo_root=repo_root,
                    timeout_seconds=timeout_seconds,
                )
                progressed = True
            elif kind == "biblical_review":
                job = _execute_agent_checkpoint(
                    job,
                    _biblical_review_phase_spec(layout),
                    store,
                    repo_root=repo_root,
                    timeout_seconds=timeout_seconds,
                )
                progressed = True
            elif kind == "images":
                specs = _image_phase_specs(layout)
                if not specs:
                    raise RuntimeError("Fila de imagens pendente sem item executável.")
                for spec in specs:
                    job = _execute_agent_checkpoint(
                        job,
                        spec,
                        store,
                        repo_root=repo_root,
                        timeout_seconds=timeout_seconds,
                    )
                progressed = True
            elif kind == "youtube_growth":
                job = _execute_agent_checkpoint(
                    job,
                    _growth_phase_spec(layout),
                    store,
                    repo_root=repo_root,
                    timeout_seconds=timeout_seconds,
                )
                progressed = True
            else:
                raise RuntimeError(f"Dependência externa incompatível com o agente remoto: {kind}")
        if not progressed:
            raise RuntimeError("O agente remoto não conseguiu avançar o estado determinístico.")
    raise RuntimeError("O agente remoto excedeu o limite de oito ciclos de retomada.")


def _complete_private_upload(job: RemoteVideoJob, layout: ProjectLayout) -> tuple[str, str]:
    if job.request.production_profile == "approved_fast_v1":
        from pipeline_videos.approved_scripts import verify_project_approved_script_lock

        verify_project_approved_script_lock(layout)
    report = validate_project(layout, require_render=True)
    if not report.valid:
        raise RuntimeError("A validação técnica do candidato remoto falhou.")
    client = authenticated_client()
    record = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=layout.load_manifest().project_id,
        privacy_status="private",
        automated_review=True,
    )
    return record.video_id, record.youtube_url


def _try_schedule(job: RemoteVideoJob, layout: ProjectLayout) -> bool:
    if not job.video_id or not job.request.publish_at:
        raise RuntimeError("Job agendado sem video_id ou publish_at.")
    client = authenticated_client()
    try:
        publish_project(
            layout,
            client=client,
            allow_external=True,
            confirmation=job.video_id,
            publish_at=job.request.publish_at.isoformat(),
            automated_schedule=True,
        )
    except RuntimeError as exc:
        if "ainda não concluiu o processamento" in str(exc):
            return False
        raise
    return True


def work_once(
    *,
    store: RemoteJobStore,
    projects_root: Path | None = None,
    repo_root: Path = REPO_ROOT,
    dry_run: bool = False,
) -> RemoteVideoJob | None:
    if not dry_run:
        from pipeline_videos.approved_scripts import ApprovedScriptStore
        from pipeline_videos.remote_schedules import (
            RemoteScheduleStore,
            materialize_daily_schedules,
        )

        materialize_daily_schedules(
            store=store,
            schedule_store=RemoteScheduleStore(store.root),
            approved_store=ApprovedScriptStore(store.root),
        )
    waiting = [job for job in reversed(store.list(status="waiting_youtube")) if job_is_due(job)]
    if waiting:
        job = waiting[0]
        if dry_run:
            return job
        assert job.project_path is not None
        layout = ProjectLayout(
            _assert_project_path(Path(job.project_path), projects_root or DEFAULT_PROJECTS_DIR)
        )
        try:
            if not _try_schedule(job, layout):
                return job
            return store.transition(
                job,
                "completed",
                publish_at=job.request.publish_at.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                error=None,
                next_retry_at=None,
            )
        except Exception as exc:
            return _schedule_automatic_recovery(
                store,
                job,
                exc,
                status="waiting_youtube",
            )

    due = store.due_jobs()
    if not due:
        return None
    job = due[0]
    if dry_run:
        return job
    job = store.transition(
        job,
        "running",
        attempts=job.attempts + 1,
        error=None,
        next_retry_at=None,
    )
    try:
        approved_store = ApprovedScriptStore(store.root)
        layout = prepare_job_project(
            job,
            projects_root=projects_root,
            approved_store=approved_store,
        )
        job = store.transition(job, "running", project_path=str(layout.root))
        resolved_projects_root = (projects_root or DEFAULT_PROJECTS_DIR).expanduser().resolve()
        job = _produce_remote_candidate(
            job,
            layout,
            store,
            projects_root=resolved_projects_root,
            repo_root=repo_root,
        )
        video_id, youtube_url = _complete_private_upload(job, layout)
        updates = {"video_id": video_id, "youtube_url": youtube_url, "error": None}
        if (
            job.request.production_profile == "approved_fast_v1"
            and job.request.approved_script_id is not None
        ):
            approved_store.mark_produced(
                job.request.approved_script_id,
                job_id=job.job_id,
                video_id=video_id,
                youtube_url=youtube_url,
            )
        if job.request.publication_mode == "scheduled":
            return store.transition(job, "waiting_youtube", **updates)
        completed = store.transition(job, "completed", **updates)
        return completed
    except Exception as exc:
        latest, _ = store.find(job.job_id)
        return _schedule_automatic_recovery(
            store,
            latest,
            exc,
            status="queued",
        )


def _automatic_recovery_delay_seconds(recovery_count: int) -> int:
    exponent = min(max(recovery_count - 1, 0), 9)
    return min(21_600, 60 * (2**exponent))


def _schedule_automatic_recovery(
    store: RemoteJobStore,
    job: RemoteVideoJob,
    exc: Exception,
    *,
    status: Literal["queued", "waiting_youtube"],
) -> RemoteVideoJob:
    latest, _ = store.find(job.job_id)
    recovery_count = latest.automatic_recovery_count + 1
    delay_seconds = _automatic_recovery_delay_seconds(recovery_count)
    next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    error = _failure_with_codex_context(latest, store, exc)
    recovered = store.transition(
        latest,
        status,
        current_phase=None,
        automatic_recovery_count=recovery_count,
        next_retry_at=next_retry_at,
        error=error,
    )
    with store.log_path(job.job_id).open("a", encoding="utf-8") as log:
        log.write(
            f"[{utc_now()}] automatic_recovery_scheduled status={status} "
            f"count={recovery_count} next_retry_at={next_retry_at.isoformat()} "
            f"error={error[:500]!r}\n"
        )
    return recovered


def codex_usage_summary(job: RemoteVideoJob) -> dict[str, int]:
    input_tokens = sum(run.input_tokens for run in job.codex_runs)
    cached_input_tokens = sum(run.cached_input_tokens for run in job.codex_runs)
    output_tokens = sum(run.output_tokens for run in job.codex_runs)
    reasoning_output_tokens = sum(run.reasoning_output_tokens for run in job.codex_runs)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "fresh_input_tokens": max(0, input_tokens - cached_input_tokens),
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "fresh_plus_output_tokens": max(0, input_tokens - cached_input_tokens) + output_tokens,
    }


def job_summary(job: RemoteVideoJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "format": job.request.video_format,
        "production_profile": job.request.production_profile,
        "approved_script_id": job.request.approved_script_id,
        "topic": job.request.topic or "automatic",
        "duration_seconds": job.request.duration_seconds,
        "start_policy": job.request.start_policy,
        "not_before": (job.request.not_before.isoformat() if job.request.not_before else None),
        "publication_mode": job.request.publication_mode,
        "voice_variant": job.request.voice_variant,
        "editorial_profile": job.request.editorial_profile,
        "music_track": job.request.music_track,
        "project_path": job.project_path,
        "youtube_url": job.youtube_url,
        "publish_at": job.publish_at,
        "attempts": job.attempts,
        "current_phase": job.current_phase,
        "codex_session_id": job.codex_session_id,
        "schedule_occurrence_id": job.schedule_occurrence_id,
        "codex_run_count": len(job.codex_runs),
        "codex_usage": codex_usage_summary(job),
        "automatic_recovery_count": job.automatic_recovery_count,
        "next_retry_at": (
            job.next_retry_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if job.next_retry_at
            else None
        ),
        "error": job.error,
        "updated_at": job.updated_at,
    }
