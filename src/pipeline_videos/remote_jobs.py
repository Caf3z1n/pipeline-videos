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

from pipeline_videos.imagegen_retry import imagegen_retry_instructions
from pipeline_videos.models import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    BiblicalReview,
    Briefing,
    PipelineModel,
    ScriptDocument,
    SourceEntry,
    SourceLibrary,
    StageStatus,
    VerseCandidate,
)
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

REMOTE_JOB_SCHEMA_VERSION = "1.0"
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
    schema_version: Literal["1.0"] = REMOTE_JOB_SCHEMA_VERSION
    video_format: Literal["short", "long"]
    topic: str | None = Field(default=None, max_length=240)
    title: str | None = Field(default=None, max_length=240)
    duration_seconds: int | None = Field(default=None, ge=30, le=3600)
    start_policy: Literal["immediate", "overnight"] = "immediate"
    not_before: datetime | None = None
    publication_mode: Literal["private_review", "scheduled"] = "private_review"
    publish_at: datetime | None = None
    audio_provider: Literal["elevenlabs"] = "elevenlabs"
    external_services_authorized: bool = False

    @model_validator(mode="after")
    def validate_request(self) -> RemoteVideoRequest:
        object.__setattr__(self, "topic", self.topic.strip() if self.topic else None)
        object.__setattr__(self, "title", self.title.strip() if self.title else None)
        if self.duration_seconds is None:
            object.__setattr__(
                self,
                "duration_seconds",
                50 if self.video_format == "short" else 540,
            )
        if self.video_format == "short" and self.duration_seconds > 180:
            raise ValueError("Shorts precisam ter duração entre 30 e 180 segundos.")
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
) -> ProjectLayout:
    root = (projects_root or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    if job.project_path:
        return ProjectLayout(_assert_project_path(Path(job.project_path), root))
    request = job.request
    video_format: Literal["landscape", "short"] = (
        "short" if request.video_format == "short" else "landscape"
    )
    assert request.duration_seconds is not None
    if request.topic:
        layout = create_project(
            slugify(request.topic),
            request.title or request.topic,
            projects_dir=root,
            target_duration_seconds=request.duration_seconds,
            video_format=video_format,
            production_mode="draft_one_shot",
        )
    else:
        layout, _, _ = create_auto_video(
            projects_dir=root,
            target_duration_seconds=request.duration_seconds,
            video_format=video_format,
            production_mode="draft_one_shot",
        )
    manifest = layout.load_manifest()
    manifest.config.production.use_subagents = True
    manifest.config.production.image_workers = 3
    manifest.config.shorts_script.use_specialist_subagent = True
    manifest.config.youtube_growth.use_specialist_subagent = True
    manifest.config.audio.provider = request.audio_provider
    manifest.config.audio.voice_id = DEFAULT_ELEVENLABS_VOICE_ID
    manifest.config.youtube_growth.title_variants = 1
    manifest.config.youtube_growth.thumbnail_variants = 1
    manifest.config.youtube_publication.allow_automated_private_review_upload = True
    manifest.config.youtube_publication.allow_automated_scheduled_release = (
        request.publication_mode == "scheduled"
    )
    layout.save_manifest(manifest)
    layout.append_decision(
        {
            "type": "remote_local_parity_job",
            "job_id": job.job_id,
            "start_policy": request.start_policy,
            "publication_mode": request.publication_mode,
            "publish_at": request.publish_at.isoformat() if request.publish_at else None,
            "title_thumbnail_variants": 1,
            "voice_id": DEFAULT_ELEVENLABS_VOICE_ID,
            "model": DEFAULT_REMOTE_CODEX_MODEL,
            "coordinator_reasoning_effort": DEFAULT_REMOTE_REASONING_EFFORT,
            "specialist_reasoning_effort": {
                "shorts_script_specialist": "high",
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
) -> tuple[list[VerseCandidate], str | None, set[str]]:
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
    )
    if current_main:
        main_eligible.add(current_main)
    available = (
        [candidate for candidate in catalog.candidates if candidate.candidate_id in main_eligible]
        if project.video_format == "short"
        else list(catalog.candidates)
    )
    return available, current_main, main_eligible


def _editorial_context_ready(layout: ProjectLayout) -> bool:
    if not layout.briefing.is_file() or not layout.sources.is_file():
        return False
    briefing = Briefing.model_validate(read_json(layout.briefing))
    sources = SourceLibrary.model_validate(read_json(layout.sources))
    required = (
        briefing.main_passage,
        briefing.central_theme,
        briefing.practical_application,
        briefing.audience,
        briefing.emotional_intent,
    )
    verified_count = sum(
        1
        for source in sources.entries
        if source.kind == "biblical_text" and source.verified and source.excerpt
    )
    project = layout.load_manifest()
    if project.video_format == "short":
        target = 1
    else:
        from pipeline_videos.editorial import citation_target_range

        target, _ = citation_target_range(
            project.target_duration_seconds,
            project.config.editorial_policy,
        )
    return all(value.strip() for value in required) and verified_count >= target


def _editorial_sources_phase_spec(
    job: RemoteVideoJob,
    layout: ProjectLayout,
    *,
    projects_root: Path,
) -> CodexPhaseSpec:
    from pipeline_videos.editorial import citation_target_range

    project = layout.load_manifest()
    candidates, current_main, main_eligible = _available_verse_candidates(
        layout,
        projects_root=projects_root,
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
            "desired_candidate_count": desired_count,
            "required_main_candidate_id": current_main,
            "main_eligible_candidate_ids": sorted(main_eligible),
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "reference": candidate.reference,
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
                    "retention_potential_score": candidate.retention_potential_score,
                    "themes": candidate.themes,
                    "characters": candidate.characters,
                    "interpretation_limits": candidate.interpretation_limits,
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
    from pipeline_videos.verse_selection import load_verse_catalog

    catalog = load_verse_catalog()
    return SourceEntry(
        source_id=candidate.source_id,
        kind="biblical_text",
        title=f"{candidate.reference} — {candidate.translation}",
        passage=candidate.context_reference,
        translation=candidate.translation,
        author_or_publisher=catalog.publisher,
        url=candidate.source_url,
        accessed_at=candidate.accessed_at,
        excerpt=candidate.verse_text,
        use_in_video=(
            "Citação literal verificada distribuída na narrativa do vídeo longo."
            if long_form
            else "Citação literal obrigatória e base da reflexão do Short."
        ),
        claim_scope="Texto bíblico; contexto e aplicações devem ser distinguidos.",
        rights_note=candidate.rights_note,
        verified=True,
    )


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
        select_retention_archetype,
        sync_shared_usage,
    )

    selection = read_json(selection_path)
    candidates, current_main, main_eligible = _available_verse_candidates(
        layout,
        projects_root=projects_root,
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
    reserve_project_candidates(
        projects_root,
        project_id=project.project_id,
        video_format=project.video_format,
        candidates=selected,
        catalog=load_verse_catalog(),
        retention_archetype=retention_archetype,
    )
    current = Briefing.model_validate(read_json(layout.briefing))
    suggested_title = main.title_seed.strip() or main.title
    title = job.request.title or job.request.topic or current.title or suggested_title
    central_theme = current.central_theme.strip() or job.request.topic or main.central_theme
    call_to_action = current.call_to_action.strip()
    if not call_to_action:
        call_to_action = (
            "Escolher exatamente uma ação natural — comentar, inscrever-se ou compartilhar — "
            "integrada ao payoff, em até doze palavras e quatro segundos."
            if project.video_format == "short"
            else "Concluir com aplicação fiel e convite comunitário natural, sem pressão."
        )
    briefing = Briefing(
        title=title,
        main_passage=main.reference,
        complementary_references=[candidate.reference for candidate in selected[1:]],
        central_theme=central_theme,
        practical_application=current.practical_application.strip() or main.practical_application,
        audience=current.audience.strip() or main.audience,
        emotional_intent=current.emotional_intent.strip() or main.emotional_intent,
        narration_tone=current.narration_tone or main.narration_tone,
        target_duration_seconds=project.target_duration_seconds,
        bible_translation=main.translation,
        literal_quote_policy=(
            "Citar literalmente somente excerpts verificados destas fontes em domínio público."
        ),
        interpretation_limits=list(
            dict.fromkeys(current.interpretation_limits or main.interpretation_limits)
        ),
        claims_to_avoid=list(dict.fromkeys(current.claims_to_avoid or main.claims_to_avoid)),
        visual_notes=list(dict.fromkeys(current.visual_notes or main.visual_notes)),
        call_to_action=call_to_action,
        retention_archetype=retention_archetype,
        human_conflict=current.human_conflict.strip() or main.human_conflict,
        central_question=current.central_question.strip() or main.central_question,
        visual_progression=current.visual_progression or main.visual_progression,
        possible_reveal=current.possible_reveal.strip() or main.possible_reveal,
        title_seed=current.title_seed.strip() or main.title_seed,
    )
    current_sources = SourceLibrary.model_validate(read_json(layout.sources))
    non_biblical = [source for source in current_sources.entries if source.kind != "biblical_text"]
    sources = SourceLibrary(
        entries=[
            *[
                _source_entry(candidate, long_form=project.video_format != "short")
                for candidate in selected
            ],
            *non_biblical,
        ]
    )
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
            "human_approval_recorded": False,
        }
    )
    sync_shared_usage(projects_root, catalog=load_verse_catalog())


def _shorts_script_phase_spec(layout: ProjectLayout) -> CodexPhaseSpec:
    queue = read_json(layout.shorts_script_queue)
    context = _project_file(layout, queue["inputs"]["compact_context"])
    package = _project_file(layout, queue["outputs"]["package"])
    return CodexPhaseSpec(
        phase="shorts_script",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "delegation": {
                "required": True,
                "role": "shorts_script_specialist",
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": "high",
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": [str(layout.shorts_script_queue), str(context)],
            "task": {
                "kind": "produce_shorts_script_package",
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
    return CodexPhaseSpec(
        phase="shorts_script_correction",
        item_id=str(queue.get("job_id")),
        model=DEFAULT_REMOTE_CODEX_MODEL,
        reasoning_effort="medium",
        payload={
            "delegation": {
                "required": True,
                "role": "shorts_script_specialist",
                "count": 1,
                "fork_turns": "none",
                "reasoning_effort": "high",
                "model": DEFAULT_REMOTE_CODEX_MODEL,
            },
            "allowed_inputs": [str(layout.shorts_script_queue), str(package)],
            "task": {
                "kind": "correct_shorts_script_package",
                "queue_path": str(layout.shorts_script_queue),
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


def _shorts_script_correction_is_available(job: RemoteVideoJob) -> bool:
    correction_runs = sum(
        run.phase == "shorts_script_correction" and run.job_attempt == job.attempts
        for run in job.codex_runs
    )
    return correction_runs < 2


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
        import_shorts_script_package,
        prepare_shorts_script_queue,
        validate_shorts_script_package_staging,
    )

    latest, _ = store.find(job.job_id)
    if not _shorts_script_correction_is_available(latest):
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

    from pipeline_videos.shorts_script import validate_shorts_script_package_staging

    try:
        validate_shorts_script_package_staging(layout)
        return job
    except (ValueError, TypeError, KeyError) as exc:
        latest, _ = store.find(job.job_id)
        if not _shorts_script_correction_is_available(latest):
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
    context = _project_file(layout, queue["inputs"]["compact_context"])
    package = _project_file(layout, queue["outputs"]["package"])
    thumbnails = tuple(
        _project_file(layout, str(value)) for value in queue["outputs"].get("thumbnails", [])
    )
    reference_scene_id = str(
        queue.get("inputs", {}).get("thumbnail_visual_reference_scene_id", "")
    ).strip()
    visual_reference: Path | None = None
    declared_reference = str(
        queue.get("inputs", {}).get("thumbnail_visual_reference_path") or ""
    ).strip()
    if declared_reference:
        candidate = _project_file(layout, declared_reference)
        if candidate.is_file():
            visual_reference = candidate
    if reference_scene_id and layout.image_queue.is_file():
        image_queue = read_json(layout.image_queue)
        for item in image_queue.get("items", []):
            if str(item.get("scene_id")) != reference_scene_id:
                continue
            candidate = _project_file(layout, str(item.get("staging_path", "")))
            if visual_reference is None and candidate.is_file():
                visual_reference = candidate
            break
    if visual_reference is None and reference_scene_id and layout.images.is_file():
        image_manifest = read_json(layout.images)
        for item in image_manifest.get("items", []):
            if str(item.get("scene_id")) != reference_scene_id:
                continue
            active_version = item.get("active_version")
            for version in item.get("versions", []):
                if version.get("version") == active_version:
                    candidate = _project_file(layout, str(version.get("path", "")))
                    if visual_reference is None and candidate.is_file():
                        visual_reference = candidate
                    break
            break
    reference_policy = queue.get("visual_standard", {}).get("reference_policy")
    if reference_policy == "required_opening_scene_image" and visual_reference is None:
        raise RuntimeError(
            "A thumbnail remota exige a imagem da cena de abertura como referência visual."
        )
    allowed_inputs = [str(layout.growth_queue), str(context)]
    if visual_reference is not None:
        allowed_inputs.append(str(visual_reference))
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
                "kind": "produce_one_title_thumbnail_pair",
                "tool": "$imagegen",
                "queue_path": str(layout.growth_queue),
                "compact_context_path": str(context),
                "thumbnail_visual_reference_path": (
                    str(visual_reference) if visual_reference is not None else None
                ),
                "instructions": [
                    "Siga integralmente instructions, schemas e generation_retry_policy da fila.",
                    (
                        "Use thumbnail_visual_reference_path como edit obrigatório de imagegen: "
                        "preserve a imagem, a cena e o personagem e acrescente a composição "
                        "editorial, o texto e a marcação vermelha; não a converta em fotografia. "
                        "Aceite a primeira saída válida sem repetir por qualidade ou deriva visual."
                        if visual_reference is not None
                        else "Siga integralmente o visual_standard textual da fila."
                    ),
                    "Gere exatamente um título e uma thumbnail integrada nesta produção remota.",
                    "Escreva apenas outputs.package e outputs.thumbnails.",
                    "O executor importará o pacote; não altere metadados compartilhados.",
                ],
            },
        },
        expected_outputs=(package, *thumbnails),
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
            "kind": "correct_one_title_thumbnail_pair",
            "validation_error": str(validation_error)[:4000],
            "instructions": [
                "Corrija somente o pacote e a thumbnail integrados que falharam na validação.",
                (
                    "Preserve título, promessa, passagem, personagem, cena de abertura e todos "
                    "os campos já válidos."
                ),
                (
                    "Mantenha cinematic-family-3d-v1 e animação 3D cinematográfica familiar "
                    "como estilo positivo. Termos fotográficos podem aparecer somente dentro "
                    "de restrições negativas como Avoid."
                ),
                (
                    "Se o estilo positivo pedir fotografia, reescreva o prompt e regenere uma "
                    "única vez a thumbnail a partir da imagem de abertura obrigatória."
                ),
                (
                    "Nesta correção, ignore a regra de pular staging já existente: sobrescreva "
                    "explicitamente cada saída que precisar ser corrigida."
                ),
                (
                    "Sobrescreva outputs.package e outputs.thumbnails. Não altere project.json, "
                    "fila, aprovações ou outros ativos."
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
    payload = {
        "allowed_inputs": [],
        "task": {
            "kind": "produce_long_script_and_biblical_review",
            "project": {
                "title": project.title,
                "target_duration_seconds": project.target_duration_seconds,
                "video_format": project.video_format,
            },
            "briefing": read_json(layout.briefing),
            "sources": read_json(layout.sources),
            "literal_citation_target": {"minimum": minimum, "maximum": maximum},
            "creative_contract": {
                "narrative_standard": long_config.narrative_standard,
                "content_promise_source": "briefing.central_theme",
                "payoff_source": "briefing.practical_application",
                "pacing_profile": "long_retention_v1",
                "opening_target_max_seconds": long_config.opening_target_max_seconds,
                "required_reengagements": required_reengagements,
                "maximum_reengagement_gap_seconds": (long_config.maximum_reengagement_gap_seconds),
                "payoff_start_minimum_ratio": long_config.payoff_start_minimum_ratio,
                "cta_max_words": long_config.cta_max_words,
                "cta_max_seconds": long_config.cta_max_seconds,
                "cta_actions": ["comment", "subscribe", "share"],
            },
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
            "instructions": [
                (
                    "Escreva locução integralmente em pt-BR, natural, pastoral e sem jargão "
                    "editorial."
                ),
                (
                    "Use somente excerpts verificados de sources em citações literais e "
                    "distribua-os."
                ),
                "Verbalize Livro, capítulo e versículo; deixe somente o excerpt entre aspas.",
                "Classifique afirmações e preserve limites de interpretação do briefing.",
                (
                    "Comece dentro de um conflito concreto, sem saudação, apresentação do canal ou "
                    "'no vídeo de hoje'. Faça opening_hook ser o prefixo literal da locução, "
                    "verbalize content_promise e central_question na abertura e conclua o cold "
                    f"open até {long_config.opening_target_max_seconds:.0f}s."
                ),
                (
                    "Copie briefing.audience literalmente em target_viewer, "
                    "briefing.central_theme em content_promise e "
                    "briefing.practical_application em practical_application; inclua as duas "
                    "últimas frases literalmente nos movimentos correspondentes da locução."
                ),
                (
                    "Construa uma progressão por movimentos: cada seção acrescenta fato, ação, "
                    "consequência, reversão ou mudança de entendimento. Use ao menos "
                    f"{required_reengagements} reengajamentos e nunca passe de "
                    f"{long_config.maximum_reengagement_gap_seconds:.0f}s sem nova informação "
                    "relevante."
                ),
                (
                    "Preencha long_form_strategy por completo. Cada beat mapeia exatamente uma "
                    "seção em ordem; copie beat.viewer_value em retention_goal e "
                    "beat.visual_progression em visual_direction. Contemplação só pode permanecer "
                    "quando acrescentar significado."
                ),
                (
                    "Mapeie cada citação em scripture_anchors na mesma ordem da locução. Insira a "
                    "primeira até "
                    f"{long_config.first_scripture_citation_max_seconds:.0f}s, mantenha "
                    "as citações separadas e não deixe um intervalo superior a "
                    f"{long_config.maximum_scripture_citation_gap_seconds:.0f}s. Cada citação deve "
                    "funcionar como evidência, mudança de ato ou aprofundamento da história."
                ),
                (
                    "Reserve o payoff para o quarto final, resolva central_question, entregue uma "
                    "aplicação específica e faça o callback retomar a abertura com novo "
                    "significado."
                ),
                (
                    "A closing termina imediatamente após exatamente uma ação de CTA: comentar, "
                    "inscrever-se ou compartilhar. Integre-a ao payoff, respeite os limites do "
                    "creative_contract e, se escolher comentário, coloque a resposta entre aspas."
                ),
                "A revisão deve manter approved=false e registrar blockers reais sem ocultá-los.",
                f"Grave ScriptDocument em {script_output}.",
                f"Grave BiblicalReview com script_version=1 em {review_output}.",
            ],
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
            result = run_autopilot_once(layout, allow_external=True, review_candidate=True)
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
        from pipeline_videos.remote_schedules import (
            RemoteScheduleStore,
            materialize_daily_schedules,
        )

        materialize_daily_schedules(
            store=store,
            schedule_store=RemoteScheduleStore(store.root),
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
        layout = prepare_job_project(job, projects_root=projects_root)
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
        if job.request.publication_mode == "scheduled":
            return store.transition(job, "waiting_youtube", **updates)
        return store.transition(job, "completed", **updates)
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
        "topic": job.request.topic or "automatic",
        "start_policy": job.request.start_policy,
        "publication_mode": job.request.publication_mode,
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
