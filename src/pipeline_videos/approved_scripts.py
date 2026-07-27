from __future__ import annotations

import os
import re
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    PipelineModel,
    ScriptDocument,
    ShortsScriptStrategy,
    SourceLibrary,
    YouTubeMetadata,
)
from pipeline_videos.project import create_project
from pipeline_videos.review import validate_draft_preflight
from pipeline_videos.state import complete_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value, slugify, utc_now

APPROVED_SCRIPT_SCHEMA_VERSION = "1.0"
APPROVED_PRODUCTION_STATE_SCHEMA_VERSION = "1.0"
APPROVED_SCRIPT_ID_PATTERN = re.compile(r"^approved-[a-z0-9][a-z0-9-]{0,72}-[a-f0-9]{8}$")
CONTENT_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
HASHTAG_PATTERN = re.compile(r"(?<!\w)#[\wÀ-ÿ]+", flags=re.UNICODE)
ApprovedScriptStatus = Literal["draft", "approved", "reserved", "produced", "withdrawn"]
PRODUCTION_STATUS_ORDER = {"approved": 0, "reserved": 1, "produced": 2}


class ApprovedCtaOption(PipelineModel):
    option_id: str = Field(min_length=1, max_length=40)
    spoken_text: str = Field(min_length=1, max_length=240)
    mode: Literal["comment", "subscribe", "share", "like"]

    @field_validator("option_id", "spoken_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class ApprovedShortScript(PipelineModel):
    schema_version: Literal["1.0"] = APPROVED_SCRIPT_SCHEMA_VERSION
    approved_script_id: str
    status: ApprovedScriptStatus = "draft"
    label: str = Field(min_length=1, max_length=120)
    source_project_id: str
    source_project_path: str
    target_duration_seconds: int = Field(ge=1, le=180)
    editorial_profile: str
    briefing: Briefing
    sources: SourceLibrary
    script: ScriptDocument
    biblical_review: BiblicalReview
    strategy: ShortsScriptStrategy
    metadata: YouTubeMetadata | None = None
    cta_options: list[ApprovedCtaOption] = Field(default_factory=list, max_length=8)
    selected_cta_id: str | None = None
    voice_variant: Literal["jesus_1", "jesus_2"] = "jesus_1"
    music_track: Literal["leaning", "aloboi"] = "leaning"
    content_hash: str
    created_at: str
    updated_at: str
    approved_at: str | None = None
    approved_by: str | None = None
    approval_note: str | None = None
    reserved_by_job_id: str | None = None
    reserved_at: str | None = None
    produced_at: str | None = None
    youtube_video_id: str | None = None
    youtube_url: str | None = None

    @field_validator("approved_script_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not APPROVED_SCRIPT_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de roteiro aprovado inválido.")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self) -> ApprovedShortScript:
        if self.biblical_review.script_version != self.script.version:
            raise ValueError("A revisão bíblica não corresponde à versão congelada do roteiro.")
        if self.biblical_review.has_open_blockers:
            raise ValueError("Roteiro com bloqueio bíblico aberto não pode entrar nesta fila.")
        option_ids = [option.option_id for option in self.cta_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("Cada opção de CTA precisa ter um ID único.")
        if self.selected_cta_id is not None:
            selected = next(
                (item for item in self.cta_options if item.option_id == self.selected_cta_id),
                None,
            )
            if selected is None:
                raise ValueError("O CTA selecionado não existe entre as opções registradas.")
            if _normalize(selected.spoken_text) not in _normalize(self.script.narration_text()):
                raise ValueError("O CTA selecionado precisa constar literalmente na narração.")
        if self.metadata is not None:
            hashtags = HASHTAG_PATTERN.findall(self.metadata.description)
            standards = [
                tag.casefold() for tag in hashtags if tag.casefold() in {"#biblia", "#jesus"}
            ]
            specific = [tag for tag in hashtags if tag.casefold() not in {"#biblia", "#jesus"}]
            if (
                len(hashtags) != 5
                or len(specific) != 3
                or set(standards)
                != {
                    "#biblia",
                    "#jesus",
                }
            ):
                raise ValueError(
                    "O SEO aprovado exige três hashtags específicas, #biblia e #jesus."
                )
            upload_tags = [tag.strip() for tag in self.metadata.topics if tag.strip()]
            if not 10 <= len(upload_tags) <= 12:
                raise ValueError("O SEO aprovado exige entre dez e doze tags internas.")
            if any("#" in tag for tag in upload_tags):
                raise ValueError("As tags internas do YouTube não usam #.")
            if len({tag.casefold() for tag in upload_tags}) != len(upload_tags):
                raise ValueError("As tags internas do YouTube precisam ser distintas.")
        _validate_scene_coverage(self.script, self.strategy)
        if self.content_hash != approved_content_hash(self):
            raise ValueError("O hash do pacote aprovado não corresponde ao conteúdo.")
        return self


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _validate_scene_coverage(script: ScriptDocument, strategy: ShortsScriptStrategy) -> None:
    if not strategy.visual_scenes:
        raise ValueError("O pacote pré-aprovado precisa incluir o plano visual completo.")
    by_section: dict[str, list[str]] = {}
    for scene in strategy.visual_scenes:
        by_section.setdefault(scene.section_id, []).append(scene.narration)
    for section in script.sections:
        planned = _normalize(" ".join(by_section.get(section.section_id, [])))
        if planned != _normalize(section.narration):
            raise ValueError(
                f"O plano visual não reconstrói exatamente a seção {section.section_id}."
            )


def _default_cta_options(
    script: ScriptDocument,
    strategy: ShortsScriptStrategy,
) -> tuple[list[ApprovedCtaOption], str | None]:
    narration = _normalize(script.narration_text())
    current = strategy.cta.strip()
    options: list[ApprovedCtaOption] = []
    selected: str | None = None
    if current and _normalize(current) in narration:
        mode: Literal["comment", "subscribe", "share", "like"] = (
            "comment"
            if "coment" in current.casefold()
            else "subscribe"
            if "inscrev" in current.casefold()
            else "like"
            if "curta" in current.casefold() or "like" in current.casefold()
            else "share"
        )
        options.append(
            ApprovedCtaOption(
                option_id="atual",
                spoken_text=current,
                mode=mode,
            )
        )
        selected = "atual"
    defaults = (
        ApprovedCtaOption(
            option_id="pergunta",
            spoken_text=("Comente qual dúvida bíblica você quer ver respondida no próximo vídeo."),
            mode="comment",
        ),
        ApprovedCtaOption(
            option_id="compartilhar",
            spoken_text="Compartilhe com alguém que também faria essa pergunta.",
            mode="share",
        ),
        ApprovedCtaOption(
            option_id="acompanhar",
            spoken_text=(
                "Inscreva-se para acompanhar outras perguntas bíblicas explicadas com clareza."
            ),
            mode="subscribe",
        ),
        ApprovedCtaOption(
            option_id="curtir",
            spoken_text="Curta se esta verdade trouxe uma nova compreensão para você.",
            mode="like",
        ),
    )
    existing = {_normalize(item.spoken_text) for item in options}
    options.extend(item for item in defaults if _normalize(item.spoken_text) not in existing)
    return options, selected


def _compact_visual_plan(
    strategy: ShortsScriptStrategy,
    *,
    max_scenes: int,
) -> ShortsScriptStrategy:
    scenes = list(strategy.visual_scenes)
    while len(scenes) > max_scenes:
        merge_index = next(
            (
                index
                for index in range(1, len(scenes))
                if scenes[index - 1].section_id == scenes[index].section_id
            ),
            None,
        )
        if merge_index is None:
            break
        first = scenes[merge_index - 1]
        second = scenes[merge_index]
        merged = first.model_copy(
            update={
                "narration": f"{first.narration.rstrip()} {second.narration.lstrip()}",
                "scene_description": (
                    f"{first.scene_description.rstrip()} A mesma composição sustenta "
                    "visualmente todo este bloco de narração."
                ),
                "visual_relation": (
                    first.visual_relation
                    if first.visual_relation == second.visual_relation
                    else "contextual"
                ),
            }
        )
        scenes[merge_index - 1 : merge_index + 1] = [merged]
    return strategy.model_copy(update={"visual_scenes": scenes})


def _hashtag(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    ignored = {
        "a",
        "as",
        "com",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "por",
        "que",
        "se",
        "um",
        "uma",
    }
    words = [
        word for word in re.findall(r"[A-Za-z0-9]+", ascii_value) if word.casefold() not in ignored
    ]
    joined = "".join(word[:1].upper() + word[1:] for word in words)[:40]
    return f"#{joined}" if joined else ""


def _normalize_specific_hashtags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        hashtag = _hashtag(value.lstrip("#"))
        if not hashtag or hashtag.casefold() in {"#biblia", "#jesus"}:
            continue
        if hashtag.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(hashtag)
    if len(normalized) != 3:
        raise ValueError("Informe exatamente três hashtags específicas e distintas.")
    return normalized


def _approved_upload_tags(
    *,
    briefing: Briefing,
    title: str,
    existing: YouTubeMetadata | None,
) -> list[str]:
    focus = title.split("|", 1)[0].strip()
    book = re.sub(r"\s+\d.*$", "", briefing.main_passage).strip()
    candidates = [
        focus,
        briefing.main_passage,
        briefing.central_theme,
        briefing.central_question,
        *(existing.topics if existing else []),
        f"{focus} explicado",
        f"{briefing.main_passage} explicado",
        f"livro de {book}" if book else "",
        "curiosidades bíblicas",
        "perguntas sobre a Bíblia",
        "explicação bíblica",
        "contexto bíblico",
        "estudo bíblico",
        "Jesus na Bíblia",
        "Bíblia em português",
    ]
    tags: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        tag = " ".join(candidate.replace("#", "").split()).strip(" ,")[:60]
        normalized = tag.casefold()
        if not tag or normalized in seen:
            continue
        tags.append(tag)
        seen.add(normalized)
        if len(tags) == 12:
            break
    if len(tags) < 10:
        raise ValueError("Não foi possível gerar ao menos dez tags internas distintas.")
    return tags


def _approved_metadata(
    *,
    briefing: Briefing,
    script: ScriptDocument,
    existing: YouTubeMetadata | None,
    title: str | None = None,
    description: str | None = None,
    specific_hashtags: list[str] | None = None,
) -> YouTubeMetadata:
    resolved_title = (title or (existing.title if existing else "") or script.title).strip()
    if not resolved_title or len(resolved_title) > 100:
        raise ValueError("O título SEO precisa ter entre 1 e 100 caracteres.")
    base_description = (
        description
        or (existing.description if existing else "")
        or "\n\n".join(
            part
            for part in (
                (
                    f"{briefing.central_question} — {briefing.main_passage}."
                    if briefing.central_question.strip()
                    else f"{briefing.central_theme} — {briefing.main_passage}."
                ),
                briefing.viewer_outcome or briefing.central_theme,
            )
            if part.strip(" —.")
        )
    )
    base_description = HASHTAG_PATTERN.sub("", base_description)
    base_description = re.sub(r"[ \t]+\n", "\n", base_description)
    base_description = re.sub(r"\n{3,}", "\n\n", base_description).strip()
    if not base_description:
        raise ValueError("A descrição SEO não pode ficar vazia.")
    if specific_hashtags is None:
        candidates = [
            resolved_title.split("|", 1)[0],
            briefing.main_passage,
            briefing.central_theme,
            briefing.central_question,
            *((existing.topics if existing else [])[:5]),
            "Curiosidade bíblica",
            "Explicação bíblica",
            "Dúvida bíblica",
        ]
        derived: list[str] = []
        for candidate in candidates:
            hashtag = _hashtag(candidate)
            if (
                hashtag
                and hashtag.casefold() not in {"#biblia", "#jesus"}
                and hashtag.casefold() not in {item.casefold() for item in derived}
            ):
                derived.append(hashtag)
            if len(derived) == 3:
                break
        specific_hashtags = derived
    resolved_hashtags = _normalize_specific_hashtags(specific_hashtags)
    description_with_hashtags = (
        f"{base_description}\n\n{' '.join([*resolved_hashtags, '#biblia', '#jesus'])}"
    )
    topics = _approved_upload_tags(
        briefing=briefing,
        title=resolved_title,
        existing=existing,
    )
    return YouTubeMetadata(
        status="draft",
        title=resolved_title,
        alternative_titles=[],
        description=description_with_hashtags,
        chapters=[],
        topics=topics,
        pinned_comment=(
            existing.pinned_comment
            if existing and existing.pinned_comment.strip()
            else "Qual dúvida bíblica você gostaria de ver explicada a seguir?"
        ),
        publication_visibility="private",
    )


def approved_specific_hashtags(metadata: YouTubeMetadata) -> list[str]:
    return [
        hashtag
        for hashtag in HASHTAG_PATTERN.findall(metadata.description)
        if hashtag.casefold() not in {"#biblia", "#jesus"}
    ]


def approved_content_payload(script: ApprovedShortScript) -> dict[str, Any]:
    return {
        "label": script.label,
        "source_project_id": script.source_project_id,
        "target_duration_seconds": script.target_duration_seconds,
        "editorial_profile": script.editorial_profile,
        "briefing": script.briefing,
        "sources": script.sources,
        "script": script.script,
        "biblical_review": script.biblical_review,
        "strategy": script.strategy,
        "metadata": script.metadata,
        "cta_options": script.cta_options,
        "selected_cta_id": script.selected_cta_id,
        "voice_variant": script.voice_variant,
        "music_track": script.music_track,
    }


def approved_content_hash(script: ApprovedShortScript) -> str:
    return sha256_value(approved_content_payload(script))


def _approved_production_state_item(script: ApprovedShortScript) -> dict[str, Any]:
    return {
        "approved_script_id": script.approved_script_id,
        "content_hash": script.content_hash,
        "label": script.label,
        "status": script.status,
        "updated_at": script.updated_at,
        "reserved_by_job_id": script.reserved_by_job_id,
        "reserved_at": script.reserved_at,
        "produced_at": script.produced_at,
        "youtube_video_id": script.youtube_video_id,
        "youtube_url": script.youtube_url,
    }


def _validate_production_state_snapshot(
    snapshot: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    if snapshot.get("schema_version") != APPROVED_PRODUCTION_STATE_SCHEMA_VERSION:
        raise ValueError("Versão do snapshot de produção inválida.")
    exported_at = snapshot.get("exported_at")
    if not isinstance(exported_at, str) or not exported_at.strip():
        raise ValueError("Snapshot de produção sem exported_at válido.")
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Snapshot de produção sem lista de itens válida.")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Item inválido no snapshot de produção.")
        script_id = raw.get("approved_script_id")
        content_hash = raw.get("content_hash")
        label = raw.get("label")
        status = raw.get("status")
        updated_at = raw.get("updated_at")
        if not isinstance(script_id, str) or not APPROVED_SCRIPT_ID_PATTERN.fullmatch(script_id):
            raise ValueError("ID inválido no snapshot de produção.")
        if script_id in seen:
            raise ValueError(f"ID duplicado no snapshot de produção: {script_id}")
        if not isinstance(content_hash, str) or not CONTENT_HASH_PATTERN.fullmatch(content_hash):
            raise ValueError(f"Hash inválido no snapshot de produção: {script_id}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Título inválido no snapshot de produção: {script_id}")
        if status not in PRODUCTION_STATUS_ORDER:
            raise ValueError(f"Estado inválido no snapshot de produção: {script_id}")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError(f"updated_at inválido no snapshot de produção: {script_id}")
        item = {
            "approved_script_id": script_id,
            "content_hash": content_hash,
            "label": label.strip(),
            "status": status,
            "updated_at": updated_at,
            "reserved_by_job_id": raw.get("reserved_by_job_id"),
            "reserved_at": raw.get("reserved_at"),
            "produced_at": raw.get("produced_at"),
            "youtube_video_id": raw.get("youtube_video_id"),
            "youtube_url": raw.get("youtube_url"),
        }
        for field in (
            "reserved_by_job_id",
            "reserved_at",
            "produced_at",
            "youtube_video_id",
            "youtube_url",
        ):
            if item[field] is not None and not isinstance(item[field], str):
                raise ValueError(f"{field} inválido no snapshot de produção: {script_id}")
        seen.add(script_id)
        items.append(item)
    return exported_at, items


class ApprovedScriptStore:
    statuses: tuple[ApprovedScriptStatus, ...] = (
        "draft",
        "approved",
        "reserved",
        "produced",
        "withdrawn",
    )

    def __init__(self, jobs_root: Path) -> None:
        self.root = jobs_root.expanduser().resolve() / "approved-scripts"
        for status in self.statuses:
            (self.root / status).mkdir(parents=True, exist_ok=True)
        self.remote_state_path = self.root / "remote-production-state.json"

    def path(self, script_id: str, status: ApprovedScriptStatus) -> Path:
        if not APPROVED_SCRIPT_ID_PATTERN.fullmatch(script_id):
            raise ValueError("ID de roteiro aprovado inválido.")
        return self.root / status / f"{script_id}.json"

    def find(self, script_id: str) -> tuple[ApprovedShortScript, Path]:
        for status in self.statuses:
            path = self.path(script_id, status)
            if path.is_file():
                script = ApprovedShortScript.model_validate_json(path.read_text(encoding="utf-8"))
                return script, path
        raise FileNotFoundError(f"Roteiro pré-aprovado não encontrado: {script_id}")

    def save(self, script: ApprovedShortScript) -> Path:
        path = self.path(script.approved_script_id, script.status)
        write_json(path, script)
        with suppress(OSError):
            path.chmod(0o600)
        return path

    def list(self, *, status: ApprovedScriptStatus | None = None) -> list[ApprovedShortScript]:
        statuses = (status,) if status else self.statuses
        items = [
            ApprovedShortScript.model_validate_json(path.read_text(encoding="utf-8"))
            for current in statuses
            for path in (self.root / current).glob("approved-*.json")
        ]
        return sorted(items, key=lambda item: item.created_at)

    def production_state_snapshot(self) -> dict[str, Any]:
        items = [
            _approved_production_state_item(script)
            for script in self.list()
            if script.status in PRODUCTION_STATUS_ORDER
        ]
        return {
            "schema_version": APPROVED_PRODUCTION_STATE_SCHEMA_VERSION,
            "exported_at": utc_now(),
            "counts": {
                status: sum(item["status"] == status for item in items)
                for status in PRODUCTION_STATUS_ORDER
            },
            "items": items,
        }

    def import_approved_snapshot(
        self,
        script: ApprovedShortScript,
    ) -> tuple[ApprovedShortScript, bool]:
        if script.status != "approved":
            raise ValueError("Somente um snapshot aprovado pode ser importado para produção.")
        try:
            existing, _ = self.find(script.approved_script_id)
        except FileNotFoundError:
            self.save(script)
            return script, True
        if existing.content_hash != script.content_hash:
            raise RuntimeError(f"O pacote {script.approved_script_id} já existe com outro hash.")
        return existing, False

    def merge_production_state(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        exported_at, remote_items = _validate_production_state_snapshot(snapshot)
        local_by_id = {script.approved_script_id: script for script in self.list()}
        resolved: list[tuple[ApprovedShortScript, Path, dict[str, Any]]] = []
        missing_local: list[str] = []

        # Validate the complete snapshot before mutating any local state.
        for item in remote_items:
            script_id = item["approved_script_id"]
            local = local_by_id.get(script_id)
            if local is None:
                missing_local.append(script_id)
                continue
            if local.content_hash != item["content_hash"]:
                raise RuntimeError(
                    f"Hash divergente para {script_id}; nenhuma alteração foi aplicada."
                )
            if local.status == "withdrawn":
                continue
            if local.status == "draft":
                raise RuntimeError(
                    f"O roteiro local {script_id} ainda é rascunho; nenhuma alteração foi aplicada."
                )
            resolved.append((local, self.find(script_id)[1], item))

        advanced = 0
        enriched = 0
        unchanged = 0
        regressions_ignored = 0
        for local, old_path, item in resolved:
            local_rank = PRODUCTION_STATUS_ORDER[local.status]
            remote_rank = PRODUCTION_STATUS_ORDER[item["status"]]
            if remote_rank < local_rank:
                regressions_ignored += 1
                continue
            updates = {
                "status": item["status"],
                "reserved_by_job_id": item.get("reserved_by_job_id"),
                "reserved_at": item.get("reserved_at"),
                "produced_at": item.get("produced_at"),
                "youtube_video_id": item.get("youtube_video_id"),
                "youtube_url": item.get("youtube_url"),
                "updated_at": item["updated_at"],
            }
            updated = local.model_copy(update=updates)
            if updated == local:
                unchanged += 1
                continue
            if remote_rank > local_rank:
                advanced += 1
            else:
                enriched += 1
            new_path = self.save(updated)
            if old_path != new_path:
                old_path.unlink(missing_ok=True)

        write_json(self.remote_state_path, snapshot)
        with suppress(OSError):
            self.remote_state_path.chmod(0o600)
        overview = self.production_overview()
        return {
            "schema_version": APPROVED_PRODUCTION_STATE_SCHEMA_VERSION,
            "remote_exported_at": exported_at,
            "advanced": advanced,
            "enriched": enriched,
            "unchanged": unchanged,
            "regressions_ignored": regressions_ignored,
            "missing_local": missing_local,
            "production_counts": overview["counts"],
        }

    def production_overview(self) -> dict[str, Any]:
        items_by_id = {
            script.approved_script_id: _approved_production_state_item(script)
            for script in self.list()
            if script.status in PRODUCTION_STATUS_ORDER
        }
        remote_exported_at: str | None = None
        if self.remote_state_path.is_file():
            snapshot = read_json(self.remote_state_path)
            remote_exported_at, remote_items = _validate_production_state_snapshot(snapshot)
            for item in remote_items:
                local = items_by_id.get(item["approved_script_id"])
                if local is None or (
                    PRODUCTION_STATUS_ORDER[item["status"]]
                    >= PRODUCTION_STATUS_ORDER[local["status"]]
                ):
                    items_by_id[item["approved_script_id"]] = item
        items = sorted(
            items_by_id.values(),
            key=lambda item: (item["label"].casefold(), item["approved_script_id"]),
        )
        return {
            "counts": {
                status: sum(item["status"] == status for item in items)
                for status in PRODUCTION_STATUS_ORDER
            },
            "items": items,
            "remote_synced_at": remote_exported_at,
        }

    def find_by_label(
        self,
        label: str,
        *,
        status: ApprovedScriptStatus | None = None,
    ) -> ApprovedShortScript:
        normalized = _normalize(label).casefold()
        matches = [
            script
            for script in self.list(status=status)
            if _normalize(script.label).casefold() == normalized
        ]
        if not matches:
            raise FileNotFoundError(f"Roteiro pré-aprovado não encontrado pelo título: {label}")
        if len(matches) > 1:
            raise RuntimeError(
                "Mais de um roteiro possui esse título; use o approved_script_id exato."
            )
        return matches[0]

    def capture_project(
        self,
        layout: ProjectLayout,
        *,
        label: str | None = None,
        cta_options: list[ApprovedCtaOption] | None = None,
        selected_cta_id: str | None = None,
        voice_variant: Literal["jesus_1", "jesus_2"] = "jesus_1",
        music_track: Literal["leaning", "aloboi"] = "leaning",
        max_scenes: int = 7,
        seo_title: str | None = None,
        seo_description: str | None = None,
        seo_hashtags: list[str] | None = None,
    ) -> ApprovedShortScript:
        manifest = layout.load_manifest()
        if manifest.video_format != "short":
            raise ValueError("A fila pré-aprovada aceita somente Shorts.")
        validate_draft_preflight(layout)
        if not layout.shorts_script_strategy.is_file():
            raise ValueError("O projeto ainda não possui plano visual de Short.")
        briefing = read_model(layout.briefing, Briefing)
        sources = read_model(layout.sources, SourceLibrary)
        script = read_model(layout.script, ScriptDocument)
        review = read_model(layout.biblical_review, BiblicalReview)
        strategy = read_model(layout.shorts_script_strategy, ShortsScriptStrategy)
        if manifest.target_duration_seconds >= 45:
            strategy = _compact_visual_plan(strategy, max_scenes=max_scenes)
        existing_metadata = (
            read_model(layout.metadata, YouTubeMetadata) if layout.metadata.is_file() else None
        )
        metadata = _approved_metadata(
            briefing=briefing,
            script=script,
            existing=existing_metadata,
            title=seo_title,
            description=seo_description,
            specific_hashtags=seo_hashtags,
        )
        now = utc_now()
        script_label = (label or script.title).strip()
        resolved_options, inferred_selected = (
            _default_cta_options(script, strategy) if cta_options is None else (cta_options, None)
        )
        resolved_selected = selected_cta_id if selected_cta_id is not None else inferred_selected
        shell = ApprovedShortScript.model_construct(
            approved_script_id="approved-placeholder-00000000",
            label=script_label,
            source_project_id=manifest.project_id,
            source_project_path=str(layout.root),
            target_duration_seconds=manifest.target_duration_seconds,
            editorial_profile=manifest.config.shorts_script.editorial_profile,
            briefing=briefing,
            sources=sources,
            script=script,
            biblical_review=review,
            strategy=strategy,
            metadata=metadata,
            cta_options=resolved_options,
            selected_cta_id=resolved_selected,
            voice_variant=voice_variant,
            music_track=music_track,
        )
        digest = sha256_value(approved_content_payload(shell))
        script_id = f"approved-{slugify(script_label)[:60]}-{digest[:8]}"
        with suppress(FileNotFoundError):
            existing, _ = self.find(script_id)
            return existing
        candidate = ApprovedShortScript(
            approved_script_id=script_id,
            status="draft",
            label=script_label,
            source_project_id=manifest.project_id,
            source_project_path=str(layout.root),
            target_duration_seconds=manifest.target_duration_seconds,
            editorial_profile=manifest.config.shorts_script.editorial_profile,
            briefing=briefing,
            sources=sources,
            script=script,
            biblical_review=review,
            strategy=strategy,
            metadata=metadata,
            cta_options=resolved_options,
            selected_cta_id=resolved_selected,
            voice_variant=voice_variant,
            music_track=music_track,
            content_hash=digest,
            created_at=now,
            updated_at=now,
        )
        self.save(candidate)
        return candidate

    def select_cta(
        self,
        script_id: str,
        *,
        option_id: str,
        spoken_text: str | None = None,
        mode: Literal["comment", "subscribe", "share", "like"] | None = None,
    ) -> ApprovedShortScript:
        script, path = self.find(script_id)
        if script.status != "draft":
            raise RuntimeError("O CTA só pode ser alterado antes da aprovação.")
        if script.target_duration_seconds < 45:
            raise RuntimeError("O perfil compacto não aceita CTA final.")
        options = list(script.cta_options)
        selected = next((item for item in options if item.option_id == option_id), None)
        if spoken_text is not None:
            if mode is None:
                raise ValueError("Informe o modo ao cadastrar um novo CTA.")
            replacement = ApprovedCtaOption(
                option_id=option_id,
                spoken_text=spoken_text,
                mode=mode,
            )
            options = [item for item in options if item.option_id != option_id]
            options.append(replacement)
            selected = replacement
        if selected is None:
            raise ValueError("A opção de CTA solicitada não existe.")
        previous = next(
            (item for item in script.cta_options if item.option_id == script.selected_cta_id),
            None,
        )
        if previous is None:
            raise RuntimeError(
                "O rascunho não identificou o CTA atual; ajuste o projeto editorial e recapture."
            )
        old_text = previous.spoken_text
        new_text = selected.spoken_text
        sections = []
        replaced_sections = 0
        for section in script.script.sections:
            if old_text in section.narration:
                sections.append(
                    section.model_copy(
                        update={"narration": section.narration.replace(old_text, new_text, 1)}
                    )
                )
                replaced_sections += 1
            else:
                sections.append(section)
        if replaced_sections != 1:
            raise RuntimeError("O CTA atual precisa aparecer exatamente uma vez no roteiro.")
        visual_scenes = []
        replaced_scenes = 0
        for scene in script.strategy.visual_scenes:
            if old_text in scene.narration:
                visual_scenes.append(
                    scene.model_copy(
                        update={"narration": scene.narration.replace(old_text, new_text, 1)}
                    )
                )
                replaced_scenes += 1
            else:
                visual_scenes.append(scene)
        if replaced_scenes != 1:
            raise RuntimeError("O CTA atual precisa aparecer exatamente uma vez no plano visual.")
        updated_script = script.script.model_copy(update={"sections": sections})
        strategy_updates: dict[str, Any] = {
            "cta": new_text,
            "visual_scenes": visual_scenes,
        }
        if script.strategy.creative_direction is not None:
            engagement = script.strategy.creative_direction.engagement.model_copy(
                update={"spoken_text": new_text, "mode": selected.mode}
            )
            strategy_updates["creative_direction"] = script.strategy.creative_direction.model_copy(
                update={"engagement": engagement}
            )
        if script.strategy.retention_story is not None:
            engagement = script.strategy.retention_story.engagement.model_copy(
                update={"spoken_text": new_text, "mode": selected.mode}
            )
            strategy_updates["retention_story"] = script.strategy.retention_story.model_copy(
                update={"engagement": engagement}
            )
        updated_strategy = script.strategy.model_copy(update=strategy_updates)
        updated_briefing = script.briefing.model_copy(update={"call_to_action": new_text})
        draft = script.model_copy(
            update={
                "script": updated_script,
                "strategy": updated_strategy,
                "briefing": updated_briefing,
                "cta_options": options,
                "selected_cta_id": selected.option_id,
                "updated_at": utc_now(),
            }
        )
        draft = draft.model_copy(update={"content_hash": approved_content_hash(draft)})
        validated = ApprovedShortScript.model_validate(draft.model_dump(mode="json"))
        write_json(path, validated)
        return validated

    def set_seo(
        self,
        script_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        specific_hashtags: list[str] | None = None,
    ) -> ApprovedShortScript:
        script, path = self.find(script_id)
        if script.status != "draft":
            raise RuntimeError("O SEO só pode ser alterado antes da aprovação.")
        metadata = _approved_metadata(
            briefing=script.briefing,
            script=script.script,
            existing=script.metadata,
            title=title,
            description=description,
            specific_hashtags=specific_hashtags,
        )
        draft = script.model_copy(
            update={
                "metadata": metadata,
                "updated_at": utc_now(),
            }
        )
        draft = draft.model_copy(update={"content_hash": approved_content_hash(draft)})
        validated = ApprovedShortScript.model_validate(draft.model_dump(mode="json"))
        write_json(path, validated)
        return validated

    def approve(
        self,
        script_id: str,
        *,
        confirm_hash: str,
        approved_by: str,
        note: str | None = None,
    ) -> ApprovedShortScript:
        script, old_path = self.find(script_id)
        if script.status == "approved":
            if script.content_hash != confirm_hash:
                raise ValueError("O hash informado não corresponde ao roteiro já aprovado.")
            return script
        if script.status != "draft":
            raise RuntimeError("Somente um rascunho pode ser aprovado.")
        if script.content_hash != confirm_hash:
            raise ValueError("O conteúdo mudou ou o hash de confirmação está incorreto.")
        approved = script.model_copy(
            update={
                "status": "approved",
                "approved_at": utc_now(),
                "approved_by": approved_by.strip(),
                "approval_note": note,
                "updated_at": utc_now(),
            }
        )
        self.save(approved)
        old_path.unlink(missing_ok=True)
        return approved

    def reserve(
        self,
        job_id: str,
        *,
        script_id: str | None = None,
    ) -> ApprovedShortScript:
        candidates = [self.find(script_id)[0]] if script_id else self.list(status="approved")
        for candidate in candidates:
            if candidate.status != "approved":
                if script_id:
                    raise RuntimeError("O roteiro solicitado não está disponível para produção.")
                continue
            source = self.path(candidate.approved_script_id, "approved")
            claimed = self.path(candidate.approved_script_id, "reserved")
            try:
                os.replace(source, claimed)
            except FileNotFoundError:
                if script_id:
                    raise RuntimeError("O roteiro foi reservado por outro job.") from None
                continue
            reserved = candidate.model_copy(
                update={
                    "status": "reserved",
                    "reserved_by_job_id": job_id,
                    "reserved_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            write_json(claimed, reserved)
            return reserved
        raise RuntimeError("Não há roteiros aprovados disponíveis.")

    def release_reservation(self, script_id: str, *, job_id: str) -> ApprovedShortScript:
        script, old_path = self.find(script_id)
        if script.status != "reserved" or script.reserved_by_job_id != job_id:
            raise RuntimeError("A reserva não pertence a este job.")
        released = script.model_copy(
            update={
                "status": "approved",
                "reserved_by_job_id": None,
                "reserved_at": None,
                "updated_at": utc_now(),
            }
        )
        self.save(released)
        old_path.unlink(missing_ok=True)
        return released

    def mark_produced(
        self,
        script_id: str,
        *,
        job_id: str,
        video_id: str,
        youtube_url: str,
    ) -> ApprovedShortScript:
        script, old_path = self.find(script_id)
        if script.status == "produced":
            return script
        if script.status != "reserved" or script.reserved_by_job_id != job_id:
            raise RuntimeError("O roteiro não está reservado por este job.")
        produced = script.model_copy(
            update={
                "status": "produced",
                "produced_at": datetime.now(UTC).isoformat(),
                "youtube_video_id": video_id,
                "youtube_url": youtube_url,
                "updated_at": utc_now(),
            }
        )
        self.save(produced)
        old_path.unlink(missing_ok=True)
        return produced


def approved_script_summary(
    script: ApprovedShortScript,
    *,
    include_narration: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "approved_script_id": script.approved_script_id,
        "status": script.status,
        "label": script.label,
        "duration_seconds": script.target_duration_seconds,
        "duration_role": "editorial_estimate_only",
        "production_duration_policy": "approved_script_locked_v1",
        "editorial_profile": script.editorial_profile,
        "content_hash": script.content_hash,
        "selected_cta_id": script.selected_cta_id,
        "cta_options": [item.model_dump(mode="json") for item in script.cta_options],
        "voice_variant": script.voice_variant,
        "music_track": script.music_track,
        "scene_count": len(script.strategy.visual_scenes),
        "seo": (
            {
                "title": script.metadata.title,
                "description": script.metadata.description,
                "specific_hashtags": approved_specific_hashtags(script.metadata),
                "standard_hashtags": ["#biblia", "#jesus"],
            }
            if script.metadata is not None
            else None
        ),
        "approved_at": script.approved_at,
        "reserved_by_job_id": script.reserved_by_job_id,
        "youtube_url": script.youtube_url,
    }
    if include_narration:
        payload["narration"] = script.script.narration_text()
    return payload


def materialize_approved_project(
    approved: ApprovedShortScript,
    *,
    job_id: str,
    projects_root: Path,
    publication_mode: Literal["private_review", "scheduled"],
    voice_id: str,
    voice_model_id: str,
) -> ProjectLayout:
    """Create a production project without reopening editorial model stages."""

    project_slug = slugify(f"{approved.label}-{job_id[-8:]}")
    partials = list((projects_root / "shorts").glob(f"??-??-????_{project_slug}"))
    if len(partials) > 1:
        raise RuntimeError("Mais de um projeto parcial corresponde ao job pré-aprovado.")
    if partials:
        layout = ProjectLayout(partials[0])
        if layout.approved_script_lock.is_file():
            verify_project_approved_script_lock(layout)
            resumed = layout.load_manifest()
            resumed = resumed.model_copy(
                update={
                    "target_duration_seconds": approved.target_duration_seconds,
                    "duration_policy": "approved_script_locked_v1",
                    "requested_duration_min_seconds": None,
                    "requested_duration_max_seconds": None,
                }
            )
            resumed.config.shorts_script.minimum_audio_duration_ratio = 0.0
            layout.save_manifest(resumed)
            return layout
    else:
        bootstrap_duration = max(15, min(180, approved.target_duration_seconds))
        layout = create_project(
            project_slug,
            approved.script.title,
            projects_dir=projects_root,
            target_duration_seconds=bootstrap_duration,
            video_format="short",
            production_mode="final_candidate_one_shot",
        )
    manifest = layout.load_manifest()
    manifest = manifest.model_copy(
        update={
            "target_duration_seconds": approved.target_duration_seconds,
            "duration_policy": "approved_script_locked_v1",
            "requested_duration_min_seconds": None,
            "requested_duration_max_seconds": None,
            "effective_duration_seconds": None,
        }
    )
    manifest.config.shorts_script.editorial_profile = approved.editorial_profile
    manifest.config.shorts_script.minimum_audio_duration_ratio = 0.0
    scene_count = len(approved.strategy.visual_scenes)
    manifest.config.shorts_script.target_scene_min = scene_count
    manifest.config.shorts_script.target_scene_max = scene_count
    manifest.config.production.use_subagents = True
    manifest.config.production.image_workers = 2
    manifest.config.shorts_script.use_specialist_subagent = False
    manifest.config.shorts_script.require_independent_biblical_review = False
    manifest.config.youtube_growth.enabled = False
    manifest.config.youtube_growth.use_specialist_subagent = False
    manifest.config.audio.provider = "elevenlabs"
    manifest.config.audio.voice_id = voice_id
    manifest.config.audio.model_id = voice_model_id
    manifest.config.youtube_publication.allow_automated_private_review_upload = True
    manifest.config.youtube_publication.allow_automated_scheduled_release = (
        publication_mode == "scheduled"
    )
    layout.save_manifest(manifest)

    write_json(layout.briefing, approved.briefing)
    write_json(layout.sources, approved.sources)
    briefing_hash = sha256_value({"briefing": approved.briefing, "sources": approved.sources})
    complete_stage(
        layout,
        "briefing",
        briefing_hash,
        [
            relative_posix(layout.briefing, layout.root),
            relative_posix(layout.sources, layout.root),
        ],
        provider="approved-script-lock-v1",
    )

    from pipeline_videos.review import save_biblical_review
    from pipeline_videos.script import save_script

    if layout.script.is_file():
        saved_script = read_model(layout.script, ScriptDocument)
        if _normalize(saved_script.narration_text()) != _normalize(
            approved.script.narration_text()
        ):
            raise RuntimeError("O projeto parcial contém uma narração diferente da aprovada.")
    else:
        saved_script = save_script(
            layout,
            approved.script.model_copy(deep=True),
            provider="approved-script-lock-v1",
            creative_strategy=approved.strategy,
            trusted_approved_snapshot=True,
        )
    review = approved.biblical_review.model_copy(update={"script_version": saved_script.version})
    save_biblical_review(layout, review, provider="approved-script-lock-v1")
    write_json(layout.shorts_script_strategy, approved.strategy)
    package_hash = sha256_value(
        {
            "script": saved_script,
            "review": review,
            "strategy": approved.strategy,
        }
    )
    write_json(
        layout.shorts_script_package,
        {
            "job_id": job_id,
            "input_hash": package_hash,
            "script": saved_script.model_dump(mode="json"),
            "biblical_review": review.model_dump(mode="json"),
            "strategy": approved.strategy.model_dump(mode="json"),
        },
    )
    complete_stage(
        layout,
        "shorts_script",
        package_hash,
        [
            relative_posix(layout.shorts_script_package, layout.root),
            relative_posix(layout.shorts_script_strategy, layout.root),
        ],
        provider="approved-script-lock-v1",
        usage={
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        note="Roteiro, revisão e plano visual importados do pacote pré-aprovado.",
    )
    metadata = approved.metadata or _approved_metadata(
        briefing=approved.briefing,
        script=saved_script,
        existing=None,
    )
    write_json(layout.metadata, metadata)
    write_text(layout.description, metadata.description)
    metadata_hash = sha256_value(
        {
            "approved_script_id": approved.approved_script_id,
            "content_hash": approved.content_hash,
            "metadata": metadata,
        }
    )
    complete_stage(
        layout,
        "metadata",
        metadata_hash,
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
        ],
        provider="approved-script-lock-v1",
        usage={
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        },
        note="Título, descrição e hashtags reutilizados do roteiro pré-aprovado.",
    )
    artifacts = {
        relative_posix(path, layout.root): sha256_file(path)
        for path in (
            layout.briefing,
            layout.sources,
            layout.script,
            layout.narration,
            layout.biblical_review,
            layout.shorts_script_strategy,
            layout.metadata,
            layout.description,
        )
    }
    write_json(
        layout.approved_script_lock,
        {
            "schema_version": "1.0",
            "approved_script_id": approved.approved_script_id,
            "content_hash": approved.content_hash,
            "reserved_by_job_id": job_id,
            "artifacts": artifacts,
            "created_at": utc_now(),
        },
    )
    verify_project_approved_script_lock(layout)
    layout.append_decision(
        {
            "type": "approved_fast_v1",
            "approved_script_id": approved.approved_script_id,
            "approved_content_hash": approved.content_hash,
            "job_id": job_id,
            "editorial_model_calls": 0,
            "youtube_growth_model_calls": 0,
            "image_workers": 2,
            "human_approval_recorded": False,
        }
    )
    return layout


def verify_project_approved_script_lock(layout: ProjectLayout) -> None:
    if not layout.approved_script_lock.is_file():
        return
    lock = read_json(layout.approved_script_lock)
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise RuntimeError("O lock do roteiro aprovado está incompleto.")
    for relative, expected_hash in artifacts.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise RuntimeError("O lock do roteiro aprovado contém um artefato inválido.")
        path = (layout.root / relative).resolve()
        try:
            path.relative_to(layout.root.resolve())
        except ValueError as exc:
            raise RuntimeError("O lock aponta para um caminho fora do projeto.") from exc
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise RuntimeError(
                f"O roteiro pré-aprovado foi alterado depois da aprovação: {relative}"
            )
