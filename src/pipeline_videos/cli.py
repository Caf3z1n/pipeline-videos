from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipeline_videos.models import ApprovalStatus, ImageManifest, Palette, StageStatus, StyleProfile
from pipeline_videos.project import (
    DEFAULT_PALETTE,
    DEFAULT_STYLE,
    create_project,
    load_palette,
    load_style,
    next_actions,
)
from pipeline_videos.review import (
    approve_biblical_review,
    approve_briefing,
    approve_script,
    import_biblical_review,
    validate_draft_preflight,
)
from pipeline_videos.scenes import build_scenes
from pipeline_videos.script import import_script
from pipeline_videos.state import invalidate, require_approval, set_approval, stage_summary
from pipeline_videos.storage import (
    ProjectLayout,
    read_model,
    resolve_project,
    write_json,
)
from pipeline_videos.verse_selection import create_auto_short

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
metadata_app = typer.Typer(help="Rascunhos de metadados e thumbnail.")
youtube_app = typer.Typer(help="OAuth, upload, status e publicação segura no YouTube.")
remote_app = typer.Typer(help="Fila remota de produção, revisão privada e agendamento.")
app.add_typer(images_app, name="images")
app.add_typer(script_app, name="script")
app.add_typer(audio_app, name="audio")
app.add_typer(music_app, name="music")
app.add_typer(captions_app, name="captions")
app.add_typer(metadata_app, name="metadata")
app.add_typer(youtube_app, name="youtube")
app.add_typer(remote_app, name="remote")


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


@remote_app.command("cancel")
def remote_cancel(
    job_id: str,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
) -> None:
    """Cancela um job ainda não iniciado ou aguardando agendamento."""
    from pipeline_videos.remote_jobs import RemoteJobStore, job_summary

    job = RemoteJobStore(jobs_dir).cancel(job_id)
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
    table.add_row(
        "ElevenLabs voice",
        "CONFIGURADA" if os.getenv("ELEVENLABS_VOICE_ID") else "PENDENTE",
        os.getenv("ELEVENLABS_VOICE_ID", "defina localmente antes do TTS real"),
    )
    table.add_row(
        "Imagem Codex",
        "VIA CONVERSA",
        "a ferramenta embutida não é um endpoint invocável pelo CLI",
    )
    from pipeline_videos.publication import token_file

    youtube_token = token_file()
    table.add_row(
        "YouTube OAuth",
        "CONFIGURADO" if youtube_token.is_file() else "PENDENTE",
        str(youtube_token),
    )
    console.print(table)


@app.command("new")
def new_project(
    project_id: Annotated[str, typer.Argument(help="Identificador curto do vídeo.")],
    title: Annotated[str, typer.Option("--title", "-t", help="Título editorial inicial.")],
    duration: Annotated[int, typer.Option("--duration", help="Duração-alvo em segundos.")] = 540,
    style: Annotated[str, typer.Option("--style")] = DEFAULT_STYLE,
    palette: Annotated[str, typer.Option("--palette")] = DEFAULT_PALETTE,
    video_format: Annotated[
        str, typer.Option("--format", help="Formato do vídeo: landscape ou short.")
    ] = "landscape",
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    if video_format not in {"landscape", "short"}:
        raise typer.BadParameter("Use landscape ou short em --format.")
    layout = create_project(
        project_id,
        title,
        projects_dir=projects_dir,
        target_duration_seconds=duration,
        style_id=style,
        palette_id=palette,
        video_format=video_format,
    )
    console.print(f"[green]Projeto criado:[/green] {layout.root}")
    console.print(
        "Próximo passo: execute o comando resume-prompt e use o texto na conversa com o Codex."
    )


@app.command("new-short")
def new_short(
    duration: Annotated[
        int, typer.Option("--duration", help="Duração-alvo em segundos.", min=30, max=180)
    ] = 50,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short com versículo inédito, briefing e fonte verificada."""
    layout, candidate, score = create_auto_short(
        projects_dir=projects_dir,
        target_duration_seconds=duration,
    )
    console.print(f"[green]Short criado:[/green] {layout.root}")
    console.print(f"[bold]Versículo reservado:[/bold] {candidate.reference}")
    console.print(candidate.verse_text, markup=False)
    console.print(f"Seleção editorial: {candidate.title} (pontuação {score:g})")
    console.print(
        "Briefing e fonte foram preenchidos automaticamente; a aprovação humana continua pendente."
    )
    console.print(f'Próximo passo: uv run pipeline-videos run "{layout.root}" --dry-run')


@app.command("new-short-audio-manual")
def new_short_audio_manual(
    duration: Annotated[
        int, typer.Option("--duration", help="Duração-alvo em segundos.", min=30, max=180)
    ] = 50,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short que aguarda a narração anexada e a alinha localmente."""
    layout, candidate, score = create_auto_short(
        projects_dir=projects_dir,
        target_duration_seconds=duration,
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


def _new_light_short(*, automatic: bool, duration: int, projects_dir: Path | None) -> None:
    mode = "light_automatic" if automatic else "light_manual"
    layout, candidate, score = create_auto_short(
        projects_dir=projects_dir,
        target_duration_seconds=duration,
        production_mode=mode,
    )
    label = "light-automática" if automatic else "light-manual"
    console.print(f"[green]Short {label} criado:[/green] {layout.root}")
    console.print(f"[bold]Versículo reservado:[/bold] {candidate.reference}")
    console.print(candidate.verse_text, markup=False)
    console.print(f"Seleção editorial: {candidate.title} (pontuação {score:g})")
    if automatic:
        console.print(
            "O Codex fará o roteiro leve, gerará as imagens e concluirá a edição; "
            "a aprovação final continua sendo do usuário."
        )
    else:
        console.print(
            "O fluxo produzirá prompts em partes de até 10 imagens e aguardará os arquivos "
            "gerados fora do Codex."
        )
    console.print(f'Próximo passo: uv run pipeline-videos run "{layout.root}" --dry-run')


@app.command("new-short-light-manual")
def new_short_light_manual(
    duration: Annotated[
        int, typer.Option("--duration", help="Duração-alvo em segundos.", min=30, max=180)
    ] = 50,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short Light que aguarda imagens geradas externamente."""
    _new_light_short(automatic=False, duration=duration, projects_dir=projects_dir)


@app.command("new-short-light-automatica")
def new_short_light_automatic(
    duration: Annotated[
        int, typer.Option("--duration", help="Duração-alvo em segundos.", min=30, max=180)
    ] = 50,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    """Cria um Short Light concluído com imagens geradas pelo próprio Codex."""
    _new_light_short(automatic=True, duration=duration, projects_dir=projects_dir)


@app.command("status")
def status(
    project: str,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    layout = _project(project, projects_dir)
    manifest = layout.load_manifest()
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


@app.command("resume-prompt")
def resume_prompt(
    project: str,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    layout = _project(project, projects_dir)
    config = layout.load_manifest().config
    production = config.production
    manual_audio = config.audio.provider == "local_manual"
    if production.optimization_profile == "token_optimized_v1":
        audio_instruction = (
            "Quando a etapa de áudio ficar pendente, mostre ao usuário todo o conteúdo de "
            "`script/narration.txt` e aguarde um único arquivo de voz anexado. Salve o anexo no "
            "projeto e importe com `audio import-manual`; o alinhamento é local e não usa "
            "ElevenLabs nem `--allow-external`. "
            if manual_audio
            else ""
        )
        prompt = f"""Retome o projeto em `{layout.root}`. Leia `AGENTS.md`,
`docs/optimized-production.md` e depois `project.json`. Execute
`uv run pipeline-videos status "{project}"` e retome com
`uv run pipeline-videos autopilot "{project}"`
{"sem --allow-external" if manual_audio else "com --allow-external"}. {audio_instruction}
Quando houver filas externas,
crie apenas os papéis declarados, sempre com `fork_turns=none`: cada image_worker recebe somente
seu `images/jobs/image-worker-N.json`, e o youtube_growth_specialist recebe somente
`youtube/growth-queue.json`. Para Short completo, prepare antes o shorts_script_specialist usando
somente `script/shorts-queue.json`. Não releia manifestos grandes, não importe por cena e não altere
estado fora do agente principal. Depois de iniciar os workers, execute autopilot com
`--wait-external` para importar, renderizar e validar automaticamente. Não publique nem registre
aprovação humana."""
        console.print(Panel(prompt, title="Cole esta mensagem em uma conversa com o Codex"))
        return
    if production.mode == "light_manual":
        prompt = f"""Retome o projeto Short light-manual em `{layout.root}`. Leia `project.json`,
execute `uv run pipeline-videos status "{project}"` e consulte
`uv run pipeline-videos run "{project}" --dry-run`. Produza diretamente, com raciocínio baixo e
sem subagente roteirista, o pacote declarado em `script/shorts-queue.json`; valide e importe uma
única vez com `script shorts-import`. Construa as cenas e prepare `images/manual-prompts.md`, com a
quantidade dinâmica de imagens necessária e partes de no máximo 10, repetindo o padrão 3D
completo em cada imagem. Mostre os prompts ao usuário e pare para ele gerar e anexar as imagens.
Quando os anexos chegarem, salve-os e importe todos de uma vez, na ordem global declarada, com
`images import-manual`. Em paralelo, crie exatamente um `youtube_growth_specialist` com raciocínio
baixo para a fila `youtube/growth-queue.json`; ele escreve somente em staging e o agente principal
importa uma vez com `metadata growth-import`. Depois retome `run` até a prévia final. A capa deve
reutilizar a imagem da primeira cena e ser incorporada ao frame zero. Não publique nem aprove pelo
usuário."""
    elif production.mode == "light_automatic":
        prompt = f"""Retome o projeto Short light-automática em `{layout.root}`. Leia
`project.json`, execute `uv run pipeline-videos status "{project}"` e consulte
`uv run pipeline-videos run "{project}" --dry-run`. Produza diretamente, com raciocínio baixo e
sem subagente roteirista, o pacote declarado em `script/shorts-queue.json`; valide e importe uma
única vez com `script shorts-import`. Depois construa as cenas e gere uma imagem por job de
`images/codex-queue.json` usando o imagegen embutido. Quando as filas estiverem pendentes, inicie
até dois `image_worker` e exatamente um `youtube_growth_specialist` com raciocínio baixo. O
especialista escreve somente em staging; o agente principal importa o lote de imagens e o pacote
de crescimento
uma única vez e conclui áudio, música, legendas, metadados, capa e render. A capa deve reutilizar a
imagem da primeira cena e ser incorporada ao frame zero. Não publique nem aprove pelo usuário."""
    elif production.mode == "draft_one_shot":
        short_instructions = ""
        if layout.load_manifest().video_format == "short":
            short_instructions = """Antes das outras lanes, prepare `script/shorts-queue.json` e
crie exatamente um `shorts_script_specialist`. Ele deve usar o versículo e a fonte já reservados,
abrir literalmente com o versículo, escrever em português falado natural, escolher a ponte bíblica
mais adequada, usar somente um dos cinco convites comunitários para comentar e terminar com o CTA
fixo configurado. O especialista escreve somente no staging; valide e importe uma única vez com
`script shorts-import`. """
        prompt = f"""Retome o projeto de vídeo em `{layout.root}` no modo draft one-shot. Leia
`project.json` e os manifestos existentes, execute `uv run pipeline-videos status "{project}"` e
continue do primeiro estágio incompleto sem regenerar artefatos atuais. Use até
três subagentes no total e consulte
`uv run pipeline-videos run "{project}" --dry-run` para seguir as cinco ondas.
{short_instructions}Quando as duas filas estiverem pendentes, inicie dois
`image_worker` e um
`youtube_growth_specialist` antes de esperar; sem a fila de crescimento, use até
{production.image_workers} trabalhadores de imagem. `worker_id` é a afinidade inicial: entregue o
próximo job ordenado ao primeiro trabalhador livre. Eles salvam somente em staging_path; apenas o
agente principal pode importar em lote ou modificar JSONs compartilhados. Enquanto as lanes externas
trabalham, o principal executa áudio, música e legendas. Não faça revisão artística
intermediária nem peça aprovações por cena. Quando `youtube/growth-queue.json` estiver pendente,
use também um subagente `youtube_growth_specialist`; ele escreve somente no staging indicado e o
agente principal importa o pacote. Gere áudio, legendas, metadados e a prévia completa,
mantendo verificações técnicas e bloqueios bíblicos. Pare somente diante de bloqueio real ou antes
de consumir um serviço externo sem autorização. Não publique e não aprove o resultado pelo
usuário."""
    else:
        prompt = f"""Abra o projeto de vídeo em `{layout.root}`. Leia primeiro `project.json`,
depois briefing, fontes, decisões e manifestos existentes. Retome sem regenerar nada e preserve as
aprovações por etapa. Não faça chamadas pagas nem publique sem aprovação explícita."""
    console.print(Panel(prompt, title="Cole esta mensagem em uma conversa com o Codex"))


@app.command("production-mode")
def production_mode(
    project: str,
    mode: Annotated[
        str,
        typer.Argument(
            help="draft_one_shot, guided, light_manual, light_automatic ou remote_review"
        ),
    ],
    image_workers: Annotated[int, typer.Option("--image-workers", min=1, max=8)] = 3,
) -> None:
    mode = mode.replace("-", "_")
    if mode not in {
        "draft_one_shot",
        "guided",
        "light_manual",
        "light_automatic",
        "remote_review",
    }:
        raise typer.BadParameter(
            "Use draft_one_shot, guided, light_manual, light_automatic ou remote_review."
        )
    layout = _project(project)
    manifest = layout.load_manifest()
    previous_mode = manifest.config.production.mode
    if mode in {"light_manual", "light_automatic"} and manifest.video_format != "short":
        raise typer.BadParameter("Os modos Light estão disponíveis somente para Shorts.")
    manifest.config.production.mode = mode  # type: ignore[assignment]
    light_mode = mode in {"light_manual", "light_automatic"}
    manifest.config.production.use_subagents = mode in {
        "draft_one_shot",
        "light_manual",
        "light_automatic",
    }
    manifest.config.production.image_workers = 1 if mode == "light_manual" else image_workers
    manifest.config.production.approval_policy = (  # type: ignore[assignment]
        "final_only" if mode != "guided" else "stage_gates"
    )
    manifest.config.shorts_script.use_specialist_subagent = not light_mode
    manifest.config.youtube_growth.enabled = True
    manifest.config.youtube_growth.use_specialist_subagent = True
    if mode == "remote_review":
        manifest.config.production.use_subagents = False
        manifest.config.production.image_workers = 1
        manifest.config.production.optimization_profile = "remote_single_variant_v1"
        manifest.config.production.worker_context_policy = "compact_only"
        manifest.config.shorts_script.use_specialist_subagent = False
        manifest.config.youtube_growth.title_variants = 1
        manifest.config.youtube_growth.thumbnail_variants = 1
        manifest.config.youtube_publication.allow_automated_private_review_upload = True
        manifest.config.youtube_publication.allow_automated_scheduled_release = True
    if light_mode:
        manifest.config.render.final_crf = 16
        manifest.config.render.preview_crf = 16
        manifest.config.render.final_preset = "fast"
        manifest.config.render.preview_preset = "fast"
        manifest.config.render.motion_supersample = 2
        if manifest.stages["youtube_growth"].status == StageStatus.SKIPPED:
            manifest.stages["youtube_growth"] = manifest.stages["youtube_growth"].model_copy(
                update={"status": StageStatus.PENDING, "note": None}
            )
    elif previous_mode in {"light_manual", "light_automatic"}:
        manifest.config.render.final_crf = 12
        manifest.config.render.preview_crf = 12
        manifest.config.render.final_preset = "slow"
        manifest.config.render.preview_preset = "slow"
        manifest.config.render.motion_supersample = 3
        manifest.stages["youtube_growth"] = manifest.stages["youtube_growth"].model_copy(
            update={"status": StageStatus.PENDING, "note": None}
        )
    layout.save_manifest(manifest)
    layout.append_decision(
        {
            "type": "production_mode",
            "mode": mode,
            "use_subagents": manifest.config.production.use_subagents,
            "image_workers": manifest.config.production.image_workers,
        }
    )
    console.print(f"[green]Modo de produção atualizado:[/green] {mode}.")


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
    target_seconds: Annotated[float, typer.Option("--target-seconds")] = 8.0,
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
        if result.get("provider") == "chatgpt-web-manual":
            console.print(
                f"[yellow]Prompts manuais criados para {pending} imagem(ns):[/yellow] "
                f"{layout.manual_image_prompts_markdown}"
            )
            console.print("Anexe os arquivos na ordem indicada e use images import-manual.")
        else:
            console.print(
                f"[yellow]Fila Codex criada com {pending} item(ns):[/yellow] {layout.image_queue}"
            )
            console.print(
                "Gere uma imagem por item na conversa e use images import para cada arquivo."
            )
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


@images_app.command("import-manual")
def images_import_manual(
    project: str,
    files: Annotated[
        list[Path],
        typer.Argument(help="Arquivos na ordem global indicada em images/manual-prompts.md."),
    ],
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.images import import_manual_images

    manifest = import_manual_images(_project(project), files, force=force)
    imported = sum(item.active_version is not None for item in manifest.items)
    console.print(f"[green]Lote manual importado em ordem:[/green] {imported} cena(s) ativas.")


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
    model: Annotated[str | None, typer.Option("--model")] = None,
    output_format: Annotated[str | None, typer.Option("--output-format")] = None,
) -> None:
    layout = _project(project)
    _save_provider(layout, audio=provider)
    manifest = layout.load_manifest()
    before = manifest.config.audio.model_dump(mode="json")
    if voice_id:
        manifest.config.audio.voice_id = voice_id
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


@metadata_app.command("retouch")
def metadata_retouch(project: str, spec: Path) -> None:
    """Corrige título, descrição e candidatas existentes sem regenerar o vídeo."""

    from pipeline_videos.growth import retouch_youtube_package

    result = retouch_youtube_package(_project(project), spec)
    console.print(f"[green]Pacote editorial retocado:[/green] {result.title}")


@metadata_app.command("integrated-thumbnails-import")
def metadata_integrated_thumbnails_import(project: str, sources: list[Path]) -> None:
    """Importa thumbnails finais geradas com texto e símbolos incorporados."""

    from pipeline_videos.growth import import_integrated_thumbnail_candidates

    results = import_integrated_thumbnail_candidates(_project(project), sources)
    console.print(f"[green]{len(results)} thumbnails integradas importadas.[/green]")


@metadata_app.command("thumbnail")
def thumbnail_prepare(
    project: str,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.metadata import generate_fixture_thumbnail, queue_thumbnail_for_codex

    layout = _project(project)
    path = (
        generate_fixture_thumbnail(layout, force=force)
        if fixture
        else queue_thumbnail_for_codex(layout)
    )
    console.print(f"[green]Thumbnail preparada:[/green] {path}")


@metadata_app.command("import-thumbnail")
def thumbnail_import(
    project: str,
    file: Path,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    from pipeline_videos.metadata import import_thumbnail

    path = import_thumbnail(_project(project), file, force=force)
    console.print(f"[green]Thumbnail importada:[/green] {path}")


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
        ("Thumbnail", str(plan.thumbnail_path)),
        (
            "Aplicação da thumbnail",
            (
                "selecionar o frame manualmente no app do YouTube"
                if plan.video_format == "short"
                else "upload automático pela API"
            ),
        ),
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
    draft: Annotated[bool, typer.Option("--draft")] = False,
    final: Annotated[bool, typer.Option("--final")] = False,
) -> None:
    if draft and final:
        raise typer.BadParameter("Use somente --draft ou --final.")
    layout = _project(project)
    from pipeline_videos.music import select_music

    select_music(layout)
    manifest = layout.load_manifest()
    draft_mode = draft or (
        manifest.config.production.mode in {"draft_one_shot", "light_manual", "light_automatic"}
        and not final
    )
    if draft_mode:
        validate_draft_preflight(layout)
        from pipeline_videos.render import render_draft_preview

        result = render_draft_preview(layout, force=force)
        console.print(f"[green]Prévia one-shot:[/green] {result.preview_video}")
        return
    if not manifest.demo:
        require_approval(layout, "script")
        require_approval(layout, "biblical_review")
        require_approval(layout, "images")
    from pipeline_videos.render import render_project

    result = render_project(layout, force=force)
    console.print(f"[green]Render final:[/green] {result.final_video}")
    console.print(f"[green]Prévia:[/green] {result.preview_video}")


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
    console.print(f"[green]Candidato remoto:[/green] {result.final_video}")
    console.print(f"[green]Prévia:[/green] {result.preview_video}")


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
    production_mode = manifest.config.production.mode
    light_mode = production_mode in {"light_manual", "light_automatic"}
    main_agent_script_mode = light_mode or production_mode == "remote_review"
    draft_mode = production_mode in {
        "draft_one_shot",
        "light_manual",
        "light_automatic",
        "remote_review",
    }
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
    if draft_mode:
        validate_draft_preflight(layout)
    build_scenes(layout, force=force, skip_approvals=draft_mode)
    growth_queue = None
    if (
        not manifest.demo
        and manifest.config.youtube_growth.enabled
        and manifest.config.youtube_growth.use_specialist_subagent
    ):
        from pipeline_videos.growth import prepare_growth_queue

        growth_queue = prepare_growth_queue(layout, force=force)
    from pipeline_videos.images import prepare_images

    image_result = prepare_images(layout, force=force)
    if isinstance(image_result, dict) and image_result.get("items"):
        if production_mode == "light_manual":
            console.print(
                f"[yellow]Pipeline aguardando as imagens do usuário:[/yellow] "
                f"{layout.manual_image_prompts_markdown}"
            )
            console.print(
                "Envie os arquivos no chat na ordem global; depois eles serão importados com "
                "images import-manual."
            )
        else:
            console.print(
                f"[yellow]Pipeline pausado na fila de imagens:[/yellow] {layout.image_queue}"
            )
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
    if not manifest.demo and not draft_mode:
        require_approval(layout, "images")
    from pipeline_videos.audio import generate_audio
    from pipeline_videos.captions import generate_captions
    from pipeline_videos.metadata import (
        build_metadata,
        generate_fixture_thumbnail,
        queue_thumbnail_for_codex,
    )
    from pipeline_videos.music import select_music
    from pipeline_videos.render import render_draft_preview, render_project
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
    if (
        not manifest.demo
        and manifest.config.youtube_growth.enabled
        and manifest.config.youtube_growth.use_specialist_subagent
    ):
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
    if manifest.demo or manifest.config.images.provider == "fixture":
        generate_fixture_thumbnail(layout, force=force)
    elif light_mode:
        from pipeline_videos.growth import compose_light_short_cover

        compose_light_short_cover(layout, force=force)
    elif not manifest.config.youtube_growth.enabled and not layout.thumbnail.exists():
        queue_thumbnail_for_codex(layout)
    if production_mode == "remote_review":
        from pipeline_videos.render import render_review_candidate

        render_result = render_review_candidate(layout, force=force)
        report = validate_project(layout, require_render=True)
        console.print(f"[green]Candidato remoto:[/green] {render_result.final_video}")
    elif draft_mode:
        draft_result = render_draft_preview(layout, force=force)
        report = validate_project(layout, require_render=False)
        console.print(f"[green]Prévia one-shot:[/green] {draft_result.preview_video}")
    else:
        render_project(layout, force=force)
        report = validate_project(layout, require_render=True)
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
) -> None:
    """Executa etapas determinísticas e retoma automaticamente após os workers."""
    from pipeline_videos.autopilot import run_autopilot_once, wait_until_external_ready

    layout = _project(project)
    result = run_autopilot_once(
        layout,
        allow_external=allow_external,
        force=force,
    )
    if result.status == "waiting_external":
        console.print("[yellow]Autopilot aguardando saídas externas.[/yellow]")
        for wait in result.waits:
            console.print_json(json.dumps(wait, ensure_ascii=False))
        if not wait_external:
            console.print(
                f"Estado compacto: {layout.autopilot_state}. "
                "Inicie os workers declarados e execute autopilot novamente."
            )
            return
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
    console.print(f"[green]Autopilot concluído:[/green] {result.preview_path}")
    console.print(
        f"Validação: {'OK' if result.validation_ok else 'com erros em validation/report.md'}."
    )


@app.command("demo")
def demo_command(
    force: Annotated[bool, typer.Option("--force")] = False,
    projects_dir: Annotated[Path | None, typer.Option("--projects-dir")] = None,
) -> None:
    from pipeline_videos.demo import run_demo

    layout, result = run_demo(projects_dir=projects_dir, force=force)
    report = result["validation"]
    console.print(f"[green]Demonstração pronta:[/green] {layout.final_video}")
    console.print(f"Prévia: {layout.preview_video}")
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
            "thumbnail",
            "render",
            "validation",
        ),
        approvals=("images", "metadata", "final_render"),
        note=f"Paleta alterada para {palette_id}.",
    )
    console.print(f"[green]Paleta atualizada:[/green] {palette.name}")


@app.command("set-style")
def set_style(project: str, style_id: str) -> None:
    layout = _project(project)
    style = load_style(style_id)
    manifest = layout.load_manifest()
    enforce_standard = style.visual_standard_id == manifest.config.images.visual_standard_id
    if (
        layout.style_profile.exists()
        and read_model(layout.style_profile, StyleProfile) == style
        and manifest.config.images.enforce_visual_standard == enforce_standard
    ):
        console.print(f"[green]Estilo já estava ativo:[/green] {style.name}")
        return
    manifest.config.images.enforce_visual_standard = enforce_standard
    layout.save_manifest(manifest)
    write_json(layout.style_profile, style)
    invalidate(
        layout,
        stages=(
            "scenes",
            "image_manifest",
            "images",
            "youtube_growth",
            "metadata",
            "thumbnail",
            "render",
            "validation",
        ),
        approvals=("images", "metadata", "final_render"),
        note=f"Estilo alterado para {style_id}.",
    )
    console.print(f"[green]Estilo atualizado:[/green] {style.name}")


if __name__ == "__main__":
    app()
