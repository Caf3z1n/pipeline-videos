from __future__ import annotations

from pathlib import Path

import pytest

from pipeline_videos.models import MusicManifest, StageStatus
from pipeline_videos.music import import_music, list_music_tracks, select_music
from pipeline_videos.project import create_project
from pipeline_videos.storage import read_model


def _new_project(tmp_path: Path):
    return create_project(
        "video-com-musica",
        "Vídeo com música",
        projects_dir=tmp_path / "projects",
        target_duration_seconds=60,
        video_format="short",
    )


def test_music_stage_randomly_selects_copies_and_reuses_track(tmp_path) -> None:
    layout = _new_project(tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    first_track = library / "primeira.mp3"
    second_track = library / "segunda.wav"
    first_track.write_bytes(b"primeira-faixa")
    second_track.write_bytes(b"segunda-faixa")

    selected = select_music(
        layout,
        library_dir=library,
        chooser=lambda tracks: next(track for track in tracks if track.name == "segunda.wav"),
    )

    assert selected.selection_mode == "random"
    assert selected.source_name == "segunda.wav"
    assert selected.library_track_count == 2
    assert selected.path == "audio/music/segunda.wav"
    assert selected.mix_standard == "voice_music_loudness_v1"
    assert selected.narration_target_lufs == -18.0
    assert selected.background_music_target_lufs == -34.0
    assert selected.background_music_gap_lu == 16.0
    assert (layout.root / selected.path).read_bytes() == b"segunda-faixa"
    manifest = layout.load_manifest()
    assert manifest.config.render.background_music_path == selected.path
    assert manifest.stages["music"].status == StageStatus.COMPLETED
    assert manifest.stages["music"].attempts == 1
    assert manifest.stages["render"].status == StageStatus.STALE

    reused = select_music(
        layout,
        library_dir=library,
        chooser=lambda tracks: (_ for _ in ()).throw(AssertionError("não deveria sortear")),
    )

    assert reused == selected
    assert layout.load_manifest().stages["music"].attempts == 1

    changed = select_music(
        layout,
        library_dir=library,
        force=True,
        chooser=lambda tracks: tracks[0],
    )

    assert changed.source_name == "primeira.mp3"
    assert changed.path == "audio/music/primeira.mp3"
    assert (layout.root / "audio/music/segunda.wav").is_file()
    assert layout.load_manifest().stages["music"].attempts == 2


def test_music_stage_registers_an_existing_project_track_without_replacing_it(tmp_path) -> None:
    layout = _new_project(tmp_path)
    track = layout.music_assets / "escolhida.mp3"
    track.write_bytes(b"faixa-ja-escolhida")
    project = layout.load_manifest()
    project.config.render.background_music_path = "audio/music/escolhida.mp3"
    layout.save_manifest(project)

    selected = select_music(layout, library_dir=tmp_path / "empty")

    assert selected.selection_mode == "existing"
    assert selected.path == "audio/music/escolhida.mp3"
    assert track.read_bytes() == b"faixa-ja-escolhida"
    saved = read_model(layout.music_manifest, MusicManifest)
    assert saved == selected
    assert layout.load_manifest().stages["music"].status == StageStatus.COMPLETED


def test_music_import_copies_one_explicit_file_without_temporary_library(tmp_path) -> None:
    layout = _new_project(tmp_path)
    source = tmp_path / "Leaning.mp3"
    source.write_bytes(b"faixa-escolhida")

    selected = import_music(layout, source)

    assert selected.selection_mode == "existing"
    assert selected.source_name == "Leaning.mp3"
    assert selected.path == "audio/music/Leaning.mp3"
    assert (layout.root / selected.path).read_bytes() == b"faixa-escolhida"
    assert layout.load_manifest().config.render.background_music_path == selected.path


def test_music_stage_reports_an_empty_library(tmp_path) -> None:
    layout = _new_project(tmp_path)
    library = tmp_path / "empty-library"
    library.mkdir()

    with pytest.raises(FileNotFoundError, match="Nenhuma música compatível"):
        select_music(layout, library_dir=library)

    assert layout.load_manifest().stages["music"].status == StageStatus.FAILED


def test_music_library_only_lists_supported_audio_files(tmp_path) -> None:
    (tmp_path / "track.MP3").write_bytes(b"mp3")
    (tmp_path / "track.flac").write_bytes(b"flac")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [path.name for path in list_music_tracks(tmp_path)] == ["track.flac", "track.MP3"]
