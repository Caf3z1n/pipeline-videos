from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipeline_videos.models import (
    COMPACT_SHORT_THRESHOLD_SECONDS,
    DEFAULT_ELEVENLABS_MODEL_ID,
    DEFAULT_ELEVENLABS_VOICE_ID,
    HIGH_STAKES_HOOK_FAMILIES,
    MIN_LONG_DURATION_SECONDS,
    MIN_SHORT_DURATION_SECONDS,
    USER_SIX_HOOK_FAMILIES,
    ApprovalStatus,
    Briefing,
    ImageManifest,
    Palette,
    StageStatus,
    elevenlabs_voice_id_for_variant,
    normalize_elevenlabs_voice_variant,
)
from pipeline_videos.project import create_project, load_palette, next_actions
from pipeline_videos.review import (
    approve_biblical_review,
    approve_briefing,
    approve_script,
    import_biblical_review,
    validate_draft_preflight,
)
from pipeline_videos.scenes import build_scenes
from pipeline_videos.script import import_script
from pipeline_videos.state import invalidate, set_approval, stage_summary
from pipeline_videos.storage import (
    DEFAULT_PROJECTS_DIR,
    ProjectLayout,
    read_json,
    read_model,
    resolve_project,
    write_json,
)
from pipeline_videos.verse_selection import (
    HighStakesQuestionCatalogExhaustedError,
    create_auto_short,
    create_auto_video,
    sync_shared_usage,
)

load_dotenv()
console = Console()

app = typer.Typer(
    name="pipeline-videos",
    help="Pipeline local e retomável para vídeos bíblicos.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
images_app = typer.Typer(help="Manifesto, geração, importação e aprovação de imagens.")
script_app = typer.Typer(help="Roteiro manual e especialista de retenção para Shorts.")
audio_app = typer.Typer(help="Configuração e geração da narração.")
music_app = typer.Typer(help="Seleção local e aleatória da música de fundo.")
captions_app = typer.Typer(help="Geração das legendas visuais e do arquivo para YouTube.")
metadata_app = typer.Typer(help="Rascunhos de título, descrição e descoberta.")
youtube_app = typer.Typer(help="OAuth, upload, status e publicação segura no YouTube.")
remote_app = typer.Typer(help="Fila remota de produção, revisão privada e agendamento.")
analytics_app = typer.Typer(
    help="Importa métricas observadas e diagnostica apelo, engajamento e satisfação."
)
catalog_app = typer.Typer(help="Capacidade e ciclos do catálogo bíblico verificado.")
audit_app = typer.Typer(help="Auditoria local de tokens, contextos e orçamento de produção.")
approved_app = typer.Typer(help="Roteiros de Shorts congelados para produção econômica em lote.")
app.add_typer(images_app, name="images")
app.add_typer(script_app, name="script")
app.add_typer(audio_app, name="audio")
app.add_typer(music_app, name="music")
app.add_typer(captions_app, name="captions")
app.add_typer(metadata_app, name="metadata")
app.add_typer(youtube_app, name="youtube")
app.add_typer(remote_app, name="remote")
app.add_typer(analytics_app, name="analytics")
app.add_typer(catalog_app, name="catalog")
app.add_typer(audit_app, name="audit")
app.add_typer(approved_app, name="approved-scripts")


def _approved_store(jobs_dir: Path | None):
    from pipeline_videos.approved_scripts import ApprovedScriptStore
    from pipeline_videos.remote_jobs import RemoteJobStore

    return ApprovedScriptStore(RemoteJobStore(jobs_dir).root)


def _editorial_board(jobs_dir: Path | None):
    from pipeline_videos.editorial_board import EditorialBoardStore
    from pipeline_videos.remote_jobs import RemoteJobStore

    return EditorialBoardStore(RemoteJobStore(jobs_dir).root)


def _parse_cta_option(value: str):
    from pipeline_videos.approved_scripts import ApprovedCtaOption

    try:
        option_id, mode, spoken_text = value.split("|", 2)
        return ApprovedCtaOption(option_id=option_id, mode=mode, spoken_text=spoken_text)
    except Exception as exc:
        raise typer.BadParameter(
            "CTA inválido. Use id|comment|texto, id|subscribe|texto ou id|share|texto."
        ) from exc


@approved_app.command("workflow-status")
def approved_workflow_status(
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Mostra a etapa editorial atual e a única pergunta que deve ser feita."""

    typer.echo(json.dumps(_editorial_board(jobs_dir).workflow_status(), ensure_ascii=False))


@approved_app.command("production-sync")
def approved_production_sync(
    snapshot_base64: Annotated[
        str,
        typer.Option(
            "--snapshot-base64",
            help="Snapshot remoto JSON codificado em UTF-8/Base64.",
        ),
    ],
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Mescla estados remotos sem alterar nem reabrir o conteúdo aprovado."""

    try:
        decoded = base64.b64decode(snapshot_base64, validate=True).decode("utf-8")
        snapshot = json.loads(decoded)
        if not isinstance(snapshot, dict):
            raise ValueError("o conteúdo precisa ser um objeto JSON")
        result = _approved_store(jobs_dir).merge_production_state(snapshot)
    except Exception as exc:
        raise typer.BadParameter(f"Snapshot remoto inválido: {exc}") from exc
    typer.echo(json.dumps(result, ensure_ascii=False))


@approved_app.command("hook-check")
def approved_hook_check(
    hooks: Annotated[
        list[str],
        typer.Option("--hook", help="Repita para validar vários hooks antes de exibi-los."),
    ],
) -> None:
    """Valida hooks em memória; não salva nem inicia nenhuma etapa."""

    from pipeline_videos.editorial_board import validate_editorial_hook

    if not hooks:
        raise typer.BadParameter("Informe ao menos um --hook.")
    errors: list[dict[str, str | int]] = []
    for index, hook in enumerate(hooks, start=1):
        try:
            validate_editorial_hook(hook)
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
    if errors:
        typer.echo(json.dumps({"valid": False, "errors": errors}, ensure_ascii=False))
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"valid": True, "count": len(hooks)}, ensure_ascii=False))


@approved_app.command("topic-suggest")
def approved_topic_suggest(
    title: Annotated[str, typer.Option("--title")],
    central_question: Annotated[str, typer.Option("--question")] = "",
    hook_direction: Annotated[str, typer.Option("--hook-direction")] = "",
    catalog_candidate_id: Annotated[str | None, typer.Option("--catalog-id")] = None,
    source_passages: Annotated[list[str] | None, typer.Option("--passage")] = None,
    answer_points: Annotated[list[str] | None, typer.Option("--answer-point")] = None,
    uncertainty_boundaries: Annotated[
        list[str] | None,
        typer.Option("--uncertainty"),
    ] = None,
    duration_seconds: Annotated[int, typer.Option("--duration", min=1, max=180)] = 50,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Salva somente uma sugestão de tema; não cria roteiro nem projeto."""

    from pipeline_videos.editorial_board import editorial_topic_summary

    topic = _editorial_board(jobs_dir).suggest_topic(
        title=title,
        central_question=central_question,
        hook_direction=hook_direction,
        catalog_candidate_id=catalog_candidate_id,
        source_passages=source_passages,
        required_answer_points=answer_points,
        uncertainty_boundaries=uncertainty_boundaries,
        target_duration_seconds=duration_seconds,
    )
    typer.echo(json.dumps(editorial_topic_summary(topic), ensure_ascii=False))


@approved_app.command("topic-list")
def approved_topic_list(
    status_filter: Annotated[str | None, typer.Option("--status")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Lista temas de forma curta, sem iniciar outra etapa."""

    from pipeline_videos.editorial_board import editorial_topic_summary

    allowed = ("suggested", "approved", "drafted", "withdrawn")
    if status_filter is not None and status_filter not in allowed:
        raise typer.BadParameter("Status de tema inválido.")
    items = _editorial_board(jobs_dir).list_topics(status=status_filter)
    typer.echo(
        json.dumps(
            {
                "count": len(items),
                "items": [editorial_topic_summary(item) for item in items],
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("topic-approve")
def approved_topic_approve(
    topic_ids: Annotated[
        list[str],
        typer.Option("--topic-id", help="Repita para aprovar vários temas."),
    ],
    approved_by: Annotated[str, typer.Option("--approved-by")] = "Pedro",
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Aprova e salva vários temas; não escreve roteiros."""

    from pipeline_videos.editorial_board import editorial_topic_summary

    if not topic_ids:
        raise typer.BadParameter("Informe ao menos um --topic-id.")
    items = _editorial_board(jobs_dir).approve_topics(
        topic_ids,
        approved_by=approved_by,
    )
    typer.echo(
        json.dumps(
            {
                "approved": len(items),
                "items": [editorial_topic_summary(item) for item in items],
                "next_question": "Você quer gerar roteiros para quais temas aprovados?",
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("draft-batch-prepare")
def approved_draft_batch_prepare(
    topic_ids: Annotated[list[str] | None, typer.Option("--topic-id")] = None,
    count: Annotated[int, typer.Option("--count", min=1, max=12)] = 3,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Prepara uma única fila econômica; não inicia agente nem produção."""

    batch = _editorial_board(jobs_dir).prepare_batch(
        topic_ids=topic_ids,
        count=count,
    )
    typer.echo(
        json.dumps(
            {
                "batch_id": batch.batch_id,
                "status": batch.status,
                "topic_count": len(batch.topic_ids),
                "queue_path": batch.queue_path,
                "limits": {
                    "script_specialists": 1,
                    "biblical_reviewers": 0,
                    "projects": 0,
                    "media": 0,
                },
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("draft-batch-import")
def approved_draft_batch_import(
    package: Annotated[Path, typer.Option("--package")],
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Importa um lote já escrito e revisado, sem executar etapas adicionais."""

    batch = _editorial_board(jobs_dir).import_batch(package)
    typer.echo(
        json.dumps(
            {
                "batch_id": batch.batch_id,
                "status": batch.status,
                "script_count": len(batch.script_ids),
                "aggregate_hash": batch.aggregate_hash,
                "next_question": "Você quer revisar, alterar ou aprovar este lote?",
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("draft-list")
def approved_draft_list(
    status_filter: Annotated[str | None, typer.Option("--status")] = None,
    include_text: Annotated[bool, typer.Option("--text/--no-text")] = False,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Lista roteiros editoriais sem disparar validação ou produção."""

    from pipeline_videos.editorial_board import editorial_script_summary

    allowed = ("draft", "approved", "withdrawn")
    if status_filter is not None and status_filter not in allowed:
        raise typer.BadParameter("Status de roteiro editorial inválido.")
    items = _editorial_board(jobs_dir).list_scripts(status=status_filter)
    typer.echo(
        json.dumps(
            {
                "count": len(items),
                "items": [
                    editorial_script_summary(item, include_text=include_text) for item in items
                ],
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("draft-batch-review")
def approved_draft_batch_review(
    batch_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Mostra os textos e o único hash necessário para aprovação em massa."""

    typer.echo(
        json.dumps(
            _editorial_board(jobs_dir).review_batch(batch_id),
            ensure_ascii=False,
        )
    )


@approved_app.command("draft-batch-approve")
def approved_draft_batch_approve(
    batch_id: str,
    confirm_hash: Annotated[str, typer.Option("--confirm-hash")],
    approved_by: Annotated[str, typer.Option("--approved-by")] = "Pedro",
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Aprova todos os roteiros exibidos pelo hash agregado."""

    batch = _editorial_board(jobs_dir).approve_batch(
        batch_id,
        confirm_hash=confirm_hash,
        approved_by=approved_by,
    )
    typer.echo(
        json.dumps(
            {
                "batch_id": batch.batch_id,
                "status": batch.status,
                "approved_scripts": len(batch.script_ids),
                "production_script_ids": batch.production_script_ids,
                "next_question": "Você quer apenas listar os aprovados ou produzir algum?",
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("capture")
def approved_capture(
    project: str,
    label: Annotated[str | None, typer.Option("--label")] = None,
    cta_options: Annotated[list[str] | None, typer.Option("--cta")] = None,
    selected_cta: Annotated[str | None, typer.Option("--selected-cta")] = None,
    voice_variant: Annotated[str, typer.Option("--voice")] = "jesus_1",
    music_track: Annotated[str, typer.Option("--music")] = "leaning",
    max_scenes: Annotated[int, typer.Option("--max-scenes", min=3, max=10)] = 7,
    seo_title: Annotated[str | None, typer.Option("--seo-title")] = None,
    seo_description: Annotated[str | None, typer.Option("--seo-description")] = None,
    seo_hashtags: Annotated[list[str] | None, typer.Option("--seo-hashtag")] = None,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Congela um projeto editorial completo como candidato à aprovação."""

    if voice_variant not in {"jesus_1", "jesus_2"}:
        raise typer.BadParameter("--voice precisa ser jesus_1 ou jesus_2.")
    if music_track not in {"leaning", "aloboi"}:
        raise typer.BadParameter("--music precisa ser leaning ou aloboi.")
    candidate = _approved_store(jobs_dir).capture_project(
        resolve_project(project, projects_dir),
        label=label,
        cta_options=(
            [_parse_cta_option(item) for item in cta_options] if cta_options is not None else None
        ),
        selected_cta_id=selected_cta,
        voice_variant=voice_variant,
        music_track=music_track,
        max_scenes=max_scenes,
        seo_title=seo_title,
        seo_description=seo_description,
        seo_hashtags=seo_hashtags,
    )
    from pipeline_videos.approved_scripts import approved_script_summary

    typer.echo(
        json.dumps(
            approved_script_summary(candidate, include_narration=True),
            ensure_ascii=False,
        )
    )


@approved_app.command("approve")
def approved_approve(
    script_id: str,
    confirm_hash: Annotated[str, typer.Option("--confirm-hash")],
    approved_by: Annotated[str, typer.Option("--approved-by")],
    note: Annotated[str | None, typer.Option("--note")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Aprova explicitamente o conteúdo exato mostrado pelo hash."""

    from pipeline_videos.approved_scripts import approved_script_summary

    approved = _approved_store(jobs_dir).approve(
        script_id,
        confirm_hash=confirm_hash,
        approved_by=approved_by,
        note=note,
    )
    typer.echo(json.dumps(approved_script_summary(approved), ensure_ascii=False))


@approved_app.command("cta-select")
def approved_cta_select(
    script_id: str,
    option_id: Annotated[str, typer.Option("--option")],
    spoken_text: Annotated[str | None, typer.Option("--text")] = None,
    mode: Annotated[str | None, typer.Option("--mode")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Escolhe uma opção ou cadastra um novo CTA antes da aprovação."""

    if mode is not None and mode not in {"comment", "subscribe", "share"}:
        raise typer.BadParameter("--mode precisa ser comment, subscribe ou share.")
    from pipeline_videos.approved_scripts import approved_script_summary

    updated = _approved_store(jobs_dir).select_cta(
        script_id,
        option_id=option_id,
        spoken_text=spoken_text,
        mode=mode,
    )
    typer.echo(
        json.dumps(
            approved_script_summary(updated, include_narration=True),
            ensure_ascii=False,
        )
    )


@approved_app.command("seo-set")
def approved_seo_set(
    script_id: str,
    title: Annotated[str | None, typer.Option("--title")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    specific_hashtags: Annotated[
        list[str] | None,
        typer.Option("--hashtag", help="Quando usado, repita exatamente três vezes."),
    ] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Ajusta título, descrição e três hashtags específicas antes da aprovação."""

    if specific_hashtags is not None and len(specific_hashtags) != 3:
        raise typer.BadParameter("Repita --hashtag exatamente três vezes.")
    from pipeline_videos.approved_scripts import approved_script_summary

    updated = _approved_store(jobs_dir).set_seo(
        script_id,
        title=title,
        description=description,
        specific_hashtags=specific_hashtags,
    )
    typer.echo(
        json.dumps(
            approved_script_summary(updated, include_narration=True),
            ensure_ascii=False,
        )
    )


@approved_app.command("list")
def approved_list(
    status_filter: Annotated[str | None, typer.Option("--status")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Lista quantos e quais roteiros estão em cada estado."""

    from pipeline_videos.approved_scripts import approved_script_summary

    allowed = ("draft", "approved", "reserved", "produced", "withdrawn")
    if status_filter is not None and status_filter not in allowed:
        raise typer.BadParameter("Status de roteiro inválido.")
    store = _approved_store(jobs_dir)
    items = store.list(status=status_filter)
    typer.echo(
        json.dumps(
            {
                "counts": {status: len(store.list(status=status)) for status in allowed},
                "items": [approved_script_summary(item) for item in items],
            },
            ensure_ascii=False,
        )
    )


@approved_app.command("show")
def approved_show(
    script_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Mostra narração, CTA e hash exatos de um roteiro."""

    from pipeline_videos.approved_scripts import approved_script_summary

    script, _ = _approved_store(jobs_dir).find(script_id)
    typer.echo(
        json.dumps(
            approved_script_summary(script, include_narration=True),
            ensure_ascii=False,
        )
    )


@approved_app.command("release")
def approved_release(
    count: Annotated[int, typer.Option("--count", min=1, max=20)] = 1,
    script_ids: Annotated[list[str] | None, typer.Option("--script-id")] = None,
    script_titles: Annotated[list[str] | None, typer.Option("--title")] = None,
    authorize_external: Annotated[bool, typer.Option("--authorize-external")] = False,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Cria jobs econômicos com upload privado a partir da fila aprovada."""

    if not authorize_external:
        raise typer.BadParameter(
            "Use --authorize-external para autorizar imagens, ElevenLabs e upload privado."
        )
    from pipeline_videos.approved_scripts import approved_script_summary
    from pipeline_videos.remote_jobs import RemoteJobStore, RemoteVideoRequest, job_summary

    job_store = RemoteJobStore(jobs_dir)
    script_store = _approved_store(jobs_dir)
    if script_ids and script_titles:
        raise typer.BadParameter("Use --script-id ou --title, não ambos.")
    if script_titles:
        script_ids = [
            script_store.find_by_label(title, status="approved").approved_script_id
            for title in script_titles
        ]
    jobs = []
    for script_id in script_ids or [None] * count:
        now = datetime.now(UTC)
        job_id = f"video-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
        approved = script_store.reserve(job_id, script_id=script_id)
        request = RemoteVideoRequest(
            video_format="short",
            production_profile="approved_fast_v1",
            approved_script_id=approved.approved_script_id,
            approved_script_hash=approved.content_hash,
            duration_seconds=max(MIN_SHORT_DURATION_SECONDS, approved.target_duration_seconds),
            editorial_profile=approved.editorial_profile,
            music_track=approved.music_track,
            voice_variant=approved.voice_variant,
            publication_mode="private_review",
            external_services_authorized=True,
        )
        try:
            job = job_store.enqueue(request, job_id=job_id, now=now)
        except Exception:
            script_store.release_reservation(approved.approved_script_id, job_id=job_id)
            raise
        jobs.append(
            {
                "job": job_summary(job),
                "script": approved_script_summary(approved),
            }
        )
    typer.echo(json.dumps(jobs, ensure_ascii=False))


@approved_app.command("schedule-create")
def approved_schedule_create(
    name: Annotated[str, typer.Option("--name")],
    production_times: Annotated[list[str], typer.Option("--production-time")],
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    authorize_external: Annotated[bool, typer.Option("--authorize-external")] = False,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Produz diariamente roteiros aprovados e envia cada vídeo como privado."""

    if len(production_times) not in {1, 2}:
        raise typer.BadParameter("Informe um ou dois horários de produção.")
    from pipeline_videos.remote_jobs import RemoteJobStore
    from pipeline_videos.remote_schedules import (
        DailyShortSlot,
        RemoteScheduleStore,
        schedule_summary,
    )

    job_store = RemoteJobStore(jobs_dir)
    schedule = RemoteScheduleStore(job_store.root).create(
        name=name,
        slots=[DailyShortSlot(production_time=_daily_time(value)) for value in production_times],
        timezone=timezone,
        script_source="approved_pool",
        publication_mode="private_review",
        external_services_authorized=authorize_external,
        public_scheduling_authorized=False,
        enabled=enabled,
    )
    typer.echo(json.dumps(schedule_summary(schedule), ensure_ascii=False))


@audit_app.command("tokens")
def audit_tokens(
    project: str,
    sessions_root: Annotated[Path | None, typer.Option("--sessions-root")] = None,
    session_logs: Annotated[list[Path] | None, typer.Option("--session-log")] = None,
) -> None:
    """Descobre rollouts locais e persiste tokens totais e por etapa."""

    from pipeline_videos.token_audit import write_local_token_audit

    layout = _project(project)
    payload = write_local_token_audit(
        layout,
        sessions_root=sessions_root,
        session_logs=session_logs,
    )
    console.print_json(
        json.dumps(
            {
                "metrics": str(layout.root / "audit" / "production-metrics.json"),
                "report": str(layout.root / "audit" / "report.md"),
                "sessions": payload["codex"]["sessions"],
                "usage": payload["codex"]["usage"],
                "by_stage": payload["codex"]["by_stage"],
                "budget": payload["token_budget"],
            },
            ensure_ascii=False,
        )
    )


@catalog_app.command("status")
def catalog_status(
    video_format: Annotated[
        str,
        typer.Option("--format", help="Formato principal: short ou long."),
    ] = "short",
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Exibe a capacidade do ciclo sem reservar passagem nem alterar o registro."""
    from pipeline_videos.verse_selection import (
        catalog_capacity_summary,
        combined_usage_index,
        load_verse_catalog,
    )

    normalized = (
        "short" if video_format == "short" else "landscape" if video_format == "long" else None
    )
    if normalized is None:
        raise typer.BadParameter("--format precisa ser short ou long.")
    root = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    catalog = load_verse_catalog()
    usage = combined_usage_index(root, catalog=catalog)
    summary = catalog_capacity_summary(catalog, usage, video_format=normalized)
    typer.echo(json.dumps(summary, ensure_ascii=False))


def _codex_image_generation_status() -> tuple[str, str]:
    codex = shutil.which("codex")
    if not codex:
        return "AUSENTE", "instale e autentique o Codex CLI"
    try:
        completed = subprocess.run(
            [codex, "features", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "PENDENTE", "não foi possível consultar os recursos do Codex CLI"
    enabled = any(
        line.split()[:3] == ["image_generation", "stable", "true"]
        for line in completed.stdout.splitlines()
    )
    if enabled:
        return "DISPONÍVEL", "Codex CLI com image_generation habilitado"
    return "PENDENTE", "image_generation não está habilitado neste Codex CLI"


@remote_app.command("enqueue")
def remote_enqueue(
    payload_base64: Annotated[
        str, typer.Option("--payload-base64", help="Pedido JSON codificado em UTF-8/Base64.")
    ],
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Valida e inclui um pedido remoto na fila persistente."""
    from pipeline_videos.remote_jobs import (
        RemoteJobStore,
        RemoteVideoRequest,
        job_summary,
    )

    try:
        payload = base64.b64decode(payload_base64, validate=True).decode("utf-8")
        request = RemoteVideoRequest.model_validate_json(payload)
    except Exception as exc:
        raise typer.BadParameter(f"Payload remoto inválido: {exc}") from exc
    job = RemoteJobStore(jobs_dir).enqueue(request)
    typer.echo(json.dumps(job_summary(job), ensure_ascii=False))


@remote_app.command("status")
def remote_status(
    job_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Exibe o estado persistido de um job."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary

    job, _ = RemoteJobStore(jobs_dir).find(job_id)
    typer.echo(json.dumps(job_summary(job), ensure_ascii=False))


@remote_app.command("wait")
def remote_wait(
    job_id: str,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout", min=0, max=28_800),
    ] = 21_600,
    poll_interval_seconds: Annotated[
        float,
        typer.Option("--poll-interval", min=0.25, max=60),
    ] = 2.0,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Aguarda o evento terminal no servidor sem envolver um agente."""
    from pipeline_videos.remote_jobs import (
        RemoteJobStore,
        job_summary,
        wait_for_terminal_job,
    )

    store = RemoteJobStore(jobs_dir)
    job = wait_for_terminal_job(
        store,
        job_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    typer.echo(json.dumps(job_summary(job), ensure_ascii=False))


@remote_app.command("list")
def remote_list(
    status_filter: Annotated[str | None, typer.Option("--status")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Lista os jobs mais recentes sem acessar serviços externos."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary

    allowed = {"queued", "running", "waiting_youtube", "completed", "failed", "cancelled"}
    if status_filter is not None and status_filter not in allowed:
        raise typer.BadParameter("Status remoto inválido.")
    jobs = RemoteJobStore(jobs_dir).list(status=status_filter)  # type: ignore[arg-type]
    typer.echo(json.dumps([job_summary(job) for job in jobs[:limit]], ensure_ascii=False))


@remote_app.command("approved-state")
def remote_approved_state(
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Exporta um snapshot compacto do estado dos roteiros pré-aprovados."""

    typer.echo(
        json.dumps(
            _approved_store(jobs_dir).production_state_snapshot(),
            ensure_ascii=False,
        )
    )


@remote_app.command("approved-import")
def remote_approved_import(
    snapshot_base64: Annotated[
        str,
        typer.Option(
            "--snapshot-base64",
            help="ApprovedShortScript completo codificado em UTF-8/Base64.",
        ),
    ],
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Importa um pacote aprovado imutável antes da liberação remota."""

    from pipeline_videos.approved_scripts import (
        ApprovedShortScript,
        approved_script_summary,
    )

    try:
        decoded = base64.b64decode(snapshot_base64, validate=True).decode("utf-8")
        script = ApprovedShortScript.model_validate_json(decoded)
        imported, created = _approved_store(jobs_dir).import_approved_snapshot(script)
    except Exception as exc:
        raise typer.BadParameter(f"Snapshot aprovado inválido: {exc}") from exc
    typer.echo(
        json.dumps(
            {
                "created": created,
                "script": approved_script_summary(imported),
            },
            ensure_ascii=False,
        )
    )


@remote_app.command("cancel")
def remote_cancel(
    job_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Cancela um job ainda não iniciado ou aguardando agendamento."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary

    store = RemoteJobStore(jobs_dir)
    job = store.cancel(job_id)
    if (
        job.request.production_profile == "approved_fast_v1"
        and job.request.approved_script_id is not None
    ):
        _approved_store(jobs_dir).release_reservation(
            job.request.approved_script_id,
            job_id=job.job_id,
        )
    typer.echo(json.dumps(job_summary(job), ensure_ascii=False))


@remote_app.command("retry")
def remote_retry(
    job_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Recoloca um job com falha na fila."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary

    job = RemoteJobStore(jobs_dir).retry(job_id)
    typer.echo(json.dumps(job_summary(job), ensure_ascii=False))


@remote_app.command("work-once")
def remote_work_once(
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Processa no máximo um job devido; usado pelo timer da VPS."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary, work_once

    job = work_once(
        store=RemoteJobStore(jobs_dir),
        projects_root=projects_dir,
        dry_run=dry_run,
    )
    payload = {"status": "idle"} if job is None else job_summary(job)
    typer.echo(json.dumps(payload, ensure_ascii=False))


def _daily_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Horário inválido: {value}. Use HH:MM.") from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise typer.BadParameter(f"Horário inválido: {value}. Use HH:MM sem fuso.")
    return parsed


@remote_app.command("schedule-create")
def remote_schedule_create(
    name: Annotated[str, typer.Option("--name")],
    production_times: Annotated[
        list[str],
        typer.Option("--production-time", help="Repita uma ou duas vezes; formato HH:MM."),
    ],
    publication_times: Annotated[
        list[str], typer.Option("--publication-time", help="Um horário público para cada Short.")
    ],
    publish_days_after: Annotated[int, typer.Option("--publish-days-after", min=0, max=30)] = 1,
    timezone: Annotated[str, typer.Option("--timezone")] = "America/Sao_Paulo",
    duration_seconds: Annotated[
        int, typer.Option("--duration", min=MIN_SHORT_DURATION_SECONDS, max=180)
    ] = 50,
    enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    authorize_external: Annotated[bool, typer.Option("--authorize-external")] = False,
    authorize_public_schedule: Annotated[bool, typer.Option("--authorize-public-schedule")] = False,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Cria uma agenda idempotente de um ou dois Shorts por dia."""
    from pipeline_videos.remote_jobs import RemoteJobStore
    from pipeline_videos.remote_schedules import (
        DailyShortSlot,
        RemoteScheduleStore,
        schedule_summary,
    )

    if len(production_times) not in {1, 2}:
        raise typer.BadParameter("Informe um ou dois horários de produção.")
    if len(publication_times) != len(production_times):
        raise typer.BadParameter("Informe um horário de publicação para cada produção.")
    slots = [
        DailyShortSlot(
            production_time=_daily_time(production_value),
            publication_time=_daily_time(publication_value),
            publication_day_offset=publish_days_after,
        )
        for production_value, publication_value in zip(
            production_times, publication_times, strict=True
        )
    ]
    job_store = RemoteJobStore(jobs_dir)
    schedule = RemoteScheduleStore(job_store.root).create(
        name=name,
        slots=slots,
        timezone=timezone,
        duration_seconds=duration_seconds,
        enabled=enabled,
        external_services_authorized=authorize_external,
        public_scheduling_authorized=authorize_public_schedule,
    )
    typer.echo(json.dumps(schedule_summary(schedule), ensure_ascii=False))


@remote_app.command("schedule-list")
def remote_schedule_list(
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Lista agendas diárias sem criar jobs."""
    from pipeline_videos.remote_jobs import RemoteJobStore
    from pipeline_videos.remote_schedules import RemoteScheduleStore, schedule_summary

    job_store = RemoteJobStore(jobs_dir)
    schedules = RemoteScheduleStore(job_store.root).list()
    typer.echo(json.dumps([schedule_summary(item) for item in schedules], ensure_ascii=False))


def _set_remote_schedule_enabled(schedule_id: str, jobs_dir: Path | None, enabled: bool) -> None:
    from pipeline_videos.remote_jobs import RemoteJobStore
    from pipeline_videos.remote_schedules import RemoteScheduleStore, schedule_summary

    job_store = RemoteJobStore(jobs_dir)
    schedule = RemoteScheduleStore(job_store.root).set_enabled(schedule_id, enabled)
    typer.echo(json.dumps(schedule_summary(schedule), ensure_ascii=False))


@remote_app.command("schedule-enable")
def remote_schedule_enable(
    schedule_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Ativa uma agenda diária existente."""
    _set_remote_schedule_enabled(schedule_id, jobs_dir, True)


@remote_app.command("schedule-disable")
def remote_schedule_disable(
    schedule_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Pausa uma agenda sem cancelar jobs já materializados."""
    _set_remote_schedule_enabled(schedule_id, jobs_dir, False)


@remote_app.command("schedule-delete")
def remote_schedule_delete(
    schedule_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Remove uma agenda sem cancelar jobs já materializados."""
    from pipeline_videos.remote_jobs import RemoteJobStore
    from pipeline_videos.remote_schedules import RemoteScheduleStore, schedule_summary

    job_store = RemoteJobStore(jobs_dir)
    schedule = RemoteScheduleStore(job_store.root).delete(schedule_id)
    typer.echo(json.dumps(schedule_summary(schedule), ensure_ascii=False))


@remote_app.command("schedule-materialize")
def remote_schedule_materialize(
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Materializa uma vez os jobs de hoje; chamadas repetidas são idempotentes."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary
    from pipeline_videos.remote_schedules import RemoteScheduleStore, materialize_daily_schedules

    job_store = RemoteJobStore(jobs_dir)
    jobs = materialize_daily_schedules(
        store=job_store,
        schedule_store=RemoteScheduleStore(job_store.root),
        approved_store=_approved_store(jobs_dir),
    )
    typer.echo(json.dumps([job_summary(job) for job in jobs], ensure_ascii=False))


def _print_orchestration_plan(
    layout: ProjectLayout,
    *,
    allow_external: bool,
    title: str = "Ondas e dependências",
) -> None:
    from pipeline_videos.orchestration import build_one_shot_plan, format_plan_lines

    plan = build_one_shot_plan(layout, allow_external=allow_external)
    console.print(f"[bold]{title}[/bold]")
    for line in format_plan_lines(plan):
        console.print(line, markup=False)


def _project(reference: str, projects_dir: Path | None = None) -> ProjectLayout:
    return resolve_project(reference, projects_dir)


@analytics_app.command("import")
def analytics_import(project: str, source: Path) -> None:
    from pipeline_videos.analytics import import_experiment_file

    memory = import_experiment_file(_project(project), source)
    console.print(f"[green]{len(memory.experiments)} experimento(s) registrado(s).[/green]")


@analytics_app.command("metrics-import")
def analytics_metrics_import(project: str, source: Path) -> None:
    """Importa snapshots imutáveis de Shorts e atualiza alertas idempotentes."""

    from pipeline_videos.analytics import active_short_notifications, import_short_metric_file

    memory, diagnostics = import_short_metric_file(_project(project), source)
    active = [
        {
            "video_id": diagnostic.video_id,
            "snapshot_id": diagnostic.snapshot_id,
            "evidence_status": diagnostic.evidence_status,
            **notification.model_dump(mode="json"),
        }
        for diagnostic, notification in active_short_notifications(diagnostics)
    ]
    console.print_json(
        json.dumps(
            {
                "snapshots": len(memory.snapshots),
                "diagnostics": len(diagnostics.diagnostics),
                "active_alerts": active,
            },
            ensure_ascii=False,
        )
    )


@analytics_app.command("diagnose")
def analytics_diagnose(project: str) -> None:
    """Mostra o diagnóstico atual sem alterar snapshots nem estado editorial."""

    from pipeline_videos.analytics import load_short_performance_diagnostics

    result = load_short_performance_diagnostics(_project(project))
    console.print_json(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


def _save_provider(
    layout: ProjectLayout, *, audio: str | None = None, images: str | None = None
) -> None:
    manifest = layout.load_manifest()
    audio_changed = False
    images_changed = False
    if audio:
        if audio not in {"mock", "elevenlabs", "local_manual"}:
            raise typer.BadParameter("Áudio deve ser mock, elevenlabs ou local_manual.")
        audio_changed = manifest.config.audio.provider != audio
        manifest.config.audio.provider = audio  # type: ignore[assignment]
    if images:
        if images not in {"fixture", "codex"}:
            raise typer.BadParameter("Imagens devem usar fixture ou codex.")
        images_changed = manifest.config.images.provider != images
        manifest.config.images.provider = images  # type: ignore[assignment]
    layout.save_manifest(manifest)
    if audio_changed:
        invalidate(
            layout,
            stages=("audio", "captions", "metadata", "render", "validation"),
            approvals=("metadata", "final_render"),
            note=f"O provedor de áudio foi alterado para {audio}.",
        )
    if images_changed:
        invalidate(
            layout,
            stages=("images", "render", "validation"),
            approvals=("images", "final_render"),
            note=f"O provedor de imagens foi alterado para {images}.",
        )


@app.command("doctor")
def doctor() -> None:
    """Verifica dependências locais sem executar chamadas externas."""
    table = Table(title="Diagnóstico local")
    table.add_column("Componente")
    table.add_column("Estado")
    table.add_column("Detalhe")
    for name in ("uv", "ffmpeg", "ffprobe"):
        path = shutil.which(name)
        table.add_row(name, "OK" if path else "AUSENTE", path or "Instale antes de renderizar")
    table.add_row("Python", "OK", sys.version.split()[0])
    table.add_row(
        "ElevenLabs key",
        "CONFIGURADA" if os.getenv("ELEVENLABS_API_KEY") else "OPCIONAL",
        "valor não exibido" if os.getenv("ELEVENLABS_API_KEY") else "mock disponível para testes",
    )
    configured_voice = os.getenv("ELEVENLABS_VOICE_ID")
    table.add_row(
        "ElevenLabs voice",
        "CONFIGURADA" if configured_voice else "PADRÃO",
        configured_voice or DEFAULT_ELEVENLABS_VOICE_ID,
    )
    table.add_row(
        "ElevenLabs model",
        "CONFIGURADO" if os.getenv("ELEVENLABS_MODEL_ID") else "PADRÃO",
        os.getenv("ELEVENLABS_MODEL_ID") or DEFAULT_ELEVENLABS_MODEL_ID,
    )
    image_status, image_detail = _codex_image_generation_status()
    table.add_row("Imagem Codex", image_status, image_detail)
    from pipeline_videos.publication import token_file

    youtube_token = token_file()
    table.add_row(
        "YouTube OAuth",
        "CONFIGURADO" if youtube_token.is_file() else "PENDENTE",
        str(youtube_token),
    )
    console.print(table)


def _validate_short_bootstrap_options(
    *,
    creative_mode: str,
    hook_family: str,
    voice_variant: str,
    music_file: Path | None,
    duration: int,
    editorial_profile: str = "verse_led_v1",
) -> tuple[str | None, str | None, str]:
    from pipeline_videos.creative_direction import CREATIVE_MODE_SPECS
    from pipeline_videos.music import SUPPORTED_MUSIC_EXTENSIONS

    mode = None if creative_mode == "auto" else creative_mode
    if mode is not None and mode not in CREATIVE_MODE_SPECS:
        raise typer.BadParameter(f"Modo criativo desconhecido: {creative_mode}")
    family = None if hook_family == "auto" else hook_family
    compact = duration < COMPACT_SHORT_THRESHOLD_SECONDS
    high_stakes = editorial_profile == "high_stakes_biblical_question_v1"
    if high_stakes and not COMPACT_SHORT_THRESHOLD_SECONDS <= duration <= 60:
        raise typer.BadParameter("O perfil high-stakes exige --duration entre 45 e 60 segundos.")
    if compact and mode not in {None, "devotional_application", "pastoral_encouragement"}:
        raise typer.BadParameter(
            "Shorts compactos aceitam somente devotional_application ou pastoral_encouragement."
        )
    if compact and family not in {None, "emotional_interruption"}:
        raise typer.BadParameter(
            "Shorts compactos usam somente --hook-family emotional_interruption."
        )
    if high_stakes and family is not None and family not in HIGH_STAKES_HOOK_FAMILIES:
        choices = ", ".join(HIGH_STAKES_HOOK_FAMILIES)
        raise typer.BadParameter(f"Use uma familia investigativa: {choices}")
    if (
        not compact
        and not high_stakes
        and family is not None
        and family not in USER_SIX_HOOK_FAMILIES
    ):
        choices = ", ".join(USER_SIX_HOOK_FAMILIES)
        raise typer.BadParameter(f"Use uma das seis famílias oficiais: {choices}")
    if family is not None and mode is None and not high_stakes:
        raise typer.BadParameter("--hook-family exige também --creative-mode.")
    try:
        variant = normalize_elevenlabs_voice_variant(voice_variant)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if music_file is not None:
        resolved = music_file.expanduser().resolve()
        if not resolved.is_file():
            raise typer.BadParameter(f"Arquivo de música não encontrado: {resolved}")
        if resolved.suffix.casefold() not in SUPPORTED_MUSIC_EXTENSIONS:
            raise typer.BadParameter(f"Formato de música não suportado: {resolved.suffix}")
    return mode, family, variant


def _apply_short_bootstrap_settings(
    layout: ProjectLayout,
    *,
    creative_mode: str | None,
    hook_family: str | None,
    voice_variant: str,
    music_file: Path | None,
    editorial_profile: str = "verse_led_v1",
) -> None:
    from pipeline_videos.creative_direction import (
        CREATIVE_MODE_SPECS,
        default_interpretive_stance,
        hook_families_for_mode,
        narration_tone_for_mode,
    )
    from pipeline_videos.music import import_music

    manifest = layout.load_manifest()
    compact = manifest.config.shorts_script.duration_profile == "adaptive_compact_v1"
    if editorial_profile != "high_stakes_biblical_question_v1" and (
        creative_mode is not None or compact
    ):
        briefing = read_model(layout.briefing, Briefing)
        selected_mode = creative_mode or (
            briefing.creative_mode
            if briefing.creative_mode in {"devotional_application", "pastoral_encouragement"}
            else "pastoral_encouragement"
        )
        selected_family = (
            "emotional_interruption"
            if compact
            else hook_family
            or hook_families_for_mode(
                selected_mode,
                short=True,
                hook_contract="user_six_v1",
            )[0]
        )
        spec = CREATIVE_MODE_SPECS[selected_mode]
        briefing.creative_mode = selected_mode
        briefing.interpretive_stance = default_interpretive_stance(selected_mode)
        briefing.narration_tone = (
            "íntimo, emocional e cinematográfico, como uma fala de Jesus em um filme épico"
            if compact
            else narration_tone_for_mode(selected_mode)
        )
        briefing.hook_policy = "required"
        briefing.hook_family = selected_family
        briefing.hook_contract = "cinematic_interruption_v1" if compact else "user_six_v1"
        briefing.call_to_action = "" if compact else briefing.call_to_action
        briefing.creative_rationale = f"{spec.label}: {spec.purpose}"
        write_json(layout.briefing, briefing)
    manifest = layout.load_manifest()
    manifest.config.audio.voice_id = elevenlabs_voice_id_for_variant(voice_variant)
    layout.save_manifest(manifest)
    if music_file is not None:
        import_music(layout, music_file)


@app.command("new")
def new_project(
    project_id: Annotated[str, typer.Argument(help="Identificador curto do vídeo.")],
    title: Annotated[str, typer.Option("--title", "-t", help="Título editorial inicial.")],
    duration: Annotated[
        int,
        typer.Option("--duration", help="Meta aproximada de duração em segundos."),
    ] = 540,
    exact_duration: Annotated[
        bool,
        typer.Option(
            "--exact-duration",
            help="Exige o alvo informado; use somente quando a duração for explicitamente exata.",
        ),
    ] = False,
    palette: Annotated[str | None, typer.Option("--palette")] = None,
    video_format: Annotated[
        str, typer.Option("--format", help="Formato do vídeo: landscape ou short.")
    ] = "landscape",
    creative_mode: Annotated[str, typer.Option("--creative-mode")] = "auto",
    hook_family: Annotated[str, typer.Option("--hook-family")] = "auto",
    voice_variant: Annotated[str, typer.Option("--voice-variant")] = "jesus_1",
    music_file: Annotated[Path | None, typer.Option("--music-file")] = None,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    if video_format not in {"landscape", "short"}:
        raise typer.BadParameter("Use landscape ou short em --format.")
    mode: str | None = None
    family: str | None = None
    variant = voice_variant
    if video_format == "short":
        mode, family, variant = _validate_short_bootstrap_options(
            creative_mode=creative_mode,
            hook_family=hook_family,
            voice_variant=voice_variant,
            music_file=music_file,
            duration=duration,
        )
    elif (
        creative_mode != "auto"
        or hook_family != "auto"
        or voice_variant != "jesus_1"
        or music_file is not None
    ):
        raise typer.BadParameter(
            "As opções criativas e --music-file deste bootstrap são de Shorts."
        )
    layout = create_project(
        project_id,
        title,
        projects_dir=projects_dir,
        target_duration_seconds=duration,
        exact_duration=exact_duration,
        palette_id=palette,
        video_format=video_format,
    )
    if video_format == "short":
        _apply_short_bootstrap_settings(
            layout,
            creative_mode=mode,
            hook_family=family,
            voice_variant=variant,
            music_file=music_file,
        )
    sync_shared_usage(layout.root.parent.parent)
    console.print(f"[green]Projeto criado:[/green] {layout.root}")
    console.print(
        "Próximo passo: execute o comando resume-prompt e use o texto na conversa com o Codex."
    )


@app.command("new-short")
def new_short(
    duration: Annotated[
        int,
        typer.Option(
            "--duration",
            help="Meta aproximada de duração em segundos.",
            min=MIN_SHORT_DURATION_SECONDS,
            max=180,
        ),
    ] = 50,
    duration_min: Annotated[
        float | None,
        typer.Option("--duration-min", help="Limite mínimo aceito para a duração real."),
    ] = None,
    duration_max: Annotated[
        float | None,
        typer.Option("--duration-max", help="Limite máximo aceito para a duração real."),
    ] = None,
    exact_duration: Annotated[
        bool,
        typer.Option(
            "--exact-duration",
            help="Exige o alvo informado; não use para pedidos como 'um Short de 50 segundos'.",
        ),
    ] = False,
    profile: Annotated[
        str,
        typer.Option("--profile", help="Perfil editorial: verse-led ou high-stakes."),
    ] = "verse-led",
    creative_mode: Annotated[str, typer.Option("--creative-mode")] = "auto",
    hook_family: Annotated[str, typer.Option("--hook-family")] = "auto",
    voice_variant: Annotated[str, typer.Option("--voice-variant")] = "jesus_1",
    music_file: Annotated[Path | None, typer.Option("--music-file")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short com pacote bíblico verificado e perfil editorial persistido."""
    profile_aliases = {
        "verse-led": "verse_led_v1",
        "verse_led_v1": "verse_led_v1",
        "high-stakes": "high_stakes_biblical_question_v1",
        "high_stakes_biblical_question_v1": "high_stakes_biblical_question_v1",
        "biblical-investigation": "high_stakes_biblical_question_v1",
    }
    editorial_profile = profile_aliases.get(profile.strip().casefold())
    if editorial_profile is None:
        raise typer.BadParameter("Use verse-led ou high-stakes em --profile.")
    mode, family, variant = _validate_short_bootstrap_options(
        creative_mode=creative_mode,
        hook_family=hook_family,
        voice_variant=voice_variant,
        music_file=music_file,
        duration=duration,
        editorial_profile=editorial_profile,
    )
    try:
        layout, candidate, score = create_auto_short(
            projects_dir=projects_dir,
            target_duration_seconds=duration,
            duration_min_seconds=duration_min,
            duration_max_seconds=duration_max,
            exact_duration=exact_duration,
            creative_mode_override=mode,
            hook_family_override=family,
            editorial_profile=editorial_profile,
        )
    except HighStakesQuestionCatalogExhaustedError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _apply_short_bootstrap_settings(
        layout,
        creative_mode=mode,
        hook_family=family,
        voice_variant=variant,
        music_file=music_file,
        editorial_profile=editorial_profile,
    )
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "project_id": layout.load_manifest().project_id,
                    "project_path": str(layout.root),
                    "passage": candidate.reference,
                    "editorial_profile": editorial_profile,
                    "question_id": (
                        candidate.candidate_id
                        if editorial_profile == "high_stakes_biblical_question_v1"
                        else None
                    ),
                    "central_question": (
                        candidate.central_question
                        if editorial_profile == "high_stakes_biblical_question_v1"
                        else None
                    ),
                    "opening_hook": read_model(layout.briefing, Briefing).opening_hook or None,
                    "duration_policy": layout.load_manifest().duration_policy,
                    "duration_min_seconds": layout.load_manifest().requested_duration_min_seconds,
                    "duration_max_seconds": layout.load_manifest().requested_duration_max_seconds,
                    "creative_mode": read_model(layout.briefing, Briefing).creative_mode,
                    "hook_family": read_model(layout.briefing, Briefing).hook_family,
                    "hook_contract": read_model(layout.briefing, Briefing).hook_contract,
                    "voice_variant": variant,
                    "music": str(music_file.resolve()) if music_file else None,
                    "next_command": f'uv run pipeline-videos run "{layout.root}" --dry-run',
                },
                ensure_ascii=False,
            )
        )
        return
    console.print(f"[green]Short criado:[/green] {layout.root}")
    if editorial_profile == "high_stakes_biblical_question_v1":
        console.print(f"[bold]Grande pergunta reservada:[/bold] {candidate.central_question}")
        console.print(
            f"[bold]Abertura curada:[/bold] {read_model(layout.briefing, Briefing).opening_hook}"
        )
    console.print(f"[bold]Versículo literal reservado:[/bold] {candidate.reference}")
    console.print(candidate.verse_text, markup=False)
    console.print(f"Seleção editorial: {candidate.title} (pontuação {score:g})")
    console.print(
        "Briefing e fonte foram preenchidos automaticamente; a aprovação humana continua pendente."
    )
    console.print(f'Próximo passo: uv run pipeline-videos run "{layout.root}" --dry-run')


@app.command("new-character-video")
def new_character_video(
    character: Annotated[str, typer.Argument(help="Personagem bíblico desejado.")],
    video_format: Annotated[
        str,
        typer.Option("--format", help="Formato: short ou long."),
    ] = "long",
    duration: Annotated[
        int | None,
        typer.Option("--duration", help="Meta aproximada de duração em segundos."),
    ] = None,
    exact_duration: Annotated[
        bool,
        typer.Option(
            "--exact-duration",
            help="Exige o alvo informado somente quando o pedido disser que a duração é exata.",
        ),
    ] = False,
    voice_variant: Annotated[str, typer.Option("--voice-variant")] = "jesus_1",
    profile: Annotated[
        str,
        typer.Option(
            "--profile",
            help="Perfil editorial: storytelling (padrão) ou portrait (analítico).",
        ),
    ] = "storytelling",
    json_output: Annotated[bool, typer.Option("--json")] = False,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria uma história de personagem com um pacote narrativo bíblico verificado."""
    normalized_format = video_format.strip().casefold()
    if normalized_format not in {"short", "long"}:
        raise typer.BadParameter("--format precisa ser short ou long.")
    target_duration = duration or (60 if normalized_format == "short" else 540)
    if normalized_format == "short" and not (
        COMPACT_SHORT_THRESHOLD_SECONDS <= target_duration <= 180
    ):
        raise typer.BadParameter(
            "Retratos narrativos em Short exigem duração entre "
            f"{COMPACT_SHORT_THRESHOLD_SECONDS} e 180 segundos."
        )
    if normalized_format == "long" and target_duration < MIN_LONG_DURATION_SECONDS:
        raise typer.BadParameter(
            f"Vídeos longos exigem ao menos {MIN_LONG_DURATION_SECONDS} segundos."
        )
    try:
        variant = normalize_elevenlabs_voice_variant(voice_variant)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    profile_aliases = {
        "storytelling": "biblical_storytelling",
        "biblical-storytelling": "biblical_storytelling",
        "biblical_storytelling": "biblical_storytelling",
        "portrait": "character_portrait",
        "character-portrait": "character_portrait",
        "character_portrait": "character_portrait",
    }
    selected_mode = profile_aliases.get(profile.strip().casefold())
    if selected_mode is None:
        raise typer.BadParameter("Use storytelling ou portrait em --profile.")
    layout, candidate, score = create_auto_video(
        projects_dir=projects_dir,
        target_duration_seconds=target_duration,
        exact_duration=exact_duration,
        video_format="short" if normalized_format == "short" else "landscape",
        creative_mode_override=selected_mode,
        requested_character=character,
    )
    if normalized_format == "short":
        _apply_short_bootstrap_settings(
            layout,
            creative_mode=selected_mode,
            hook_family=None,
            voice_variant=variant,
            music_file=None,
        )
    else:
        manifest = layout.load_manifest()
        manifest.config.audio.voice_id = elevenlabs_voice_id_for_variant(variant)
        layout.save_manifest(manifest)
    payload = {
        "project_id": layout.load_manifest().project_id,
        "project_path": str(layout.root),
        "character": character,
        "story": candidate.title,
        "main_passage": candidate.reference,
        "related_passages": [item.reference for item in candidate.related_passages],
        "profile": profile.strip().casefold(),
        "creative_mode": selected_mode,
        "score": score,
        "next_command": f'uv run pipeline-videos run "{layout.root}" --dry-run',
    }
    if json_output:
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return
    console.print(f"[green]Vídeo de personagem criado:[/green] {layout.root}")
    console.print(f"[bold]Personagem:[/bold] {', '.join(candidate.characters)}")
    console.print(f"[bold]História principal:[/bold] {candidate.title}")
    console.print(f"[bold]Passagem-âncora:[/bold] {candidate.reference}")
    console.print(
        f"[bold]Fontes relacionadas:[/bold] {len(candidate.related_passages)} passagens verificadas"
    )
    console.print(payload["next_command"])


@app.command("new-short-audio-manual")
def new_short_audio_manual(
    duration: Annotated[
        int,
        typer.Option(
            "--duration",
            help="Meta aproximada de duração em segundos.",
            min=MIN_SHORT_DURATION_SECONDS,
            max=180,
        ),
    ] = 50,
    exact_duration: Annotated[
        bool,
        typer.Option(
            "--exact-duration",
            help="Exige o alvo informado somente quando o pedido disser que a duração é exata.",
        ),
    ] = False,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short que aguarda a narração anexada e a alinha localmente."""
    layout, candidate, score = create_auto_short(
        projects_dir=projects_dir,
        target_duration_seconds=duration,
        exact_duration=exact_duration,
    )
    manifest = layout.load_manifest()
    manifest.config.audio.provider = "local_manual"
    layout.save_manifest(manifest)
    console.print(f"[green]Short com áudio manual criado:[/green] {layout.root}")
    console.print(f"[bold]Versículo reservado:[/bold] {candidate.reference}")
    console.print(candidate.verse_text, markup=False)
    console.print(f"Seleção editorial: {candidate.title} (pontuação {score:g})")
    console.print(
        "Após o roteiro, o fluxo mostrará a locução completa e aguardará um único arquivo de voz. "
        "O alinhamento será local, sem ElevenLabs."
    )
    console.print(f'Próximo passo: uv run pipeline-videos run "{layout.root}" --dry-run')


@app.command("status")
def status(
    project: str,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    layout = _project(project, projects_dir)
    manifest = layout.load_manifest()
    analytics_summary: dict[str, Any] | None = None
    if manifest.video_format == "short":
        from pipeline_videos.analytics import (
            active_short_notifications,
            load_short_performance_diagnostics,
        )

        diagnostics = load_short_performance_diagnostics(layout)
        active_alerts = [
            {
                "video_id": diagnostic.video_id,
                "snapshot_id": diagnostic.snapshot_id,
                "code": notification.code,
                "severity": notification.severity,
                "stage": notification.stage,
                "title": notification.title,
                "evidence_status": diagnostic.evidence_status,
            }
            for diagnostic, notification in active_short_notifications(diagnostics)
        ]
        analytics_summary = {
            "diagnostics": len(diagnostics.diagnostics),
            "active_alerts": active_alerts,
        }
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "project_id": manifest.project_id,
                    "path": str(layout.root),
                    "format": manifest.video_format,
                    "stages": stage_summary(layout),
                    "next_actions": next_actions(layout),
                    "analytics": analytics_summary,
                    "autopilot_state": (
                        read_json(layout.autopilot_state)
                        if layout.autopilot_state.is_file()
                        else None
                    ),
                },
                ensure_ascii=False,
            )
        )
        return
    console.print(Panel(f"[bold]{manifest.title}[/bold]\n{layout.root}", title=manifest.project_id))
    table = Table(title="Etapas")
    table.add_column("Etapa")
    table.add_column("Estado")
    table.add_column("Tentativas", justify="right")
    table.add_column("Observação")
    for row in stage_summary(layout):
        table.add_row(row["stage"], row["status"], str(row["attempts"]), row["note"])
    console.print(table)
    approvals = Table(title="Aprovações humanas")
    approvals.add_column("Item")
    approvals.add_column("Estado")
    approvals.add_column("Responsável")
    for name, record in manifest.approvals.items():
        approvals.add_row(name, record.status.value, record.decided_by or "—")
    console.print(approvals)
    console.print("[bold]Próximas ações[/bold]")
    for item in next_actions(layout):
        console.print(f"- {item}")
    if analytics_summary and analytics_summary["active_alerts"]:
        alerts = Table(title="Alertas de desempenho do Short")
        alerts.add_column("Vídeo")
        alerts.add_column("Estágio")
        alerts.add_column("Severidade")
        alerts.add_column("Código")
        alerts.add_column("Evidência")
        alerts.add_column("Diagnóstico")
        for item in analytics_summary["active_alerts"]:
            alerts.add_row(
                item["video_id"],
                item["stage"],
                item["severity"],
                item["code"],
                item["evidence_status"],
                item["title"],
            )
        console.print(alerts)


@app.command("resume-prompt")
def resume_prompt(
    project: str,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    layout = _project(project, projects_dir)
    manifest = layout.load_manifest()
    config = manifest.config
    manual_audio = config.audio.provider == "local_manual"
    audio_instruction = (
        "Quando a etapa de áudio ficar pendente, mostre ao usuário todo o conteúdo de "
        "`script/narration.txt` e aguarde um único arquivo de voz anexado. Salve o anexo no "
        "projeto e importe com `audio import-manual`; o alinhamento é local e não usa "
        "ElevenLabs nem `--allow-external`. "
        if manual_audio
        else ""
    )
    script_standard = (
        "`docs/shorts-script-standard.md`"
        if manifest.video_format == "short"
        else "`docs/long-form-script-standard.md`"
    )
    long_script_instruction = (
        "Para vídeo longo novo, resolva modo criativo, postura e política de hook no briefing; "
        "produza um ScriptDocument com long_form_strategy completo, valide a cadência das "
        "citações e importe o roteiro antes de construir cenas. "
        if manifest.video_format != "short"
        else ""
    )
    from pipeline_videos.autopilot import prepare_local_coordinator_job

    coordinator_job = prepare_local_coordinator_job(layout)
    prompt = f"""Retome o projeto em `{layout.root}`. Leia `AGENTS.md`,
`docs/optimized-production.md`, {script_standard} e `{layout.coordinator_job}`. Crie exatamente um
`local_video_coordinator` isolado, com `fork_turns=none`, modelo
`{coordinator_job["orchestration"]["recommended_model"]}` e raciocínio
`{coordinator_job["orchestration"]["reasoning_effort"]}`. Entregue a ele somente o caminho de
`coordinator-job.json`; esse coordenador será o único escritor do estado compartilhado. O chat pai
apenas acompanha e entrega o resultado final.

O coordenador executa o comando declarado no job e cria somente os papéis que aparecerem nas filas,
sempre com `fork_turns=none`: o planejador de cenas lê `script/shorts-queue.json`, até dois
image_workers leem exclusivamente seus arquivos em `images/jobs/`, e o especialista de metadados lê
`youtube/growth-queue.json`. Não crie `biblical_reviewer`; a validação de fontes acontece no
preflight incorporado. Após uma única
correção agregada, use o fallback determinístico declarado e continue. {audio_instruction}
{long_script_instruction}
Não releia manifestos grandes, não importe por cena, não publique e não registre aprovação humana.
Entregue o único candidato final mesmo que existam alertas consultivos; somente bloqueios técnicos,
bíblicos, de segurança ou de publicação podem impedir a conclusão."""
    console.print(Panel(prompt, title="Cole esta mensagem em uma conversa com o Codex"))


@app.command("import-script")
def import_script_command(
    project: str,
    file: Path,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    layout = _project(project)
    document = import_script(layout, file, force=force)
    console.print(
        f"[green]Roteiro v{document.version} importado:[/green] "
        f"{document.word_count} palavras, ~{document.estimated_duration_seconds:.0f}s."
    )


@script_app.command("shorts-prepare")
def shorts_script_prepare(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.shorts_script import prepare_shorts_script_queue

    layout = _project(project)
    result = prepare_shorts_script_queue(layout, force=force)
    console.print(
        f"[yellow]Fila do roteirista de Shorts preparada:[/yellow] {layout.shorts_script_queue}"
    )
    console.print(f"Job: {result['job_id']}")


@script_app.command("shorts-import")
def shorts_script_import(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.shorts_script import import_shorts_script_package

    result = import_shorts_script_package(_project(project), force=force)
    console.print(
        f"[green]Roteiro especialista importado:[/green] v{result.script.version}, "
        f"{result.script.word_count} palavras e {len(result.strategy.retention_beats)} beats."
    )


@script_app.command("shorts-check-staging")
def shorts_script_check_staging(project: str) -> None:
    """Valida o staging do roteirista sem importar nem alterar o projeto."""

    from pipeline_videos.shorts_script import check_shorts_script_package_staging

    result = check_shorts_script_package_staging(_project(project))
    console.print_json(json.dumps(result.as_dict(), ensure_ascii=False))
    if not result.valid:
        raise typer.Exit(code=1)


@script_app.command("shorts-fallback")
def shorts_script_fallback(project: str) -> None:
    """Materializa o plano visual determinístico após a única correção permitida."""

    from pipeline_videos.shorts_script import materialize_compact_visual_fallback

    result = materialize_compact_visual_fallback(_project(project))
    console.print_json(
        json.dumps(
            {
                "fallback": "compact_visual_plan_v1",
                "concepts": len(result.concepts),
                "valid": True,
            },
            ensure_ascii=False,
        )
    )


@app.command("import-review")
def import_review_command(project: str, file: Path) -> None:
    review = import_biblical_review(_project(project), file)
    console.print(
        f"[green]Revisão registrada:[/green] {len(review.findings)} apontamentos; "
        f"bloqueios abertos: {'sim' if review.has_open_blockers else 'não'}."
    )


@app.command("approve")
def approve(
    project: str,
    subject: Annotated[
        str,
        typer.Argument(help="briefing, biblical_review, script, metadata, images ou final_render"),
    ],
    by: Annotated[
        str, typer.Option("--by", help="Nome ou identificação de quem revisou.")
    ] = "user",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    layout = _project(project)
    if subject == "briefing":
        approve_briefing(layout, decided_by=by, note=note)
    elif subject == "biblical_review":
        approve_biblical_review(layout, decided_by=by, note=note)
    elif subject == "script":
        approve_script(layout, decided_by=by, note=note)
    elif subject == "metadata":
        from pipeline_videos.metadata import approve_metadata

        approve_metadata(layout, decided_by=by, note=note)
    elif subject == "images":
        from pipeline_videos.images import approve_image

        image_manifest = read_model(layout.images, ImageManifest)
        for item in image_manifest.items:
            if item.approved_version is None:
                approve_image(layout, item.scene_id, decided_by=by, note=note)
    elif subject == "final_render":
        from pipeline_videos.utils import sha256_file
        from pipeline_videos.validation import validate_project

        report = validate_project(layout, require_render=True)
        if not report.valid:
            raise typer.BadParameter(
                "O render final não passou na validação; consulte validation/report.md."
            )
        set_approval(
            layout,
            "final_render",
            ApprovalStatus.APPROVED,
            decided_by=by,
            note=note,
            artifact_hash=sha256_file(layout.final_video),
        )
    else:
        raise typer.BadParameter(f"Aprovação desconhecida: {subject}")
    console.print(f"[green]Aprovação registrada:[/green] {subject}")


@app.command("build-scenes")
def build_scenes_command(
    project: str,
    target_seconds: Annotated[float | None, typer.Option("--target-seconds")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    result = build_scenes(_project(project), target_scene_seconds=target_seconds, force=force)
    console.print(f"[green]{len(result.scenes)} cenas prontas[/green] em scenes/manifest.json.")


@images_app.command("prepare")
def images_prepare(
    project: str,
    scene: Annotated[str | None, typer.Option("--scene")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    layout = _project(project)
    if provider:
        _save_provider(layout, images=provider)
    from pipeline_videos.images import prepare_images

    result = prepare_images(layout, scene_id=scene, force=force)
    if isinstance(result, dict):
        pending = len(result.get("items", []))
        console.print(
            f"[yellow]Fila Codex criada com {pending} item(ns):[/yellow] {layout.image_queue}"
        )
        console.print("Gere uma imagem por item na conversa e use images import para cada arquivo.")
    else:
        console.print(
            f"[green]Imagens fixture geradas:[/green] {len(result.items)} itens no manifesto."
        )


@images_app.command("queue")
def images_queue(
    project: str,
    scene: Annotated[str | None, typer.Option("--scene")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.images import build_codex_queue

    layout = _project(project)
    queue = build_codex_queue(layout, scene_id=scene, force=force)
    console.print_json(json.dumps(queue, ensure_ascii=False))


@images_app.command("import")
def images_import(
    project: str,
    scene: str,
    file: Path,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.images import import_external_image

    manifest = import_external_image(_project(project), scene, file, force=force)
    item = next(entry for entry in manifest.items if entry.scene_id == scene)
    console.print(f"[green]Imagem importada:[/green] {scene}, versão {item.active_version}.")


@images_app.command("import-batch")
def images_import_batch(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.images import import_staged_images

    manifest = import_staged_images(_project(project), force=force)
    imported = sum(item.active_version is not None for item in manifest.items)
    console.print(f"[green]Importação em lote concluída:[/green] {imported} cena(s) ativas.")


@images_app.command("approve")
def images_approve(
    project: str,
    scene: str,
    version: Annotated[int | None, typer.Option("--version")] = None,
    by: Annotated[str, typer.Option("--by")] = "user",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    from pipeline_videos.images import approve_image

    approve_image(_project(project), scene, version=version, decided_by=by, note=note)
    console.print(f"[green]Imagem aprovada:[/green] {scene}.")


@images_app.command("reuse-approved")
def images_reuse_approved(
    project: str,
    by: Annotated[str, typer.Option("--by")] = "user",
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    from pipeline_videos.images import reuse_approved_images

    manifest = reuse_approved_images(_project(project), decided_by=by, note=note)
    console.print(f"[green]Imagens aprovadas reutilizadas:[/green] {len(manifest.items)} cena(s).")


@audio_app.command("configure")
def audio_configure(
    project: str,
    provider: Annotated[str, typer.Option("--provider")] = "elevenlabs",
    voice_id: Annotated[str | None, typer.Option("--voice-id")] = None,
    voice_variant: Annotated[
        str | None,
        typer.Option("--voice-variant", help="Preset jesus_1 ou jesus_2."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    output_format: Annotated[str | None, typer.Option("--output-format")] = None,
) -> None:
    if voice_id and voice_variant:
        raise typer.BadParameter("Use apenas --voice-id ou --voice-variant, não ambos.")
    resolved_variant_voice_id: str | None = None
    if voice_variant:
        try:
            variant = normalize_elevenlabs_voice_variant(voice_variant)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        resolved_variant_voice_id = elevenlabs_voice_id_for_variant(variant)
    layout = _project(project)
    _save_provider(layout, audio=provider)
    manifest = layout.load_manifest()
    before = manifest.config.audio.model_dump(mode="json")
    if voice_id:
        manifest.config.audio.voice_id = voice_id
    elif resolved_variant_voice_id:
        manifest.config.audio.voice_id = resolved_variant_voice_id
    if model:
        manifest.config.audio.model_id = model
    if output_format:
        manifest.config.audio.output_format = output_format
    layout.save_manifest(manifest)
    if manifest.config.audio.model_dump(mode="json") != before:
        invalidate(
            layout,
            stages=("audio", "captions", "metadata", "render", "validation"),
            approvals=("metadata", "final_render"),
            note="A configuração de voz foi atualizada.",
        )
    console.print("[green]Configuração de áudio atualizada.[/green]")


@audio_app.command("generate")
def audio_generate(
    project: str,
    scene: Annotated[str | None, typer.Option("--scene")] = None,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    allow_external: Annotated[
        bool,
        typer.Option(
            "--allow-external",
            help="Confirma consumo de créditos quando o provider for ElevenLabs.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    layout = _project(project)
    if provider:
        _save_provider(layout, audio=provider)
    from pipeline_videos.audio import generate_audio

    result = generate_audio(
        layout,
        scene_id=scene,
        force=force,
        allow_external=allow_external,
    )
    console.print(
        f"[green]Áudio pronto:[/green] {result.duration:.2f}s, {len(result.words)} palavras, "
        f"provider {result.provider}."
    )


@audio_app.command("prepare-manual")
def audio_prepare_manual(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.manual_audio import prepare_manual_audio

    layout = _project(project)
    result = prepare_manual_audio(layout, force=force)
    console.print(f"[yellow]Áudio manual pendente:[/yellow] {layout.manual_audio_queue}")
    console.print(f"Roteiro integral: {layout.root / result['transcript_path']}")


@audio_app.command("import-manual")
def audio_import_manual(project: str, source: Path) -> None:
    from pipeline_videos.manual_audio import import_manual_audio

    result = import_manual_audio(_project(project), source)
    console.print(
        f"[green]Áudio manual importado:[/green] {result.duration:.2f}s, "
        f"{len(result.words)} palavras, alinhamento local."
    )


@music_app.command("library")
def music_library(
    library: Annotated[Path | None, typer.Option("--library")] = None,
) -> None:
    from pipeline_videos.music import DEFAULT_MUSIC_LIBRARY, list_music_tracks

    directory = (library or DEFAULT_MUSIC_LIBRARY).expanduser().resolve()
    tracks = list_music_tracks(directory)
    console.print(f"[bold]Biblioteca de músicas:[/bold] {directory}")
    if not tracks:
        console.print("[yellow]Nenhuma faixa compatível encontrada.[/yellow]")
        return
    for track in tracks:
        console.print(f"- {track.name}")


@music_app.command("select")
def music_select(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
    library: Annotated[Path | None, typer.Option("--library")] = None,
) -> None:
    from pipeline_videos.music import select_music

    result = select_music(_project(project), library_dir=library, force=force)
    if result.path:
        console.print(f"[green]Música selecionada:[/green] {result.source_name}")
        console.print(f"Cópia do projeto: {result.path}")
    else:
        console.print("[yellow]Música desativada neste projeto.[/yellow]")


@music_app.command("import")
def music_import(project: str, source: Path) -> None:
    """Importa diretamente uma faixa específica para o projeto."""

    from pipeline_videos.music import import_music

    result = import_music(_project(project), source)
    console.print(f"[green]Música importada:[/green] {result.source_name}")
    console.print(f"Cópia do projeto: {result.path}")


@captions_app.command("build")
def captions_build(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.captions import generate_captions

    result = generate_captions(_project(project), force=force)
    console.print(
        f"[green]Legendas prontas:[/green] {result.word_count} palavras em "
        f"{len(result.blocks)} blocos."
    )


@metadata_app.command("build")
def metadata_build(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.metadata import build_metadata

    result = build_metadata(_project(project), force=force)
    console.print(f"[green]Rascunho de metadados pronto:[/green] {result.title}")


@metadata_app.command("growth-prepare")
def metadata_growth_prepare(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.growth import prepare_growth_queue

    layout = _project(project)
    result = prepare_growth_queue(layout, force=force)
    console.print(f"[yellow]Fila do especialista preparada:[/yellow] {layout.growth_queue}")
    console.print(f"Job: {result['job_id']}")


@metadata_app.command("growth-import")
def metadata_growth_import(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.growth import import_growth_package

    result = import_growth_package(_project(project), force=force)
    console.print(f"[green]Pacote do especialista importado:[/green] {result.metadata.title}")


@metadata_app.command("growth-check-staging")
def metadata_growth_check_staging(project: str) -> None:
    """Valida de uma vez a estrutura e relata SEO somente como aviso no modo compacto."""

    from pipeline_videos.growth import check_growth_package_staging

    result = check_growth_package_staging(_project(project))
    console.print_json(json.dumps(result.as_dict(), ensure_ascii=False))
    if not result.valid:
        raise typer.Exit(code=1)


@metadata_app.command("growth-fallback")
def metadata_growth_fallback(project: str) -> None:
    """Materializa metadados seguros após a única correção do especialista."""

    from pipeline_videos.growth import materialize_compact_growth_fallback

    result = materialize_compact_growth_fallback(_project(project))
    console.print_json(
        json.dumps(
            {"fallback": "compact_metadata_draft_v1", "title": result.title, "valid": True},
            ensure_ascii=False,
        )
    )


@youtube_app.command("auth")
def youtube_auth(
    client_secrets: Annotated[
        Path | None,
        typer.Option("--client-secrets", help="JSON OAuth Desktop baixado do Google Cloud."),
    ] = None,
    token: Annotated[Path | None, typer.Option("--token-file")] = None,
    allow_external: Annotated[bool, typer.Option("--allow-external")] = False,
) -> None:
    """Autoriza o canal no navegador e guarda o token fora dos projetos."""
    if not allow_external:
        raise typer.BadParameter("Use --allow-external para iniciar o OAuth no navegador.")
    configured = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE")
    source = client_secrets or (Path(configured) if configured else None)
    if source is None:
        raise typer.BadParameter("Informe --client-secrets ou defina YOUTUBE_CLIENT_SECRETS_FILE.")
    from pipeline_videos.publication import authorize

    destination = authorize(source, destination=token)
    console.print(f"[green]Canal autorizado.[/green] Token local: {destination}")


@youtube_app.command("preflight")
def youtube_preflight(
    project: str,
    privacy: Annotated[str | None, typer.Option("--privacy")] = None,
    category_id: Annotated[str | None, typer.Option("--category-id")] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    made_for_kids: Annotated[
        bool | None, typer.Option("--made-for-kids/--not-made-for-kids")
    ] = None,
    synthetic_media: Annotated[
        bool | None, typer.Option("--synthetic-media/--no-synthetic-media")
    ] = None,
    captions: Annotated[bool | None, typer.Option("--captions/--no-captions")] = None,
    notify_subscribers: Annotated[
        bool | None, typer.Option("--notify-subscribers/--no-notify-subscribers")
    ] = None,
    automated_review: Annotated[bool, typer.Option("--automated-review")] = False,
) -> None:
    """Valida o pacote que seria enviado, sem acessar o YouTube."""
    from pipeline_videos.publication import prepare_publication

    plan = prepare_publication(
        _project(project),
        privacy_status=privacy,
        category_id=category_id,
        default_language=language,
        made_for_kids=made_for_kids,
        contains_synthetic_media=synthetic_media,
        upload_captions=captions,
        notify_subscribers=notify_subscribers,
        automated_review=automated_review,
    )
    table = Table(title="Preflight de upload do YouTube")
    table.add_column("Campo")
    table.add_column("Valor")
    rows = (
        ("Projeto", plan.project_id),
        ("Título", plan.title),
        ("Visibilidade inicial", plan.privacy_status),
        ("Canal infantil", "sim" if plan.made_for_kids else "não"),
        ("Mídia sintética", "sim" if plan.contains_synthetic_media else "não"),
        ("Notificar inscritos", "sim" if plan.notify_subscribers else "não"),
        ("Vídeo", str(plan.video_path)),
        ("Legenda", str(plan.captions_path) if plan.captions_path else "não enviar"),
        ("Hash do pacote", plan.input_hash()),
    )
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


@youtube_app.command("upload")
def youtube_upload(
    project: str,
    confirm_upload: Annotated[
        str, typer.Option("--confirm-upload", help="Deve ser exatamente o ID do projeto.")
    ],
    privacy: Annotated[str | None, typer.Option("--privacy")] = None,
    category_id: Annotated[str | None, typer.Option("--category-id")] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    made_for_kids: Annotated[
        bool | None, typer.Option("--made-for-kids/--not-made-for-kids")
    ] = None,
    synthetic_media: Annotated[
        bool | None, typer.Option("--synthetic-media/--no-synthetic-media")
    ] = None,
    captions: Annotated[bool | None, typer.Option("--captions/--no-captions")] = None,
    notify_subscribers: Annotated[
        bool | None, typer.Option("--notify-subscribers/--no-notify-subscribers")
    ] = None,
    force_new_upload: Annotated[bool, typer.Option("--force-new-upload")] = False,
    token: Annotated[Path | None, typer.Option("--token-file")] = None,
    allow_external: Annotated[bool, typer.Option("--allow-external")] = False,
    automated_review: Annotated[bool, typer.Option("--automated-review")] = False,
) -> None:
    """Envia o pacote aprovado como privado ou não listado."""
    if not allow_external:
        raise typer.BadParameter("Use --allow-external após revisar `youtube preflight`.")
    from pipeline_videos.publication import authenticated_client, upload_project

    last_percent = -1

    def show_progress(value: float) -> None:
        nonlocal last_percent
        percent = int(value * 100)
        if percent >= last_percent + 5:
            last_percent = percent
            console.print(f"Upload: {percent}%")

    record = upload_project(
        _project(project),
        client=authenticated_client(source=token),
        allow_external=True,
        confirmation=confirm_upload,
        force_new_upload=force_new_upload,
        progress=show_progress,
        privacy_status=privacy,
        category_id=category_id,
        default_language=language,
        made_for_kids=made_for_kids,
        contains_synthetic_media=synthetic_media,
        upload_captions=captions,
        notify_subscribers=notify_subscribers,
        automated_review=automated_review,
    )
    console.print(
        f"[green]Upload concluído como {record.privacy_status}:[/green] {record.youtube_url}"
    )


@youtube_app.command("status")
def youtube_status(
    project: str,
    remote: Annotated[bool, typer.Option("--remote")] = False,
    token: Annotated[Path | None, typer.Option("--token-file")] = None,
) -> None:
    """Exibe o registro local e, opcionalmente, consulta o estado remoto."""
    from pipeline_videos.publication import authenticated_client, publication_status

    client = authenticated_client(source=token) if remote else None
    record, remote_video = publication_status(_project(project), client=client)
    console.print(
        Panel(
            f"{record.title}\n{record.youtube_url}\nvisibilidade: {record.privacy_status}",
            title=record.video_id,
        )
    )
    if remote_video:
        console.print_json(json.dumps(remote_video, ensure_ascii=False))


@youtube_app.command("publish")
def youtube_publish(
    project: str,
    confirm_publication: Annotated[
        str,
        typer.Option("--confirm-publication", help="Deve ser exatamente o ID do vídeo."),
    ],
    publish_at: Annotated[
        str | None,
        typer.Option("--publish-at", help="ISO 8601 com fuso; omita para publicar agora."),
    ] = None,
    token: Annotated[Path | None, typer.Option("--token-file")] = None,
    allow_external: Annotated[bool, typer.Option("--allow-external")] = False,
) -> None:
    """Publica imediatamente ou agenda um vídeo já enviado e revisado."""
    if not allow_external:
        raise typer.BadParameter("Use --allow-external para publicar ou agendar.")
    from pipeline_videos.publication import authenticated_client, publish_project

    record = publish_project(
        _project(project),
        client=authenticated_client(source=token),
        allow_external=True,
        confirmation=confirm_publication,
        publish_at=publish_at,
    )
    if record.publish_at:
        console.print(f"[green]Publicação agendada:[/green] {record.publish_at}")
    else:
        console.print(f"[green]Vídeo publicado:[/green] {record.youtube_url}")


@app.command("render")
def render_command(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    layout = _project(project)
    from pipeline_videos.music import select_music
    from pipeline_videos.render import render_review_candidate

    validate_draft_preflight(layout)
    select_music(layout)
    result = render_review_candidate(layout, force=force)
    console.print(f"[green]Candidato final V4:[/green] {result.final_video}")


@app.command("render-review")
def render_review_command(
    project: str,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Renderiza um candidato final privado sem registrar aprovação humana."""
    from pipeline_videos.music import select_music
    from pipeline_videos.render import render_review_candidate
    from pipeline_videos.review import validate_draft_preflight

    layout = _project(project)
    validate_draft_preflight(layout)
    select_music(layout)
    result = render_review_candidate(layout, force=force)
    console.print(f"[green]Candidato privado:[/green] {result.final_video}")


@app.command("validate")
def validate_command(
    project: str,
    without_render: Annotated[bool, typer.Option("--without-render")] = False,
) -> None:
    from pipeline_videos.validation import validate_project

    report = validate_project(_project(project), require_render=not without_render)
    color = "green" if report.valid else "red"
    console.print(
        f"[{color}]Validação {'aprovada' if report.valid else 'reprovada'}:[/{color}] "
        f"{len(report.errors)} erro(s), {len(report.warnings)} alerta(s)."
    )
    if not report.valid:
        raise typer.Exit(1)


@app.command("run")
def run_pipeline(
    project: str,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    allow_external: Annotated[bool, typer.Option("--allow-external")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    layout = _project(project)
    manifest = layout.load_manifest()
    main_agent_script_mode = not manifest.config.shorts_script.use_specialist_subagent
    if dry_run:
        console.print("[bold]Plano de execução sem mutações[/bold]")
        for row in stage_summary(layout):
            console.print(f"- {row['stage']}: {row['status']}")
        console.print(f"- imagens: provider {manifest.config.images.provider}")
        console.print(f"- áudio: provider {manifest.config.audio.provider}")
        from pipeline_videos.music import DEFAULT_MUSIC_LIBRARY, list_music_tracks

        console.print(
            f"- música: {'ativada' if manifest.config.music.enabled else 'desativada'}; "
            f"{len(list_music_tracks())} faixa(s) em {DEFAULT_MUSIC_LIBRARY}"
        )
        _print_orchestration_plan(layout, allow_external=allow_external)
        return

    if (
        manifest.video_format == "short"
        and manifest.config.shorts_script.enabled
        and (manifest.config.shorts_script.use_specialist_subagent or main_agent_script_mode)
        and manifest.stages["shorts_script"].status != StageStatus.COMPLETED
    ):
        from pipeline_videos.shorts_script import prepare_shorts_script_queue

        queue = prepare_shorts_script_queue(layout, force=force)
        queue_owner = (
            "roteiro do agente principal" if main_agent_script_mode else "roteirista de Shorts"
        )
        console.print(
            f"[yellow]Pipeline pausado no {queue_owner}:[/yellow] {layout.shorts_script_queue}"
        )
        console.print(f"Job: {queue['job_id']}")
        _print_orchestration_plan(
            layout,
            allow_external=allow_external,
            title="Plano de retomada após a fila de roteiro",
        )
        return
    validate_draft_preflight(layout)
    build_scenes(layout, force=force, skip_approvals=True)
    growth_queue = None
    if not manifest.demo and manifest.config.youtube_growth.enabled:
        from pipeline_videos.growth import prepare_growth_queue

        growth_queue = prepare_growth_queue(layout, force=force)
    from pipeline_videos.images import prepare_images

    image_result = prepare_images(layout, force=force)
    if isinstance(image_result, dict) and image_result.get("items"):
        console.print(f"[yellow]Pipeline pausado na fila de imagens:[/yellow] {layout.image_queue}")
        if growth_queue and growth_queue.get("status") == "waiting_external":
            console.print(
                f"[yellow]Fila paralela do especialista YouTube:[/yellow] {layout.growth_queue}"
            )
        _print_orchestration_plan(
            layout,
            allow_external=allow_external,
            title="Plano concorrente e ponto de join",
        )
        return
    from pipeline_videos.audio import generate_audio
    from pipeline_videos.captions import generate_captions
    from pipeline_videos.metadata import build_metadata
    from pipeline_videos.music import select_music
    from pipeline_videos.render import render_review_candidate
    from pipeline_videos.validation import validate_project

    current = layout.load_manifest()
    if current.config.audio.provider == "local_manual":
        from pipeline_videos.manual_audio import prepare_manual_audio

        prepare_manual_audio(layout, force=force)
        current = layout.load_manifest()
        if current.stages["audio"].status != StageStatus.COMPLETED:
            console.print(
                f"[yellow]Pipeline aguardando a narração do usuário:[/yellow] {layout.narration}"
            )
            console.print(
                "Depois de anexar o arquivo, importe com `audio import-manual <projeto> <arquivo>`."
            )
            _print_orchestration_plan(
                layout,
                allow_external=allow_external,
                title="Plano de retomada após o áudio manual",
            )
            return
    else:
        generate_audio(layout, force=force, allow_external=allow_external)
    select_music(layout, force=force)
    generate_captions(layout, force=force)
    if not manifest.demo and manifest.config.youtube_growth.enabled:
        current = layout.load_manifest()
        if current.stages["youtube_growth"].status != StageStatus.COMPLETED:
            console.print(
                f"[yellow]Pipeline pausado no especialista YouTube:[/yellow] {layout.growth_queue}"
            )
            _print_orchestration_plan(
                layout,
                allow_external=allow_external,
                title="Plano de retomada após a fila de crescimento",
            )
            return
        if current.stages["metadata"].status != StageStatus.COMPLETED:
            from pipeline_videos.growth import refresh_growth_metadata

            refresh_growth_metadata(layout)
    else:
        build_metadata(layout, force=force)
    render_result = render_review_candidate(layout, force=force)
    report = validate_project(layout, require_render=True)
    console.print(f"[green]Candidato final V4:[/green] {render_result.final_video}")
    console.print(
        f"[green]Pipeline concluído.[/green] Validação: "
        f"{'OK' if report.valid else 'com erros em validation/report.md'}."
    )


@app.command("autopilot")
def autopilot_command(
    project: str,
    allow_external: Annotated[bool, typer.Option("--allow-external")] = False,
    wait_external: Annotated[bool, typer.Option("--wait-external")] = False,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds", min=30, max=14_400)] = 3_600,
    poll_seconds: Annotated[int, typer.Option("--poll-seconds", min=1, max=60)] = 5,
    force: Annotated[bool, typer.Option("--force")] = False,
    upload_private: Annotated[bool, typer.Option("--upload-private")] = False,
    confirm_upload: Annotated[str | None, typer.Option("--confirm-upload")] = None,
    token: Annotated[Path | None, typer.Option("--token-file")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Executa etapas determinísticas e retoma automaticamente após os workers."""
    from pipeline_videos.autopilot import run_autopilot_once, wait_until_external_ready

    layout = _project(project)
    manifest = layout.load_manifest()
    if upload_private:
        if not allow_external:
            raise typer.BadParameter("--upload-private exige --allow-external.")
        if confirm_upload != manifest.project_id:
            raise typer.BadParameter(
                "--confirm-upload precisa ser exatamente o ID do projeto para upload privado."
            )
    result = run_autopilot_once(
        layout,
        allow_external=allow_external,
        force=force,
    )
    if result.status == "waiting_external":
        if not json_output:
            console.print("[yellow]Autopilot aguardando saídas externas.[/yellow]")
            for wait in result.waits:
                console.print_json(json.dumps(wait, ensure_ascii=False))
        if not wait_external:
            if json_output:
                console.print_json(
                    json.dumps(
                        {
                            "status": result.status,
                            "waits": result.waits,
                            "state": str(layout.autopilot_state),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                console.print(
                    f"Estado compacto: {layout.autopilot_state}. "
                    "Inicie os workers declarados e execute autopilot novamente."
                )
            return
        if not json_output:
            console.print(
                f"Aguardando por até {timeout_seconds} segundos; o processo continuará sozinho."
            )
        wait_until_external_ready(
            layout,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        result = run_autopilot_once(
            layout,
            allow_external=allow_external,
            force=False,
        )
    if result.status != "completed":
        raise RuntimeError("O autopilot ainda possui dependências externas pendentes.")
    publication_url: str | None = None
    if upload_private:
        if result.validation_ok is not True:
            raise RuntimeError("Upload privado bloqueado porque a validação final não está OK.")
        from pipeline_videos.publication import authenticated_client, upload_project

        record = upload_project(
            layout,
            client=authenticated_client(source=token),
            allow_external=True,
            confirmation=manifest.project_id,
            privacy_status="private",
            automated_review=True,
        )
        publication_url = record.youtube_url
    from pipeline_videos.token_audit import write_local_token_audit

    token_metrics = write_local_token_audit(layout)
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "status": result.status,
                    "render_path": str(result.render_path) if result.render_path else None,
                    "validation_ok": result.validation_ok,
                    "publication_url": publication_url,
                    "privacy": "private" if publication_url else None,
                    "token_usage": token_metrics["codex"]["usage"],
                    "token_metrics": str(layout.root / "audit" / "production-metrics.json"),
                },
                ensure_ascii=False,
            )
        )
    else:
        console.print(f"[green]Autopilot concluído:[/green] {result.render_path}")
        console.print(
            f"Validação: {'OK' if result.validation_ok else 'com erros em validation/report.md'}."
        )
        if publication_url:
            console.print(f"[green]Upload privado concluído:[/green] {publication_url}")
        console.print(f"Auditoria de tokens: {layout.root / 'audit' / 'production-metrics.json'}")


@app.command("demo")
def demo_command(
    force: Annotated[bool, typer.Option("--force")] = False,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    from pipeline_videos.demo import run_demo

    layout, result = run_demo(projects_dir=projects_dir, force=force)
    report = result["validation"]
    console.print(f"[green]Demonstração pronta:[/green] {layout.final_video}")
    console.print(f"Validação: {'OK' if report.valid else 'REPROVADA'}")


@app.command("set-palette")
def set_palette(project: str, palette_id: str) -> None:
    layout = _project(project)
    palette = load_palette(palette_id)
    if layout.palette.exists() and read_model(layout.palette, Palette) == palette:
        console.print(f"[green]Paleta já estava ativa:[/green] {palette.name}")
        return
    write_json(layout.palette, palette)
    invalidate(
        layout,
        stages=(
            "scenes",
            "image_manifest",
            "images",
            "youtube_growth",
            "metadata",
            "render",
            "validation",
        ),
        approvals=("images", "metadata", "final_render"),
        note=f"Paleta alterada para {palette_id}.",
    )
    console.print(f"[green]Paleta atualizada:[/green] {palette.name}")


if __name__ == "__main__":
    app()
