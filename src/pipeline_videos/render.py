from __future__ import annotations

import json
import math
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from pipeline_videos.models import (
    AudioManifest,
    CaptionManifest,
    ImageManifest,
    RenderConfig,
    Scene,
    SceneManifest,
    ScriptDocument,
)
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json, write_text
from pipeline_videos.utils import (
    executable,
    ffprobe_json,
    relative_posix,
    run_process,
    sha256_file,
    sha256_value,
)

RENDER_ALGORITHM_VERSION = "7"
MAX_SEGMENT_RENDER_WORKERS = 2
OUTPUT_PIXEL_FORMAT = "yuv420p"
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_AUDIO_CHANNELS = 2
_CINEMATIC_TRANSITIONS = (
    "fadeslow",
    "smoothleft",
    "smoothleft",
    "hblur",
    "fadeslow",
    "fade",
)


@dataclass(frozen=True)
class SceneRenderPlan:
    scene_id: str
    order: int
    image_path: Path
    frame_count: int
    blur_role: str = "closing"


@dataclass(frozen=True)
class RenderResult:
    base_video: Path
    final_video: Path
    reused_base: bool
    final_validation: dict[str, Any]


def _project_file(root: Path, value: str | Path, *, label: str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} está fora da pasta portátil do projeto: {resolved}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} não encontrado: {resolved}")
    return resolved


def _frame_counts(durations: Sequence[float], total_duration: float, fps: int) -> list[int]:
    if not durations or fps < 1 or total_duration <= 0:
        raise ValueError("Durações, duração total e frame rate devem ser positivos.")
    if any(duration <= 0 for duration in durations):
        raise ValueError("Cada cena deve ter duração positiva.")

    total_frames = max(len(durations), round(total_duration * fps))
    remaining = total_frames - len(durations)
    weight_total = sum(durations)
    raw_extras = [duration / weight_total * remaining for duration in durations]
    extras = [math.floor(value) for value in raw_extras]
    leftover = remaining - sum(extras)
    order = sorted(
        range(len(durations)),
        key=lambda index: (raw_extras[index] - extras[index], -index),
        reverse=True,
    )
    for index in order[:leftover]:
        extras[index] += 1
    return [1 + extra for extra in extras]


def allocate_scene_frames(
    scenes: Sequence[Scene],
    audio: AudioManifest,
    fps: int,
) -> dict[str, int]:
    """Allocate exactly the narration frame count while preserving scene proportions."""

    segment_durations: dict[str, float] = {}
    for segment in audio.segments:
        if segment.scene_id in segment_durations:
            raise ValueError(f"Há mais de um segmento de áudio para {segment.scene_id}.")
        segment_durations[segment.scene_id] = segment.duration
    durations = [
        segment_durations.get(scene.scene_id, scene.estimated_duration_seconds) for scene in scenes
    ]
    counts = _frame_counts(durations, audio.duration, fps)
    return {scene.scene_id: count for scene, count in zip(scenes, counts, strict=True)}


def _selected_images(
    layout: ProjectLayout,
    scenes: Sequence[Scene],
    manifest: ImageManifest,
) -> dict[str, Path]:
    items = {item.image_id: item for item in manifest.items}
    selected: dict[str, Path] = {}
    for scene in scenes:
        item = items.get(scene.image_id)
        if item is None:
            raise ValueError(
                f"A cena {scene.scene_id} referencia imagem inexistente: {scene.image_id}."
            )
        version_number = item.active_version or item.approved_version
        if version_number is None:
            raise ValueError(f"A imagem {item.image_id} não possui versão ativa ou aprovada.")
        version = next((entry for entry in item.versions if entry.version == version_number), None)
        if version is None:
            raise ValueError(
                f"A versão {version_number} da imagem {item.image_id} não consta no manifesto."
            )
        if version.status == "rejected":
            raise ValueError(f"A versão ativa de {item.image_id} está rejeitada.")
        selected[scene.scene_id] = _project_file(
            layout.root,
            version.path,
            label=f"Imagem da cena {scene.scene_id}",
        )
    return selected


def _scene_plans(
    layout: ProjectLayout,
    scenes_manifest: SceneManifest,
    images_manifest: ImageManifest,
    audio: AudioManifest,
    config: RenderConfig,
) -> list[SceneRenderPlan]:
    scenes = sorted(scenes_manifest.scenes, key=lambda scene: scene.order)
    if not scenes:
        raise ValueError("O manifesto de cenas está vazio.")
    if len({scene.scene_id for scene in scenes}) != len(scenes):
        raise ValueError("O manifesto contém scene_id duplicado.")
    if len({scene.order for scene in scenes}) != len(scenes):
        raise ValueError("O manifesto contém ordem de cena duplicada.")
    frames = allocate_scene_frames(scenes, audio, config.fps)
    selected_images = _selected_images(
        layout,
        scenes,
        images_manifest,
    )
    section_roles: dict[str, str] = {}
    if layout.script.is_file():
        script = read_model(layout.script, ScriptDocument)
        section_roles = {section.section_id: section.role for section in script.sections}
    return [
        SceneRenderPlan(
            scene_id=scene.scene_id,
            order=scene.order,
            image_path=selected_images[scene.scene_id],
            frame_count=frames[scene.scene_id],
            blur_role=_scene_blur_role(scene, section_roles),
        )
        for scene in scenes
    ]


def _scene_blur_role(scene: Scene, section_roles: dict[str, str]) -> str:
    if scene.order == 1:
        return "opening"
    role = section_roles.get(scene.section_id, "").strip().casefold()
    function = scene.narrative_function.strip().casefold()
    if role == "opening":
        return "opening"
    if role == "biblical_narrative":
        return "scripture"
    if role == "application" or any(
        marker in function for marker in ("aplica", "resolver", "resolu", "payoff")
    ):
        return "payoff"
    if role == "reflection":
        return "reflection"
    return "closing"


def _blur_sigma(plan: SceneRenderPlan, config: RenderConfig) -> float:
    if config.background_blur_profile != "functional_caption_focus_v1":
        return 0.0
    return {
        "opening": config.background_blur_opening_sigma,
        "scripture": config.background_blur_scripture_sigma,
        "reflection": config.background_blur_reflection_sigma,
        "payoff": config.background_blur_payoff_sigma,
        "closing": config.background_blur_closing_sigma,
    }.get(plan.blur_role, config.background_blur_closing_sigma)


def _functional_blur_filter(plan: SceneRenderPlan, config: RenderConfig) -> str | None:
    if config.background_blur_profile != "functional_caption_focus_v1":
        return None
    scale = min(config.width, config.height) / 1080
    base_sigma = _blur_sigma(plan, config) * scale
    focus_sigma = base_sigma + config.caption_focus_blur_extra_sigma * scale
    top = round(config.height * config.caption_focus_top_ratio)
    height = max(2, round(config.height * config.caption_focus_height_ratio))
    height = min(height, config.height - top)
    background_filter = f"gblur=sigma={base_sigma:.3f}:steps=1" if base_sigma > 0 else "null"
    if config.caption_focus_blur_extra_sigma <= 0:
        return background_filter
    focus_filter = f"gblur=sigma={focus_sigma:.3f}:steps=1"
    return (
        "split=2[blur_background][caption_focus];"
        f"[blur_background]{background_filter}[blurred_background];"
        f"[caption_focus]crop={config.width}:{height}:0:{top},"
        f"{focus_filter}[blurred_caption_focus];"
        f"[blurred_background][blurred_caption_focus]overlay=0:{top}"
    )


def _codec_args(codec: str, *, preset: str, crf: int) -> list[str]:
    args = ["-c:v", codec]
    if codec in {"libx264", "libx264rgb"}:
        args.extend(["-preset", preset, "-crf", str(crf)])
    return args


def _visual_filter(plan: SceneRenderPlan, config: RenderConfig) -> str:
    duration = plan.frame_count / config.fps
    fade = min(config.transition_seconds, duration / 4)
    fade_out_start = max(0.0, duration - fade)
    if config.motion_style == "smooth_pendulum":
        supersample = max(1, config.motion_supersample)
        work_width = config.width * supersample
        work_height = config.height * supersample
        last_frame = max(1, plan.frame_count - 1)
        zoom_min = config.motion_zoom_min
        zoom_delta = config.motion_zoom_max - zoom_min
        pan = config.motion_pan_strength
        vertical_pan = pan * 0.3
        envelope = f"sin(PI*on/{last_frame})"
        zoom_expression = f"{zoom_min:.6f}+{zoom_delta:.6f}*({envelope})"
        x_expression = f"(iw-iw/zoom)/2*(1+{pan:.6f}*({envelope})*sin(2*PI*on/{last_frame}))"
        y_expression = (
            f"(ih-ih/zoom)/2*(1+{vertical_pan:.6f}*({envelope})*sin(2*PI*on/{last_frame}+PI/2))"
        )
        filters = [
            (
                f"scale={work_width}:{work_height}:"
                "force_original_aspect_ratio=increase:flags=lanczos"
            ),
            f"crop={work_width}:{work_height}",
            "format=yuv444p",
            (
                "zoompan="
                f"z='{zoom_expression}':x='{x_expression}':y='{y_expression}':"
                f"d={plan.frame_count}:s={config.width}x{config.height}:fps={config.fps}"
            ),
            "setsar=1",
        ]
    else:
        motion = max(0.0, config.motion_strength)
        filters = [
            (
                f"scale={config.width}:{config.height}:"
                "force_original_aspect_ratio=increase:flags=lanczos"
            ),
            f"crop={config.width}:{config.height}",
            (
                "zoompan="
                f"z='min(max(pzoom,1.0)+{motion:.8f},1.06)':"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d=1:s={config.width}x{config.height}:fps={config.fps}"
            ),
            "setsar=1",
        ]
    finish_filters: list[str] = []
    if fade > 0 and config.transition_style == "fade_black":
        finish_filters.extend(
            [
                f"fade=t=in:st=0:d={fade:.6f}",
                f"fade=t=out:st={fade_out_start:.6f}:d={fade:.6f}",
            ]
        )
    finish_filters.append("format=yuv420p")
    functional_blur = _functional_blur_filter(plan, config)
    if functional_blur is None:
        return ",".join([*filters, *finish_filters])
    return ",".join(filters) + "," + functional_blur + "," + ",".join(finish_filters)


def _cache_hash(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    input_hash = value.get("input_hash")
    return input_hash if isinstance(input_hash, str) else None


def _replace_output(temp: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp.replace(destination)


def _require_nonempty_file(path: Path, *, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} nÃ£o encontrado: {path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"{label} estÃ¡ vazio: {path}")


def _cached_artifact_is_current(path: Path, cache: Path, input_hash: str) -> bool:
    return path.is_file() and path.stat().st_size > 0 and _cache_hash(cache) == input_hash


def _validate_visual_inputs(
    plans: Sequence[SceneRenderPlan],
    config: RenderConfig,
) -> None:
    if not plans:
        raise ValueError("NÃ£o hÃ¡ planos de cena para renderizar.")
    if config.width <= 0 or config.height <= 0 or config.fps <= 0:
        raise ValueError("ResoluÃ§Ã£o e frame rate do render devem ser positivos.")
    for plan in plans:
        if plan.frame_count <= 0:
            raise ValueError(f"A cena {plan.scene_id} nÃ£o possui frames para renderizar.")
        _require_nonempty_file(plan.image_path, label=f"Imagem da cena {plan.scene_id}")


def _render_segment(
    layout: ProjectLayout,
    plan: SceneRenderPlan,
    config: RenderConfig,
    output: Path,
) -> None:
    _validate_visual_inputs([plan], config)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".part.mp4")
    temp.unlink(missing_ok=True)
    image_arg = relative_posix(plan.image_path, layout.root)
    args = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(config.fps),
        "-i",
        image_arg,
        "-vf",
        _visual_filter(plan, config),
        "-frames:v",
        str(plan.frame_count),
        "-fps_mode",
        "cfr",
        "-an",
        *_codec_args(
            config.video_codec,
            preset=config.final_preset,
            crf=max(0, config.final_crf - 4),
        ),
        "-pix_fmt",
        OUTPUT_PIXEL_FORMAT,
        "-movflags",
        "+faststart",
        str(temp.relative_to(layout.root).as_posix()),
    ]
    run_process(args, cwd=layout.root, timeout=3_600)
    _require_nonempty_file(temp, label="SaÃ­da visual temporÃ¡ria")
    _replace_output(temp, output)


def _ffconcat_quote(value: str) -> str:
    return value.replace("'", "'\\''")


def _concat_segments(layout: ProjectLayout, segments: Sequence[Path], output: Path) -> None:
    if not segments:
        raise ValueError("NÃ£o hÃ¡ segmentos para concatenar.")
    for segment in segments:
        _require_nonempty_file(segment, label="Segmento visual")
    concat_file = output.with_suffix(".ffconcat")
    entries = ["ffconcat version 1.0"]
    for segment in segments:
        relative = segment.relative_to(concat_file.parent).as_posix()
        entries.append(f"file '{_ffconcat_quote(relative)}'")
    write_text(concat_file, "\n".join(entries))

    temp = output.with_name(output.stem + ".part.mp4")
    temp.unlink(missing_ok=True)
    args = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        relative_posix(concat_file, layout.root),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        relative_posix(temp, layout.root),
    ]
    run_process(args, cwd=layout.root, timeout=600)
    _require_nonempty_file(temp, label="ConcatenaÃ§Ã£o temporÃ¡ria")
    _replace_output(temp, output)


def _cinematic_filter_graph(
    plans: Sequence[SceneRenderPlan],
    config: RenderConfig,
) -> tuple[str, str]:
    if len(plans) < 2:
        raise ValueError("A transiÃ§Ã£o cinematogrÃ¡fica exige ao menos duas cenas.")
    transition_frames = max(1, round(config.transition_seconds * config.fps))
    transition_duration = transition_frames / config.fps
    filters = [
        f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS[v{index}]" for index in range(len(plans))
    ]
    current = "v0"
    offset_frames = 0
    for boundary, plan in enumerate(plans[:-1]):
        offset_frames += plan.frame_count
        output = f"x{boundary + 1}"
        transition = _CINEMATIC_TRANSITIONS[boundary % len(_CINEMATIC_TRANSITIONS)]
        filters.append(
            f"[{current}][v{boundary + 1}]xfade=transition={transition}:"
            f"duration={transition_duration:.6f}:"
            f"offset={offset_frames / config.fps:.6f}[{output}]"
        )
        current = output
    return ";".join(filters), current


def _crossfade_segments(
    layout: ProjectLayout,
    segments: Sequence[Path],
    plans: Sequence[SceneRenderPlan],
    config: RenderConfig,
    output: Path,
) -> None:
    if len(segments) != len(plans):
        raise ValueError("A quantidade de segmentos nÃ£o corresponde aos planos de cena.")
    for segment in segments:
        _require_nonempty_file(segment, label="Segmento visual")
    filter_graph, output_label = _cinematic_filter_graph(plans, config)
    temp = output.with_name(output.stem + ".part.mp4")
    temp.unlink(missing_ok=True)
    args = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]
    for segment in segments:
        args.extend(["-i", relative_posix(segment, layout.root)])
    args.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            f"[{output_label}]",
            "-frames:v",
            str(sum(plan.frame_count for plan in plans)),
            "-r",
            str(config.fps),
            "-fps_mode",
            "cfr",
            "-an",
            *_codec_args(
                config.video_codec,
                preset=config.final_preset,
                crf=max(0, config.final_crf - 4),
            ),
            "-pix_fmt",
            OUTPUT_PIXEL_FORMAT,
            "-movflags",
            "+faststart",
            relative_posix(temp, layout.root),
        ]
    )
    run_process(args, cwd=layout.root, timeout=3_600)
    _require_nonempty_file(temp, label="SaÃ­da visual temporÃ¡ria")
    _replace_output(temp, output)


def _base_input_hash(plans: Sequence[SceneRenderPlan], config: RenderConfig) -> str:
    return sha256_value(
        {
            "algorithm": RENDER_ALGORITHM_VERSION,
            "plans": [
                {
                    "scene_id": plan.scene_id,
                    "order": plan.order,
                    "image_hash": sha256_file(plan.image_path),
                    "frames": plan.frame_count,
                    "blur_role": plan.blur_role,
                }
                for plan in plans
            ],
            "visual_config": {
                "width": config.width,
                "height": config.height,
                "fps": config.fps,
                "video_codec": config.video_codec,
                "base_crf": max(0, config.final_crf - 4),
                "preset": config.final_preset,
                "transition_seconds": config.transition_seconds,
                "transition_style": config.transition_style,
                "motion_strength": config.motion_strength,
                "motion_style": config.motion_style,
                "motion_zoom_min": config.motion_zoom_min,
                "motion_zoom_max": config.motion_zoom_max,
                "motion_pan_strength": config.motion_pan_strength,
                "motion_supersample": config.motion_supersample,
                "background_blur_profile": config.background_blur_profile,
                "background_blur_opening_sigma": config.background_blur_opening_sigma,
                "background_blur_scripture_sigma": config.background_blur_scripture_sigma,
                "background_blur_reflection_sigma": config.background_blur_reflection_sigma,
                "background_blur_payoff_sigma": config.background_blur_payoff_sigma,
                "background_blur_closing_sigma": config.background_blur_closing_sigma,
                "caption_focus_blur_extra_sigma": config.caption_focus_blur_extra_sigma,
                "caption_focus_top_ratio": config.caption_focus_top_ratio,
                "caption_focus_height_ratio": config.caption_focus_height_ratio,
            },
        }
    )


def _base_paths(layout: ProjectLayout, namespace: str) -> tuple[Path, Path]:
    prefix = "" if namespace == "final" else f"{namespace}-"
    return (
        layout.render_work / f"{prefix}base.mp4",
        layout.render_work / f"{prefix}base.json",
    )


def _build_visual_base(
    layout: ProjectLayout,
    plans: Sequence[SceneRenderPlan],
    config: RenderConfig,
    base_hash: str,
    *,
    force: bool,
    namespace: str = "final",
) -> tuple[Path, bool]:
    _validate_visual_inputs(plans, config)
    base_video, base_cache = _base_paths(layout, namespace)
    if not force and _cached_artifact_is_current(base_video, base_cache, base_hash):
        return base_video, True

    segment_dir = layout.render_work / (
        "segments" if namespace == "final" else f"{namespace}-segments"
    )
    segment_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    cinematic = (
        config.transition_style == "cinematic_mix"
        and config.transition_seconds > 0
        and len(plans) > 1
    )
    transition_frames = max(1, round(config.transition_seconds * config.fps))
    render_jobs: list[tuple[SceneRenderPlan, Path, Path, str]] = []
    for sequence, plan in enumerate(plans, start=1):
        render_plan = plan
        if cinematic and sequence < len(plans):
            render_plan = SceneRenderPlan(
                scene_id=plan.scene_id,
                order=plan.order,
                image_path=plan.image_path,
                frame_count=plan.frame_count + transition_frames,
                blur_role=plan.blur_role,
            )
        segment = segment_dir / f"scene-{sequence:03d}.mp4"
        cache = segment.with_suffix(".json")
        segment_hash = sha256_value(
            {
                "algorithm": RENDER_ALGORITHM_VERSION,
                "scene_id": plan.scene_id,
                "image_hash": sha256_file(plan.image_path),
                "timeline_frames": plan.frame_count,
                "render_frames": render_plan.frame_count,
                "filter": _visual_filter(render_plan, config),
                "codec": config.video_codec,
                "crf": max(0, config.final_crf - 4),
                "preset": config.final_preset,
            }
        )
        if force or not _cached_artifact_is_current(segment, cache, segment_hash):
            render_jobs.append((render_plan, segment, cache, segment_hash))
        segments.append(segment)

    def render_job(job: tuple[SceneRenderPlan, Path, Path, str]) -> None:
        render_plan, segment, cache, segment_hash = job
        _render_segment(layout, render_plan, config, segment)
        write_json(
            cache,
            {"input_hash": segment_hash, "scene_id": render_plan.scene_id},
        )

    if len(render_jobs) == 1:
        render_job(render_jobs[0])
    elif render_jobs:
        worker_count = min(MAX_SEGMENT_RENDER_WORKERS, len(render_jobs))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="pipeline-video-segment",
        ) as executor:
            list(executor.map(render_job, render_jobs))

    if cinematic:
        _crossfade_segments(layout, segments, plans, config, base_video)
    else:
        _concat_segments(layout, segments, base_video)
    write_json(base_cache, {"input_hash": base_hash, "segments": len(segments)})
    return base_video, False


def _escape_filter_path(value: str) -> str:
    return value.replace("\\", "/").replace("'", r"\'")


def _voice_filters(config: RenderConfig) -> list[str]:
    filters = [
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "asetpts=PTS-STARTPTS",
    ]
    if config.audio_mix_standard == "voice_music_loudness_v1":
        filters.append(
            "loudnorm="
            f"I={config.narration_target_lufs:.2f}:"
            f"LRA={config.audio_loudness_range_lu:.2f}:"
            f"TP={config.narration_true_peak_dbfs:.2f}"
        )
        filters.append("aresample=48000")
    if config.narration_gain_db:
        filters.append(f"volume={config.narration_gain_db:.2f}dB")
    return filters


def _narration_filter(config: RenderConfig) -> str:
    return f"[1:a]{','.join(_voice_filters(config))}[voice_audio]"


def _background_music_filter(audio: AudioManifest, config: RenderConfig) -> str:
    music_filters = [
        f"atrim=duration={audio.duration:.6f}",
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "asetpts=PTS-STARTPTS",
    ]
    if config.audio_mix_standard == "voice_music_loudness_v1":
        music_target_lufs = config.narration_target_lufs - config.background_music_gap_lu
        music_filters.append(
            "loudnorm="
            f"I={music_target_lufs:.2f}:"
            f"LRA={config.audio_loudness_range_lu:.2f}:"
            f"TP={config.background_music_true_peak_dbfs:.2f}"
        )
        music_filters.append("aresample=48000")
    if config.background_music_gain_db:
        music_filters.append(f"volume={config.background_music_gain_db:.2f}dB")
    if config.background_music_fade_in_seconds > 0:
        music_filters.append(f"afade=t=in:st=0:d={config.background_music_fade_in_seconds:.6f}")
    if config.background_music_fade_out_seconds > 0:
        fade_out_start = max(0.0, audio.duration - config.background_music_fade_out_seconds)
        music_filters.append(
            f"afade=t=out:st={fade_out_start:.6f}:d={config.background_music_fade_out_seconds:.6f}"
        )
    return (
        f"[1:a]{','.join(_voice_filters(config))}[voice];"
        f"[2:a]{','.join(music_filters)}[music];"
        "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:"
        "normalize=0[mixed_audio]"
    )


def _short_cover_video_filter(
    ass_filter: str,
    config: RenderConfig,
    cover_input_index: int,
) -> str:
    duration = config.short_cover_duration_seconds
    transition = min(config.short_cover_transition_seconds, duration)
    cover_filters = [
        (
            f"scale={config.width}:{config.height}:"
            "force_original_aspect_ratio=increase:flags=lanczos"
        ),
        f"crop={config.width}:{config.height}",
        "setsar=1",
        "format=rgba",
        "setpts=PTS-STARTPTS",
    ]
    if transition > 0:
        fade_start = max(0.0, duration - transition)
        cover_filters.append(f"fade=t=out:st={fade_start:.6f}:d={transition:.6f}:alpha=1")
    return (
        f"[0:v]{ass_filter}[captioned];"
        f"[{cover_input_index}:v]{','.join(cover_filters)}[cover];"
        f"[captioned][cover]overlay=0:0:enable='lt(t,{duration:.6f})':"
        "eof_action=pass[video_out]"
    )


def _render_final(
    layout: ProjectLayout,
    base_video: Path,
    narration: Path,
    ass_file: Path,
    audio: AudioManifest,
    config: RenderConfig,
    *,
    destination: Path | None = None,
    caption_canvas: tuple[int, int] | None = None,
    caption_font_file: Path | None = None,
    background_music: Path | None = None,
    short_cover: Path | None = None,
) -> None:
    destination = destination or layout.final_video
    _require_nonempty_file(base_video, label="Base visual")
    _require_nonempty_file(narration, label="NarraÃ§Ã£o consolidada")
    _require_nonempty_file(ass_file, label="Legenda ASS")
    if caption_font_file is not None:
        _require_nonempty_file(caption_font_file, label="Fonte da legenda")
    if background_music is not None:
        _require_nonempty_file(background_music, label="MÃºsica de fundo")
    if short_cover is not None:
        _require_nonempty_file(short_cover, label="Capa inicial do Short")
    if audio.duration <= 0 or audio.sample_rate <= 0:
        raise ValueError("DuraÃ§Ã£o e sample rate da narraÃ§Ã£o devem ser positivos.")
    temp = destination.with_name(destination.stem + ".part.mp4")
    temp.unlink(missing_ok=True)
    ass_relative = relative_posix(ass_file, layout.root)
    caption_width, caption_height = caption_canvas or (config.width, config.height)
    if caption_width <= 0 or caption_height <= 0:
        raise ValueError("As dimensÃµes da legenda devem ser positivas.")
    ass_filter = (
        f"ass=filename='{_escape_filter_path(ass_relative)}':"
        f"original_size={caption_width}x{caption_height}"
    )
    if caption_font_file is not None:
        fonts_dir = relative_posix(caption_font_file.parent, layout.root)
        ass_filter += f":fontsdir='{_escape_filter_path(fonts_dir)}'"
    args = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        relative_posix(base_video, layout.root),
        "-i",
        relative_posix(narration, layout.root),
    ]
    if background_music is not None:
        args.extend(
            [
                "-stream_loop",
                "-1",
                "-i",
                relative_posix(background_music, layout.root),
            ]
        )
    cover_input_index = 3 if background_music is not None else 2
    if short_cover is not None:
        args.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(config.fps),
                "-i",
                relative_posix(short_cover, layout.root),
            ]
        )
        filter_graph = _short_cover_video_filter(ass_filter, config, cover_input_index)
        filter_graph += ";" + (
            _background_music_filter(audio, config)
            if background_music is not None
            else _narration_filter(config)
        )
        args.extend(
            [
                "-filter_complex",
                filter_graph,
                "-map",
                "[video_out]",
                "-map",
                "[mixed_audio]" if background_music is not None else "[voice_audio]",
            ]
        )
    else:
        args.extend(["-map", "0:v:0"])
        if background_music is not None:
            args.extend(
                [
                    "-filter_complex",
                    _background_music_filter(audio, config),
                    "-map",
                    "[mixed_audio]",
                ]
            )
        else:
            args.extend(
                [
                    "-filter_complex",
                    _narration_filter(config),
                    "-map",
                    "[voice_audio]",
                ]
            )
    video_filter_args = [] if short_cover is not None else ["-vf", ass_filter]
    args.extend(
        [
            *video_filter_args,
            *_codec_args(
                config.video_codec,
                preset=config.final_preset,
                crf=config.final_crf,
            ),
            "-pix_fmt",
            OUTPUT_PIXEL_FORMAT,
            "-r",
            str(config.fps),
            "-fps_mode",
            "cfr",
            "-c:a",
            OUTPUT_AUDIO_CODEC,
            "-ar",
            str(audio.sample_rate),
            "-ac",
            str(OUTPUT_AUDIO_CHANNELS),
            "-b:a",
            config.audio_bitrate,
            "-movflags",
            "+faststart",
            "-shortest",
            relative_posix(temp, layout.root),
        ]
    )
    run_process(args, cwd=layout.root, timeout=7_200)
    _require_nonempty_file(temp, label="Render temporÃ¡rio")
    _replace_output(temp, destination)


def _stream_rate(stream: dict[str, Any]) -> float:
    value = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_duration(data: dict[str, Any]) -> float:
    value = data.get("format", {}).get("duration")
    if value is not None:
        return float(value)
    durations = [stream.get("duration") for stream in data.get("streams", [])]
    valid = [float(item) for item in durations if item is not None]
    if not valid:
        raise RuntimeError("FFprobe não informou a duração do arquivo.")
    return max(valid)


def validate_media_output(
    path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    expected_duration: float,
    require_audio: bool,
    expected_sample_rate: int = 48_000,
    duration_tolerance: float | None = None,
    raise_on_error: bool = True,
) -> dict[str, Any]:
    """Validate a rendered artifact using ffprobe, without decoding the full video."""

    if not path.is_file():
        raise FileNotFoundError(f"Vídeo renderizado não encontrado: {path}")
    data = ffprobe_json(path)
    video = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == "audio"),
        None,
    )
    duration = _probe_duration(data)
    tolerance = duration_tolerance if duration_tolerance is not None else 1 / fps + 0.025
    actual_fps = _stream_rate(video or {})

    checks: dict[str, bool] = {
        "has_video": video is not None,
        "width": video is not None and int(video.get("width", 0)) == width,
        "height": video is not None and int(video.get("height", 0)) == height,
        "fps": video is not None and abs(actual_fps - fps) <= 0.01,
        "codec_h264": video is not None and video.get("codec_name") == "h264",
        "pixel_format_yuv420p": video is not None and video.get("pix_fmt") == "yuv420p",
        "duration": abs(duration - expected_duration) <= tolerance,
    }
    if require_audio:
        checks.update(
            {
                "has_audio": audio is not None,
                "codec_aac": audio is not None and audio.get("codec_name") == "aac",
                "sample_rate": audio is not None
                and int(audio.get("sample_rate", 0)) == expected_sample_rate,
            }
        )

    failed = [name for name, passed in checks.items() if not passed]
    report: dict[str, Any] = {
        "valid": not failed,
        "path": str(path),
        "actual_width": int(video.get("width", 0)) if video else 0,
        "actual_height": int(video.get("height", 0)) if video else 0,
        "actual_fps": actual_fps,
        "actual_duration": duration,
        "duration_delta": abs(duration - expected_duration),
        "checks": checks,
        "failed_checks": failed,
    }
    if failed and raise_on_error:
        raise RuntimeError(f"Validação do render falhou em {path.name}: {', '.join(failed)}")
    return report


def validate_render_output(
    path: Path,
    config: RenderConfig,
    expected_duration: float,
    *,
    expected_sample_rate: int = 48_000,
    raise_on_error: bool = True,
) -> dict[str, Any]:
    return validate_media_output(
        path,
        width=config.width,
        height=config.height,
        fps=config.fps,
        expected_duration=expected_duration,
        require_audio=True,
        expected_sample_rate=expected_sample_rate,
        raise_on_error=raise_on_error,
    )


def _render_project_output(
    layout: ProjectLayout,
    *,
    force: bool,
) -> RenderResult:
    project = layout.load_manifest()
    config = project.config.render
    scenes = read_model(layout.scenes, SceneManifest)
    images = read_model(layout.images, ImageManifest)
    audio = read_model(layout.audio, AudioManifest)
    captions = read_model(layout.captions, CaptionManifest)
    narration = _project_file(layout.root, audio.narration_path, label="Narração consolidada")
    ass_file = _project_file(layout.root, captions.ass_path, label="Legenda ASS")
    caption_font_file = (
        _project_file(
            layout.root,
            project.config.captions.font_file,
            label="Fonte da legenda",
        )
        if project.config.captions.font_file
        else None
    )
    background_music = (
        _project_file(
            layout.root,
            config.background_music_path,
            label="Música de fundo",
        )
        if config.background_music_path
        else None
    )
    short_cover = (
        _project_file(layout.root, layout.thumbnail, label="Capa inicial do Short")
        if project.video_format == "short" and config.short_cover_enabled
        else None
    )
    plans = _scene_plans(
        layout,
        scenes,
        images,
        audio,
        config,
    )
    base_hash = _base_input_hash(plans, config)
    input_hash = sha256_value(
        {
            "algorithm": RENDER_ALGORITHM_VERSION,
            "image_source_policy": "active_final_candidate",
            "base_hash": base_hash,
            "narration_hash": sha256_file(narration),
            "captions_hash": sha256_file(ass_file),
            "caption_font_hash": sha256_file(caption_font_file) if caption_font_file else None,
            "background_music_hash": sha256_file(background_music) if background_music else None,
            "short_cover_hash": sha256_file(short_cover) if short_cover else None,
            "render_config": config.model_dump(mode="json"),
            "audio_sample_rate": audio.sample_rate,
            "audio_duration": audio.duration,
        }
    )
    base_video = layout.render_work / "review-candidate-base.mp4"

    if stage_is_current(layout, "render", input_hash, force=force):
        final_report = validate_render_output(
            layout.final_video,
            config,
            audio.duration,
            expected_sample_rate=audio.sample_rate,
            raise_on_error=False,
        )
        if final_report["valid"]:
            return RenderResult(
                base_video=base_video,
                final_video=layout.final_video,
                reused_base=True,
                final_validation=final_report,
            )

    invalidate(
        layout,
        stages=("validation",),
        approvals=("final_render",),
        note="O render foi atualizado.",
    )
    start_stage(layout, "render", input_hash, provider="ffmpeg-review-candidate")
    try:
        base_video, reused_base = _build_visual_base(
            layout,
            plans,
            config,
            base_hash,
            force=force,
            namespace="review-candidate",
        )
        expected_base_duration = sum(plan.frame_count for plan in plans) / config.fps
        validate_media_output(
            base_video,
            width=config.width,
            height=config.height,
            fps=config.fps,
            expected_duration=expected_base_duration,
            require_audio=False,
        )
        _render_final(
            layout,
            base_video,
            narration,
            ass_file,
            audio,
            config,
            caption_font_file=caption_font_file,
            background_music=background_music,
            short_cover=short_cover,
        )
        final_report = validate_render_output(
            layout.final_video,
            config,
            audio.duration,
            expected_sample_rate=audio.sample_rate,
        )
        artifacts = [
            relative_posix(base_video, layout.root),
            relative_posix(layout.final_video, layout.root),
        ]
        complete_stage(
            layout,
            "render",
            input_hash,
            artifacts,
            provider="ffmpeg-review-candidate",
            usage={
                "scenes": len(plans),
                "frames": sum(plan.frame_count for plan in plans),
                "base_reused": int(reused_base),
                "short_cover_frames": (
                    round(config.short_cover_duration_seconds * config.fps) if short_cover else 0
                ),
            },
        )
        return RenderResult(
            base_video=base_video,
            final_video=layout.final_video,
            reused_base=reused_base,
            final_validation=final_report,
        )
    except Exception as exc:
        fail_stage(layout, "render", exc)
        raise


def render_review_candidate(layout: ProjectLayout, *, force: bool = False) -> RenderResult:
    """Render full quality from active assets without recording a human approval."""
    result = _render_project_output(
        layout,
        force=force,
    )
    layout.append_event(
        {
            "event": "private_review_candidate_rendered",
            "path": relative_posix(result.final_video, layout.root),
            "human_approval_recorded": False,
        }
    )
    return result


render_project = render_review_candidate
render_video = render_review_candidate
