from __future__ import annotations

import json
import re
import shutil
import tempfile
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pipeline_videos.audio import (
    TARGET_SAMPLE_RATE,
    LocalWord,
    _comment_response_span,
    _convert_to_wav_48k,
    _insert_pcm_silence,
    _validated_wav_frames,
)
from pipeline_videos.models import AudioManifest, AudioSegment, Scene, SceneManifest, WordTiming
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    wait_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json
from pipeline_videos.utils import (
    executable,
    media_duration,
    relative_posix,
    run_process,
    sha256_file,
    sha256_value,
    utc_now,
)

LOCAL_ALIGNER_VERSION = "whisperx-3.8.6"
_WORD_RE = re.compile(r"\S+", flags=re.UNICODE)

type AlignmentRunner = Callable[[Path, str, float], Sequence[dict[str, Any]]]


def _scenes(layout: ProjectLayout) -> tuple[SceneManifest, list[Scene]]:
    manifest = read_model(layout.scenes, SceneManifest)
    scenes = sorted(manifest.scenes, key=lambda item: item.order)
    if not scenes:
        raise ValueError("O manifesto de cenas não contém cenas para alinhar.")
    return manifest, scenes


def _transcript(scenes: Sequence[Scene]) -> str:
    return " ".join(" ".join(scene.narration.split()) for scene in scenes)


def _queue_hash(scene_manifest: SceneManifest, scenes: Sequence[Scene]) -> str:
    return sha256_value(
        {
            "script_version": scene_manifest.script_version,
            "scenes": [{"scene_id": scene.scene_id, "text": scene.narration} for scene in scenes],
            "provider": "local_manual",
            "aligner": LOCAL_ALIGNER_VERSION,
        }
    )


def prepare_manual_audio(layout: ProjectLayout, *, force: bool = False) -> dict[str, Any]:
    project = layout.load_manifest()
    if project.config.audio.provider != "local_manual":
        raise ValueError("O projeto não está configurado com audio.provider=local_manual.")
    scene_manifest, scenes = _scenes(layout)
    input_hash = _queue_hash(scene_manifest, scenes)
    if not force and layout.manual_audio_queue.is_file():
        current = json.loads(layout.manual_audio_queue.read_text(encoding="utf-8"))
        if current.get("input_hash") == input_hash:
            return current
    queue = {
        "schema_version": "1.0",
        "project_id": project.project_id,
        "status": "waiting_external",
        "input_hash": input_hash,
        "provider": "local_manual",
        "transcript_path": relative_posix(layout.narration, layout.root),
        "staging_dir": relative_posix(layout.manual_audio_staging, layout.root),
        "accepted_formats": ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "webm"],
        "instructions": (
            "Narrar exatamente todo o conteúdo de script/narration.txt em um único arquivo, "
            "somente voz, sem música, efeitos, introduções ou reformulações."
        ),
    }
    write_json(layout.manual_audio_queue, queue)
    wait_stage(
        layout,
        "audio",
        input_hash,
        "Aguardando o arquivo de narração anexado pelo usuário.",
        artifacts=[relative_posix(layout.manual_audio_queue, layout.root)],
    )
    return queue


def _canonical_token(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _validate_aligned_words(transcript: str, rows: Sequence[dict[str, Any]]) -> list[LocalWord]:
    expected = [match.group(0) for match in _WORD_RE.finditer(transcript)]
    aligned = [str(row.get("word", "")).strip() for row in rows]
    if len(expected) != len(aligned):
        raise ValueError(
            "O alinhador local não cobriu todas as palavras: "
            f"esperadas {len(expected)}, alinhadas {len(aligned)}. "
            "Confirme que o áudio narra exatamente o roteiro."
        )
    words: list[LocalWord] = []
    for index, (display_word, aligned_word, row) in enumerate(
        zip(expected, aligned, rows, strict=True)
    ):
        if _canonical_token(display_word) != _canonical_token(aligned_word):
            raise ValueError(
                f"Divergência no alinhamento da palavra {index + 1}: "
                f"roteiro={display_word!r}, áudio={aligned_word!r}."
            )
        start = float(row["start"])
        end = float(row["end"])
        if start < 0 or end <= start or (words and start < words[-1].start):
            raise ValueError("O alinhamento local retornou timestamps inválidos ou fora de ordem.")
        words.append(LocalWord(display_word, start, end))
    return words


def _run_local_aligner(
    audio_path: Path,
    transcript: str,
    duration: float,
) -> Sequence[dict[str, Any]]:
    worker = Path(__file__).with_name("local_aligner_worker.py")
    cache = Path.home() / ".cache" / "pipeline-videos" / "alignment-models"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pipeline-local-align-") as temporary:
        temporary_path = Path(temporary)
        request = temporary_path / "request.json"
        response = temporary_path / "response.json"
        request.write_text(
            json.dumps(
                {
                    "audio_path": str(audio_path.resolve()),
                    "transcript": transcript,
                    "duration": duration,
                    "model_dir": str(cache),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run_process(
            [
                executable("uv"),
                "run",
                "--isolated",
                "--no-project",
                "--with",
                f"whisperx=={LOCAL_ALIGNER_VERSION.removeprefix('whisperx-')}",
                "python",
                str(worker),
                str(request),
                str(response),
            ],
            timeout=3600,
        )
        payload = json.loads(response.read_text(encoding="utf-8"))
    return payload["words"]


def _slice_wav(source: Path, destination: Path, start_frame: int, end_frame: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), "rb") as input_wav:
        input_wav.setpos(start_frame)
        frames = input_wav.readframes(end_frame - start_frame)
        with wave.open(str(destination), "wb") as output_wav:
            output_wav.setnchannels(1)
            output_wav.setsampwidth(2)
            output_wav.setframerate(TARGET_SAMPLE_RATE)
            output_wav.writeframes(frames)
    return end_frame - start_frame


def _apply_required_pause(
    path: Path,
    scenes: Sequence[Scene],
    words: list[LocalWord],
    seconds: float,
) -> tuple[list[LocalWord], int]:
    if seconds <= 0:
        return words, 0
    cursor = 0
    for scene in scenes:
        count = len(list(_WORD_RE.finditer(scene.narration)))
        local = [
            LocalWord(item.word, item.start, item.end) for item in words[cursor : cursor + count]
        ]
        span = _comment_response_span(scene.narration, local)
        if span is not None:
            insertion = words[cursor + span[1]].end
            silence_frames = round(seconds * TARGET_SAMPLE_RATE)
            _insert_pcm_silence(
                path,
                at_frame=round(insertion * TARGET_SAMPLE_RATE),
                silence_frames=silence_frames,
            )
            shifted = [
                LocalWord(
                    item.word,
                    item.start + (seconds if item.start >= insertion else 0.0),
                    item.end + (seconds if item.start >= insertion else 0.0),
                )
                for item in words
            ]
            return shifted, silence_frames
        cursor += count
    return words, 0


def import_manual_audio(
    layout: ProjectLayout,
    source: Path,
    *,
    force: bool = False,
    aligner: AlignmentRunner | None = None,
) -> AudioManifest:
    project = layout.load_manifest()
    if project.config.audio.provider != "local_manual":
        raise ValueError("Configure o projeto com audio.provider=local_manual antes da importação.")
    source = source.expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"Arquivo de narração ausente ou vazio: {source}")
    scene_manifest, scenes = _scenes(layout)
    transcript = _transcript(scenes)
    input_hash = sha256_value(
        {
            "queue": _queue_hash(scene_manifest, scenes),
            "source_hash": sha256_file(source),
            "aligner": LOCAL_ALIGNER_VERSION,
            "pause": project.config.audio.comment_response_silence_seconds,
        }
    )
    if stage_is_current(layout, "audio", input_hash, force=force):
        return read_model(layout.audio, AudioManifest)
    invalidate(
        layout,
        stages=("captions", "metadata", "render", "validation"),
        approvals=("metadata", "final_render"),
        note="A narração manual foi importada.",
    )
    try:
        normalized = layout.manual_audio_staging / "normalized-48k.wav"
        _convert_to_wav_48k(source, normalized)
        duration = media_duration(normalized)
        runner = aligner or _run_local_aligner
        local_words = _validate_aligned_words(transcript, runner(normalized, transcript, duration))
        local_words, inserted_frames = _apply_required_pause(
            normalized,
            scenes,
            local_words,
            project.config.audio.comment_response_silence_seconds,
        )
        total_frames = _validated_wav_frames(normalized)
        duration = total_frames / TARGET_SAMPLE_RATE
        shutil.copy2(normalized, layout.narration_audio)

        counts = [len(list(_WORD_RE.finditer(scene.narration))) for scene in scenes]
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for count in counts:
            offsets.append((cursor, cursor + count))
            cursor += count
        if cursor != len(local_words):
            raise ValueError("A divisão do roteiro em cenas diverge do alinhamento local.")

        boundaries = [0]
        for index in range(len(scenes) - 1):
            current_end = local_words[offsets[index][1] - 1].end
            next_start = local_words[offsets[index + 1][0]].start
            boundaries.append(round(((current_end + next_start) / 2) * TARGET_SAMPLE_RATE))
        boundaries.append(total_frames)

        segments: list[AudioSegment] = []
        global_words: list[WordTiming] = []
        for index, scene in enumerate(scenes):
            start_frame = boundaries[index]
            end_frame = boundaries[index + 1]
            destination = layout.audio_segments / f"{scene.scene_id}-v001.wav"
            _slice_wav(normalized, destination, start_frame, end_frame)
            scene_words: list[WordTiming] = []
            for item in local_words[offsets[index][0] : offsets[index][1]]:
                timing = WordTiming(
                    index=len(global_words),
                    word=item.word,
                    start=round(item.start, 6),
                    end=round(item.end, 6),
                    scene_id=scene.scene_id,
                )
                global_words.append(timing)
                scene_words.append(timing)
            segments.append(
                AudioSegment(
                    scene_id=scene.scene_id,
                    version=1,
                    text=scene.narration,
                    input_hash=sha256_value({"audio": input_hash, "scene": scene.scene_id}),
                    provider="local_manual",
                    model_id=LOCAL_ALIGNER_VERSION,
                    path=relative_posix(destination, layout.root),
                    start=round(start_frame / TARGET_SAMPLE_RATE, 6),
                    end=round(end_frame / TARGET_SAMPLE_RATE, 6),
                    duration=round((end_frame - start_frame) / TARGET_SAMPLE_RATE, 6),
                    alignment_source="local_forced_alignment",
                    words=scene_words,
                    usage={},
                )
            )
        manifest = AudioManifest(
            generated_at=utc_now(),
            provider="local_manual",
            sample_rate=TARGET_SAMPLE_RATE,
            narration_path=relative_posix(layout.narration_audio, layout.root),
            duration=round(duration, 6),
            segments=segments,
            words=global_words,
        )
        write_json(layout.audio, manifest)
        queue = prepare_manual_audio(layout)
        queue["status"] = "completed"
        queue["source_name"] = source.name
        queue["source_hash"] = sha256_file(source)
        write_json(layout.manual_audio_queue, queue)
        artifacts = [
            relative_posix(layout.audio, layout.root),
            relative_posix(layout.narration_audio, layout.root),
            relative_posix(layout.manual_audio_queue, layout.root),
            *(segment.path for segment in segments),
        ]
        complete_stage(
            layout,
            "audio",
            input_hash,
            artifacts,
            provider="local_manual",
            usage={
                "segments": len(segments),
                "words": len(global_words),
                "inserted_silence_seconds": inserted_frames / TARGET_SAMPLE_RATE,
                "external_calls": 0,
            },
            note="Narração anexada alinhada localmente, sem ElevenLabs.",
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "audio", exc)
        raise
