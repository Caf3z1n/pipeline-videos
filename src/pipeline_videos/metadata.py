from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline_videos.creative_direction import CREATIVE_MODE_SPECS
from pipeline_videos.editorial import scripture_citations
from pipeline_videos.models import (
    ApprovalStatus,
    AudioManifest,
    Briefing,
    Palette,
    SceneManifest,
    ScriptDocument,
    SourceLibrary,
    StyleProfile,
    YouTubeMetadata,
)
from pipeline_videos.state import complete_stage, invalidate, set_approval, wait_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import (
    aspect_ratio_label,
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

METADATA_ALGORITHM_VERSION = "1.3"


def _timestamp(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{second:02d}" if hours else f"{minute}:{second:02d}"


def _timeline_timestamp(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, second = divmod(total, 60)
    return f"{minutes:02d}:{second:02d}"


def _chapter_title(role: str) -> str:
    return {
        "opening": "Uma pergunta para o coração",
        "biblical_narrative": "A passagem bíblica",
        "reflection": "O que essa mensagem revela",
        "application": "Como viver isso hoje",
        "closing": "Uma palavra final",
    }.get(role, "Reflexão")


def _build_chapters(
    scenes: SceneManifest,
    audio: AudioManifest | None,
    script: ScriptDocument | None = None,
    *,
    scripture_timeline: bool = False,
) -> list[str]:
    starts = {segment.scene_id: segment.start for segment in audio.segments} if audio else {}
    if scripture_timeline and script is not None:
        section_starts: dict[str, float] = {}
        elapsed = 0.0
        for scene in scenes.scenes:
            section_starts.setdefault(scene.section_id, starts.get(scene.scene_id, elapsed))
            elapsed += scene.estimated_duration_seconds
        timeline = ["00:00 - Abertura"]
        cited_section_ids = {item.section_id for item in scripture_citations(script)}
        for section in script.sections:
            if section.section_id not in cited_section_ids or not section.biblical_basis:
                continue
            references = "; ".join(dict.fromkeys(section.biblical_basis))
            timeline.append(
                f"{_timeline_timestamp(section_starts.get(section.section_id, 0.0))} - {references}"
            )
        return timeline
    chapters: list[str] = []
    elapsed = 0.0
    seen_sections: set[str] = set()
    for scene in scenes.scenes:
        if scene.section_id not in seen_sections:
            seen_sections.add(scene.section_id)
            chapters.append(
                f"{_timestamp(starts.get(scene.scene_id, elapsed))} {_chapter_title(_role(scene))}"
            )
        elapsed += scene.estimated_duration_seconds
    if chapters and not chapters[0].startswith("0:00 "):
        chapters[0] = "0:00 " + chapters[0].split(" ", 1)[1]
    return chapters


def _role(scene: object) -> str:
    function = getattr(scene, "narrative_function", "")
    text = str(function).casefold()
    if "curiosidade" in text:
        return "opening"
    if "passagem" in text:
        return "biblical_narrative"
    if "reflex" in text:
        return "reflection"
    if "prática" in text:
        return "application"
    return "closing"


def build_metadata(layout: ProjectLayout, *, force: bool = False) -> YouTubeMetadata:
    briefing = read_model(layout.briefing, Briefing)
    scenes = read_model(layout.scenes, SceneManifest)
    sources = read_model(layout.sources, SourceLibrary)
    style = read_model(layout.style_profile, StyleProfile)
    palette = read_model(layout.palette, Palette)
    project = layout.load_manifest()
    script = read_model(layout.script, ScriptDocument) if layout.script.is_file() else None
    audio = read_model(layout.audio, AudioManifest) if layout.audio.exists() else None
    input_hash = sha256_value(
        {
            "algorithm": METADATA_ALGORITHM_VERSION,
            "briefing": briefing,
            "scenes": scenes,
            "sources": sources,
            "style": style,
            "palette": palette,
            "script": script,
        }
    )
    stage = layout.load_manifest().stages["metadata"]
    if (
        not force
        and stage.input_hash == input_hash
        and stage.status.value == "completed"
        and layout.metadata.exists()
    ):
        return read_model(layout.metadata, YouTubeMetadata)

    invalidate(
        layout,
        stages=("thumbnail", "validation"),
        approvals=("metadata", "final_render"),
        note="Os metadados foram atualizados.",
    )
    subject = briefing.central_theme.strip().rstrip(".") or briefing.main_passage
    subject_title = subject[:1].upper() + subject[1:]
    mode_label = (
        CREATIVE_MODE_SPECS[briefing.creative_mode].label
        if briefing.creative_mode in CREATIVE_MODE_SPECS
        else "Conteúdo bíblico"
    )
    title = (
        script.title[:100]
        if script is not None
        else f"{subject_title} — reflexão sobre {briefing.main_passage}"[:100]
    )
    alternatives = [
        f"{subject_title} | {briefing.main_passage}"[:100],
        f"{mode_label}: {subject}"[:100],
        f"O que {briefing.main_passage} revela sobre {subject}"[:100],
    ]
    source_lines = []
    for entry in sources.entries:
        label = entry.title
        if entry.url:
            label += f" — {entry.url}"
        source_lines.append(f"- {label}")
    description = "\n\n".join(
        part
        for part in (
            (
                f"{subject_title} em {briefing.main_passage}: uma abordagem "
                f"{mode_label.casefold()} fiel à passagem esclarece seu ponto central."
            ),
            briefing.viewer_outcome or briefing.practical_application,
            (
                f"A análise diferencia o que a Bíblia afirma em {briefing.main_passage}, "
                "o contexto verificado e as interpretações usadas para organizar a explicação."
            ),
            "Referências consultadas:\n" + "\n".join(source_lines) if source_lines else "",
            "A apresentação distingue texto bíblico, contexto verificado, interpretação e "
            "elaboração criativa. Consulte a passagem completa na tradução de sua preferência.",
        )
        if part
    )
    colors = ", ".join(f"{item.name} {item.hex}" for item in palette.colors)
    render_config = project.config.render
    aspect = aspect_ratio_label(render_config.width, render_config.height)
    is_vertical = render_config.height > render_config.width
    orientation = "vertical" if is_vertical else "horizontal"
    concept = (
        "Uma silhueta pequena diante de um caminho iluminado, leitura imediata em tamanho "
        "reduzido, com um único foco visual e amplo contraste."
    )
    thumbnail_prompt = "\n".join(
        (
            "Use case: illustration-story",
            f"Asset type: capa {orientation} {aspect} para YouTube",
            f"Primary request: {concept}",
            f"Style/medium: {style.medium}; {'; '.join(style.principles)}",
            f"Color palette: {colors}; clima {palette.mood}",
            "Composition/framing: foco central forte, silhueta grande, legível em tela pequena; "
            f"composição {orientation} {aspect}",
            "Constraints: sem texto gerado na imagem; reservar área limpa para título posterior",
            f"Avoid: {'; '.join(style.negative_constraints)}; composição congestionada",
        )
    )
    book_name = re.sub(r"\s+\d.*$", "", briefing.main_passage).strip()
    topic_candidates = [
        subject,
        briefing.main_passage,
        f"{subject} na Bíblia",
        f"{briefing.main_passage} explicado",
        f"livro de {book_name}" if book_name else "Bíblia",
        "estudo bíblico",
        "explicação bíblica",
        "Bíblia em português",
        "contexto bíblico",
        mode_label,
    ]
    topics = list(dict.fromkeys(item.strip() for item in topic_candidates if item.strip()))
    metadata = YouTubeMetadata(
        title=title,
        alternative_titles=alternatives,
        description=description,
        chapters=_build_chapters(
            scenes,
            audio,
            script,
            scripture_timeline=(
                project.config.youtube_growth.description_standard
                in {"scripture_timeline_v1", "search_first_unique_v2"}
            ),
        ),
        topics=topics,
        pinned_comment=(
            f"O que {briefing.main_passage} esclarece sobre {subject.casefold()} para você?"
        ),
        thumbnail_concept=concept,
        thumbnail_prompt=thumbnail_prompt,
        thumbnail_alternatives=[
            "Caminho iluminado entre vales escuros",
            "Pastor e ovelha em silhueta ao amanhecer",
            "Figura humana sob um feixe de luz suave",
        ],
    )
    write_json(layout.metadata, metadata)
    write_json(
        layout.thumbnail_prompt,
        {
            "schema_version": "1.0",
            "status": "pending",
            "prompt": thumbnail_prompt,
            "expected_path": "youtube/thumbnail.png",
            "do_not_overwrite_approved": True,
        },
    )
    write_text(layout.description, description + "\n\n" + "\n".join(metadata.chapters))
    complete_stage(
        layout,
        "metadata",
        input_hash,
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
            relative_posix(layout.thumbnail_prompt, layout.root),
        ],
        provider="deterministic-draft",
    )
    return metadata


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "arialbd.ttf" if bold else "arial.ttf"
    path = Path("C:/Windows/Fonts") / filename
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit_headline(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    max_width: int,
    max_lines: int = 2,
) -> tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    words = text.split()
    for size in range(58, 31, -2):
        font = _font(size, True)
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            width = draw.textbbox((0, 0), candidate, font=font)[2]
            if current and width > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) <= max_lines:
            return "\n".join(lines), font
    fallback = _font(32, True)
    return text[:60], fallback


def generate_fixture_thumbnail(layout: ProjectLayout, *, force: bool = False) -> Path:
    if layout.thumbnail.exists() and not force:
        return layout.thumbnail
    image_manifest = read_json(layout.images)
    image_path: Path | None = None
    for item in image_manifest.get("items", []):
        active = item.get("approved_version") or item.get("active_version")
        for version in item.get("versions", []):
            if version.get("version") == active:
                image_path = layout.root / version["path"]
                break
        if image_path:
            break
    if image_path and image_path.exists():
        source = Image.open(image_path).convert("RGB")
        crop_top = min(round(source.height * 0.12), source.height - 1)
        canvas = source.crop((0, crop_top, source.width, source.height)).resize((1280, 720))
    else:
        canvas = Image.new("RGB", (1280, 720), "#132238")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, 1280, 720), fill=(8, 18, 30, 90))
    draw.rectangle((0, 410, 1280, 720), fill=(8, 18, 30, 215))
    draw.rounded_rectangle((42, 36, 210, 86), radius=6, fill=(227, 182, 87, 235))
    draw.text((62, 47), "FIXTURE", font=_font(24, True), fill="#132238")
    metadata = read_model(layout.metadata, YouTubeMetadata)
    short = metadata.title.split("—", 1)[0].strip().upper()
    headline, headline_font = _fit_headline(draw, short, max_width=1155)
    draw.multiline_text(
        (62, 448),
        headline,
        font=headline_font,
        fill="#FFFFFF",
        spacing=2,
    )
    draw.text((64, 655), "REFLEXÕES DA BÍBLIA", font=_font(26), fill="#E3B657")
    result = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    layout.thumbnail.parent.mkdir(parents=True, exist_ok=True)
    result.save(layout.thumbnail, quality=92)
    digest = sha256_file(layout.thumbnail)
    complete_stage(
        layout,
        "thumbnail",
        digest,
        [relative_posix(layout.thumbnail, layout.root)],
        provider="fixture",
        note="Thumbnail de demonstração; substituir antes de publicação.",
    )
    return layout.thumbnail


def queue_thumbnail_for_codex(layout: ProjectLayout) -> Path:
    payload = read_json(layout.thumbnail_prompt)
    input_hash = sha256_value(payload["prompt"])
    stage = layout.load_manifest().stages["thumbnail"]
    if (
        stage.status.value == "waiting_external"
        and stage.input_hash == input_hash
        and layout.thumbnail_prompt.is_file()
    ):
        return layout.thumbnail_prompt
    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Uma nova thumbnail foi solicitada.",
    )
    wait_stage(
        layout,
        "thumbnail",
        input_hash,
        "Use a geração embutida do Codex e importe o PNG resultante; o CLI local não acessa "
        "a ferramenta.",
        [relative_posix(layout.thumbnail_prompt, layout.root)],
    )
    return layout.thumbnail_prompt


def import_thumbnail(layout: ProjectLayout, source: Path, *, force: bool = False) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if layout.thumbnail.exists() and not force:
        raise FileExistsError("Já existe thumbnail ativa. Use --force para criar uma nova versão.")
    versions = sorted(layout.thumbnail.parent.glob("thumbnail-v*.png"))
    destination = layout.thumbnail.parent / f"thumbnail-v{len(versions) + 1:03d}.png"
    shutil.copy2(source, destination)
    shutil.copy2(destination, layout.thumbnail)
    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Uma nova versão da thumbnail foi importada.",
    )
    digest = sha256_file(destination)
    write_json(
        layout.thumbnail.parent / "thumbnail-manifest.json",
        {
            "active_path": relative_posix(destination, layout.root),
            "active_hash": digest,
            "updated_at": utc_now(),
            "approved": False,
        },
    )
    complete_stage(
        layout,
        "thumbnail",
        digest,
        [relative_posix(destination, layout.root), relative_posix(layout.thumbnail, layout.root)],
        provider="codex-import",
    )
    return layout.thumbnail


def approve_metadata(layout: ProjectLayout, *, decided_by: str, note: str | None = None) -> None:
    metadata = read_model(layout.metadata, YouTubeMetadata)
    if not metadata.title.strip() or not metadata.description.strip():
        raise ValueError("Título e descrição precisam estar completos antes da aprovação.")
    project = layout.load_manifest()
    youtube_auto_frame = (
        project.video_format == "short"
        and project.config.youtube_growth.thumbnail_generation_mode == "youtube_auto_frame_v1"
    )
    if not youtube_auto_frame and not layout.thumbnail.is_file():
        raise FileNotFoundError("Importe ou gere uma thumbnail antes de aprovar os metadados.")
    thumbnail_manifest = layout.thumbnail.parent / "thumbnail-manifest.json"
    if thumbnail_manifest.is_file() and not youtube_auto_frame:
        thumbnail_state = read_json(thumbnail_manifest)
        thumbnail_state.update(
            {
                "approved": True,
                "approved_at": utc_now(),
                "approved_by": decided_by,
                "approval_note": note,
            }
        )
        write_json(thumbnail_manifest, thumbnail_state)
    set_approval(
        layout,
        "metadata",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=note,
        artifact_hash=sha256_value(
            {
                "metadata": sha256_file(layout.metadata),
                "description": sha256_file(layout.description),
                "thumbnail": None if youtube_auto_frame else sha256_file(layout.thumbnail),
            }
        ),
    )
