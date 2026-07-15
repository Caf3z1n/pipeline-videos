from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, ImageStat

from pipeline_videos.models import (
    AudioManifest,
    BiblicalReview,
    CaptionManifest,
    ImageManifest,
    MusicManifest,
    SceneManifest,
    ScriptDocument,
    StageStatus,
    YouTubeGrowthPackage,
    YouTubeGrowthStrategy,
    YouTubeMetadata,
)
from pipeline_videos.state import complete_stage, fail_stage, start_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import (
    aspect_ratio_label,
    executable,
    ffprobe_json,
    matches_aspect_ratio,
    relative_posix,
    run_process,
    sha256_file,
    sha256_value,
    tokenize_words,
    utc_now,
)

VALIDATION_ALGORITHM_VERSION = "4"


@dataclass
class ValidationReport:
    project_id: str
    generated_at: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool | int | float | str] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def _canonical_words(text_or_words: str | list[str]) -> list[str]:
    words = tokenize_words(text_or_words) if isinstance(text_or_words, str) else text_or_words
    normalized = [
        re.sub(r"[^\wÀ-ÿ]+", "", word, flags=re.UNICODE).casefold() for word in words if word
    ]
    return [word for word in normalized if word]


def _validate_video(
    path: Path,
    report: ValidationReport,
    prefix: str,
    *,
    expected_width: int,
    expected_height: int,
) -> None:
    if not path.exists():
        report.errors.append(f"Vídeo ausente: {path.name}")
        return
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        report.errors.append(f"{path.name} não possui faixa de vídeo.")
        return
    if not audio:
        report.errors.append(f"{path.name} não possui faixa de áudio.")
    width = int(video.get("width", 0))
    height = int(video.get("height", 0))
    report.checks[f"{prefix}_resolution"] = f"{width}x{height}"
    report.checks[f"{prefix}_codec"] = str(video.get("codec_name", ""))
    expected_aspect = aspect_ratio_label(expected_width, expected_height)
    if not matches_aspect_ratio(width, height, expected_width, expected_height):
        report.errors.append(f"{path.name} não está em proporção {expected_aspect}.")
    if (width, height) != (expected_width, expected_height):
        report.errors.append(
            f"{path.name} tem resolução {width}x{height}; esperado "
            f"{expected_width}x{expected_height}."
        )
    if video.get("pix_fmt") != "yuv420p":
        report.warnings.append(f"{path.name} usa pixel format {video.get('pix_fmt')}, não yuv420p.")
    duration = float(data.get("format", {}).get("duration", 0))
    report.checks[f"{prefix}_duration_seconds"] = round(duration, 3)


def _validate_short_cover_first_frame(
    layout: ProjectLayout,
    report: ValidationReport,
    *,
    width: int,
    height: int,
) -> None:
    if not layout.final_video.is_file() or not layout.thumbnail.is_file():
        return
    frame_path = layout.render_work / "validated-first-frame.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            relative_posix(layout.final_video, layout.root),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:{height}:flags=lanczos",
            relative_posix(frame_path, layout.root),
        ],
        cwd=layout.root,
        timeout=120,
    )
    with Image.open(frame_path) as decoded, Image.open(layout.thumbnail) as thumbnail:
        expected = ImageOps.fit(
            thumbnail.convert("RGB"),
            (width, height),
            method=Image.Resampling.LANCZOS,
        )
        difference = ImageChops.difference(decoded.convert("RGB"), expected)
        rms = sum(ImageStat.Stat(difference).rms) / 3
    threshold = 18.0
    report.checks["short_cover_first_frame_rms"] = round(rms, 3)
    report.checks["short_cover_first_frame_match"] = rms <= threshold
    if rms > threshold:
        report.errors.append(
            "O primeiro frame do Short não corresponde à thumbnail ativa "
            f"(RMS {rms:.2f}, limite {threshold:.2f})."
        )


def validate_project(layout: ProjectLayout, *, require_render: bool = True) -> ValidationReport:
    manifest = layout.load_manifest()
    report_path = layout.root / "validation" / "report.json"
    markdown_path = layout.root / "validation" / "report.md"
    input_hash = sha256_value(
        {
            "algorithm": VALIDATION_ALGORITHM_VERSION,
            "config": manifest.config,
            "stage_hashes": {
                name: record.input_hash
                for name, record in manifest.stages.items()
                if name != "validation"
            },
            "require_render": require_render,
            "files": {
                name: path.stat().st_mtime_ns if path.exists() else None
                for name, path in {
                    "script": layout.script,
                    "review": layout.biblical_review,
                    "scenes": layout.scenes,
                    "images": layout.images,
                    "audio": layout.audio,
                    "music": layout.music_manifest,
                    "selected_music": (
                        layout.root / manifest.config.render.background_music_path
                        if manifest.config.render.background_music_path
                        else layout.music_manifest
                    ),
                    "captions": layout.captions,
                    "metadata": layout.metadata,
                    "thumbnail": layout.thumbnail,
                    "final": layout.final_video,
                    "preview": layout.preview_video,
                }.items()
            },
        }
    )
    cached = manifest.stages["validation"]
    if (
        cached.status in {StageStatus.COMPLETED, StageStatus.FAILED}
        and cached.input_hash == input_hash
        and report_path.is_file()
    ):
        data = read_json(report_path)
        return ValidationReport(
            project_id=data["project_id"],
            generated_at=data["generated_at"],
            errors=list(data.get("errors", [])),
            warnings=list(data.get("warnings", [])),
            checks=dict(data.get("checks", {})),
        )
    start_stage(layout, "validation", input_hash, provider="contract-validator")
    report = ValidationReport(project_id=manifest.project_id, generated_at=utc_now())
    try:
        script = read_model(layout.script, ScriptDocument)
        review = read_model(layout.biblical_review, BiblicalReview)
        scenes = read_model(layout.scenes, SceneManifest)
        images = read_model(layout.images, ImageManifest)
        audio = read_model(layout.audio, AudioManifest)
        captions = read_model(layout.captions, CaptionManifest)
        metadata = read_model(layout.metadata, YouTubeMetadata)

        if review.script_version != script.version:
            report.errors.append("A revisão bíblica não corresponde à versão ativa do roteiro.")
        if review.has_open_blockers:
            report.errors.append("A revisão bíblica ainda possui bloqueios abertos.")
        if not review.approved:
            report.warnings.append("A revisão bíblica existe, mas ainda não está aprovada.")

        script_words = _canonical_words(script.narration_text())
        scene_words = _canonical_words(" ".join(scene.narration for scene in scenes.scenes))
        report.checks["script_word_count"] = len(script_words)
        report.checks["scene_word_count"] = len(scene_words)
        if script_words != scene_words:
            report.errors.append(
                "A divisão em cenas omitiu, duplicou ou reordenou palavras do roteiro."
            )

        scene_ids = {scene.scene_id for scene in scenes.scenes}
        image_ids = {item.scene_id for item in images.items}
        if image_ids != scene_ids:
            report.errors.append("O manifesto de imagens não corresponde exatamente às cenas.")
        missing_images = []
        for item in images.items:
            version_id = item.approved_version or item.active_version
            version = next((entry for entry in item.versions if entry.version == version_id), None)
            if not version or not (layout.root / version.path).is_file():
                missing_images.append(item.scene_id)
        if missing_images:
            report.errors.append("Imagens ativas ausentes: " + ", ".join(missing_images))
        report.checks["image_count"] = len(images.items)

        audio_words = _canonical_words([word.word for word in audio.words])
        report.checks["audio_word_count"] = len(audio.words)
        if len(audio.words) != len({word.index for word in audio.words}):
            report.errors.append("Há índices de palavra duplicados no alinhamento de áudio.")
        for previous, current in zip(audio.words, audio.words[1:], strict=False):
            if current.start < previous.start or current.end <= current.start:
                report.errors.append("Os timestamps de áudio não são monotônicos e positivos.")
                break
        if audio_words != scene_words:
            report.warnings.append(
                "As palavras normalizadas pelo provedor de áudio diferem do roteiro; revise "
                "números, abreviações e pontuação no alinhamento."
            )
        if abs(audio.duration - audio.words[-1].end) > 2.0:
            report.warnings.append(
                "Há mais de dois segundos entre a última palavra e o fim do áudio."
            )

        if manifest.config.music.enabled:
            if manifest.stages["music"].status != StageStatus.COMPLETED:
                report.errors.append("A etapa de música ainda não selecionou uma faixa.")
            elif not layout.music_manifest.is_file():
                report.errors.append("O manifesto da música selecionada está ausente.")
            else:
                music = read_model(layout.music_manifest, MusicManifest)
                if not music.path:
                    report.errors.append("O manifesto de música não informa a faixa selecionada.")
                elif music.path != manifest.config.render.background_music_path:
                    report.errors.append(
                        "A música do render não corresponde ao manifesto da etapa de música."
                    )
                else:
                    music_path = (layout.root / music.path).resolve()
                    try:
                        music_path.relative_to(layout.root.resolve())
                    except ValueError:
                        report.errors.append("A música selecionada está fora da pasta do projeto.")
                    else:
                        if not music_path.is_file():
                            report.errors.append("O arquivo da música selecionada está ausente.")
                        elif sha256_file(music_path) != music.source_hash:
                            report.errors.append("O arquivo da música selecionada foi alterado.")
                        else:
                            report.checks["music_track"] = music.source_name or music_path.name
        else:
            report.checks["music_track"] = "disabled"

        if captions.word_count != len(audio.words):
            report.errors.append("A legenda não cobre a mesma quantidade de palavras do áudio.")
        if not all(captions.validation.values()):
            report.errors.append("O manifesto de legendas registra uma validação com falha.")
        for subtitle in (layout.ass, layout.srt, layout.vtt):
            if not subtitle.is_file():
                report.errors.append(f"Arquivo de legenda ausente: {subtitle.name}")

        if not metadata.title.strip() or not metadata.description.strip():
            report.errors.append("Os metadados do YouTube não têm título e descrição completos.")
        if len(metadata.title) > 100 or any(
            len(title) > 100 for title in metadata.alternative_titles
        ):
            report.errors.append("Um título do YouTube ultrapassa 100 caracteres.")
        title_variants = {metadata.title.strip().casefold()}
        title_variants.update(title.strip().casefold() for title in metadata.alternative_titles)
        report.checks["youtube_title_variants"] = len(title_variants)
        if manifest.config.youtube_growth.enabled:
            growth_stage = manifest.stages["youtube_growth"]
            if growth_stage.status != StageStatus.COMPLETED:
                message = "O pacote do especialista YouTube ainda não foi concluído."
                (report.errors if require_render else report.warnings).append(message)
            else:
                for artifact in (layout.growth_package, layout.growth_strategy):
                    if not artifact.is_file():
                        report.errors.append(f"Artefato do especialista ausente: {artifact.name}")
                if layout.growth_package.is_file() and layout.growth_strategy.is_file():
                    package = read_model(layout.growth_package, YouTubeGrowthPackage)
                    strategy = read_model(layout.growth_strategy, YouTubeGrowthStrategy)
                    if package.strategy != strategy:
                        report.errors.append("A estratégia ativa difere do pacote importado.")
                    report.checks["youtube_primary_keyword"] = strategy.primary_keyword
                    expected_variants = manifest.config.youtube_growth.title_variants
                    if len(title_variants) < expected_variants:
                        report.errors.append(
                            "O especialista entregou menos de "
                            f"{expected_variants} títulos distintos."
                        )
                    if manifest.video_format == "short":
                        report.checks["short_cover_timestamp_seconds"] = (
                            strategy.short_cover_timestamp_seconds or 0.0
                        )
        if manifest.video_format != "short" and (
            not metadata.chapters or re.match(r"^0{1,2}:00(?:\s|$)", metadata.chapters[0]) is None
        ):
            report.warnings.append("Os capítulos do YouTube não começam em 0:00.")
        if not layout.thumbnail.is_file():
            if manifest.config.production.mode == "draft_one_shot" and not require_render:
                report.warnings.append("A thumbnail ainda não foi gerada para o rascunho.")
            else:
                report.errors.append("A thumbnail está ausente.")
        else:
            with Image.open(layout.thumbnail) as thumbnail:
                width, height = thumbnail.size
            report.checks["thumbnail_resolution"] = f"{width}x{height}"
            render_config = manifest.config.render
            expected_aspect = aspect_ratio_label(render_config.width, render_config.height)
            if not matches_aspect_ratio(
                width,
                height,
                render_config.width,
                render_config.height,
            ):
                report.errors.append(f"A thumbnail não está em proporção {expected_aspect}.")
            minimum_width, minimum_height = (
                (render_config.width, render_config.height)
                if manifest.video_format == "short"
                else (1280, 720)
            )
            if width < minimum_width or height < minimum_height:
                report.warnings.append(
                    f"A thumbnail está abaixo da resolução mínima {minimum_width}×{minimum_height}."
                )

        if require_render:
            render_config = manifest.config.render
            if manifest.video_format == "short" and render_config.short_cover_enabled:
                cover_frames = round(render_config.short_cover_duration_seconds * render_config.fps)
                report.checks["short_cover_frames"] = cover_frames
                if cover_frames != 1:
                    report.errors.append("A capa inicial do Short deve ocupar exatamente um frame.")
            _validate_video(
                layout.final_video,
                report,
                "final",
                expected_width=render_config.width,
                expected_height=render_config.height,
            )
            _validate_video(
                layout.preview_video,
                report,
                "preview",
                expected_width=render_config.preview_width,
                expected_height=render_config.preview_height,
            )
            if layout.final_video.exists():
                final_data = ffprobe_json(layout.final_video)
                video_duration = float(final_data.get("format", {}).get("duration", 0))
                tolerance = max(1 / manifest.config.render.fps, 0.08)
                report.checks["av_duration_delta"] = round(abs(video_duration - audio.duration), 3)
                if abs(video_duration - audio.duration) > tolerance:
                    report.warnings.append(
                        "A duração final difere do áudio por mais de um frame; o container pode "
                        "incluir arredondamento ou silêncio residual."
                    )
                if manifest.video_format == "short" and render_config.short_cover_enabled:
                    _validate_short_cover_first_frame(
                        layout,
                        report,
                        width=render_config.width,
                        height=render_config.height,
                    )

        write_json(report_path, report.as_dict())
        lines = [
            "# Relatório de validação",
            "",
            f"Resultado: {'APROVADO' if report.valid else 'REPROVADO'}",
            "",
            "## Erros",
            "",
            *(f"- {item}" for item in report.errors),
            "",
            "## Alertas",
            "",
            *(f"- {item}" for item in report.warnings),
            "",
            "## Checagens",
            "",
            *(f"- {key}: {value}" for key, value in report.checks.items()),
        ]
        write_text(markdown_path, "\n".join(lines))
        artifacts = [
            relative_posix(report_path, layout.root),
            relative_posix(markdown_path, layout.root),
        ]
        if report.errors:
            fail_stage(
                layout,
                "validation",
                f"A validação encontrou {len(report.errors)} erro(s).",
                artifacts,
            )
        else:
            complete_stage(
                layout,
                "validation",
                input_hash,
                artifacts,
                provider="contract-validator",
                usage={"warnings": len(report.warnings), "checks": len(report.checks)},
            )
        return report
    except Exception as exc:
        fail_stage(layout, "validation", exc)
        raise
