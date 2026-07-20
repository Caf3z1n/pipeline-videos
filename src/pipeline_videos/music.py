from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pipeline_videos.models import MusicManifest
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import REPO_ROOT, ProjectLayout, read_model, write_json
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value, utc_now

DEFAULT_MUSIC_LIBRARY = REPO_ROOT / "music-library"
SUPPORTED_MUSIC_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})


def list_music_tracks(library_dir: Path | None = None) -> list[Path]:
    library = (library_dir or DEFAULT_MUSIC_LIBRARY).expanduser().resolve()
    if not library.is_dir():
        return []
    return sorted(
        (
            path.resolve()
            for path in library.iterdir()
            if path.is_file() and path.suffix.casefold() in SUPPORTED_MUSIC_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )


def _project_music_file(layout: ProjectLayout, relative_path: str) -> Path:
    path = (layout.root / relative_path).resolve()
    try:
        path.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError(f"A música está fora da pasta portátil do projeto: {path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Música selecionada não encontrada: {path}")
    return path


def _selection_hash(layout: ProjectLayout, path: Path | None) -> str:
    project = layout.load_manifest()
    render = project.config.render
    return sha256_value(
        {
            "enabled": project.config.music.enabled,
            "selection_mode": project.config.music.selection_mode,
            "music_hash": sha256_file(path) if path else None,
            "mix_standard": render.audio_mix_standard,
            "narration_target_lufs": render.narration_target_lufs,
            "background_music_gap_lu": render.background_music_gap_lu,
            "narration_true_peak_dbfs": render.narration_true_peak_dbfs,
            "background_music_true_peak_dbfs": render.background_music_true_peak_dbfs,
            "audio_loudness_range_lu": render.audio_loudness_range_lu,
            "narration_gain_db": render.narration_gain_db,
            "gain_db": render.background_music_gain_db,
            "fade_in_seconds": render.background_music_fade_in_seconds,
            "fade_out_seconds": render.background_music_fade_out_seconds,
        }
    )


def _write_selection(
    layout: ProjectLayout,
    *,
    path: Path | None,
    selection_mode: Literal["random", "existing", "disabled"],
    source_name: str | None,
    library_track_count: int,
) -> MusicManifest:
    render = layout.load_manifest().config.render
    uses_loudness_standard = render.audio_mix_standard == "voice_music_loudness_v1"
    selected = MusicManifest(
        selected_at=utc_now(),
        selection_mode=selection_mode,
        source_name=source_name,
        source_hash=sha256_file(path) if path else None,
        path=relative_posix(path, layout.root) if path else None,
        library_track_count=library_track_count,
        mix_standard=render.audio_mix_standard,
        narration_target_lufs=(render.narration_target_lufs if uses_loudness_standard else None),
        background_music_target_lufs=(
            render.narration_target_lufs - render.background_music_gap_lu
            if uses_loudness_standard
            else None
        ),
        background_music_gap_lu=(
            render.background_music_gap_lu if uses_loudness_standard else None
        ),
    )
    write_json(layout.music_manifest, selected)
    return selected


def _copy_track_to_project(
    layout: ProjectLayout,
    source: Path,
    *,
    destination_name: str | None = None,
) -> Path:
    destination = layout.music_assets / (destination_name or source.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    return destination


def import_music(layout: ProjectLayout, source: Path) -> MusicManifest:
    """Copy one explicit local track and register it without a temporary library."""

    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo de música não encontrado: {source}")
    if source.suffix.casefold() not in SUPPORTED_MUSIC_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_MUSIC_EXTENSIONS))
        raise ValueError(f"Formato de música não suportado: {source.suffix}. Use: {allowed}.")
    destination_name = source.name
    existing = layout.music_assets / destination_name
    source_hash = sha256_file(source)
    if existing.is_file() and sha256_file(existing) != source_hash:
        destination_name = f"{source.stem}-{source_hash[:8]}{source.suffix.casefold()}"
    destination = _copy_track_to_project(
        layout,
        source,
        destination_name=destination_name,
    )
    project = layout.load_manifest()
    project.config.music.enabled = True
    project.config.render.background_music_path = relative_posix(destination, layout.root)
    layout.save_manifest(project)
    return select_music(layout)


def select_music(
    layout: ProjectLayout,
    *,
    library_dir: Path | None = None,
    force: bool = False,
    chooser: Callable[[Sequence[Path]], Path] | None = None,
) -> MusicManifest:
    project = layout.load_manifest()
    configured_path = project.config.render.background_music_path

    if not project.config.music.enabled:
        input_hash = _selection_hash(layout, None)
        if stage_is_current(layout, "music", input_hash, force=force):
            return read_model(layout.music_manifest, MusicManifest)
        start_stage(layout, "music", input_hash, provider="disabled")
        try:
            if configured_path:
                project = layout.load_manifest()
                project.config.render.background_music_path = None
                layout.save_manifest(project)
                invalidate(
                    layout,
                    stages=("render", "validation"),
                    approvals=("final_render",),
                    note="A música de fundo foi desativada.",
                )
            selected = _write_selection(
                layout,
                path=None,
                selection_mode="disabled",
                source_name=None,
                library_track_count=0,
            )
            complete_stage(
                layout,
                "music",
                input_hash,
                [relative_posix(layout.music_manifest, layout.root)],
                provider="disabled",
                note="Música de fundo desativada neste projeto.",
            )
            return selected
        except Exception as exc:
            fail_stage(layout, "music", exc)
            raise

    may_register_configured = True
    if not force and layout.music_manifest.is_file():
        existing = read_model(layout.music_manifest, MusicManifest)
        if existing.path and existing.path == configured_path:
            try:
                selected_path = _project_music_file(layout, existing.path)
            except FileNotFoundError:
                may_register_configured = False
            else:
                may_register_configured = sha256_file(selected_path) == existing.source_hash
            if may_register_configured:
                input_hash = _selection_hash(layout, selected_path)
                if stage_is_current(layout, "music", input_hash):
                    return existing

    if not force and configured_path and may_register_configured:
        selected_path = _project_music_file(layout, configured_path)
        input_hash = _selection_hash(layout, selected_path)
        start_stage(layout, "music", input_hash, provider="existing-project-track")
        try:
            selected = _write_selection(
                layout,
                path=selected_path,
                selection_mode="existing",
                source_name=selected_path.name,
                library_track_count=0,
            )
            invalidate(
                layout,
                stages=("validation",),
                note="A faixa existente foi registrada na nova etapa de música.",
            )
            complete_stage(
                layout,
                "music",
                input_hash,
                [
                    relative_posix(layout.music_manifest, layout.root),
                    relative_posix(selected_path, layout.root),
                ],
                provider="existing-project-track",
                usage={"tracks_available": 0},
                note=f"Faixa existente registrada: {selected_path.name}",
            )
            return selected
        except Exception as exc:
            fail_stage(layout, "music", exc)
            raise

    tracks = list_music_tracks(library_dir)
    library = (library_dir or DEFAULT_MUSIC_LIBRARY).expanduser().resolve()
    library_hash = sha256_value([{"name": path.name, "hash": sha256_file(path)} for path in tracks])
    if not tracks:
        input_hash = sha256_value({"library": str(library), "tracks": library_hash})
        start_stage(layout, "music", input_hash, provider="local-random-library")
        error = FileNotFoundError(
            f"Nenhuma música compatível foi encontrada em {library}. "
            "Adicione arquivos MP3, WAV, M4A, AAC, FLAC ou OGG."
        )
        fail_stage(layout, "music", error)
        raise error

    choices = tracks
    previous_hash: str | None = None
    if configured_path:
        try:
            previous_hash = sha256_file(_project_music_file(layout, configured_path))
        except FileNotFoundError:
            previous_hash = None
    if force and previous_hash and len(tracks) > 1:
        alternatives = [path for path in tracks if sha256_file(path) != previous_hash]
        if alternatives:
            choices = alternatives

    source = (chooser or secrets.choice)(choices)
    source = source.resolve()
    input_hash = _selection_hash(layout, source)
    start_stage(layout, "music", input_hash, provider="local-random-library")
    try:
        destination = _copy_track_to_project(layout, source)
        old_path = configured_path
        old_hash = previous_hash
        project = layout.load_manifest()
        project.config.render.background_music_path = relative_posix(destination, layout.root)
        layout.save_manifest(project)
        selected = _write_selection(
            layout,
            path=destination,
            selection_mode="random",
            source_name=source.name,
            library_track_count=len(tracks),
        )
        if old_path != selected.path or old_hash != selected.source_hash:
            invalidate(
                layout,
                stages=("render", "validation"),
                approvals=("final_render",),
                note=f"A música de fundo foi selecionada: {source.name}",
            )
        complete_stage(
            layout,
            "music",
            input_hash,
            [
                relative_posix(layout.music_manifest, layout.root),
                relative_posix(destination, layout.root),
            ],
            provider="local-random-library",
            usage={"tracks_available": len(tracks)},
            note=f"Faixa sorteada: {source.name}",
        )
        return selected
    except Exception as exc:
        fail_stage(layout, "music", exc)
        raise
