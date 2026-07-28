from __future__ import annotations

import json
import re
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline_videos.audio import (
    _eleven_v3_prompt_for_scenes,
    _elevenlabs_source_spec,
    _request_elevenlabs,
    _validate_compact_narration_audio,
    _validate_tts_text,
    character_alignment_to_words,
    generate_audio,
)
from pipeline_videos.manual_audio import import_manual_audio, prepare_manual_audio
from pipeline_videos.models import (
    DEFAULT_ELEVENLABS_MODEL_ID,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_VOICE_VARIANTS,
    AudioConfig,
    PromptBlocks,
    Scene,
    SceneManifest,
    StageStatus,
    WordTiming,
    default_elevenlabs_model_id,
    elevenlabs_voice_id_for_variant,
    normalize_elevenlabs_voice_variant,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import ProjectLayout, write_json


def _scene(scene_id: str, order: int, narration: str) -> Scene:
    prompt = PromptBlocks(
        style_profile_id="minimalist-cinematic-flat-vector",
        palette_id="noite-azul-dourada",
        continuity=[],
        scene=f"Cena {order}",
        composition="Plano geral",
        emotion="serenidade",
        negative=[],
        assembled_prompt=f"Cena {order}, plano geral, serenidade",
    )
    return Scene(
        scene_id=scene_id,
        order=order,
        section_id="section-001",
        narration=narration,
        estimated_duration_seconds=1,
        emotional_intent="serenidade",
        environment="paisagem",
        shot="plano geral",
        narrative_function="narração",
        prompt=prompt,
        image_id=f"image-{order:03d}",
    )


def _project(tmp_path: Path, *, provider: str = "mock") -> ProjectLayout:
    layout = create_project(
        "audio-test",
        "Teste de áudio",
        projects_dir=tmp_path,
        target_duration_seconds=30,
        demo=True,
    )
    project = layout.load_manifest()
    project.config.audio.provider = provider  # type: ignore[assignment]
    if provider == "elevenlabs":
        project.config.audio.voice_id = "voice-test"
    layout.save_manifest(project)
    scenes = [_scene("scene-001", 1, "No princípio"), _scene("scene-002", 2, "havia luz")]
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="minimalist-cinematic-flat-vector",
            palette_id="noite-azul-dourada",
            total_estimated_duration_seconds=2,
            scenes=scenes,
        ),
    )
    return layout


def test_tts_text_guard_rejects_encoding_damage_without_blocking_questions() -> None:
    _validate_tts_text("Você confia? Sim, com esperança.")

    with pytest.raises(ValueError, match="parece corrompido"):
        _validate_tts_text("Davi decidiu enfrent?-lo com sua pr?pria for?a.")

    with pytest.raises(ValueError, match="substituição Unicode"):
        _validate_tts_text("Texto com caractere inválido: \ufffd")


def test_eleven_v3_prompt_adds_strategic_tags_without_changing_spoken_words() -> None:
    scenes = [
        _scene("scene-001", 1, "A Bíblia realmente diz isso?"),
        _scene("scene-002", 2, "Agora observe a imagem profética."),
        _scene("scene-003", 3, "A resposta exige cuidado e contexto."),
    ]
    scenes[0].narrative_function = "hook com pergunta"
    scenes[1].narrative_function = "reengajamento e revelação"
    scenes[2].narrative_function = "conclusão"

    prompt, records = _eleven_v3_prompt_for_scenes(scenes)

    assert prompt == (
        "[curious] A Bíblia realmente diz isso? "
        "[surprised] Agora observe a imagem profética. "
        "[thoughtful] A resposta exige cuidado e contexto."
    )
    assert [item["tag"] for item in records] == [
        "curious",
        "surprised",
        "thoughtful",
    ]
    clean = re.sub(r"\[(?:curious|surprised|thoughtful)\]\s*", "", prompt)
    assert clean == " ".join(scene.narration for scene in scenes)
    assert "<break" not in prompt


def test_character_alignment_to_words_preserves_unicode_and_punctuation() -> None:
    words = character_alignment_to_words(
        {
            "characters": ["Olá", ",", "\u00a0", "mundo", "!"],
            "character_start_times_seconds": [0, 0.2, 0.3, 0.4, 0.8],
            "character_end_times_seconds": [0.2, 0.3, 0.4, 0.8, 1.0],
        }
    )

    assert [(item.word, item.start, item.end) for item in words] == [
        ("Olá,", 0.0, 0.3),
        ("mundo!", 0.4, 1.0),
    ]


def test_character_alignment_rejects_truncated_positional_arrays() -> None:
    with pytest.raises(ValueError, match="tamanhos diferentes"):
        character_alignment_to_words(
            {
                "characters": ["a", "b"],
                "character_start_times_seconds": [0],
                "character_end_times_seconds": [0.1, 0.2],
            }
        )


def test_mock_audio_is_48k_deterministic_reusable_and_versioned(tmp_path: Path) -> None:
    layout = _project(tmp_path)

    first = generate_audio(layout)

    assert first.provider == "mock"
    assert first.sample_rate == 48_000
    assert first.duration == 1.5
    assert [segment.version for segment in first.segments] == [1, 1]
    assert [segment.alignment_source for segment in first.segments] == [
        "simulated",
        "simulated",
    ]
    assert [(word.word, word.start, word.end) for word in first.words] == [
        ("No", 0.0, 0.3),
        ("princípio", 0.375, 0.675),
        ("havia", 0.75, 1.05),
        ("luz", 1.125, 1.425),
    ]
    narration_bytes = layout.narration_audio.read_bytes()
    with wave.open(str(layout.narration_audio), "rb") as narration:
        assert narration.getframerate() == 48_000
        assert narration.getnchannels() == 1
        assert narration.getsampwidth() == 2
        assert narration.getnframes() == 72_000

    repeated = generate_audio(layout)

    assert repeated == first
    assert layout.narration_audio.read_bytes() == narration_bytes
    assert not list(layout.audio_segments.glob("*-v002.wav"))

    regenerated = generate_audio(layout, scene_id="scene-001", force=True)

    assert [segment.version for segment in regenerated.segments] == [2, 1]
    assert layout.narration_audio.read_bytes() == narration_bytes
    assert (layout.audio_segments / "scene-001-v001.wav").is_file()
    assert (layout.audio_segments / "scene-001-v002.wav").is_file()
    stage = layout.load_manifest().stages["audio"]
    assert stage.status == StageStatus.COMPLETED
    assert stage.usage["generated_segments"] == 1
    assert stage.usage["reused_segments"] == 1


def test_audio_inserts_one_second_of_silence_after_quoted_comment_response(
    tmp_path: Path,
) -> None:
    layout = _project(tmp_path)
    project = layout.load_manifest()
    project.config.audio.comment_response_silence_seconds = 1.0
    layout.save_manifest(project)
    scene = _scene(
        "scene-001",
        1,
        "Escreva “CORAGEM” nos comentários. Continue agora.",
    )
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=3,
            scenes=[scene],
        ),
    )

    audio = generate_audio(layout)

    response = next(word for word in audio.words if "CORAGEM" in word.word)
    following = next(word for word in audio.words if word.word == "nos")
    assert following.start - response.end >= 1.0
    assert audio.duration == pytest.approx(3.25)
    assert audio.segments[0].usage["comment_response_silence_seconds"] == 1.0
    with wave.open(str(layout.narration_audio), "rb") as narration:
        assert narration.getnframes() == 156_000


def test_compact_short_never_inserts_silence_to_match_target_duration(
    tmp_path: Path,
) -> None:
    layout = create_project(
        "compact-audio",
        "Short compacto",
        projects_dir=tmp_path,
        target_duration_seconds=15,
        video_format="short",
    )
    project = layout.load_manifest()
    project.config.audio.provider = "mock"
    layout.save_manifest(project)
    scenes = [
        _scene("scene-001", 1, "Olhe para mim"),
        _scene("scene-002", 2, "Façam assim a elas"),
        _scene("scene-003", 3, "Lucas capítulo seis versículo trinta e um"),
    ]
    scenes[0].section_id = "opening"
    scenes[1].section_id = "scripture"
    scenes[2].section_id = "reference"
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=15,
            scenes=scenes,
        ),
    )

    audio = generate_audio(layout)

    first_reference_word = next(word for word in audio.words if word.scene_id == "scene-003")
    assert first_reference_word.start == pytest.approx(2.625, abs=1 / 48_000)
    assert audio.duration == 5.25
    assert all(
        not any("adaptive_compact" in key for key in segment.usage) for segment in audio.segments
    )
    with wave.open(str(layout.narration_audio), "rb") as narration:
        assert narration.getnframes() == round(audio.duration * 48_000)

    repeated = generate_audio(layout)
    assert repeated == audio
    assert not list(layout.audio_segments.glob("*-v002.wav"))


def test_compact_narration_is_synthesized_once_and_split_without_sample_loss(
    tmp_path: Path,
) -> None:
    layout = create_project(
        "continuous-compact-audio",
        "Short compacto contínuo",
        projects_dir=tmp_path,
        target_duration_seconds=15,
        video_format="short",
    )
    project = layout.load_manifest()
    project.config.audio.provider = "mock"
    layout.save_manifest(project)
    scenes = [
        _scene("scene-001", 1, "Olhe para mim."),
        _scene("scene-002", 2, "Eu sou a videira verdadeira."),
        _scene("scene-003", 3, "Todo ramo que dá fruto,"),
        _scene("scene-004", 4, "ele limpa para que dê mais fruto."),
        _scene("scene-005", 5, "João capítulo quinze versículos um e dois."),
    ]
    for scene, section_id in zip(
        scenes,
        ("opening", "scripture", "scripture", "scripture", "reference"),
        strict=True,
    ):
        scene.section_id = section_id
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=15,
            scenes=scenes,
        ),
    )

    audio = generate_audio(layout)

    source_path = next(layout.audio_segments.glob("continuous-group-001-full-narration-v*.wav"))
    with wave.open(str(source_path), "rb") as source:
        source_pcm = source.readframes(source.getnframes())
    derived_pcm: list[bytes] = []
    for segment in audio.segments:
        with wave.open(str(layout.root / segment.path), "rb") as derived:
            derived_pcm.append(derived.readframes(derived.getnframes()))

    assert b"".join(derived_pcm) == source_pcm
    assert [segment.text for segment in audio.segments[1:4]] == [
        "Eu sou a videira verdadeira.",
        "Todo ramo que dá fruto,",
        "ele limpa para que dê mais fruto.",
    ]
    stage = layout.load_manifest().stages["audio"]
    assert stage.usage["synthesis_segmentation"] == "continuous_narration_v1"
    assert stage.usage["tts_requests"] == 1
    assert stage.usage["generated_segments"] == 5


def test_compact_audio_quality_gate_rejects_short_narration_and_large_gap(tmp_path: Path) -> None:
    layout = create_project(
        "compact-audio-gate",
        "Short compacto",
        projects_dir=tmp_path,
        target_duration_seconds=15,
        video_format="short",
    )
    project = layout.load_manifest()
    continuous_words = [
        WordTiming(
            index=index,
            word=f"palavra-{index}",
            start=index * 0.4,
            end=index * 0.4 + 0.3,
            scene_id="scene-001",
        )
        for index in range(20)
    ]

    with pytest.raises(ValueError, match="hook ou versículo mais longo"):
        _validate_compact_narration_audio(
            project,
            provider="elevenlabs",
            duration=9.0,
            words=continuous_words,
        )

    words_with_gap = [
        WordTiming(index=0, word="antes", start=0.0, end=0.5, scene_id="scene-001"),
        WordTiming(index=1, word="depois", start=6.0, end=6.5, scene_id="scene-002"),
        WordTiming(index=2, word="fim", start=14.0, end=14.5, scene_id="scene-003"),
    ]
    project.config.production.validation_policy = "strict_v1"
    with pytest.raises(ValueError, match="mensagem, não por silêncio"):
        _validate_compact_narration_audio(
            project,
            provider="elevenlabs",
            duration=15.0,
            words=words_with_gap,
        )

    project.config.production.validation_policy = "completion_first_v1"
    _validate_compact_narration_audio(
        project,
        provider="elevenlabs",
        duration=15.0,
        words=words_with_gap,
    )


def test_adaptive_duration_accepts_real_audio_in_range_and_persists_it(tmp_path: Path) -> None:
    layout = create_project(
        "compact-audio-range",
        "Short compacto em faixa",
        projects_dir=tmp_path,
        target_duration_seconds=18,
        duration_min_seconds=15,
        duration_max_seconds=20,
        video_format="short",
    )
    project = layout.load_manifest()
    project.config.audio.provider = "mock"
    layout.save_manifest(project)
    narration = " ".join(f"palavra{index}" for index in range(44))
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="style",
            palette_id="palette",
            total_estimated_duration_seconds=18,
            scenes=[_scene("scene-001", 1, narration)],
        ),
    )

    audio = generate_audio(layout)

    assert audio.duration == pytest.approx(16.5)
    manifest = layout.load_manifest()
    assert manifest.effective_duration_seconds == pytest.approx(16.5)
    assert any(
        '"event": "adaptive_duration_settled"' in line
        for line in layout.events.read_text(encoding="utf-8").splitlines()
    )


def test_content_cache_reuses_segments_when_scene_ids_change(tmp_path: Path) -> None:
    layout = _project(tmp_path)
    first = generate_audio(layout)
    original_bytes = [(layout.root / segment.path).read_bytes() for segment in first.segments]
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=2,
            style_profile_id="minimalist-cinematic-flat-vector",
            palette_id="noite-azul-dourada",
            total_estimated_duration_seconds=2,
            scenes=[
                _scene("renamed-001", 1, "No princípio"),
                _scene("renamed-002", 2, "havia luz"),
            ],
        ),
    )

    recovered = generate_audio(layout)

    assert [segment.scene_id for segment in recovered.segments] == [
        "renamed-001",
        "renamed-002",
    ]
    assert [
        (layout.root / segment.path).read_bytes() for segment in recovered.segments
    ] == original_bytes
    assert all(
        segment.usage.get("recovered_from_content_cache") == 1 for segment in recovered.segments
    )
    stage = layout.load_manifest().stages["audio"]
    assert stage.usage["generated_segments"] == 0
    assert stage.usage["recovered_segments"] == 2


def test_elevenlabs_requires_explicit_external_permission(tmp_path: Path) -> None:
    layout = _project(tmp_path, provider="elevenlabs")

    class ForbiddenClient:
        called = False

        def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            self.called = True
            raise AssertionError("A proteção deveria impedir qualquer request.")

    client = ForbiddenClient()
    with pytest.raises(RuntimeError, match="allow_external=True"):
        generate_audio(layout, http_client=client)

    assert client.called is False
    assert layout.load_manifest().stages["audio"].status == StageStatus.PENDING


def test_elevenlabs_recovers_valid_wavs_from_failed_same_hash_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _project(tmp_path, provider="elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    class FailingClient:
        def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise RuntimeError("quota exhausted")

    with pytest.raises(RuntimeError, match="quota exhausted"):
        generate_audio(layout, allow_external=True, http_client=FailingClient())

    for scene_id in ("scene-001", "scene-002"):
        path = layout.audio_segments / f"{scene_id}-v001.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(b"\x00\x00" * 4_800)

    class ForbiddenClient:
        def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise AssertionError("WAVs recuperados não devem chamar a API novamente.")

    recovered = generate_audio(
        layout,
        allow_external=True,
        http_client=ForbiddenClient(),
    )

    assert [segment.version for segment in recovered.segments] == [1, 1]
    assert [segment.alignment_source for segment in recovered.segments] == [
        "simulated",
        "simulated",
    ]
    assert all(
        segment.usage == {"recovered_from_failed_attempt": 1} for segment in recovered.segments
    )
    stage = layout.load_manifest().stages["audio"]
    assert stage.status == StageStatus.COMPLETED
    assert stage.usage["generated_segments"] == 0
    assert stage.usage["reused_segments"] == 2
    assert stage.usage["recovered_segments"] == 2


def test_elevenlabs_retries_locally_and_falls_back_to_raw_alignment() -> None:
    calls: list[httpx.Request] = []
    alignment = {
        "characters": ["O", "i"],
        "character_start_times_seconds": [0, 0.1],
        "character_end_times_seconds": [0.1, 0.2],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"detail": {"code": "rate_limit_exceeded"}})
        if len(calls) == 2:
            return httpx.Response(500, json={"detail": {"code": "internal_error"}})
        return httpx.Response(
            200,
            headers={
                "request-id": "req-123",
                "character-cost": "12",
                "x-trace-id": "trace-456",
                "current-concurrent-requests": "1",
                "maximum-concurrent-requests": "4",
            },
            json={
                "audio_base64": "ZmFrZS1tcDM=",
                "normalized_alignment": None,
                "alignment": alignment,
            },
        )

    sleeps: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _request_elevenlabs(
            client=client,
            api_key="super-secret",
            voice_id="voice/id",
            body={"text": "Oi", "model_id": "eleven_multilingual_v2"},
            output_format="mp3_44100_64",
            sleep=sleeps.append,
            jitter=lambda: 0,
        )

    assert result.audio == b"fake-mp3"
    assert result.alignment == alignment
    assert len(calls) == 3
    assert sleeps == [0.0, 0.0]
    assert calls[-1].url.params["output_format"] == "mp3_44100_64"
    raw_path = calls[-1].url.raw_path.split(b"?", 1)[0]
    assert raw_path.endswith(b"/voice%2Fid/with-timestamps")
    assert calls[-1].headers["xi-api-key"] == "super-secret"
    assert json.loads(calls[-1].content)["text"] == "Oi"
    assert result.usage == {
        "http_attempts": 3,
        "request-id": "req-123",
        "character-cost": 12,
        "x-trace-id": "trace-456",
        "current-concurrent-requests": 1,
        "maximum-concurrent-requests": 4,
    }
    serialized_usage = json.dumps(result.usage)
    assert "super-secret" not in serialized_usage
    assert "ZmFrZS1tcDM=" not in serialized_usage


def test_elevenlabs_source_spec_supports_encoded_and_raw_formats() -> None:
    assert _elevenlabs_source_spec("mp3_44100_128") == (".mp3", [])
    assert _elevenlabs_source_spec("pcm_16000") == (
        ".pcm",
        ["-f", "s16le", "-ar", "16000", "-ac", "1"],
    )


def test_legacy_sapi_config_migrates_to_elevenlabs() -> None:
    assert AudioConfig.model_validate({"provider": "sapi"}).provider == "elevenlabs"
    assert (
        AudioConfig.model_validate(
            {"synthesis_segmentation": "continuous_section_v1"}
        ).synthesis_segmentation
        == "continuous_section_v1"
    )


def test_elevenlabs_speed_defaults_and_bounds() -> None:
    assert AudioConfig().speed == 1.0
    with pytest.raises(ValueError, match="pedido explícito"):
        AudioConfig(speed=0.87)
    explicit = AudioConfig(
        speed=0.87,
        speech_rate_policy="explicit_user_override_v1",
    )
    assert explicit.speed == 0.87
    with pytest.raises(ValueError):
        AudioConfig(speed=0.69)
    with pytest.raises(ValueError):
        AudioConfig(speed=1.21)
    with pytest.raises(ValueError, match="eleven_v3"):
        AudioConfig(prompting_profile="eleven_v3_strategic_tags_v1")
    tagged = AudioConfig(
        model_id="eleven_v3",
        prompting_profile="eleven_v3_strategic_tags_v1",
    )
    assert tagged.prompting_profile == "eleven_v3_strategic_tags_v1"


def test_elevenlabs_defaults_and_named_voice_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "ELEVENLABS_VOICE_ID",
        "ELEVENLABS_VOICE_JESUS_1_ID",
        "ELEVENLABS_VOICE_JESUS_2_ID",
        "ELEVENLABS_VOICE_MATHEUS_ID",
        "ELEVENLABS_MODEL_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AudioConfig()

    assert config.voice_id == DEFAULT_ELEVENLABS_VOICE_ID
    assert config.model_id == DEFAULT_ELEVENLABS_MODEL_ID
    assert normalize_elevenlabs_voice_variant("Jesus-2") == "jesus_2"
    assert elevenlabs_voice_id_for_variant("jesus_2") == ELEVENLABS_VOICE_VARIANTS["jesus_2"]
    assert normalize_elevenlabs_voice_variant("Matheus") == "matheus"
    assert elevenlabs_voice_id_for_variant("matheus") == "36rVQA1AOIPwpA3Hg1tC"

    monkeypatch.setenv("ELEVENLABS_VOICE_JESUS_1_ID", "voice-jesus-1-env")
    monkeypatch.setenv("ELEVENLABS_VOICE_JESUS_2_ID", "voice-jesus-2-env")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "model-env")

    assert AudioConfig().voice_id == "voice-jesus-1-env"
    assert elevenlabs_voice_id_for_variant("jesus_2") == "voice-jesus-2-env"
    assert default_elevenlabs_model_id() == "model-env"

    with pytest.raises(ValueError, match="Variação de voz desconhecida"):
        normalize_elevenlabs_voice_variant("voz-inexistente")


def test_manual_audio_waits_then_imports_with_local_alignment(tmp_path: Path) -> None:
    layout = _project(tmp_path, provider="local_manual")

    queue = prepare_manual_audio(layout)

    assert queue["status"] == "waiting_external"
    assert layout.load_manifest().stages["audio"].status == StageStatus.WAITING_EXTERNAL
    source = tmp_path / "voice.mp3.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\x00\x00" * 96_000)

    def aligner(_audio: Path, transcript: str, _duration: float) -> list[dict[str, Any]]:
        return [
            {"word": word, "start": 0.2 + index * 0.35, "end": 0.4 + index * 0.35}
            for index, word in enumerate(transcript.split())
        ]

    audio = import_manual_audio(layout, source, aligner=aligner)

    assert audio.provider == "local_manual"
    assert audio.sample_rate == 48_000
    assert [word.word for word in audio.words] == ["No", "princípio", "havia", "luz"]
    assert {segment.alignment_source for segment in audio.segments} == {"local_forced_alignment"}
    assert all((layout.root / segment.path).is_file() for segment in audio.segments)
    assert layout.narration_audio.is_file()
    assert layout.load_manifest().stages["audio"].usage["external_calls"] == 0
    imported_queue = json.loads(layout.manual_audio_queue.read_text(encoding="utf-8"))
    assert imported_queue["status"] == "completed"


def test_manual_audio_rejects_incomplete_alignment(tmp_path: Path) -> None:
    layout = _project(tmp_path, provider="local_manual")
    source = tmp_path / "voice.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\x00\x00" * 48_000)

    with pytest.raises(ValueError, match="não cobriu todas as palavras"):
        import_manual_audio(
            layout,
            source,
            aligner=lambda _audio, _transcript, _duration: [
                {"word": "No", "start": 0.1, "end": 0.2}
            ],
        )

    assert layout.load_manifest().stages["audio"].status == StageStatus.FAILED
