from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from math import gcd
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    if not slug:
        raise ValueError("Não foi possível gerar um identificador a partir do texto informado.")
    return slug


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=repr)
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compact_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove presentation-only JSON Schema fields from agent handoffs.

    Pydantic validates the imported package with the complete model, so titles,
    descriptions and defaults only spend context in the external handoff.  Keep
    structural constraints, references, enums and required fields intact.
    """

    presentation_keys = {"title", "description", "default", "examples"}

    def compact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: compact(item) for key, item in value.items() if key not in presentation_keys
            }
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    return compact(schema)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tokenize_words(text: str) -> list[str]:
    return re.findall(r"\S+", text.strip(), flags=re.UNICODE)


def normalize_spoken_text(text: str) -> str:
    return " ".join(tokenize_words(text)).casefold()


def aspect_ratio(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("As dimensões precisam ser positivas.")
    divisor = gcd(width, height)
    return width // divisor, height // divisor


def aspect_ratio_label(width: int, height: int) -> str:
    horizontal, vertical = aspect_ratio(width, height)
    return f"{horizontal}:{vertical}"


def matches_aspect_ratio(
    width: int,
    height: int,
    expected_width: int,
    expected_height: int,
    *,
    tolerance: float = 0.02,
) -> bool:
    if min(width, height, expected_width, expected_height) <= 0:
        return False
    return abs(width / height - expected_width / expected_height) <= tolerance


def executable(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"Executável obrigatório não encontrado no PATH: {name}")
    return found


def run_process(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        timeout=timeout,
        shell=False,
    )


def ffprobe_json(path: Path) -> dict[str, Any]:
    result = run_process(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def media_duration(path: Path) -> float:
    data = ffprobe_json(path)
    duration = data.get("format", {}).get("duration")
    if duration is None:
        durations = [stream.get("duration") for stream in data.get("streams", [])]
        duration = next((item for item in durations if item is not None), None)
    if duration is None:
        raise RuntimeError(f"FFprobe não informou a duração de {path}")
    return float(duration)


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
