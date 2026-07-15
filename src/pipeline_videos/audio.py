from __future__ import annotations

import base64
import binascii
import json
import math
import os
import random
import re
import struct
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from pipeline_videos.editorial import quoted_comment_response
from pipeline_videos.models import (
    AudioManifest,
    AudioSegment,
    Scene,
    SceneManifest,
    StageStatus,
    WordTiming,
)
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json
from pipeline_videos.utils import executable, relative_posix, run_process, sha256_value, utc_now

TARGET_SAMPLE_RATE = 48_000
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
ELEVENLABS_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_RETRYABLE_STATUSES = frozenset({429, 500, 503})
ELEVENLABS_MAX_RETRIES = 3

_MOCK_WORD_FRAMES = 18_000  # 375 ms at 48 kHz
_MOCK_SPOKEN_FRAMES = 14_400  # 300 ms at 48 kHz
_VERSION_RE = re.compile(r"-v(?P<version>\d+)\.wav$", flags=re.IGNORECASE)
_WORD_RE = re.compile(r"\S+", flags=re.UNICODE)
_SUSPICIOUS_QUESTION_MARK_RE = re.compile(r"(?<=\w)\?(?=[\w-])", flags=re.UNICODE)


class HttpPoster(Protocol):
    def post(
        self,
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        json: Mapping[str, Any],
    ) -> httpx.Response: ...


@dataclass(frozen=True)
class LocalWord:
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class SynthesisResult:
    words: list[LocalWord]
    duration_frames: int
    alignment_source: Literal["provider", "simulated"]
    usage: dict[str, int | float | str | None]
    model_id: str | None
    voice_id: str | None


@dataclass(frozen=True)
class ElevenLabsResponse:
    audio: bytes
    alignment: Mapping[str, Any] | None
    usage: dict[str, int | float | str | None]


def _validate_tts_text(text: str) -> None:
    if "\ufffd" in text:
        raise ValueError(
            "O texto TTS contém o caractere de substituição Unicode; revise a codificação UTF-8."
        )
    match = _SUSPICIOUS_QUESTION_MARK_RE.search(text)
    if match:
        start = max(0, match.start() - 12)
        end = min(len(text), match.end() + 12)
        context = text[start:end].replace("\n", " ")
        raise ValueError(
            f"O texto TTS parece corrompido: há '?' dentro de uma palavra próxima de {context!r}."
        )


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} deve ser numérico.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} deve ser numérico.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} deve ser finito.")
    return result


def character_alignment_to_words(alignment: Mapping[str, Any] | None) -> list[LocalWord]:
    """Transforma o alinhamento posicional da ElevenLabs em palavras.

    O provedor normalmente envia um caractere por posição, mas o parser também aceita entradas com
    mais de um code point. Pontuação permanece anexada à palavra, e qualquer espaço Unicode encerra
    o token atual.
    """

    if not alignment:
        return []
    characters = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if not all(
        isinstance(item, Sequence) and not isinstance(item, (str, bytes))
        for item in (
            characters,
            starts,
            ends,
        )
    ):
        raise ValueError("Alinhamento inválido: characters, starts e ends devem ser listas.")
    if len(characters) != len(starts) or len(characters) != len(ends):
        raise ValueError("Alinhamento inválido: as listas posicionais têm tamanhos diferentes.")

    words: list[LocalWord] = []
    pieces: list[str] = []
    word_start: float | None = None
    word_end: float | None = None

    def flush() -> None:
        nonlocal pieces, word_start, word_end
        if pieces and word_start is not None and word_end is not None:
            token = "".join(pieces)
            if token:
                words.append(LocalWord(token, round(word_start, 6), round(word_end, 6)))
        pieces = []
        word_start = None
        word_end = None

    for index, raw_piece in enumerate(characters):
        if not isinstance(raw_piece, str):
            raise ValueError(f"Alinhamento inválido: characters[{index}] não é texto.")
        if not raw_piece:
            continue
        start = max(0.0, _number(starts[index], f"start[{index}]"))
        end = max(start, _number(ends[index], f"end[{index}]"))
        for character in raw_piece:
            if character.isspace():
                flush()
                continue
            pieces.append(character)
            word_start = start if word_start is None else min(word_start, start)
            word_end = end if word_end is None else max(word_end, end)
    flush()
    return words


def _proportional_words(text: str, duration: float) -> list[LocalWord]:
    matches = list(_WORD_RE.finditer(text))
    if not matches:
        return []
    duration = max(0.0, duration)
    weights = [max(1, len(match.group(0))) for match in matches]
    total_weight = sum(weights)
    cursor = 0
    words: list[LocalWord] = []
    for match, weight in zip(matches, weights, strict=True):
        start = duration * cursor / total_weight
        cursor += weight
        end = duration * cursor / total_weight
        words.append(LocalWord(match.group(0), round(start, 6), round(end, 6)))
    return words


def _clamp_words(words: Sequence[LocalWord], duration: float) -> list[LocalWord]:
    clamped: list[LocalWord] = []
    for item in words:
        start = min(max(0.0, item.start), duration)
        end = min(max(start, item.end), duration)
        clamped.append(LocalWord(item.word, round(start, 6), round(end, 6)))
    return clamped


def _safe_scene_id(scene_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", scene_id).strip("-.")
    if not safe:
        safe = "scene"
    return safe[:80]


def _next_segment_version(layout: ProjectLayout, scene_id: str) -> int:
    safe = _safe_scene_id(scene_id)
    versions: list[int] = []
    for candidate in layout.audio_segments.glob(f"{safe}-v*.wav"):
        match = _VERSION_RE.search(candidate.name)
        if match:
            versions.append(int(match.group("version")))
    return max(versions, default=0) + 1


def _latest_segment(layout: ProjectLayout, scene_id: str) -> tuple[int, Path] | None:
    safe = _safe_scene_id(scene_id)
    candidates: list[tuple[int, Path]] = []
    for candidate in layout.audio_segments.glob(f"{safe}-v*.wav"):
        match = _VERSION_RE.search(candidate.name)
        if match:
            candidates.append((int(match.group("version")), candidate))
    return max(candidates, key=lambda item: item[0]) if candidates else None


def _segment_destination(layout: ProjectLayout, scene_id: str, version: int) -> Path:
    return layout.audio_segments / f"{_safe_scene_id(scene_id)}-v{version:03d}.wav"


def _resolve_artifact(layout: ProjectLayout, relative_path: str) -> Path:
    candidate = (layout.root / Path(relative_path)).resolve()
    try:
        candidate.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError(f"Artefato de áudio aponta para fora do projeto: {relative_path}") from exc
    return candidate


def _wav_properties(path: Path) -> tuple[int, int, int, int]:
    try:
        with wave.open(str(path), "rb") as source:
            return (
                source.getframerate(),
                source.getnchannels(),
                source.getsampwidth(),
                source.getnframes(),
            )
    except (EOFError, wave.Error) as exc:
        raise ValueError(f"WAV inválido: {path}") from exc


def _validated_wav_frames(path: Path) -> int:
    sample_rate, channels, sample_width, frames = _wav_properties(path)
    if sample_rate != TARGET_SAMPLE_RATE or channels != 1 or sample_width != 2:
        raise ValueError(
            f"Segmento fora do padrão PCM mono 16-bit/48kHz: {path} "
            f"({sample_rate} Hz, {channels} canais, {sample_width * 8} bits)."
        )
    return frames


def _convert_to_wav_48k(
    source: Path,
    destination: Path,
    *,
    input_args: Sequence[str] = (),
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp.wav")
    temporary.unlink(missing_ok=True)
    try:
        run_process(
            [
                executable("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *input_args,
                "-i",
                str(source),
                "-map_metadata",
                "-1",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(TARGET_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            timeout=900,
        )
        frames = _validated_wav_frames(temporary)
        os.replace(temporary, destination)
        return frames
    finally:
        temporary.unlink(missing_ok=True)


def _mock_tone() -> bytes:
    frequency = 440.0
    amplitude = 5_000
    return b"".join(
        struct.pack(
            "<h",
            int(amplitude * math.sin(2 * math.pi * frequency * frame / TARGET_SAMPLE_RATE)),
        )
        for frame in range(_MOCK_SPOKEN_FRAMES)
    )


def _synthesize_mock(text: str, destination: Path) -> SynthesisResult:
    tokens = [match.group(0) for match in _WORD_RE.finditer(text)]
    if not tokens:
        raise ValueError("A narração da cena está vazia.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    tone = _mock_tone()
    silence = b"\x00\x00" * (_MOCK_WORD_FRAMES - _MOCK_SPOKEN_FRAMES)
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(TARGET_SAMPLE_RATE)
            for _token in tokens:
                output.writeframesraw(tone)
                output.writeframesraw(silence)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    words = [
        LocalWord(
            token,
            round(index * _MOCK_WORD_FRAMES / TARGET_SAMPLE_RATE, 6),
            round(
                (index * _MOCK_WORD_FRAMES + _MOCK_SPOKEN_FRAMES) / TARGET_SAMPLE_RATE,
                6,
            ),
        )
        for index, token in enumerate(tokens)
    ]
    return SynthesisResult(
        words=words,
        duration_frames=len(tokens) * _MOCK_WORD_FRAMES,
        alignment_source="simulated",
        usage={"characters": len(text), "words": len(words)},
        model_id="mock-deterministic-v1",
        voice_id=None,
    )


def _header_value(headers: httpx.Headers, name: str) -> int | float | str | None:
    value = headers.get(name)
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def _safe_response_usage(
    response: httpx.Response,
    attempts: int,
) -> dict[str, int | float | str | None]:
    usage: dict[str, int | float | str | None] = {"http_attempts": attempts}
    for header in (
        "request-id",
        "character-cost",
        "x-trace-id",
        "current-concurrent-requests",
        "maximum-concurrent-requests",
    ):
        value = _header_value(response.headers, header)
        if value is not None:
            usage[header] = value
    return usage


def _elevenlabs_error(response: httpx.Response) -> RuntimeError:
    code = "unknown_error"
    message = response.reason_phrase or "Falha na API"
    try:
        payload = response.json()
        detail = payload.get("detail", {}) if isinstance(payload, dict) else {}
        if isinstance(detail, dict):
            code = str(detail.get("code") or detail.get("status") or code)
            message = str(detail.get("message") or message)
    except (ValueError, json.JSONDecodeError):
        pass
    return RuntimeError(f"ElevenLabs respondeu HTTP {response.status_code} ({code}): {message}")


def _request_elevenlabs(
    *,
    client: HttpPoster,
    api_key: str,
    voice_id: str,
    body: Mapping[str, Any],
    output_format: str = ELEVENLABS_OUTPUT_FORMAT,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    max_retries: int = ELEVENLABS_MAX_RETRIES,
) -> ElevenLabsResponse:
    endpoint = f"{ELEVENLABS_ENDPOINT}/{quote(voice_id, safe='')}/with-timestamps"
    response: httpx.Response | None = None
    for attempt in range(max_retries + 1):
        response = client.post(
            endpoint,
            params={"output_format": output_format},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
        )
        attempts = attempt + 1
        if response.status_code not in ELEVENLABS_RETRYABLE_STATUSES:
            break
        if attempt >= max_retries:
            break
        ceiling = min(0.5, 0.05 * (2**attempt))
        sleep(max(0.0, min(1.0, jitter())) * ceiling)

    assert response is not None
    if response.status_code < 200 or response.status_code >= 300:
        raise _elevenlabs_error(response)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("A ElevenLabs retornou JSON inválido.") from exc
    if not isinstance(payload, dict):
        raise ValueError("A ElevenLabs retornou um payload inesperado.")
    encoded_audio = payload.get("audio_base64")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise ValueError("A resposta da ElevenLabs não contém audio_base64.")
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("A resposta da ElevenLabs contém base64 inválido.") from exc
    normalized = payload.get("normalized_alignment")
    fallback = payload.get("alignment")
    alignment = normalized if isinstance(normalized, Mapping) and normalized else fallback
    if alignment is not None and not isinstance(alignment, Mapping):
        raise ValueError("A resposta da ElevenLabs contém alinhamento inválido.")
    usage = _safe_response_usage(response, attempts)
    return ElevenLabsResponse(audio=audio, alignment=alignment, usage=usage)


def _elevenlabs_source_spec(output_format: str) -> tuple[str, list[str]]:
    parts = output_format.split("_")
    codec = parts[0].casefold()
    try:
        sample_rate = int(parts[1]) if len(parts) > 1 else None
    except ValueError as exc:
        raise ValueError(f"Formato ElevenLabs inválido: {output_format}") from exc
    if codec == "mp3":
        return ".mp3", []
    if codec == "wav":
        return ".wav", []
    if codec == "opus":
        return ".opus", []
    if codec == "pcm" and sample_rate:
        return ".pcm", ["-f", "s16le", "-ar", str(sample_rate), "-ac", "1"]
    if codec == "ulaw" and sample_rate:
        return ".ulaw", ["-f", "mulaw", "-ar", str(sample_rate), "-ac", "1"]
    if codec == "alaw" and sample_rate:
        return ".alaw", ["-f", "alaw", "-ar", str(sample_rate), "-ac", "1"]
    raise ValueError(f"Formato de áudio ElevenLabs não suportado pelo pipeline: {output_format}")


def _synthesize_elevenlabs(
    text: str,
    destination: Path,
    *,
    api_key: str,
    voice_id: str,
    model_id: str,
    output_format: str,
    language_code: str | None,
    stability: float,
    similarity_boost: float,
    style: float,
    use_speaker_boost: bool,
    previous_text: str | None,
    next_text: str | None,
    client: HttpPoster | None,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
) -> SynthesisResult:
    if not text.strip():
        raise ValueError("A narração da cena está vazia.")
    _validate_tts_text(text)
    source_suffix, input_args = _elevenlabs_source_spec(output_format)
    body: dict[str, Any] = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "use_speaker_boost": use_speaker_boost,
        },
        "apply_text_normalization": "auto",
    }
    if language_code and model_id != "eleven_multilingual_v2":
        body["language_code"] = language_code
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text

    own_client = client is None
    active_client: HttpPoster = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))
    try:
        result = _request_elevenlabs(
            client=active_client,
            api_key=api_key,
            voice_id=voice_id,
            body=body,
            output_format=output_format,
            sleep=sleep,
            jitter=jitter,
        )
    finally:
        if own_client and isinstance(active_client, httpx.Client):
            active_client.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="elevenlabs-", dir=destination.parent) as temporary_dir:
        source = Path(temporary_dir) / f"source{source_suffix}"
        source.write_bytes(result.audio)
        duration_frames = _convert_to_wav_48k(source, destination, input_args=input_args)
    duration = duration_frames / TARGET_SAMPLE_RATE
    words = character_alignment_to_words(result.alignment)
    alignment_source: Literal["provider", "simulated"] = "provider"
    if not words:
        words = _proportional_words(text, duration)
        alignment_source = "simulated"
    words = _clamp_words(words, duration)
    usage = {
        **result.usage,
        "characters": len(text),
        "words": len(words),
        "output-format": output_format,
    }
    return SynthesisResult(
        words=words,
        duration_frames=duration_frames,
        alignment_source=alignment_source,
        usage=usage,
        model_id=model_id,
        voice_id=voice_id,
    )


def _concatenate_wavs(sources: Sequence[Path], destination: Path) -> int:
    if not sources:
        raise ValueError("Não há segmentos de áudio para concatenar.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    total_frames = 0
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(TARGET_SAMPLE_RATE)
            for source_path in sources:
                _validated_wav_frames(source_path)
                with wave.open(str(source_path), "rb") as source:
                    while chunk := source.readframes(65_536):
                        output.writeframesraw(chunk)
                        total_frames += len(chunk) // 2
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    actual_frames = _validated_wav_frames(destination)
    if actual_frames != total_frames:
        raise RuntimeError("A concatenação WAV produziu quantidade inesperada de frames.")
    return actual_frames


def _spoken_token(value: str) -> str:
    return "".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _comment_response_span(text: str, words: Sequence[LocalWord]) -> tuple[int, int] | None:
    response = quoted_comment_response(text)
    if not response:
        return None
    target = [_spoken_token(item) for item in response.split()]
    target = [item for item in target if item]
    spoken = [_spoken_token(item.word) for item in words]
    if not target:
        return None
    for start in range(len(spoken) - len(target) + 1):
        if spoken[start : start + len(target)] == target:
            return start, start + len(target) - 1
    raise ValueError(
        "A palavra ou frase entre aspas do convite aos comentários não apareceu no "
        "alinhamento do áudio."
    )


def _insert_pcm_silence(path: Path, *, at_frame: int, silence_frames: int) -> None:
    if silence_frames <= 0:
        return
    sample_rate, channels, sample_width, total_frames = _wav_properties(path)
    if sample_rate != TARGET_SAMPLE_RATE or channels != 1 or sample_width != 2:
        raise ValueError("A pausa editorial exige WAV PCM mono 16-bit/48kHz.")
    insertion = min(max(0, at_frame), total_frames)
    with wave.open(str(path), "rb") as source:
        before = source.readframes(insertion)
        after = source.readframes(total_frames - insertion)
    temporary = path.with_suffix(path.suffix + ".pause.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(TARGET_SAMPLE_RATE)
            output.writeframesraw(before)
            output.writeframesraw(b"\x00\x00" * silence_frames)
            output.writeframesraw(after)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_comment_response_pause(
    scene: Scene,
    result: SynthesisResult,
    path: Path,
    silence_seconds: float,
) -> SynthesisResult:
    if silence_seconds <= 0:
        return result
    span = _comment_response_span(scene.narration, result.words)
    if span is None:
        return result
    _start_index, end_index = span
    insertion_seconds = result.words[end_index].end
    silence_frames = round(silence_seconds * TARGET_SAMPLE_RATE)
    _insert_pcm_silence(
        path,
        at_frame=round(insertion_seconds * TARGET_SAMPLE_RATE),
        silence_frames=silence_frames,
    )
    shifted = [
        LocalWord(
            item.word,
            round(item.start + (silence_seconds if index > end_index else 0.0), 6),
            round(item.end + (silence_seconds if index > end_index else 0.0), 6),
        )
        for index, item in enumerate(result.words)
    ]
    return SynthesisResult(
        words=shifted,
        duration_frames=result.duration_frames + silence_frames,
        alignment_source=result.alignment_source,
        usage={**result.usage, "comment_response_silence_seconds": silence_seconds},
        model_id=result.model_id,
        voice_id=result.voice_id,
    )


def _effective_elevenlabs_values(config: Any) -> tuple[str | None, str]:
    voice_id = config.voice_id or os.getenv("ELEVENLABS_VOICE_ID")
    model_id = config.model_id or os.getenv("ELEVENLABS_MODEL_ID") or "eleven_flash_v2_5"
    return voice_id, model_id


def _scene_input_hash(
    scene: Scene,
    *,
    previous_scene: Scene | None,
    next_scene: Scene | None,
    provider: str,
    config: Any,
    voice_id: str | None,
    model_id: str | None,
) -> str:
    return sha256_value(
        {
            "scene_id": scene.scene_id,
            "text": scene.narration,
            "previous_text": (
                previous_scene.narration
                if provider == "elevenlabs" and previous_scene is not None
                else None
            ),
            "next_text": (
                next_scene.narration
                if provider == "elevenlabs" and next_scene is not None
                else None
            ),
            "provider": provider,
            "voice_id": voice_id,
            "model_id": model_id,
            "language_code": config.language_code,
            "stability": config.stability,
            "similarity_boost": config.similarity_boost,
            "style": config.style,
            "use_speaker_boost": config.use_speaker_boost,
            "output_format": config.output_format if provider == "elevenlabs" else "wav",
            "sample_rate": TARGET_SAMPLE_RATE,
            "comment_response_silence_seconds": config.comment_response_silence_seconds,
            "mock_contract": 1 if provider == "mock" else None,
        }
    )


def _existing_local_words(segment: AudioSegment, duration: float) -> list[LocalWord]:
    words = [
        LocalWord(
            item.word,
            max(0.0, item.start - segment.start),
            max(0.0, item.end - segment.start),
        )
        for item in segment.words
    ]
    return _clamp_words(words, duration)


def _synthesize_scene(
    *,
    scene: Scene,
    previous_scene: Scene | None,
    next_scene: Scene | None,
    destination: Path,
    provider: str,
    config: Any,
    voice_id: str | None,
    model_id: str | None,
    http_client: HttpPoster | None,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
) -> SynthesisResult:
    if provider == "mock":
        return _synthesize_mock(scene.narration, destination)
    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("Defina ELEVENLABS_API_KEY para usar o provedor ElevenLabs.")
        if not voice_id:
            raise RuntimeError(
                "Configure audio.voice_id no projeto ou ELEVENLABS_VOICE_ID no ambiente."
            )
        assert model_id is not None
        return _synthesize_elevenlabs(
            scene.narration,
            destination,
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            output_format=config.output_format,
            language_code=config.language_code,
            stability=config.stability,
            similarity_boost=config.similarity_boost,
            style=config.style,
            use_speaker_boost=config.use_speaker_boost,
            previous_text=previous_scene.narration if previous_scene else None,
            next_text=next_scene.narration if next_scene else None,
            client=http_client,
            sleep=sleep,
            jitter=jitter,
        )
    raise ValueError(f"Provedor de áudio desconhecido: {provider}")


def generate_audio(
    layout: ProjectLayout,
    *,
    scene_id: str | None = None,
    provider: Literal["mock", "elevenlabs", "local_manual"] | None = None,
    force: bool = False,
    allow_external: bool = False,
    http_client: HttpPoster | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> AudioManifest:
    """Gera um WAV por cena, reutiliza hashes atuais e monta a narração global.

    `allow_external=True` é uma confirmação obrigatória e explícita para qualquer chamada à
    ElevenLabs. O parâmetro `http_client` existe para testes com transporte local; ele não remove
    essa proteção.
    """

    project = layout.load_manifest()
    previous_audio_stage = project.stages["audio"]
    config = project.config.audio
    selected_provider = provider or config.provider
    if selected_provider == "local_manual":
        raise RuntimeError(
            "O provedor local_manual aguarda um arquivo do usuário. Use "
            "`audio prepare-manual` e depois `audio import-manual`."
        )

    scene_manifest = read_model(layout.scenes, SceneManifest)
    scenes = sorted(scene_manifest.scenes, key=lambda item: item.order)
    if not scenes:
        raise ValueError("O manifesto de cenas não contém cenas para narrar.")
    scene_ids = [scene.scene_id for scene in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        raise ValueError("O manifesto de cenas contém scene_id duplicado.")
    if scene_id is not None and scene_id not in set(scene_ids):
        raise ValueError(f"Cena de áudio não encontrada: {scene_id}")

    if selected_provider == "elevenlabs":
        voice_id, model_id = _effective_elevenlabs_values(config)
    else:
        voice_id, model_id = None, "mock-deterministic-v1"

    hashes: dict[str, str] = {}
    for index, scene in enumerate(scenes):
        hashes[scene.scene_id] = _scene_input_hash(
            scene,
            previous_scene=scenes[index - 1] if index > 0 else None,
            next_scene=scenes[index + 1] if index + 1 < len(scenes) else None,
            provider=selected_provider,
            config=config,
            voice_id=voice_id,
            model_id=model_id,
        )
    input_hash = sha256_value(
        {
            "script_version": scene_manifest.script_version,
            "provider": selected_provider,
            "scenes": [
                {"scene_id": scene.scene_id, "order": scene.order, "hash": hashes[scene.scene_id]}
                for scene in scenes
            ],
        }
    )
    if stage_is_current(layout, "audio", input_hash, force=force):
        return read_model(layout.audio, AudioManifest)
    if selected_provider == "elevenlabs" and not allow_external:
        raise RuntimeError(
            "Chamadas à ElevenLabs exigem confirmação explícita com allow_external=True."
        )

    invalidate(
        layout,
        stages=("captions", "metadata", "render", "validation"),
        approvals=("metadata", "final_render"),
        note="A narração foi atualizada.",
    )
    existing: AudioManifest | None = None
    if layout.audio.is_file():
        existing = read_model(layout.audio, AudioManifest)
    existing_by_scene = {item.scene_id: item for item in existing.segments} if existing else {}

    start_stage(layout, "audio", input_hash, provider=selected_provider)
    generated_count = 0
    reused_count = 0
    recovered_count = 0
    recover_failed_attempt = (
        selected_provider == "elevenlabs"
        and not force
        and existing is None
        and previous_audio_stage.status == StageStatus.FAILED
        and previous_audio_stage.input_hash == input_hash
    )
    try:
        local_rows: list[tuple[Scene, int, str, Path, SynthesisResult]] = []
        for index, scene in enumerate(scenes):
            previous = scenes[index - 1] if index > 0 else None
            following = scenes[index + 1] if index + 1 < len(scenes) else None
            current = existing_by_scene.get(scene.scene_id)
            should_force = force and (scene_id is None or scene.scene_id == scene_id)
            can_reuse = (
                not should_force
                and current is not None
                and current.input_hash == hashes[scene.scene_id]
                and current.provider == selected_provider
            )
            if can_reuse:
                current_path = _resolve_artifact(layout, current.path)
                can_reuse = current_path.is_file()
            if can_reuse and current is not None:
                path = _resolve_artifact(layout, current.path)
                frames = _validated_wav_frames(path)
                duration = frames / TARGET_SAMPLE_RATE
                words = _existing_local_words(current, duration)
                if not words:
                    words = _proportional_words(scene.narration, duration)
                result = SynthesisResult(
                    words=words,
                    duration_frames=frames,
                    alignment_source=current.alignment_source,
                    usage=current.usage,
                    model_id=current.model_id,
                    voice_id=current.voice_id,
                )
                local_rows.append((scene, current.version, current.input_hash, path, result))
                reused_count += 1
                continue

            if recover_failed_attempt and current is None and not should_force:
                orphan = _latest_segment(layout, scene.scene_id)
                if orphan is not None:
                    version, path = orphan
                    try:
                        frames = _validated_wav_frames(path)
                    except ValueError:
                        pass
                    else:
                        duration = frames / TARGET_SAMPLE_RATE
                        result = SynthesisResult(
                            words=_proportional_words(scene.narration, duration),
                            duration_frames=frames,
                            alignment_source="simulated",
                            usage={"recovered_from_failed_attempt": 1},
                            model_id=model_id,
                            voice_id=voice_id,
                        )
                        local_rows.append((scene, version, hashes[scene.scene_id], path, result))
                        reused_count += 1
                        recovered_count += 1
                        continue

            version = _next_segment_version(layout, scene.scene_id)
            path = _segment_destination(layout, scene.scene_id, version)
            result = _synthesize_scene(
                scene=scene,
                previous_scene=previous,
                next_scene=following,
                destination=path,
                provider=selected_provider,
                config=config,
                voice_id=voice_id,
                model_id=model_id,
                http_client=http_client,
                sleep=sleep,
                jitter=jitter,
            )
            result = _apply_comment_response_pause(
                scene,
                result,
                path,
                config.comment_response_silence_seconds,
            )
            local_rows.append((scene, version, hashes[scene.scene_id], path, result))
            generated_count += 1

        segments: list[AudioSegment] = []
        global_words: list[WordTiming] = []
        offset_frames = 0
        for scene, version, segment_hash, path, result in local_rows:
            start = offset_frames / TARGET_SAMPLE_RATE
            duration = result.duration_frames / TARGET_SAMPLE_RATE
            end = (offset_frames + result.duration_frames) / TARGET_SAMPLE_RATE
            segment_words: list[WordTiming] = []
            for local_word in result.words:
                word = WordTiming(
                    index=len(global_words),
                    word=local_word.word,
                    start=round(start + local_word.start, 6),
                    end=round(start + local_word.end, 6),
                    scene_id=scene.scene_id,
                )
                global_words.append(word)
                segment_words.append(word)
            segments.append(
                AudioSegment(
                    scene_id=scene.scene_id,
                    version=version,
                    text=scene.narration,
                    input_hash=segment_hash,
                    provider=selected_provider,
                    model_id=result.model_id,
                    voice_id=result.voice_id,
                    path=relative_posix(path, layout.root),
                    start=round(start, 6),
                    end=round(end, 6),
                    duration=round(duration, 6),
                    alignment_source=result.alignment_source,
                    words=segment_words,
                    usage=result.usage,
                )
            )
            offset_frames += result.duration_frames

        narration_frames = _concatenate_wavs(
            [path for _scene, _version, _hash, path, _result in local_rows],
            layout.narration_audio,
        )
        if narration_frames != offset_frames:
            raise RuntimeError("A duração concatenada diverge da soma dos segmentos.")
        audio_manifest = AudioManifest(
            generated_at=utc_now(),
            provider=selected_provider,
            sample_rate=TARGET_SAMPLE_RATE,
            narration_path=relative_posix(layout.narration_audio, layout.root),
            duration=round(narration_frames / TARGET_SAMPLE_RATE, 6),
            segments=segments,
            words=global_words,
        )
        write_json(layout.audio, audio_manifest)
        artifacts = [
            relative_posix(layout.audio, layout.root),
            relative_posix(layout.narration_audio, layout.root),
            *(item.path for item in segments),
        ]
        character_cost = sum(
            float(item.usage.get("character-cost", 0) or 0)
            for item in segments
            if isinstance(item.usage.get("character-cost", 0), (int, float))
        )
        complete_stage(
            layout,
            "audio",
            input_hash,
            artifacts,
            provider=selected_provider,
            usage={
                "segments": len(segments),
                "generated_segments": generated_count,
                "reused_segments": reused_count,
                "recovered_segments": recovered_count,
                "words": len(global_words),
                "character_cost": character_cost,
            },
            note=(
                f"{generated_count} segmento(s) gerado(s), {reused_count} reutilizado(s), "
                f"incluindo {recovered_count} recuperado(s) de tentativa interrompida."
            ),
        )
        return audio_manifest
    except Exception as exc:
        fail_stage(layout, "audio", exc)
        raise


generate_narration = generate_audio
