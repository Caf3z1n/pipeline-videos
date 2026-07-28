from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pipeline_videos.long_form_profile import (
    PROFILE_ID,
    LongFormCutoutEditPlan,
    preflight_project_edit_plan,
)
from pipeline_videos.models import AudioManifest, MusicManifest
from pipeline_videos.project import (
    CUTOUT_BACKGROUND_FILE,
    CUTOUT_CLICK_FILE,
    CUTOUT_FONT_FILE,
    CUTOUT_WHOOSH_FILE,
)
from pipeline_videos.state import complete_stage, fail_stage, stage_is_current, start_stage
from pipeline_videos.storage import (
    REPO_ROOT,
    ProjectLayout,
    read_json,
    read_model,
    write_json,
)
from pipeline_videos.utils import (
    relative_posix,
    sha256_file,
    sha256_value,
)

RENDERER_ROOT = REPO_ROOT / "renderers" / "biblical-cutout-explainer"


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Asset do render não encontrado: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _normalize_audio(
    source: Path,
    destination: Path,
    *,
    target_lufs: float,
    loudness_range_lu: float,
    true_peak_dbfs: float,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FileNotFoundError("FFmpeg não foi encontrado para normalizar o áudio do perfil.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp.wav")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                (
                    "aresample=48000,"
                    "aformat=channel_layouts=stereo,"
                    f"loudnorm=I={target_lufs:.2f}:"
                    f"LRA={loudness_range_lu:.2f}:"
                    f"TP={true_peak_dbfs:.2f},"
                    "aresample=48000"
                ),
                "-ar",
                "48000",
                "-ac",
                "2",
                str(temp),
            ],
            check=True,
        )
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _project_file(layout: ProjectLayout, value: str, *, label: str) -> Path:
    path = (layout.root / value).resolve()
    if not path.is_relative_to(layout.root.resolve()):
        raise ValueError(f"{label} escaparia da raiz do projeto.")
    if not path.is_file():
        raise FileNotFoundError(f"{label} não encontrado: {path}")
    return path


def _active_music(layout: ProjectLayout) -> Path | None:
    if not layout.music_manifest.is_file():
        return None
    music = read_model(layout.music_manifest, MusicManifest)
    if not music.path:
        return None
    return _project_file(layout, music.path, label="Música")


def prepare_cutout_render_bundle(layout: ProjectLayout) -> tuple[Path, dict[str, Any]]:
    project = layout.load_manifest()
    render_config = project.config.render
    report = preflight_project_edit_plan(layout)
    if not report.valid:
        raise ValueError("O render cutout exige preflight agregado válido.")
    plan = read_model(layout.long_form_edit_plan, LongFormCutoutEditPlan)
    audio = read_model(layout.audio, AudioManifest)
    inventory = read_json(layout.long_form_asset_inventory)
    if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
        raise ValueError("Inventário de assets inválido.")
    unresolved = [
        item.get("asset_id")
        for item in inventory["items"]
        if not isinstance(item, dict) or item.get("status") != "completed" or not item.get("path")
    ]
    if unresolved:
        raise ValueError(
            "Todos os assets precisam estar importados antes do render: "
            + ", ".join(str(item) for item in unresolved)
        )
    work = layout.render_work / "cutout-profile"
    public = work / "public"
    if public.exists():
        shutil.rmtree(public)
    public.mkdir(parents=True, exist_ok=True)

    background_destination = public / "backgrounds" / "warm-textured-parchment.png"
    font_destination = public / "fonts" / "Knewave-Regular.ttf"
    narration_destination = public / "audio" / "narration.wav"
    whoosh_destination = public / "audio" / "sfx" / "whoosh.wav"
    click_destination = public / "audio" / "sfx" / "click.wav"
    _copy_file(layout.root / CUTOUT_BACKGROUND_FILE, background_destination)
    _copy_file(layout.root / CUTOUT_FONT_FILE, font_destination)
    _normalize_audio(
        _project_file(layout, audio.narration_path, label="Narração"),
        narration_destination,
        target_lufs=render_config.narration_target_lufs,
        loudness_range_lu=render_config.audio_loudness_range_lu,
        true_peak_dbfs=render_config.narration_true_peak_dbfs,
    )
    _copy_file(layout.root / CUTOUT_WHOOSH_FILE, whoosh_destination)
    _copy_file(layout.root / CUTOUT_CLICK_FILE, click_destination)

    music_source = _active_music(layout)
    music_relative: str | None = None
    if music_source is not None:
        music_destination = public / "audio" / "music.wav"
        _normalize_audio(
            music_source,
            music_destination,
            target_lufs=(
                render_config.narration_target_lufs - render_config.background_music_gap_lu
            ),
            loudness_range_lu=render_config.audio_loudness_range_lu,
            true_peak_dbfs=render_config.background_music_true_peak_dbfs,
        )
        music_relative = relative_posix(music_destination, public)

    asset_paths: dict[str, str] = {}
    for item in inventory["items"]:
        asset_id = item["asset_id"]
        source = _project_file(layout, item["path"], label=f"Asset {asset_id}")
        destination = public / "assets" / f"{asset_id}.png"
        _copy_file(source, destination)
        asset_paths[asset_id] = relative_posix(destination, public)

    block_payloads: list[dict[str, Any]] = []
    for block in plan.blocks:
        payload = block.model_dump(mode="json")
        payload["asset_events"] = [
            {
                **event.model_dump(mode="json"),
                "src": asset_paths[event.asset_id],
            }
            for event in block.asset_events
        ]
        block_payloads.append(payload)
    sfx_paths = {
        "whoosh": relative_posix(whoosh_destination, public),
        "click": relative_posix(click_destination, public),
    }
    render_input = {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "duration_seconds": audio.duration,
        "background": relative_posix(background_destination, public),
        "font": relative_posix(font_destination, public),
        "narration": relative_posix(narration_destination, public),
        "music": music_relative,
        "music_volume": 1.0,
        "words": [word.model_dump(mode="json") for word in audio.words],
        "blocks": block_payloads,
        "sfx_events": [
            {
                **event.model_dump(mode="json"),
                "src": sfx_paths[event.kind],
            }
            for event in plan.sfx_events
        ],
    }
    write_json(public / "render-input.json", render_input)
    return public, render_input


def _remotion_executable() -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    executable = RENDERER_ROOT / "node_modules" / ".bin" / f"remotion{suffix}"
    if executable.is_file():
        return executable
    npm = shutil.which("npm")
    if npm is None:
        raise FileNotFoundError("npm não foi encontrado para instalar o renderer Remotion.")
    subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=RENDERER_ROOT,
        check=True,
    )
    if not executable.is_file():
        raise FileNotFoundError(f"Remotion não foi instalado em {executable}.")
    return executable


def render_cutout_profile(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> dict[str, Any]:
    project = layout.load_manifest()
    if (
        not project.config.long_form_profile.enabled
        or project.config.long_form_profile.profile_id != PROFILE_ID
    ):
        raise ValueError(f"O projeto não usa o perfil {PROFILE_ID}.")
    public, render_input = prepare_cutout_render_bundle(layout)
    plan = read_model(layout.long_form_edit_plan, LongFormCutoutEditPlan)
    inventory = read_json(layout.long_form_asset_inventory)
    input_hash = sha256_value(
        {
            "plan": plan,
            "inventory": inventory,
            "render_input": render_input,
            "renderer_package": sha256_file(RENDERER_ROOT / "package.json"),
            "renderer_lock": sha256_file(RENDERER_ROOT / "package-lock.json"),
            "renderer_source": [
                sha256_file(path) for path in sorted((RENDERER_ROOT / "src").glob("*.ts*"))
            ],
        }
    )
    if stage_is_current(layout, "render", input_hash, force=force):
        return read_json(layout.long_form_edit_preflight.parent / "cutout-render.json")
    start_stage(layout, "render", input_hash, provider="remotion-layered-cutout")
    output = layout.final_video
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".cutout.tmp.mp4")
    try:
        executable = _remotion_executable()
        subprocess.run(
            [
                str(executable),
                "render",
                "src/index.ts",
                "BiblicalCutout",
                str(temp),
                f"--public-dir={public}",
                "--codec=h264",
                "--crf=12",
                "--audio-codec=aac",
                "--pixel-format=yuv420p",
                "--overwrite",
            ],
            cwd=RENDERER_ROOT,
            check=True,
        )
        os.replace(temp, output)
        from pipeline_videos.render import validate_media_output

        audio = read_model(layout.audio, AudioManifest)
        media = validate_media_output(
            output,
            width=1920,
            height=1080,
            fps=30,
            expected_duration=audio.duration,
            require_audio=True,
            expected_sample_rate=48_000,
            duration_tolerance=0.12,
        )
        result = {
            "schema_version": "1.0",
            "profile_id": PROFILE_ID,
            "valid": True,
            "input_hash": input_hash,
            "output": relative_posix(output, layout.root),
            "sha256": sha256_file(output),
            "media": media,
        }
        report_path = layout.long_form_edit_preflight.parent / "cutout-render.json"
        write_json(report_path, result)
        complete_stage(
            layout,
            "render",
            input_hash,
            [
                relative_posix(output, layout.root),
                relative_posix(report_path, layout.root),
            ],
            provider="remotion-layered-cutout",
        )
        project = layout.load_manifest()
        project.effective_duration_seconds = media["actual_duration"]
        layout.save_manifest(project)
        return result
    except Exception as exc:
        temp.unlink(missing_ok=True)
        fail_stage(layout, "render", exc)
        raise
