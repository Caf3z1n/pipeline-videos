from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Literal

from pipeline_videos.models import (
    Briefing,
    ContinuityBible,
    ExperimentMemory,
    Palette,
    ProductionMode,
    ProjectConfig,
    ProjectManifest,
    RenderConfig,
    SourceLibrary,
    StageStatus,
    StyleProfile,
)
from pipeline_videos.state import initial_approvals, initial_stage_records
from pipeline_videos.storage import (
    DEFAULT_PROJECTS_DIR,
    LONG_PROJECTS_DIRNAME,
    REPO_ROOT,
    SHORT_PROJECTS_DIRNAME,
    ProjectLayout,
    read_model,
    write_json,
)
from pipeline_videos.utils import slugify, utc_now

DEFAULT_STYLE = "cinematic-family-3d-animation"
DEFAULT_PALETTE = "noite-azul-dourada"
DEFAULT_FONT_FILE = "visual/fonts/files/Montserrat-ExtraBold.ttf"
DEFAULT_FONT_LICENSE = "visual/fonts/OFL-Montserrat.txt"
DATED_PROJECT_PATTERN = re.compile(r"^(\d{2}-\d{2}-\d{4})_(.+)$")


def project_topic_slug(topic: str) -> str:
    match = DATED_PROJECT_PATTERN.fullmatch(topic.strip())
    return slugify(match.group(2) if match else topic)


def project_directory_name(topic: str, *, project_date: date | None = None) -> str:
    match = DATED_PROJECT_PATTERN.fullmatch(topic.strip())
    if match and project_date is None:
        datetime.strptime(match.group(1), "%d-%m-%Y")
        return f"{match.group(1)}_{slugify(match.group(2))}"
    selected_date = project_date or datetime.now().astimezone().date()
    return f"{selected_date:%d-%m-%Y}_{project_topic_slug(topic)}"


def install_default_caption_assets(layout: ProjectLayout) -> None:
    assets = resources.files("pipeline_videos").joinpath("assets", "fonts")
    for source_name, destination_path in (
        ("Montserrat-ExtraBold.ttf", DEFAULT_FONT_FILE),
        ("OFL-Montserrat.txt", DEFAULT_FONT_LICENSE),
    ):
        destination = layout.root / destination_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(assets.joinpath(source_name).read_bytes())


def load_style(style_id: str) -> StyleProfile:
    path = REPO_ROOT / "config" / "styles" / f"{style_id}.json"
    return read_model(path, StyleProfile)


def load_palette(palette_id: str) -> Palette:
    path = REPO_ROOT / "config" / "palettes" / f"{palette_id}.json"
    return read_model(path, Palette)


def project_readme(project_id: str) -> str:
    return f"""# Projeto {project_id}

Esta pasta é autocontida e é a fonte de verdade deste vídeo.

Antes de continuar em outra sessão, leia nesta ordem:

1. `project.json` para estado, configurações e aprovações;
2. `editorial/briefing.json`, `editorial/sources.json` e `editorial/decisions.jsonl`;
3. `script/script.json` e `reviews/biblical-consistency.json`, quando existirem;
4. os manifestos das etapas posteriores que já estiverem concluídas.

Não regenere artefatos concluídos sem conferir o hash e o estado. Use `--force` somente quando a
regeneração for deliberada; imagens aprovadas são preservadas como versões imutáveis.

O modo padrão é `draft_one_shot`: trabalhadores de imagem escrevem apenas em `images/staging`, e o
agente principal é o único que importa o lote e altera arquivos de estado. A prévia completa não
equivale à aprovação das imagens nem do render final.

Projetos novos usam `token_optimized_v1`. Depois de roteiro e revisão, use `autopilot`; entregue a
cada trabalhador somente `images/jobs/image-worker-N.json`, sem histórico herdado. A prévia rápida
não altera a qualidade V4 do render final aprovado.

A etapa `music` sorteia uma faixa da biblioteca local do pipeline e preserva uma cópia em
`audio/music/`. Uma escolha já registrada é reutilizada, salvo quando `--force` for deliberado.

Quando `youtube/growth-queue.json` estiver pendente, o subagente `youtube_growth_specialist` escreve
somente em staging. Apenas o agente principal importa títulos, descrição, estratégia e thumbnails.
Em produção remota, o coordenador permanece na sessão do job e cria o mesmo especialista de
crescimento do fluxo local, que entrega exatamente um par título+thumbnail no staging.

Em projetos Short, `script/shorts-queue.json` contrata o `shorts_script_specialist`. Ele escreve
somente no staging; o agente principal valida gancho, retenção, valor, CTA e revisão bíblica antes
de promover uma nova versão do roteiro.

Em vídeos longos novos, o roteiro usa `retention_story_long_v1`: começa no conflito, sustenta uma
pergunta central, progride por movimentos com nova informação, reengaja periodicamente e distribui
citações bíblicas verificadas ao longo da narrativa. Consulte `docs/long-form-script-standard.md`.
"""


def create_project(
    project_id: str,
    title: str,
    *,
    projects_dir: Path | None = None,
    target_duration_seconds: int = 540,
    style_id: str = DEFAULT_STYLE,
    palette_id: str = DEFAULT_PALETTE,
    demo: bool = False,
    video_format: Literal["landscape", "short"] = "landscape",
    project_date: date | None = None,
    production_mode: ProductionMode = "draft_one_shot",
) -> ProjectLayout:
    topic_id = project_topic_slug(project_id)
    project_id = project_directory_name(project_id, project_date=project_date)
    if target_duration_seconds < 30:
        raise ValueError("A duração-alvo deve ter pelo menos 30 segundos.")
    category = SHORT_PROJECTS_DIRNAME if video_format == "short" else LONG_PROJECTS_DIRNAME
    root = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve() / category / project_id
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"A pasta do projeto já existe e não está vazia: {root}")

    layout = ProjectLayout(root)
    layout.ensure_directories()
    now = utc_now()
    config = ProjectConfig()
    config.production.mode = production_mode
    style = load_style(style_id)
    if style.visual_standard_id != config.images.visual_standard_id:
        config.images.enforce_visual_standard = False
    if video_format == "short":
        config.render = RenderConfig(
            width=1080,
            height=1920,
            preview_width=1080,
            preview_height=1920,
        )
        config.images.width = 1024
        config.images.height = 1536
        config.captions.margin_left = 110
        config.captions.margin_right = 110
        config.captions.margin_bottom = 310
        config.captions.vertical_position = "center"
        config.captions.context_labels_enabled = False
    if demo:
        config.render = RenderConfig(
            width=1280,
            height=720,
            preview_width=640,
            preview_height=360,
            final_crf=22,
            final_preset="veryfast",
            preview_crf=27,
            preview_preset="veryfast",
            transition_seconds=0.35,
            transition_style="fade_black",
            motion_style="legacy",
            motion_supersample=1,
        )
        config.images.provider = "fixture"
        config.images.width = 1280
        config.images.height = 720
        config.audio.provider = "mock"
        config.music.enabled = False
        config.youtube_growth.enabled = False
        config.youtube_growth.use_specialist_subagent = False
        config.shorts_script.enabled = False
        config.shorts_script.use_specialist_subagent = False
    else:
        config.production.optimization_profile = "token_optimized_v1"
        config.production.worker_context_policy = "compact_only"
        config.editorial_policy.standard = "scripture_rich_ptbr_v1"
        config.editorial_policy.forbid_english_editorial_jargon = True
        config.editorial_policy.forbid_generic_text_reference = True
        config.editorial_policy.require_literal_scripture_citations = True
        long_form_retention = video_format != "short"
        config.long_form_script.enabled = long_form_retention
        config.long_form_script.narrative_standard = (
            "retention_story_long_v1" if long_form_retention else "legacy"
        )
        config.editorial_policy.require_quoted_comment_response = False
        config.captions.vertical_position = "center"
        config.captions.isolate_quoted_comment_response = False
        config.captions.comment_response_hold_seconds = 0.0
        config.audio.comment_response_silence_seconds = 0.0
        config.render.preview_crf = 16
        config.render.preview_preset = "fast"
        config.render.preview_motion_supersample = 2
        config.render.background_blur_profile = "functional_caption_focus_v1"
        config.youtube_growth.title_variants = 1
        config.youtube_growth.thumbnail_variants = 1
        config.youtube_growth.thumbnail_generation_mode = "integrated_imagegen_v1"
        config.youtube_growth.thumbnail_reference_mode = "opening_scene_edit_v1"
        config.youtube_growth.title_standard = "tension_pipe_passage_v2"
        config.youtube_growth.description_standard = "scripture_timeline_v1"
        config.youtube_growth.thumbnail_standard = "direct_conflict_red_v1"
        config.youtube_growth.thumbnail_typography_standard = "integrated_extruded_3d_v1"
        config.youtube_growth.title_thumbnail_pairing_standard = "complementary_v1"

    stages = initial_stage_records()
    if video_format != "short" or demo:
        stages["shorts_script"] = stages["shorts_script"].model_copy(
            update={"status": StageStatus.SKIPPED, "note": "Etapa exclusiva de Shorts."}
        )
    manifest = ProjectManifest(
        project_id=project_id,
        aliases=[topic_id],
        title=title,
        created_at=now,
        updated_at=now,
        target_duration_seconds=target_duration_seconds,
        demo=demo,
        video_format=video_format,
        config=config,
        stages=stages,
        approvals=initial_approvals(),
    )
    briefing = Briefing(title=title, target_duration_seconds=target_duration_seconds)
    continuity = ContinuityBible(
        global_rules=[
            "Manter roupas, proporções e silhuetas consistentes entre cenas consecutivas.",
            "Reutilizar imagens-âncora aprovadas quando o mesmo personagem reaparecer.",
            "Não inserir texto legível nas imagens de cena.",
            (
                "Cada imagem mostra um único momento, um único ambiente contínuo e uma ação "
                "principal, sem painéis, colagem, cenas empilhadas ou divisórias."
            ),
        ]
    )

    install_default_caption_assets(layout)
    write_json(layout.manifest, manifest)
    write_json(layout.briefing, briefing)
    write_json(layout.sources, SourceLibrary())
    write_json(layout.style_profile, style)
    write_json(layout.palette, load_palette(palette_id))
    write_json(layout.continuity, continuity)
    write_json(layout.render_config, config.render)
    write_json(layout.experiments, ExperimentMemory())
    (layout.root / "README.md").write_text(project_readme(project_id), encoding="utf-8")
    layout.decisions.touch()
    layout.events.touch()
    layout.append_event(
        {
            "event": "project_created",
            "project_id": project_id,
            "demo": demo,
            "video_format": video_format,
            "production_mode": production_mode,
        }
    )
    return layout


def clone_project_portably(source: ProjectLayout, destination: Path) -> ProjectLayout:
    if destination.exists():
        raise FileExistsError(f"O destino já existe: {destination}")
    shutil.copytree(source.root, destination)
    return ProjectLayout(destination.resolve())


def next_actions(layout: ProjectLayout) -> list[str]:
    manifest = layout.load_manifest()
    actions: list[str] = []
    briefing = manifest.approvals["briefing"]
    if briefing.status.value != "approved":
        briefing_stage = manifest.stages["briefing"]
        if (
            briefing_stage.status == StageStatus.COMPLETED
            and briefing_stage.provider == "automatic-verse-selector"
        ):
            actions.append(
                "Briefing automático pronto; a aprovação humana permanece pendente para o "
                "aceite editorial."
            )
        else:
            actions.append("Completar editorial/briefing.json e aprovar o briefing.")
    if manifest.stages["script"].status not in {
        StageStatus.COMPLETED,
        StageStatus.WAITING_APPROVAL,
    }:
        if (
            manifest.video_format == "short"
            and manifest.config.shorts_script.enabled
            and manifest.stages["shorts_script"].status != StageStatus.COMPLETED
        ):
            if manifest.config.shorts_script.use_specialist_subagent:
                actions.append("Executar a etapa especialista de roteiro para Shorts.")
            else:
                actions.append("Produzir e validar o pacote de roteiro no agente principal.")
        else:
            actions.append("Importar ou registrar um roteiro estruturado.")
    elif manifest.approvals["script"].status.value != "approved":
        actions.append("Revisar e aprovar o roteiro.")
    if manifest.approvals["biblical_review"].status.value != "approved":
        actions.append("Concluir e aprovar a revisão de consistência bíblica.")
    if manifest.stages["images"].status == StageStatus.WAITING_EXTERNAL:
        actions.append("Gerar/importar as imagens pendentes da fila do Codex.")
    if (
        manifest.config.audio.provider == "local_manual"
        and manifest.stages["audio"].status != StageStatus.COMPLETED
    ):
        actions.append(
            "Enviar script/narration.txt ao usuário e importar o arquivo de voz com "
            "audio import-manual."
        )
    for stage in (
        "shorts_script",
        "scenes",
        "images",
        "audio",
        "music",
        "captions",
        "youtube_growth",
        "metadata",
        "render",
        "validation",
    ):
        if manifest.stages[stage].status in {
            StageStatus.PENDING,
            StageStatus.FAILED,
            StageStatus.STALE,
        }:
            actions.append(f"Executar ou corrigir a etapa: {stage}.")
            break
    return actions or ["Revisar os artefatos finais e registrar as aprovações antes de publicar."]
