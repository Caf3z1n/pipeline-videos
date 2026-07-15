from __future__ import annotations

import shutil
import subprocess
import wave

import pytest
from PIL import Image

from pipeline_videos import render
from pipeline_videos.captions import generate_captions
from pipeline_videos.images import approve_image
from pipeline_videos.models import (
    AudioManifest,
    AudioSegment,
    ImageItem,
    ImageManifest,
    ImageVersion,
    ProjectConfig,
    ProjectManifest,
    PromptBlocks,
    RenderConfig,
    Scene,
    SceneManifest,
    WordTiming,
)
from pipeline_videos.state import initial_approvals, initial_stage_records
from pipeline_videos.storage import ProjectLayout, write_json
from pipeline_videos.utils import sha256_value, utc_now


def _probe_payload(
    *,
    width: int = 320,
    height: int = 180,
    fps: str = "30/1",
    pixel_format: str = "yuv420p",
    duration: str = "0.400000",
) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": pixel_format,
                "width": width,
                "height": height,
                "avg_frame_rate": fps,
                "duration": duration,
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "duration": duration,
            },
        ],
        "format": {"duration": duration},
    }


def test_ffprobe_validation_accepts_expected_h264_aac(monkeypatch, tmp_path) -> None:
    output = tmp_path / "fixture.mp4"
    output.touch()
    monkeypatch.setattr(render, "ffprobe_json", lambda _path: _probe_payload())

    report = render.validate_media_output(
        output,
        width=320,
        height=180,
        fps=30,
        expected_duration=0.4,
        require_audio=True,
    )

    assert report["valid"] is True
    assert report["failed_checks"] == []


def test_ffprobe_validation_reports_wrong_pixel_format(monkeypatch, tmp_path) -> None:
    output = tmp_path / "fixture.mp4"
    output.touch()
    monkeypatch.setattr(
        render,
        "ffprobe_json",
        lambda _path: _probe_payload(pixel_format="yuv444p"),
    )

    report = render.validate_media_output(
        output,
        width=320,
        height=180,
        fps=30,
        expected_duration=0.4,
        require_audio=True,
        raise_on_error=False,
    )

    assert report["valid"] is False
    assert "pixel_format_yuv420p" in report["failed_checks"]
    with pytest.raises(RuntimeError, match="pixel_format_yuv420p"):
        render.validate_media_output(
            output,
            width=320,
            height=180,
            fps=30,
            expected_duration=0.4,
            require_audio=True,
        )


def test_render_output_uses_preview_dimensions(monkeypatch, tmp_path) -> None:
    output = tmp_path / "preview.mp4"
    output.touch()
    config = RenderConfig(
        width=640,
        height=360,
        preview_width=320,
        preview_height=180,
        fps=30,
    )
    monkeypatch.setattr(render, "ffprobe_json", lambda _path: _probe_payload())

    report = render.validate_render_output(output, config, 0.4, preview=True)

    assert report["valid"] is True
    assert report["actual_width"] == 320


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe não estão disponíveis",
)
def test_validation_with_a_real_short_fixture(tmp_path) -> None:
    output = tmp_path / "short.mp4"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=30:d=0.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-shortest",
            str(output),
        ],
        check=True,
        capture_output=True,
        shell=False,
    )

    report = render.validate_media_output(
        output,
        width=320,
        height=180,
        fps=30,
        expected_duration=0.4,
        require_audio=True,
    )

    assert report["valid"] is True


def test_frame_allocator_matches_exact_timeline_length() -> None:
    counts = render._frame_counts([1.0, 2.0, 1.0], total_duration=4.0, fps=30)

    assert sum(counts) == 120
    assert all(count > 0 for count in counts)
    assert counts[1] > counts[0]


def test_smooth_pendulum_filter_uses_supersampling_and_eased_zoom(tmp_path) -> None:
    plan = render.SceneRenderPlan(
        scene_id="scene-001",
        order=1,
        image_path=tmp_path / "image.png",
        frame_count=91,
    )
    config = RenderConfig(
        width=540,
        height=960,
        fps=30,
        motion_style="smooth_pendulum",
        motion_zoom_min=1.015,
        motion_zoom_max=1.055,
        motion_pan_strength=0.28,
        motion_supersample=3,
    )

    filter_graph = render._visual_filter(plan, config)

    assert "scale=1620:2880" in filter_graph
    assert "sin(PI*on/90)" in filter_graph
    assert "sin(2*PI*on/90)" in filter_graph
    assert "pzoom" not in filter_graph


def test_cinematic_mix_uses_motivated_crossfades_without_black_fades(tmp_path) -> None:
    plans = [
        render.SceneRenderPlan(
            scene_id=f"scene-{index:03d}",
            order=index,
            image_path=tmp_path / f"image-{index:03d}.png",
            frame_count=30,
        )
        for index in range(1, 4)
    ]
    config = RenderConfig(
        width=540,
        height=960,
        fps=30,
        transition_seconds=0.3,
        transition_style="cinematic_mix",
        motion_style="smooth_pendulum",
    )

    visual_filter = render._visual_filter(plans[0], config)
    filter_graph, output_label = render._cinematic_filter_graph(plans, config)

    assert "fade=t=" not in visual_filter
    assert "transition=fadeslow:duration=0.300000:offset=1.000000" in filter_graph
    assert "transition=smoothleft:duration=0.300000:offset=2.000000" in filter_graph
    assert output_label == "x2"


def test_background_music_filter_keeps_voice_primary_and_fades_music() -> None:
    audio = AudioManifest(
        generated_at=utc_now(),
        provider="mock",
        sample_rate=48_000,
        narration_path="audio/narration.wav",
        duration=10.0,
        segments=[],
        words=[],
    )
    config = RenderConfig(
        background_music_path="audio/music/test.mp3",
        background_music_gain_db=-12.0,
        background_music_fade_in_seconds=2.0,
        background_music_fade_out_seconds=3.0,
    )

    filter_graph = render._background_music_filter(audio, config)

    assert "volume=-12.00dB" in filter_graph
    assert "afade=t=in:st=0:d=2.000000" in filter_graph
    assert "afade=t=out:st=7.000000:d=3.000000" in filter_graph
    assert "amix=inputs=2:duration=first" in filter_graph


def test_short_cover_filter_overlays_after_captions_and_fades_out() -> None:
    config = RenderConfig(
        width=1080,
        height=1920,
        short_cover_duration_seconds=0.75,
        short_cover_transition_seconds=0.18,
    )

    filter_graph = render._short_cover_video_filter("ass=caption.ass", config, 3)

    assert "[0:v]ass=caption.ass[captioned]" in filter_graph
    assert "[3:v]scale=1080:1920" in filter_graph
    assert "fade=t=out:st=0.570000:d=0.180000:alpha=1" in filter_graph
    assert "overlay=0:0:enable='lt(t,0.750000)'" in filter_graph


def _render_plans(tmp_path, count: int = 3) -> list[render.SceneRenderPlan]:
    plans = []
    for index in range(1, count + 1):
        image_path = tmp_path / f"image-{index:03d}.png"
        image_path.write_bytes(f"image-{index}".encode())
        plans.append(
            render.SceneRenderPlan(
                scene_id=f"scene-{index:03d}",
                order=index,
                image_path=image_path,
                frame_count=30,
            )
        )
    return plans


def test_visual_base_limits_parallel_segment_jobs_and_preserves_order(
    monkeypatch,
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    plans = _render_plans(tmp_path)
    config = RenderConfig(
        width=320,
        height=180,
        fps=30,
        transition_style="fade_black",
        final_preset="ultrafast",
    )
    workers = []
    rendered = []
    concatenated = []

    class InlineExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            workers.append((max_workers, thread_name_prefix))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def map(self, function, jobs):
            return [function(job) for job in jobs]

    def fake_render_segment(_layout, plan, _config, output):
        rendered.append(plan.scene_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(plan.scene_id.encode())

    def fake_concat(_layout, segments, output):
        concatenated.extend(path.name for path in segments)
        output.write_bytes(b"visual-base")

    monkeypatch.setattr(render, "ThreadPoolExecutor", InlineExecutor)
    monkeypatch.setattr(render, "_render_segment", fake_render_segment)
    monkeypatch.setattr(render, "_concat_segments", fake_concat)
    base_hash = render._base_input_hash(plans, config)

    base_video, reused = render._build_visual_base(
        layout,
        plans,
        config,
        base_hash,
        force=False,
    )
    repeated_video, repeated_reused = render._build_visual_base(
        layout,
        plans,
        config,
        base_hash,
        force=False,
    )

    assert reused is False
    assert repeated_reused is True
    assert repeated_video == base_video
    assert workers == [(2, "pipeline-video-segment")]
    assert rendered == ["scene-001", "scene-002", "scene-003"]
    assert concatenated == ["scene-001.mp4", "scene-002.mp4", "scene-003.mp4"]
    assert all(
        (layout.render_work / "segments" / f"scene-{index:03d}.json").is_file()
        for index in range(1, 4)
    )

    rendered.clear()
    concatenated.clear()
    base_video.unlink()
    (layout.render_work / "base.json").unlink()
    (layout.render_work / "segments" / "scene-002.mp4").unlink()

    rebuilt_video, rebuilt_reused = render._build_visual_base(
        layout,
        plans,
        config,
        base_hash,
        force=False,
    )

    assert rebuilt_video == base_video
    assert rebuilt_reused is False
    assert rendered == ["scene-002"]
    assert concatenated == ["scene-001.mp4", "scene-002.mp4", "scene-003.mp4"]
    assert workers == [(2, "pipeline-video-segment")]


def test_visual_base_reuses_equivalent_peer_namespace_with_independent_copy(
    monkeypatch,
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    plans = _render_plans(tmp_path, count=1)
    config = RenderConfig(width=320, height=180, fps=30)
    base_hash = render._base_input_hash(plans, config)
    final_base = layout.render_work / "base.mp4"
    final_base.write_bytes(b"shared-visual-base")
    write_json(layout.render_work / "base.json", {"input_hash": base_hash, "segments": 1})
    monkeypatch.setattr(
        render,
        "_render_segment",
        lambda *_args, **_kwargs: pytest.fail("nÃ£o deveria renderizar segmentos"),
    )

    draft_base, reused = render._build_visual_base(
        layout,
        plans,
        config,
        base_hash,
        force=False,
        namespace="draft",
    )

    assert reused is True
    assert draft_base == layout.render_work / "draft-base.mp4"
    assert draft_base.read_bytes() == b"shared-visual-base"
    final_base.write_bytes(b"changed-after-copy")
    assert draft_base.read_bytes() == b"shared-visual-base"


def test_preview_fast_path_copies_equivalent_final_without_hardlink(
    monkeypatch,
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    layout.final_video.write_bytes(b"equivalent-final")
    config = RenderConfig(
        width=320,
        height=180,
        preview_width=320,
        preview_height=180,
        final_crf=20,
        preview_crf=20,
        final_preset="fast",
        preview_preset="fast",
        audio_bitrate="192k",
    )
    monkeypatch.setattr(
        render,
        "run_process",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg nÃ£o deveria ser chamado"),
    )

    reused = render._render_preview(
        layout,
        config,
        final_audio_sample_rate=48_000,
    )

    assert reused is True
    assert layout.preview_video.read_bytes() == b"equivalent-final"
    layout.final_video.write_bytes(b"mutated-final")
    assert layout.preview_video.read_bytes() == b"equivalent-final"


def test_preview_transcodes_when_dimensions_differ_and_preserves_audio_quality(
    monkeypatch, tmp_path
) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    layout.final_video.write_bytes(b"final")
    config = RenderConfig(
        width=320,
        height=180,
        preview_width=160,
        preview_height=90,
        final_crf=20,
        preview_crf=20,
        final_preset="fast",
        preview_preset="fast",
        audio_bitrate="192k",
    )
    calls = []

    def fake_run(args, *, cwd, timeout):
        calls.append((args, timeout))
        (cwd / args[-1]).write_bytes(b"transcoded-preview")

    monkeypatch.setattr(render, "run_process", fake_run)

    reused = render._render_preview(
        layout,
        config,
        final_audio_sample_rate=48_000,
    )

    assert reused is False
    assert layout.preview_video.read_bytes() == b"transcoded-preview"
    assert len(calls) == 1
    assert calls[0][0][calls[0][0].index("-b:a") + 1] == "192k"


def test_preview_rejects_missing_final_before_starting_ffmpeg(monkeypatch, tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    monkeypatch.setattr(
        render,
        "run_process",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg nÃ£o deveria ser chamado"),
    )

    with pytest.raises(FileNotFoundError, match="Render final"):
        render._render_preview(
            layout,
            RenderConfig(),
            final_audio_sample_rate=48_000,
        )


def _scene(scene_id: str, order: int, image_id: str, narration: str) -> Scene:
    return Scene(
        scene_id=scene_id,
        order=order,
        section_id="section-001",
        narration=narration,
        estimated_duration_seconds=0.3,
        emotional_intent="sereno",
        environment="paisagem",
        shot="plano geral",
        narrative_function="acompanhar a narração",
        prompt=PromptBlocks(
            style_profile_id="style",
            palette_id="palette",
            continuity=[],
            scene="paisagem",
            composition="plano geral",
            emotion="sereno",
            negative=[],
            assembled_prompt="paisagem em plano geral",
        ),
        image_id=image_id,
    )


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg/FFprobe não estão disponíveis",
)
def test_short_end_to_end_render_reuses_visual_base_when_only_caption_style_changes(
    tmp_path,
) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_directories()
    now = utc_now()
    config = ProjectConfig(
        render=RenderConfig(
            width=320,
            height=180,
            fps=10,
            preview_width=160,
            preview_height=90,
            final_crf=28,
            final_preset="ultrafast",
            preview_crf=32,
            preview_preset="ultrafast",
            transition_seconds=0.05,
            transition_style="cinematic_mix",
            motion_strength=0.001,
            background_music_path="audio/narration.wav",
            background_music_gain_db=-18.0,
            short_cover_duration_seconds=0.2,
            short_cover_transition_seconds=0.05,
        )
    )
    config.captions.font_size = 20
    config.captions.font_file = None
    config.captions.margin_left = 20
    config.captions.margin_right = 20
    config.captions.margin_bottom = 18
    write_json(
        layout.manifest,
        ProjectManifest(
            project_id="project",
            title="Fixture",
            created_at=now,
            updated_at=now,
            video_format="short",
            config=config,
            stages=initial_stage_records(),
            approvals=initial_approvals(),
        ),
    )

    image_paths = [layout.image_assets / "image-001.png", layout.image_assets / "image-002.png"]
    for path, color in zip(image_paths, ["#20304A", "#A8793B"], strict=True):
        Image.new("RGB", (400, 240), color).save(path)
    layout.thumbnail.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), "#E5484D").save(layout.thumbnail)
    scenes = [
        _scene("scene-001", 1, "image-001", "A graça"),
        _scene("scene-002", 2, "image-002", "permanece sempre."),
    ]
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=0.6,
            scenes=scenes,
        ),
    )
    items = []
    for number, path in enumerate(image_paths, start=1):
        image_id = f"image-{number:03d}"
        items.append(
            ImageItem(
                image_id=image_id,
                scene_id=f"scene-{number:03d}",
                prompt="fixture",
                prompt_hash=sha256_value("fixture"),
                active_version=1,
                versions=[
                    ImageVersion(
                        version=1,
                        path=path.relative_to(layout.root).as_posix(),
                        provider="fixture",
                        prompt_hash=sha256_value("fixture"),
                        created_at=now,
                    )
                ],
            )
        )
    write_json(
        layout.images,
        ImageManifest(
            generated_at=now,
            style_profile_id="style",
            palette_id="palette",
            items=items,
        ),
    )

    layout.narration_audio.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(layout.narration_audio), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(b"\x00\x00\x00\x00" * round(0.6 * 48_000))
    words = [
        WordTiming(index=0, word="A", start=0.00, end=0.12, scene_id="scene-001"),
        WordTiming(index=1, word="graça", start=0.12, end=0.28, scene_id="scene-001"),
        WordTiming(index=2, word="permanece", start=0.30, end=0.48, scene_id="scene-002"),
        WordTiming(index=3, word="sempre.", start=0.48, end=0.59, scene_id="scene-002"),
    ]
    segments = [
        AudioSegment(
            scene_id="scene-001",
            version=1,
            text="A graça",
            input_hash="one",
            provider="mock",
            path="audio/segments/scene-001.wav",
            start=0.0,
            end=0.3,
            duration=0.3,
            alignment_source="simulated",
            words=words[:2],
        ),
        AudioSegment(
            scene_id="scene-002",
            version=1,
            text="permanece sempre.",
            input_hash="two",
            provider="mock",
            path="audio/segments/scene-002.wav",
            start=0.3,
            end=0.6,
            duration=0.3,
            alignment_source="simulated",
            words=words[2:],
        ),
    ]
    write_json(
        layout.audio,
        AudioManifest(
            generated_at=now,
            provider="mock",
            sample_rate=48_000,
            narration_path=layout.narration_audio.relative_to(layout.root).as_posix(),
            duration=0.6,
            segments=segments,
            words=words,
        ),
    )

    generate_captions(layout)
    draft = render.render_draft_preview(layout)
    assert draft.preview_video.is_file()
    assert not layout.final_video.exists()
    assert not layout.preview_video.exists()
    assert not (layout.render_work / "base.mp4").exists()

    approve_image(layout, "scene-001", decided_by="teste")
    approve_image(layout, "scene-002", decided_by="teste")
    first = render.render_project(layout)
    base_mtime = first.base_video.stat().st_mtime_ns

    manifest = layout.load_manifest()
    manifest.config.captions.highlight_color = "#70D6FF"
    layout.save_manifest(manifest)
    generate_captions(layout)
    second = render.render_project(layout)

    assert first.final_validation["valid"] is True
    assert first.preview_validation["valid"] is True
    assert second.reused_base is True
    assert second.base_video.stat().st_mtime_ns == base_mtime
