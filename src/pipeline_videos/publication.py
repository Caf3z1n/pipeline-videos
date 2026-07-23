from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, Literal, Protocol

from pipeline_videos.models import (
    ApprovalStatus,
    ShortPublicationAnalyticsContext,
    StageStatus,
    YouTubeMetadata,
    YouTubePublicationAsset,
    YouTubePublicationRecord,
)
from pipeline_videos.state import complete_stage, fail_stage, start_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import media_duration, relative_posix, sha256_file, sha256_value, utc_now

YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "pipeline-videos" / "youtube" / "token.json"


def _short_publication_analytics_context(
    layout: ProjectLayout,
    *,
    video_duration_seconds: float | None = None,
    video_duration_source: Literal["ffprobe", "manifest_fallback"] = "ffprobe",
) -> ShortPublicationAnalyticsContext | None:
    project = layout.load_manifest()
    if project.video_format != "short":
        return None
    briefing = read_json(layout.briefing) if layout.briefing.is_file() else {}
    strategy = (
        read_json(layout.shorts_script_strategy) if layout.shorts_script_strategy.is_file() else {}
    )

    def optional_text(source: dict[str, Any], key: str) -> str | None:
        value = source.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return ShortPublicationAnalyticsContext(
        duration_profile=project.config.shorts_script.duration_profile,
        video_duration_seconds=(
            video_duration_seconds
            if video_duration_seconds is not None
            else project.effective_duration_seconds or float(project.target_duration_seconds)
        ),
        video_duration_source=video_duration_source,
        hook_contract=optional_text(briefing, "hook_contract")
        if isinstance(briefing, dict)
        else None,
        visual_standard_id=project.config.images.visual_standard_id,
        hook_text=optional_text(strategy, "hook") if isinstance(strategy, dict) else None,
        hook_mechanism=(
            optional_text(briefing, "hook_family") if isinstance(briefing, dict) else None
        ),
        opening_visual_standard=project.config.images.opening_visual_standard,
    )


class YouTubeClient(Protocol):
    def channel(self) -> dict[str, str]: ...

    def upload_video(
        self,
        *,
        video_path: Path,
        body: dict[str, Any],
        notify_subscribers: bool,
        progress: Callable[[float], None] | None = None,
    ) -> dict[str, Any]: ...

    def update_video(
        self, video_id: str, *, snippet: dict[str, Any], status: dict[str, Any]
    ) -> None: ...

    def upsert_caption(
        self, video_id: str, path: Path, *, language: str, name: str
    ) -> dict[str, Any]: ...

    def video(self, video_id: str) -> dict[str, Any]: ...

    def set_visibility(
        self,
        video_id: str,
        *,
        privacy_status: str,
        publish_at: str | None,
        made_for_kids: bool,
        contains_synthetic_media: bool,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublicationPlan:
    project_id: str
    video_format: str
    title: str
    description: str
    tags: list[str]
    category_id: str
    default_language: str
    privacy_status: str
    made_for_kids: bool
    contains_synthetic_media: bool
    notify_subscribers: bool
    upload_captions: bool
    video_path: Path
    video_hash: str
    metadata_hash: str
    captions_path: Path | None
    captions_hash: str | None
    automated_review_candidate: bool = False

    def input_hash(self) -> str:
        payload = asdict(self)
        payload["video_path"] = str(self.video_path)
        payload["captions_path"] = str(self.captions_path) if self.captions_path else None
        return sha256_value(payload)


class GoogleYouTubeClient:
    def __init__(self, service: Any) -> None:
        self.service = service

    def channel(self) -> dict[str, str]:
        response = self.service.channels().list(part="id,snippet", mine=True).execute()
        items = response.get("items", [])
        if len(items) != 1:
            raise RuntimeError("A conta OAuth não possui exatamente um canal do YouTube acessível.")
        return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}

    def upload_video(
        self,
        *,
        video_path: Path,
        body: dict[str, Any],
        notify_subscribers: bool,
        progress: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )
        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=notify_subscribers,
        )
        response = None
        while response is None:
            upload_status, response = request.next_chunk(num_retries=5)
            if upload_status is not None and progress is not None:
                progress(float(upload_status.progress()))
        return response

    def _mutable_status(self, video_id: str) -> dict[str, Any]:
        current = self.video(video_id).get("status", {})
        allowed = {
            "privacyStatus",
            "publishAt",
            "embeddable",
            "license",
            "publicStatsViewable",
            "selfDeclaredMadeForKids",
            "containsSyntheticMedia",
        }
        return {key: value for key, value in current.items() if key in allowed}

    def update_video(
        self, video_id: str, *, snippet: dict[str, Any], status: dict[str, Any]
    ) -> None:
        self.service.videos().update(
            part="snippet",
            body={"id": video_id, "snippet": snippet},
        ).execute()
        merged_status = self._mutable_status(video_id)
        merged_status.update(status)
        self.service.videos().update(
            part="status",
            body={"id": video_id, "status": merged_status},
        ).execute()

    def upsert_caption(
        self, video_id: str, path: Path, *, language: str, name: str
    ) -> dict[str, Any]:
        from googleapiclient.http import MediaFileUpload

        tracks = self.service.captions().list(part="id,snippet", videoId=video_id).execute()
        existing = next(
            (
                item
                for item in tracks.get("items", [])
                if item.get("snippet", {}).get("language") == language
                and item.get("snippet", {}).get("name") == name
            ),
            None,
        )
        media = MediaFileUpload(str(path), mimetype="application/octet-stream", resumable=False)
        snippet = {"videoId": video_id, "language": language, "name": name, "isDraft": False}
        if existing:
            return (
                self.service.captions()
                .update(
                    part="snippet",
                    body={"id": existing["id"], "snippet": snippet},
                    media_body=media,
                )
                .execute()
            )
        return (
            self.service.captions()
            .insert(part="snippet", body={"snippet": snippet}, media_body=media)
            .execute()
        )

    def video(self, video_id: str) -> dict[str, Any]:
        response = (
            self.service.videos()
            .list(part="id,snippet,status,processingDetails", id=video_id)
            .execute()
        )
        items = response.get("items", [])
        if len(items) != 1:
            raise RuntimeError(f"Vídeo do YouTube não encontrado: {video_id}")
        return items[0]

    def set_visibility(
        self,
        video_id: str,
        *,
        privacy_status: str,
        publish_at: str | None,
        made_for_kids: bool,
        contains_synthetic_media: bool,
    ) -> dict[str, Any]:
        status = self._mutable_status(video_id)
        status["privacyStatus"] = privacy_status
        status["selfDeclaredMadeForKids"] = made_for_kids
        status["containsSyntheticMedia"] = contains_synthetic_media
        if publish_at:
            status["publishAt"] = publish_at
        else:
            status.pop("publishAt", None)
        return (
            self.service.videos()
            .update(part="status", body={"id": video_id, "status": status})
            .execute()
        )


def token_file(path: Path | None = None) -> Path:
    configured = os.getenv("YOUTUBE_TOKEN_FILE")
    return (path or (Path(configured) if configured else DEFAULT_TOKEN_FILE)).expanduser().resolve()


def authorize(client_secrets: Path, *, destination: Path | None = None) -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secrets = client_secrets.expanduser().resolve()
    if not client_secrets.is_file():
        raise FileNotFoundError(f"Credencial OAuth não encontrada: {client_secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=YOUTUBE_SCOPES)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        access_type="offline",
        prompt="consent",
        success_message="Autorização concluída. Você pode fechar esta janela.",
    )
    target = token_file(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.replace(temporary, target)
    with suppress(OSError):
        target.chmod(0o600)
    return target


def authenticated_client(*, source: Path | None = None) -> GoogleYouTubeClient:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    source = token_file(source)
    if not source.is_file():
        raise FileNotFoundError(
            f"Token OAuth não encontrado em {source}. Execute `pipeline-videos youtube auth`."
        )
    credentials = Credentials.from_authorized_user_file(str(source), scopes=YOUTUBE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        temporary = source.with_suffix(source.suffix + ".tmp")
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        os.replace(temporary, source)
    if not credentials.valid:
        raise RuntimeError("Token OAuth inválido ou sem refresh token; autorize novamente.")
    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    return GoogleYouTubeClient(service)


def _description(layout: ProjectLayout, metadata: YouTubeMetadata) -> str:
    if layout.description.is_file():
        return layout.description.read_text(encoding="utf-8").strip()
    parts = [metadata.description.strip(), *metadata.chapters]
    return "\n\n".join(part for part in parts if part)


def prepare_publication(
    layout: ProjectLayout,
    *,
    privacy_status: str | None = None,
    category_id: str | None = None,
    default_language: str | None = None,
    made_for_kids: bool | None = None,
    contains_synthetic_media: bool | None = None,
    notify_subscribers: bool | None = None,
    upload_captions: bool | None = None,
    verify_validation: bool = True,
    automated_review: bool = False,
) -> PublicationPlan:
    manifest = layout.load_manifest()
    if manifest.demo or manifest.config.images.provider == "fixture":
        raise RuntimeError(
            "Projetos demo ou com imagens fixture não podem ser enviados ao YouTube."
        )
    config = manifest.config.youtube_publication
    visibility = privacy_status or (
        read_model(layout.metadata, YouTubeMetadata).publication_visibility
        if layout.metadata.is_file()
        else "private"
    )
    automated_candidate = (
        automated_review
        and config.allow_automated_private_review_upload
        and visibility == "private"
        and manifest.stages["render"].provider == "ffmpeg-review-candidate"
    )
    if automated_review and not automated_candidate:
        raise RuntimeError(
            "Upload automatizado bloqueado: exige candidato privado validado, "
            "produzido por render-review e com a política habilitada."
        )
    if not automated_candidate:
        for approval in ("metadata", "final_render"):
            if manifest.approvals[approval].status != ApprovalStatus.APPROVED:
                raise RuntimeError(
                    f"A aprovação humana de '{approval}' é obrigatória para o upload."
                )
    if manifest.stages["validation"].status != StageStatus.COMPLETED:
        raise RuntimeError("A validação final precisa estar concluída antes do upload.")
    if not layout.final_video.is_file():
        raise FileNotFoundError(f"Render final ausente: {layout.final_video}")
    if verify_validation:
        from pipeline_videos.validation import validate_project

        validation = validate_project(layout, require_render=True)
        if not validation.valid:
            raise RuntimeError("A validação atual do projeto falhou; corrija antes do upload.")
        manifest = layout.load_manifest()

    final_approval = manifest.approvals["final_render"]
    if not automated_candidate and final_approval.artifact_hash != sha256_file(layout.final_video):
        raise RuntimeError(
            "O render atual não corresponde ao arquivo aprovado. Aprove-o novamente."
        )

    metadata = read_model(layout.metadata, YouTubeMetadata)
    description = _description(layout, metadata)
    metadata_approval_hash = sha256_value(
        {
            "metadata": sha256_file(layout.metadata),
            "description": sha256_file(layout.description),
        }
    )
    if (
        not automated_candidate
        and manifest.approvals["metadata"].artifact_hash != metadata_approval_hash
    ):
        raise RuntimeError("Metadados ou descrição mudaram após a aprovação. Aprove-os novamente.")
    if not metadata.title.strip() or len(metadata.title) > 100:
        raise ValueError("O título do YouTube precisa ter entre 1 e 100 caracteres.")
    if len(description) > 5000:
        raise ValueError("A descrição do YouTube excede 5.000 caracteres.")
    tags = list(dict.fromkeys(tag.strip() for tag in metadata.topics if tag.strip()))
    if len(",".join(tags)) > 500:
        raise ValueError("As tags do YouTube excedem o limite total de 500 caracteres.")

    if visibility not in {"private", "unlisted"}:
        raise ValueError("O primeiro upload precisa ser private ou unlisted.")
    captions_enabled = config.upload_captions if upload_captions is None else upload_captions
    captions_path = layout.srt if captions_enabled else None
    if captions_enabled and not layout.srt.is_file():
        raise FileNotFoundError(f"Legenda SRT ausente: {layout.srt}")

    metadata_payload = {
        "title": metadata.title,
        "description": description,
        "tags": tags,
        "category_id": category_id or config.category_id,
        "default_language": default_language or config.default_language,
        "made_for_kids": config.made_for_kids if made_for_kids is None else made_for_kids,
        "contains_synthetic_media": (
            config.contains_synthetic_media
            if contains_synthetic_media is None
            else contains_synthetic_media
        ),
    }
    return PublicationPlan(
        project_id=manifest.project_id,
        video_format=manifest.video_format,
        privacy_status=visibility,
        notify_subscribers=(
            config.notify_subscribers if notify_subscribers is None else notify_subscribers
        ),
        upload_captions=captions_enabled,
        video_path=layout.final_video,
        video_hash=sha256_file(layout.final_video),
        metadata_hash=sha256_value(metadata_payload),
        captions_path=captions_path,
        captions_hash=sha256_file(captions_path) if captions_path else None,
        automated_review_candidate=automated_candidate,
        **metadata_payload,
    )


def _asset(
    *, path: Path | None, layout: ProjectLayout, digest: str | None
) -> YouTubePublicationAsset:
    return YouTubePublicationAsset(
        source_path=relative_posix(path, layout.root) if path else None,
        source_hash=digest,
    )


def _save_record(layout: ProjectLayout, record: YouTubePublicationRecord) -> None:
    record.updated_at = utc_now()
    write_json(layout.publication, record)


def _snippet(plan: PublicationPlan) -> dict[str, Any]:
    return {
        "title": plan.title,
        "description": plan.description,
        "tags": plan.tags,
        "categoryId": plan.category_id,
        "defaultLanguage": plan.default_language,
    }


def _status(plan: PublicationPlan) -> dict[str, Any]:
    return {
        "privacyStatus": plan.privacy_status,
        "selfDeclaredMadeForKids": plan.made_for_kids,
        "containsSyntheticMedia": plan.contains_synthetic_media,
    }


def upload_project(
    layout: ProjectLayout,
    *,
    client: YouTubeClient,
    allow_external: bool,
    confirmation: str,
    force_new_upload: bool = False,
    progress: Callable[[float], None] | None = None,
    **plan_options: Any,
) -> YouTubePublicationRecord:
    if not allow_external:
        raise RuntimeError("Upload bloqueado: use --allow-external após revisar o preflight.")
    plan = prepare_publication(layout, **plan_options)
    if confirmation != plan.project_id:
        raise RuntimeError(
            "Confirmação inválida. --confirm-upload precisa ser exatamente o ID do projeto."
        )
    short_analytics_context = None
    if plan.video_format == "short":
        duration_source: Literal["ffprobe", "manifest_fallback"] = "ffprobe"
        try:
            video_duration_seconds = media_duration(plan.video_path)
        except (CalledProcessError, OSError, RuntimeError, ValueError):
            if plan_options.get("verify_validation", True):
                raise
            video_duration_seconds = layout.load_manifest().effective_duration_seconds or float(
                layout.load_manifest().target_duration_seconds
            )
            duration_source = "manifest_fallback"
        short_analytics_context = _short_publication_analytics_context(
            layout,
            video_duration_seconds=video_duration_seconds,
            video_duration_source=duration_source,
        )
    input_hash = plan.input_hash()
    existing = (
        read_model(layout.publication, YouTubePublicationRecord)
        if layout.publication.is_file()
        else None
    )
    if existing and force_new_upload:
        layout.publication_archive.mkdir(parents=True, exist_ok=True)
        shutil.copy2(layout.publication, layout.publication_archive / f"{existing.video_id}.json")
        existing = None
    elif existing and existing.video.source_hash != plan.video_hash:
        raise RuntimeError(
            "Já existe outro render enviado para este projeto. Use --force-new-upload "
            "somente se deseja criar um novo vídeo no YouTube."
        )

    start_stage(layout, "publication", input_hash, provider="youtube-data-api-v3")
    try:
        channel = client.channel()
        if existing is None:
            response = client.upload_video(
                video_path=plan.video_path,
                body={"snippet": _snippet(plan), "status": _status(plan)},
                notify_subscribers=plan.notify_subscribers,
                progress=progress,
            )
            video_id = str(response["id"])
            now = utc_now()
            record = YouTubePublicationRecord(
                project_id=plan.project_id,
                video_id=video_id,
                youtube_url=f"https://youtu.be/{video_id}",
                channel_id=channel["id"],
                channel_title=channel["title"],
                title=plan.title,
                privacy_status=plan.privacy_status,
                category_id=plan.category_id,
                default_language=plan.default_language,
                made_for_kids=plan.made_for_kids,
                contains_synthetic_media=plan.contains_synthetic_media,
                notify_subscribers=plan.notify_subscribers,
                short_analytics_context=short_analytics_context,
                video=YouTubePublicationAsset(
                    status="uploaded",
                    source_path=relative_posix(plan.video_path, layout.root),
                    source_hash=plan.video_hash,
                    youtube_id=video_id,
                    updated_at=now,
                ),
                metadata=YouTubePublicationAsset(
                    status="uploaded",
                    source_path=relative_posix(layout.metadata, layout.root),
                    source_hash=plan.metadata_hash,
                    updated_at=now,
                ),
                captions=_asset(path=plan.captions_path, layout=layout, digest=plan.captions_hash),
                created_at=now,
                updated_at=now,
            )
            if not plan.upload_captions:
                record.captions.status = "skipped"
            _save_record(layout, record)
        else:
            record = existing
            client.video(record.video_id)
            if record.metadata.source_hash != plan.metadata_hash:
                client.update_video(
                    record.video_id,
                    snippet=_snippet(plan),
                    status={
                        "selfDeclaredMadeForKids": plan.made_for_kids,
                        "containsSyntheticMedia": plan.contains_synthetic_media,
                    },
                )
                record.metadata = YouTubePublicationAsset(
                    status="uploaded",
                    source_path=relative_posix(layout.metadata, layout.root),
                    source_hash=plan.metadata_hash,
                    updated_at=utc_now(),
                )
                record.title = plan.title
                record.category_id = plan.category_id
                record.default_language = plan.default_language
                record.made_for_kids = plan.made_for_kids
                record.contains_synthetic_media = plan.contains_synthetic_media
                _save_record(layout, record)

        if plan.upload_captions and (
            record.captions.status != "uploaded"
            or record.captions.source_hash != plan.captions_hash
        ):
            assert plan.captions_path is not None
            response = client.upsert_caption(
                record.video_id,
                plan.captions_path,
                language=plan.default_language,
                name="Português (Brasil) - pipeline-videos",
            )
            record.captions = YouTubePublicationAsset(
                status="uploaded",
                source_path=relative_posix(plan.captions_path, layout.root),
                source_hash=plan.captions_hash,
                youtube_id=response.get("id"),
                updated_at=utc_now(),
            )
            _save_record(layout, record)
        elif not plan.upload_captions:
            record.captions.status = "skipped"
            record.captions.updated_at = utc_now()
            _save_record(layout, record)

        complete_stage(
            layout,
            "publication",
            input_hash,
            [relative_posix(layout.publication, layout.root)],
            provider="youtube-data-api-v3",
            note=f"Upload concluído como {record.privacy_status}: {record.youtube_url}",
        )
        return record
    except Exception as exc:
        if layout.publication.is_file():
            record = read_model(layout.publication, YouTubePublicationRecord)
            for asset in (record.captions,):
                if asset.status == "pending":
                    asset.status = "failed"
                    asset.error = str(exc)
                    asset.updated_at = utc_now()
                    break
            _save_record(layout, record)
        fail_stage(layout, "publication", exc)
        raise


def publication_status(
    layout: ProjectLayout, *, client: YouTubeClient | None = None
) -> tuple[YouTubePublicationRecord, dict[str, Any] | None]:
    if not layout.publication.is_file():
        raise FileNotFoundError("Este projeto ainda não possui upload registrado.")
    record = read_model(layout.publication, YouTubePublicationRecord)
    return record, client.video(record.video_id) if client else None


def publish_project(
    layout: ProjectLayout,
    *,
    client: YouTubeClient,
    allow_external: bool,
    confirmation: str,
    publish_at: str | None = None,
    automated_schedule: bool = False,
) -> YouTubePublicationRecord:
    if not allow_external:
        raise RuntimeError("Publicação bloqueada: use --allow-external após revisar o vídeo.")
    record, remote = publication_status(layout, client=client)
    manifest = layout.load_manifest()
    if (
        automated_schedule
        and not manifest.config.youtube_publication.allow_automated_scheduled_release
    ):
        raise RuntimeError("O projeto não autoriza agendamento automatizado.")
    if confirmation != record.video_id:
        raise RuntimeError(
            "Confirmação inválida. --confirm-publication precisa ser exatamente o ID do vídeo."
        )
    assert remote is not None
    processing = remote.get("processingDetails", {}).get("processingStatus")
    upload_status = remote.get("status", {}).get("uploadStatus")
    if processing not in {None, "succeeded"} or upload_status not in {None, "processed"}:
        raise RuntimeError(
            f"O YouTube ainda não concluiu o processamento (upload={upload_status}, "
            f"processing={processing})."
        )

    normalized_publish_at = None
    privacy = "public"
    if publish_at:
        if record.published_at or remote.get("status", {}).get("privacyStatus") == "public":
            raise RuntimeError("Um vídeo que já foi público não pode ser agendado novamente.")
        parsed = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("--publish-at precisa incluir fuso horário, por exemplo -03:00.")
        if parsed.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("--publish-at precisa estar no futuro.")
        normalized_publish_at = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        privacy = "private"

    client.set_visibility(
        record.video_id,
        privacy_status=privacy,
        publish_at=normalized_publish_at,
        made_for_kids=record.made_for_kids,
        contains_synthetic_media=record.contains_synthetic_media,
    )
    record.privacy_status = privacy
    record.publish_at = normalized_publish_at
    if not normalized_publish_at:
        record.published_at = utc_now()
    _save_record(layout, record)
    complete_stage(
        layout,
        "publication",
        layout.load_manifest().stages["publication"].input_hash or sha256_value(record.video_id),
        [relative_posix(layout.publication, layout.root)],
        provider="youtube-data-api-v3",
        note=(
            f"Publicação agendada para {normalized_publish_at}."
            if normalized_publish_at
            else f"Vídeo publicado: {record.youtube_url}"
        ),
    )
    return record
