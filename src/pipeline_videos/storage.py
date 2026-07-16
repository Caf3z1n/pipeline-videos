from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pipeline_videos.models import ProjectManifest, StageRecord
from pipeline_videos.utils import slugify, utc_now

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECTS_DIR = REPO_ROOT / "projects"
LONG_PROJECTS_DIRNAME = "videos-longos"
SHORT_PROJECTS_DIRNAME = "shorts"
PROJECT_CATEGORY_DIRS = (LONG_PROJECTS_DIRNAME, SHORT_PROJECTS_DIRNAME)


def write_json(path: Path, value: BaseModel | dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Arquivo obrigatório não encontrado: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON inválido em {path}: linha {exc.lineno}, coluna {exc.colno}"
        ) from exc


def read_model[TModel: BaseModel](path: Path, model: type[TModel]) -> TModel:
    return model.model_validate(read_json(path))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content.rstrip() + "\n", encoding="utf-8")
    os.replace(temp, path)


@dataclass(frozen=True)
class ProjectLayout:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "project.json"

    @property
    def briefing(self) -> Path:
        return self.root / "editorial" / "briefing.json"

    @property
    def sources(self) -> Path:
        return self.root / "editorial" / "sources.json"

    @property
    def decisions(self) -> Path:
        return self.root / "editorial" / "decisions.jsonl"

    @property
    def script(self) -> Path:
        return self.root / "script" / "script.json"

    @property
    def creative_contract(self) -> Path:
        return self.root / "script" / "creative-contract.json"

    @property
    def shorts_script_queue(self) -> Path:
        return self.root / "script" / "shorts-queue.json"

    @property
    def shorts_script_context(self) -> Path:
        return self.root / "script" / "shorts-context.json"

    @property
    def shorts_script_staging(self) -> Path:
        return self.root / "script" / "staging" / "shorts-script-specialist"

    @property
    def shorts_script_package_staging(self) -> Path:
        return self.shorts_script_staging / "package.json"

    @property
    def shorts_script_package(self) -> Path:
        return self.root / "script" / "shorts-package.json"

    @property
    def shorts_script_strategy(self) -> Path:
        return self.root / "script" / "retention-strategy.json"

    @property
    def narration(self) -> Path:
        return self.root / "script" / "narration.txt"

    @property
    def biblical_review(self) -> Path:
        return self.root / "reviews" / "biblical-consistency.json"

    @property
    def style_profile(self) -> Path:
        return self.root / "visual" / "style-profile.json"

    @property
    def palette(self) -> Path:
        return self.root / "visual" / "palette.json"

    @property
    def continuity(self) -> Path:
        return self.root / "visual" / "continuity.json"

    @property
    def scenes(self) -> Path:
        return self.root / "scenes" / "manifest.json"

    @property
    def images(self) -> Path:
        return self.root / "images" / "manifest.json"

    @property
    def image_queue(self) -> Path:
        return self.root / "images" / "codex-queue.json"

    @property
    def image_assets(self) -> Path:
        return self.root / "images" / "assets"

    @property
    def image_staging(self) -> Path:
        return self.root / "images" / "staging"

    @property
    def image_jobs(self) -> Path:
        return self.root / "images" / "jobs"

    @property
    def audio(self) -> Path:
        return self.root / "audio" / "manifest.json"

    @property
    def audio_segments(self) -> Path:
        return self.root / "audio" / "segments"

    @property
    def manual_audio_queue(self) -> Path:
        return self.root / "audio" / "manual-queue.json"

    @property
    def manual_audio_staging(self) -> Path:
        return self.root / "audio" / "staging"

    @property
    def narration_audio(self) -> Path:
        return self.root / "audio" / "narration.wav"

    @property
    def music_manifest(self) -> Path:
        return self.root / "audio" / "music" / "manifest.json"

    @property
    def music_assets(self) -> Path:
        return self.root / "audio" / "music"

    @property
    def captions(self) -> Path:
        return self.root / "captions" / "manifest.json"

    @property
    def ass(self) -> Path:
        return self.root / "captions" / "captions.ass"

    @property
    def srt(self) -> Path:
        return self.root / "captions" / "captions.srt"

    @property
    def vtt(self) -> Path:
        return self.root / "captions" / "captions.vtt"

    @property
    def render_config(self) -> Path:
        return self.root / "render" / "config.json"

    @property
    def render_work(self) -> Path:
        return self.root / "render" / "work"

    @property
    def final_video(self) -> Path:
        return self.root / "render" / "final.mp4"

    @property
    def preview_video(self) -> Path:
        return self.root / "render" / "preview.mp4"

    @property
    def draft_preview_video(self) -> Path:
        return self.root / "render" / "draft-preview.mp4"

    @property
    def metadata(self) -> Path:
        return self.root / "youtube" / "metadata.json"

    @property
    def description(self) -> Path:
        return self.root / "youtube" / "description.md"

    @property
    def thumbnail_prompt(self) -> Path:
        return self.root / "youtube" / "thumbnail-prompt.json"

    @property
    def growth_queue(self) -> Path:
        return self.root / "youtube" / "growth-queue.json"

    @property
    def growth_context(self) -> Path:
        return self.root / "youtube" / "growth-context.json"

    @property
    def growth_staging(self) -> Path:
        return self.root / "youtube" / "staging" / "youtube-growth-specialist"

    @property
    def growth_package_staging(self) -> Path:
        return self.growth_staging / "package.json"

    @property
    def growth_package(self) -> Path:
        return self.root / "youtube" / "growth-package.json"

    @property
    def growth_strategy(self) -> Path:
        return self.root / "youtube" / "seo-strategy.json"

    @property
    def thumbnail(self) -> Path:
        return self.root / "youtube" / "thumbnail.png"

    @property
    def publication(self) -> Path:
        return self.root / "youtube" / "publication.json"

    @property
    def publication_thumbnail(self) -> Path:
        return self.root / "youtube" / "publication-thumbnail.jpg"

    @property
    def publication_archive(self) -> Path:
        return self.root / "youtube" / "publications"

    @property
    def experiments(self) -> Path:
        return self.root / "analytics" / "experiments.json"

    @property
    def events(self) -> Path:
        return self.root / "logs" / "events.jsonl"

    @property
    def autopilot_state(self) -> Path:
        return self.root / "logs" / "autopilot-state.json"

    def ensure_directories(self) -> None:
        for directory in (
            self.briefing.parent,
            self.script.parent,
            self.shorts_script_staging,
            self.biblical_review.parent,
            self.style_profile.parent,
            self.scenes.parent,
            self.image_assets,
            self.image_staging,
            self.image_jobs,
            self.audio_segments,
            self.manual_audio_staging,
            self.music_assets,
            self.captions.parent,
            self.render_work,
            self.metadata.parent,
            self.growth_staging,
            self.experiments.parent,
            self.events.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> ProjectManifest:
        raw_manifest = read_json(self.manifest)
        manifest = ProjectManifest.model_validate(raw_manifest)
        raw_captions = raw_manifest.get("config", {}).get("captions", {})
        if (
            manifest.video_format == "short"
            and isinstance(raw_captions, dict)
            and "vertical_position" not in raw_captions
        ):
            manifest.config.captions.vertical_position = "center"
        manifest.stages.setdefault(
            "shorts_script",
            StageRecord(
                status=(
                    "pending"
                    if manifest.video_format == "short" and manifest.config.shorts_script.enabled
                    else "skipped"
                ),
                updated_at=utc_now(),
                note=(
                    None
                    if manifest.video_format == "short" and manifest.config.shorts_script.enabled
                    else "Etapa exclusiva de Shorts."
                ),
            ),
        )
        manifest.stages.setdefault("music", StageRecord(updated_at=utc_now()))
        manifest.stages.setdefault("youtube_growth", StageRecord(updated_at=utc_now()))
        manifest.stages.setdefault("publication", StageRecord(updated_at=utc_now()))
        return manifest

    def save_manifest(self, manifest: ProjectManifest) -> None:
        manifest.updated_at = utc_now()
        write_json(self.manifest, manifest)

    def append_event(self, event: dict[str, Any]) -> None:
        self.events.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": utc_now(), **event}
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_decision(self, decision: dict[str, Any]) -> None:
        self.decisions.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": utc_now(), **decision}
        with self.decisions.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def resolve_project(reference: str | Path, projects_dir: Path | None = None) -> ProjectLayout:
    candidate = Path(reference).expanduser()
    options: list[Path] = []
    if candidate.is_absolute() or candidate.parts:
        options.append(candidate)
    bases = [(projects_dir or DEFAULT_PROJECTS_DIR).expanduser(), DEFAULT_PROJECTS_DIR]
    for base in bases:
        options.append(base / candidate)
        options.extend(base / category / candidate for category in PROJECT_CATEGORY_DIRS)

    seen: set[Path] = set()
    for option in options:
        resolved = option.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "project.json").is_file():
            return ProjectLayout(resolved)

    reference_text = str(reference)
    reference_slug = slugify(candidate.name)
    matches: list[Path] = []
    for base in bases:
        base = base.resolve()
        for category in PROJECT_CATEGORY_DIRS:
            category_dir = base / category
            if not category_dir.is_dir():
                continue
            for manifest_path in category_dir.glob("*/project.json"):
                data = read_json(manifest_path)
                project_id = str(data.get("project_id", ""))
                aliases = {str(item) for item in data.get("aliases", [])}
                folder = manifest_path.parent.name
                if (
                    reference_text == project_id
                    or reference_text in aliases
                    or folder.endswith(f"_{reference_slug}")
                ):
                    matches.append(manifest_path.parent.resolve())
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return ProjectLayout(matches[0])
    if len(matches) > 1:
        paths = ", ".join(str(path) for path in matches)
        raise ValueError(f"A referência '{reference}' é ambígua. Projetos encontrados: {paths}")
    checked = ", ".join(str(path) for path in seen)
    raise FileNotFoundError(
        f"Projeto '{reference}' não encontrado. Caminhos verificados: {checked}"
    )
