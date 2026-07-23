from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline_videos.long_form_script import (
    minimum_long_form_reengagements,
    validate_long_form_retention_script,
)
from pipeline_videos.models import (
    AudioManifest,
    BiblicalReview,
    CaptionManifest,
    CreativeContract,
    ImageManifest,
    MusicManifest,
    ProjectManifest,
    SceneManifest,
    ScriptDocument,
    StageStatus,
    YouTubeGrowthPackage,
    YouTubeGrowthStrategy,
    YouTubeMetadata,
)
from pipeline_videos.narration_style import validate_authorial_narration_style
from pipeline_videos.script import ensure_creative_contract
from pipeline_videos.state import complete_stage, fail_stage, start_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import (
    aspect_ratio_label,
    ffprobe_json,
    matches_aspect_ratio,
    relative_posix,
    sha256_file,
    sha256_value,
    tokenize_words,
    utc_now,
)

VALIDATION_ALGORITHM_VERSION = "8"

_COMPLETION_FIRST_ADVISORY_PATTERNS = (
    "O contrato criativo",
    "contrato criativo marcou hook",
    "pontos de reengajamento",
    "A narração compacta é curta demais",
    "A narração compacta contém uma pausa",
    "O Short compacto termina com silêncio",
    "A legenda não cobre a mesma quantidade",
    "O manifesto de legendas registra uma validação",
    "títulos distintos",
)


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


def _apply_final_duration_policy(
    manifest: ProjectManifest,
    video_duration: float,
    report: ValidationReport,
) -> None:
    """Validate explicit constraints while keeping ordinary duration targets advisory."""

    if manifest.duration_policy == "approved_script_locked_v1":
        report.checks["duration_policy"] = "approved_script_locked_v1"
        report.checks["editorial_duration_estimate_seconds"] = manifest.target_duration_seconds
        report.checks["script_driven_duration_seconds"] = video_duration
        return
    if manifest.duration_policy == "adaptive_range_v1":
        assert manifest.requested_duration_min_seconds is not None
        assert manifest.requested_duration_max_seconds is not None
        if not (
            manifest.requested_duration_min_seconds
            <= video_duration
            <= manifest.requested_duration_max_seconds
        ):
            report.errors.append("A duração do vídeo final ficou fora da faixa solicitada.")
        return
    if manifest.duration_policy == "preferred_target_v1":
        assert manifest.requested_duration_min_seconds is not None
        assert manifest.requested_duration_max_seconds is not None
        report.checks["requested_duration_target_seconds"] = manifest.target_duration_seconds
        report.checks["preferred_duration_min_seconds"] = manifest.requested_duration_min_seconds
        report.checks["preferred_duration_max_seconds"] = manifest.requested_duration_max_seconds
        if not (
            manifest.requested_duration_min_seconds
            <= video_duration
            <= manifest.requested_duration_max_seconds
        ):
            report.warnings.append(
                "A duração final ficou fora da janela preferencial, mas a voz natural será "
                "preservada e nenhuma correção automática será iniciada."
            )
        return
    duration_tolerance = manifest.config.shorts_script.duration_tolerance_seconds
    report.checks["requested_duration_target_seconds"] = manifest.target_duration_seconds
    report.checks["duration_tolerance_seconds"] = duration_tolerance
    if abs(video_duration - manifest.target_duration_seconds) > duration_tolerance:
        report.errors.append(
            "A duração do vídeo final ficou fora da tolerância do alvo explicitamente exato."
        )


def _apply_completion_first_policy(report: ValidationReport) -> None:
    """Demote heuristics while preserving integrity, biblical and publication blockers."""

    retained: list[str] = []
    for error in report.errors:
        if any(
            pattern.casefold() in error.casefold()
            for pattern in _COMPLETION_FIRST_ADVISORY_PATTERNS
        ):
            report.warnings.append(f"[entrega primeiro] {error}")
        else:
            retained.append(error)
    report.errors = retained
    report.warnings = list(dict.fromkeys(report.warnings))


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


def validate_project(layout: ProjectLayout, *, require_render: bool = True) -> ValidationReport:
    manifest = layout.load_manifest()
    if layout.script.is_file() and layout.briefing.is_file():
        ensure_creative_contract(layout)
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
                    "creative_contract": layout.creative_contract,
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
                    "growth_package": layout.growth_package,
                    "growth_strategy": layout.growth_strategy,
                    "final": layout.final_video,
                }.items()
            },
        }
    )
    cached = manifest.stages["validation"]
    if (
        cached.status == StageStatus.COMPLETED
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
        validate_authorial_narration_style(script)
        contract = read_model(layout.creative_contract, CreativeContract)
        review = read_model(layout.biblical_review, BiblicalReview)
        scenes = read_model(layout.scenes, SceneManifest)
        images = read_model(layout.images, ImageManifest)
        audio = read_model(layout.audio, AudioManifest)
        captions = read_model(layout.captions, CaptionManifest)
        metadata = read_model(layout.metadata, YouTubeMetadata)

        if (
            manifest.video_format != "short"
            and manifest.config.long_form_script.enabled
            and manifest.config.long_form_script.narrative_standard
            in {"retention_story_long_v1", "creative_retention_long_v2"}
        ):
            from pipeline_videos.models import Briefing, SourceLibrary

            long_diagnostics = validate_long_form_retention_script(
                script,
                target_duration_seconds=manifest.target_duration_seconds,
                config=manifest.config.long_form_script,
                sources=read_model(layout.sources, SourceLibrary),
                briefing=read_model(layout.briefing, Briefing),
            )
            report.checks["long_form_retention_standard"] = (
                manifest.config.long_form_script.narrative_standard
            )
            report.checks["long_form_estimated_duration_seconds"] = long_diagnostics[
                "estimated_duration_seconds"
            ]
            report.checks["long_form_scripture_citations"] = len(
                long_diagnostics["scripture_citation_timestamps"]
            )

        if review.script_version != script.version:
            report.errors.append("A revisão bíblica não corresponde à versão ativa do roteiro.")
        if review.has_open_blockers:
            report.errors.append("A revisão bíblica ainda possui bloqueios abertos.")
        if not review.approved:
            report.warnings.append("A revisão bíblica existe, mas ainda não está aprovada.")

        section_ids = [section.section_id for section in script.sections]
        beat_section_ids = [beat.section_id for beat in contract.beats]
        report.checks["creative_contract_profile"] = contract.pacing.profile_id
        report.checks["creative_critical_components"] = len(contract.critical_component_ids)
        if contract.section_ids != section_ids or beat_section_ids != section_ids:
            report.errors.append(
                "O contrato criativo não cobre exatamente as seções ativas do roteiro."
            )
        functions = {function for beat in contract.beats for function in beat.functions}
        if contract.algorithm == "creative-contract-v2":
            if "resolution" not in functions:
                report.errors.append("O contrato criativo precisa declarar resolução.")
            if contract.hook_used and "hook" not in functions:
                report.errors.append("O contrato criativo marcou hook sem beat correspondente.")
        elif "hook" not in functions or "payoff" not in functions:
            report.errors.append("O contrato criativo precisa declarar gancho e payoff.")
        reengagements = sum("reengagement" in beat.functions for beat in contract.beats)
        report.checks["creative_reengagements"] = reengagements
        if contract.format == "long_form" and len(contract.beats) >= 5:
            required_reengagements = (
                minimum_long_form_reengagements(
                    manifest.target_duration_seconds,
                    manifest.config.long_form_script,
                )
                if manifest.config.long_form_script.narrative_standard
                in {"retention_story_long_v1", "creative_retention_long_v2"}
                else 2
            )
            report.checks["creative_required_reengagements"] = required_reengagements
            if reengagements < required_reengagements:
                report.errors.append(
                    "O vídeo longo precisa de ao menos "
                    f"{required_reengagements} pontos de reengajamento."
                )

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
        shorts_config = manifest.config.shorts_script
        report.checks["speech_rate_policy"] = manifest.config.audio.speech_rate_policy
        report.checks["speech_speed"] = manifest.config.audio.speed
        if (
            manifest.video_format == "short"
            and shorts_config.compact_narrative_standard == "cinematic_jesus_devotional_v1"
        ):
            narration_ratio = audio.duration / manifest.target_duration_seconds
            largest_gap = max(
                (
                    current.start - previous.end
                    for previous, current in zip(audio.words, audio.words[1:], strict=False)
                ),
                default=0.0,
            )
            terminal_silence = audio.duration - audio.words[-1].end
            report.checks["compact_narration_duration_ratio"] = round(narration_ratio, 4)
            report.checks["compact_largest_narration_gap_seconds"] = round(largest_gap, 4)
            report.checks["compact_terminal_silence_seconds"] = round(terminal_silence, 4)
            if manifest.duration_policy == "adaptive_range_v1":
                assert manifest.requested_duration_min_seconds is not None
                assert manifest.requested_duration_max_seconds is not None
                report.checks["requested_duration_min_seconds"] = (
                    manifest.requested_duration_min_seconds
                )
                report.checks["requested_duration_max_seconds"] = (
                    manifest.requested_duration_max_seconds
                )
                report.checks["effective_duration_seconds"] = audio.duration
                if not (
                    manifest.requested_duration_min_seconds
                    <= audio.duration
                    <= manifest.requested_duration_max_seconds
                ):
                    report.errors.append(
                        "A duração real da narração ficou fora da faixa solicitada para o Short."
                    )
                if (
                    manifest.effective_duration_seconds is not None
                    and abs(manifest.effective_duration_seconds - audio.duration) > 0.01
                ):
                    report.errors.append(
                        "A duração efetiva registrada diverge do manifesto de áudio."
                    )
            elif manifest.duration_policy == "preferred_target_v1":
                assert manifest.requested_duration_min_seconds is not None
                assert manifest.requested_duration_max_seconds is not None
                report.checks["preferred_duration_min_seconds"] = (
                    manifest.requested_duration_min_seconds
                )
                report.checks["preferred_duration_max_seconds"] = (
                    manifest.requested_duration_max_seconds
                )
                report.checks["effective_duration_seconds"] = audio.duration
                if not (
                    manifest.requested_duration_min_seconds
                    <= audio.duration
                    <= manifest.requested_duration_max_seconds
                ):
                    report.warnings.append(
                        "A duração natural da narração ficou fora da janela preferencial; "
                        "isso não autoriza alterar a velocidade da voz nem preencher tempo."
                    )
            elif manifest.duration_policy == "approved_script_locked_v1":
                report.checks["effective_duration_seconds"] = audio.duration
                report.checks["duration_is_defined_by_approved_script"] = True
            elif narration_ratio < shorts_config.minimum_audio_duration_ratio:
                report.errors.append(
                    "A narração compacta é curta demais para a duração-alvo; use hook ou "
                    "versículo mais longo, sem preenchimento por silêncio."
                )
            if largest_gap > shorts_config.maximum_narration_gap_seconds:
                report.errors.append(
                    "A narração compacta contém uma pausa longa demais; a mensagem deve "
                    "preencher a duração do Short."
                )
            if terminal_silence > shorts_config.maximum_terminal_silence_seconds:
                report.errors.append("O Short compacto termina com silêncio excessivo.")

        render_mix = manifest.config.render
        report.checks["audio_mix_standard"] = render_mix.audio_mix_standard
        if render_mix.audio_mix_standard == "voice_music_loudness_v1":
            report.checks["narration_target_lufs"] = render_mix.narration_target_lufs
            report.checks["background_music_gap_lu"] = render_mix.background_music_gap_lu
            report.checks["background_music_target_lufs"] = (
                render_mix.narration_target_lufs - render_mix.background_music_gap_lu
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
                            if music.mix_standard != render_mix.audio_mix_standard:
                                report.errors.append(
                                    "O padrão de mixagem da música difere da configuração "
                                    "do render."
                                )
                            if render_mix.audio_mix_standard == "voice_music_loudness_v1":
                                expected_music_target = (
                                    render_mix.narration_target_lufs
                                    - render_mix.background_music_gap_lu
                                )
                                if music.narration_target_lufs != render_mix.narration_target_lufs:
                                    report.errors.append(
                                        "O alvo de loudness da narração difere do manifesto "
                                        "da música."
                                    )
                                if music.background_music_target_lufs != expected_music_target:
                                    report.errors.append(
                                        "O alvo de loudness da música difere do manifesto da etapa."
                                    )
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
        if manifest.video_format != "short" and (
            not metadata.chapters or re.match(r"^0{1,2}:00(?:\s|$)", metadata.chapters[0]) is None
        ):
            report.warnings.append("Os capítulos do YouTube não começam em 0:00.")
        if require_render:
            render_config = manifest.config.render
            _validate_video(
                layout.final_video,
                report,
                "final",
                expected_width=render_config.width,
                expected_height=render_config.height,
            )
            if layout.final_video.exists():
                final_data = ffprobe_json(layout.final_video)
                video_duration = float(final_data.get("format", {}).get("duration", 0))
                _apply_final_duration_policy(manifest, video_duration, report)
                tolerance = max(1 / manifest.config.render.fps, 0.08)
                report.checks["av_duration_delta"] = round(abs(video_duration - audio.duration), 3)
                if abs(video_duration - audio.duration) > tolerance:
                    report.warnings.append(
                        "A duração final difere do áudio por mais de um frame; o container pode "
                        "incluir arredondamento ou silêncio residual."
                    )

        if manifest.config.production.validation_policy == "completion_first_v1":
            _apply_completion_first_policy(report)
            report.checks["validation_policy"] = "completion_first_v1"
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
