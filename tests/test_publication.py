from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from pipeline_videos.models import (
    ApprovalRecord,
    ApprovalStatus,
    StageStatus,
    YouTubeMetadata,
)
from pipeline_videos.project import create_project
from pipeline_videos.publication import (
    GoogleYouTubeClient,
    prepare_publication,
    publication_status,
    publish_project,
    upload_project,
)
from pipeline_videos.storage import read_json, write_json, write_text
from pipeline_videos.utils import sha256_file, sha256_value, utc_now


class FakeYouTubeClient:
    def __init__(self, *, fail_thumbnail_once: bool = False) -> None:
        self.upload_calls = 0
        self.update_calls = 0
        self.thumbnail_calls = 0
        self.caption_calls = 0
        self.visibility_calls: list[dict[str, str | None]] = []
        self.fail_thumbnail_once = fail_thumbnail_once

    def channel(self) -> dict[str, str]:
        return {"id": "channel-123", "title": "Canal de teste"}

    def upload_video(
        self,
        *,
        video_path: Path,
        body: dict[str, Any],
        notify_subscribers: bool,
        progress=None,
    ) -> dict[str, Any]:
        assert video_path.is_file()
        assert body["status"]["privacyStatus"] in {"private", "unlisted"}
        assert notify_subscribers is False
        self.upload_calls += 1
        if progress:
            progress(1.0)
        return {"id": "video-123"}

    def update_video(
        self, video_id: str, *, snippet: dict[str, Any], status: dict[str, Any]
    ) -> None:
        assert video_id == "video-123"
        assert snippet["title"]
        assert "selfDeclaredMadeForKids" in status
        self.update_calls += 1

    def set_thumbnail(self, video_id: str, path: Path) -> dict[str, Any]:
        assert video_id == "video-123"
        assert path.is_file()
        self.thumbnail_calls += 1
        if self.fail_thumbnail_once and self.thumbnail_calls == 1:
            raise RuntimeError("falha transitória na thumbnail")
        return {"items": []}

    def upsert_caption(
        self, video_id: str, path: Path, *, language: str, name: str
    ) -> dict[str, Any]:
        assert video_id == "video-123"
        assert path.suffix == ".srt"
        assert language == "pt-BR"
        assert name
        self.caption_calls += 1
        return {"id": "caption-123"}

    def video(self, video_id: str) -> dict[str, Any]:
        assert video_id == "video-123"
        return {
            "id": video_id,
            "status": {"uploadStatus": "processed", "privacyStatus": "private"},
            "processingDetails": {"processingStatus": "succeeded"},
        }

    def set_visibility(
        self,
        video_id: str,
        *,
        privacy_status: str,
        publish_at: str | None,
        made_for_kids: bool,
        contains_synthetic_media: bool,
    ) -> dict[str, Any]:
        assert video_id == "video-123"
        assert made_for_kids is False
        assert contains_synthetic_media is True
        self.visibility_calls.append({"privacy_status": privacy_status, "publish_at": publish_at})
        return {"id": video_id, "status": {"privacyStatus": privacy_status}}


class _FakeRequest:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def execute(self) -> dict[str, Any]:
        return self.response


class _FakeVideosResource:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []

    def list(self, **_kwargs) -> _FakeRequest:
        return _FakeRequest(
            {
                "items": [
                    {
                        "id": "video-123",
                        "snippet": {"categoryId": "22"},
                        "status": {"privacyStatus": "public", "embeddable": True},
                    }
                ]
            }
        )

    def update(self, **kwargs) -> _FakeRequest:
        self.update_calls.append(kwargs)
        return _FakeRequest({})


class _FakeGoogleService:
    def __init__(self) -> None:
        self.resource = _FakeVideosResource()

    def videos(self) -> _FakeVideosResource:
        return self.resource


def test_google_client_updates_snippet_and_status_separately() -> None:
    service = _FakeGoogleService()
    client = GoogleYouTubeClient(service)

    client.update_video(
        "video-123",
        snippet={"title": "Título", "description": "Descrição", "categoryId": "27"},
        status={"selfDeclaredMadeForKids": False},
    )

    assert [call["part"] for call in service.resource.update_calls] == ["snippet", "status"]
    assert service.resource.update_calls[0]["body"]["snippet"]["categoryId"] == "27"
    assert service.resource.update_calls[1]["body"]["status"] == {
        "privacyStatus": "public",
        "embeddable": True,
        "selfDeclaredMadeForKids": False,
    }


def _ready_project(
    tmp_path,
    *,
    video_format: str = "landscape",
    legacy_short_profile: bool = True,
):
    layout = create_project(
        "video-publicavel",
        "Vídeo publicável",
        projects_dir=tmp_path,
        video_format=video_format,
        style_id=(
            "cinematic-family-3d-animation"
            if video_format == "short" and legacy_short_profile
            else None
        ),
        project_date=date(2026, 7, 14),
    )
    metadata = YouTubeMetadata(
        title="Uma história bíblica para hoje",
        alternative_titles=[],
        description="Uma reflexão bíblica fiel e clara.",
        chapters=[] if video_format == "short" else ["0:00 Introdução"],
        topics=["Bíblia", "reflexão"],
        pinned_comment="O que você aprendeu?",
        thumbnail_concept="Conflito visual simples",
        thumbnail_prompt="Cena bíblica sem texto",
        thumbnail_alternatives=[],
    )
    write_json(layout.metadata, metadata)
    write_text(layout.description, metadata.description + "\n\n" + "\n".join(metadata.chapters))
    layout.final_video.parent.mkdir(parents=True, exist_ok=True)
    layout.final_video.write_bytes(b"mp4 de teste")
    youtube_auto_frame = video_format == "short" and not legacy_short_profile
    if not youtube_auto_frame:
        Image.new("RGB", (1280, 720), "#31485F").save(layout.thumbnail)
    write_text(layout.srt, "1\n00:00:00,000 --> 00:00:01,000\nOlá\n")

    manifest = layout.load_manifest()
    manifest.config.images.provider = "codex"
    manifest.stages["thumbnail"] = manifest.stages["thumbnail"].model_copy(
        update={
            "status": StageStatus.COMPLETED,
            "provider": "codex-import",
            "updated_at": utc_now(),
        }
    )
    manifest.stages["validation"] = manifest.stages["validation"].model_copy(
        update={"status": StageStatus.COMPLETED, "updated_at": utc_now()}
    )
    for key in ("metadata", "final_render"):
        artifact_hash = (
            sha256_file(layout.final_video)
            if key == "final_render"
            else sha256_value(
                {
                    "metadata": sha256_file(layout.metadata),
                    "description": sha256_file(layout.description),
                    "thumbnail": (None if youtube_auto_frame else sha256_file(layout.thumbnail)),
                }
            )
        )
        manifest.approvals[key] = ApprovalRecord(
            status=ApprovalStatus.APPROVED,
            decided_at=utc_now(),
            decided_by="teste",
            artifact_hash=artifact_hash,
        )
    layout.save_manifest(manifest)
    return layout


def test_preflight_requires_approved_real_assets_and_builds_safe_plan(tmp_path) -> None:
    layout = _ready_project(tmp_path)

    plan = prepare_publication(layout, privacy_status="unlisted", verify_validation=False)

    assert plan.project_id == "14-07-2026_video-publicavel"
    assert plan.video_format == "landscape"
    assert plan.privacy_status == "unlisted"
    assert plan.title == "Uma história bíblica para hoje"
    assert plan.description.endswith("0:00 Introdução")
    assert plan.contains_synthetic_media is True
    assert plan.notify_subscribers is False
    assert plan.captions_path == layout.srt
    assert len(plan.input_hash()) == 64

    with pytest.raises(ValueError, match="private ou unlisted"):
        prepare_publication(layout, privacy_status="public", verify_validation=False)


def test_private_review_candidate_can_upload_only_as_private_without_human_approval(
    tmp_path,
) -> None:
    layout = _ready_project(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.youtube_publication.allow_automated_private_review_upload = True
    manifest.stages["render"] = manifest.stages["render"].model_copy(
        update={"provider": "ffmpeg-review-candidate"}
    )
    for key in ("metadata", "final_render"):
        manifest.approvals[key] = ApprovalRecord()
    layout.save_manifest(manifest)

    plan = prepare_publication(
        layout,
        privacy_status="private",
        verify_validation=False,
        automated_review=True,
    )

    assert plan.automated_review_candidate is True
    assert plan.privacy_status == "private"
    assert layout.load_manifest().approvals["final_render"].status == ApprovalStatus.PENDING
    with pytest.raises(RuntimeError, match="Upload automatizado bloqueado"):
        prepare_publication(
            layout,
            privacy_status="unlisted",
            verify_validation=False,
            automated_review=True,
        )


def test_private_review_upload_does_not_record_human_approval(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    manifest = layout.load_manifest()
    manifest.config.youtube_publication.allow_automated_private_review_upload = True
    manifest.stages["render"] = manifest.stages["render"].model_copy(
        update={"provider": "ffmpeg-review-candidate"}
    )
    for key in ("metadata", "final_render"):
        manifest.approvals[key] = ApprovalRecord()
    layout.save_manifest(manifest)

    record = upload_project(
        layout,
        client=FakeYouTubeClient(),
        allow_external=True,
        confirmation=manifest.project_id,
        verify_validation=False,
        automated_review=True,
    )

    assert record.privacy_status == "private"
    current = layout.load_manifest()
    assert current.approvals["metadata"].status == ApprovalStatus.PENDING
    assert current.approvals["final_render"].status == ApprovalStatus.PENDING


def test_upload_is_idempotent_and_sends_metadata_thumbnail_and_captions(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    client = FakeYouTubeClient()
    project_id = layout.load_manifest().project_id

    first = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        verify_validation=False,
    )
    repeated = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        verify_validation=False,
    )

    assert first.video_id == repeated.video_id == "video-123"
    assert client.upload_calls == 1
    assert client.thumbnail_calls == 1
    assert client.caption_calls == 1
    assert client.update_calls == 0
    assert read_json(layout.publication)["captions"]["youtube_id"] == "caption-123"
    assert layout.load_manifest().stages["publication"].status == StageStatus.COMPLETED


def test_force_new_upload_archives_existing_record_even_for_same_render(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    client = FakeYouTubeClient()
    project_id = layout.load_manifest().project_id

    upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        verify_validation=False,
    )
    upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        force_new_upload=True,
        verify_validation=False,
    )

    assert client.upload_calls == 2
    assert (layout.publication_archive / "video-123.json").is_file()


def test_short_upload_skips_ineffective_custom_thumbnail_api_call(tmp_path) -> None:
    layout = _ready_project(tmp_path, video_format="short")
    client = FakeYouTubeClient()

    record = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=layout.load_manifest().project_id,
        verify_validation=False,
    )

    assert client.thumbnail_calls == 0
    assert record.thumbnail.status == "skipped"
    assert record.thumbnail.source_hash == sha256_file(layout.thumbnail)
    assert "selecione manualmente" in layout.load_manifest().stages["publication"].note


def test_new_short_upload_needs_no_thumbnail_file_and_leaves_frame_selection_to_youtube(
    tmp_path,
) -> None:
    layout = _ready_project(
        tmp_path,
        video_format="short",
        legacy_short_profile=False,
    )
    client = FakeYouTubeClient()

    plan = prepare_publication(layout, verify_validation=False)
    record = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=layout.load_manifest().project_id,
        verify_validation=False,
    )

    assert plan.thumbnail_path is None
    assert plan.thumbnail_hash is None
    assert not layout.thumbnail.exists()
    assert client.thumbnail_calls == 0
    assert record.thumbnail.status == "skipped"
    assert record.thumbnail.source_path is None
    assert record.thumbnail.source_hash is None
    assert "YouTube selecionará um frame" in layout.load_manifest().stages["publication"].note


def test_retry_after_thumbnail_failure_reuses_uploaded_video(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    client = FakeYouTubeClient(fail_thumbnail_once=True)
    project_id = layout.load_manifest().project_id

    with pytest.raises(RuntimeError, match="falha transitória"):
        upload_project(
            layout,
            client=client,
            allow_external=True,
            confirmation=project_id,
            verify_validation=False,
        )

    partial, _ = publication_status(layout)
    assert partial.video.status == "uploaded"
    assert partial.thumbnail.status == "failed"

    completed = upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        verify_validation=False,
    )

    assert completed.thumbnail.status == "uploaded"
    assert client.upload_calls == 1
    assert client.thumbnail_calls == 2
    assert client.caption_calls == 1


def test_changed_render_requires_explicit_new_upload(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    client = FakeYouTubeClient()
    project_id = layout.load_manifest().project_id
    upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=project_id,
        verify_validation=False,
    )
    layout.final_video.write_bytes(b"outro render")
    manifest = layout.load_manifest()
    manifest.approvals["final_render"].artifact_hash = sha256_file(layout.final_video)
    layout.save_manifest(manifest)

    with pytest.raises(RuntimeError, match="force-new-upload"):
        upload_project(
            layout,
            client=client,
            allow_external=True,
            confirmation=project_id,
            verify_validation=False,
        )

    assert client.upload_calls == 1


def test_publish_requires_video_id_and_supports_immediate_or_scheduled_release(tmp_path) -> None:
    layout = _ready_project(tmp_path)
    client = FakeYouTubeClient()
    upload_project(
        layout,
        client=client,
        allow_external=True,
        confirmation=layout.load_manifest().project_id,
        verify_validation=False,
    )

    with pytest.raises(RuntimeError, match="Confirmação inválida"):
        publish_project(
            layout,
            client=client,
            allow_external=True,
            confirmation="id-errado",
        )

    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    scheduled = publish_project(
        layout,
        client=client,
        allow_external=True,
        confirmation="video-123",
        publish_at=future,
    )
    assert scheduled.privacy_status == "private"
    assert scheduled.publish_at is not None and scheduled.publish_at.endswith("Z")

    published = publish_project(
        layout,
        client=client,
        allow_external=True,
        confirmation="video-123",
    )
    assert published.privacy_status == "public"
    assert published.published_at is not None
    assert client.visibility_calls[-1] == {
        "privacy_status": "public",
        "publish_at": None,
    }
