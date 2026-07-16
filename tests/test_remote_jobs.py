from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.remote_jobs import (
    RemoteJobStore,
    RemoteVideoRequest,
    _failure_with_codex_context,
    codex_job_prompt,
    job_is_due,
    prepare_job_project,
    work_once,
)


def _request(**updates):
    payload = {
        "video_format": "short",
        "external_services_authorized": True,
    }
    payload.update(updates)
    return RemoteVideoRequest.model_validate(payload)


def test_remote_request_applies_safe_defaults_and_requires_explicit_external_authorization(
    tmp_path,
) -> None:
    request = _request()

    assert request.duration_seconds == 50
    assert request.publication_mode == "private_review"
    assert request.publish_at is None

    store = RemoteJobStore(tmp_path / "jobs")
    with pytest.raises(ValueError, match="autorizar explicitamente"):
        store.enqueue(RemoteVideoRequest(video_format="short"))


def test_scheduled_request_requires_timezone_and_publish_at() -> None:
    with pytest.raises(ValueError, match="publish_at é obrigatório"):
        _request(publication_mode="scheduled")
    with pytest.raises(ValueError, match="fuso horário"):
        _request(
            publication_mode="scheduled",
            publish_at=datetime(2030, 1, 1, 13, 0),
        )


def test_store_supports_enqueue_cancel_retry_and_due_window(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request(start_policy="overnight"))

    assert queued.status == "queued"
    assert job_is_due(queued, datetime(2026, 7, 16, 4, 0, tzinfo=UTC)) is True
    assert job_is_due(queued, datetime(2026, 7, 16, 8, 0, tzinfo=UTC)) is False

    cancelled = store.cancel(queued.job_id)
    assert cancelled.status == "cancelled"

    second = store.enqueue(_request())
    failed = store.transition(second, "failed", error="falha de teste")
    retried = store.retry(failed.job_id)
    assert retried.status == "queued"
    assert retried.error is None


def test_prepare_job_project_enables_single_variant_without_human_approval(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Coragem em Josué", title="Coragem em Josué"))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.config.production.mode == "remote_review"
    assert manifest.config.production.use_subagents is False
    assert manifest.config.production.optimization_profile == "remote_single_variant_v1"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.youtube_publication.allow_automated_private_review_upload is True
    assert all(record.status.value == "pending" for record in manifest.approvals.values())
    assert "sem criar subagentes" in codex_job_prompt(job, layout)
    assert "exatamente um título e uma thumbnail" in codex_job_prompt(job, layout)
    assert "repita exatamente o mesmo prompt" in codex_job_prompt(job, layout)
    assert "Nunca substitua ou envelheça o personagem" in codex_job_prompt(job, layout)


def test_remote_failure_keeps_moderation_as_root_cause(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request())
    output = store.root / "logs" / f"{job.job_id}-last-message.txt"
    output.write_text(
        "A cena foi bloqueada por moderation_blocked.\n"
        "O render foi interrompido e o estado foi preservado.\n",
        encoding="utf-8",
    )

    message = _failure_with_codex_context(
        job,
        store,
        FileNotFoundError("youtube/metadata.json ausente"),
    )

    assert message.startswith("A cena foi bloqueada por moderation_blocked.")
    assert "Consequência técnica: youtube/metadata.json ausente" in message


def test_work_once_dry_run_does_not_claim_or_execute_job(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())

    selected = work_once(
        store=store,
        projects_root=tmp_path / "projects",
        repo_root=tmp_path,
        dry_run=True,
    )

    assert selected is not None and selected.job_id == queued.job_id
    persisted, _ = store.find(queued.job_id)
    assert persisted.status == "queued"
    assert persisted.project_path is None


def test_remote_cli_accepts_utf8_base64_payload(tmp_path) -> None:
    payload = _request(topic="Fé em tempos difíceis").model_dump(mode="json")
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode(
        "ascii"
    )

    result = CliRunner().invoke(
        app,
        [
            "remote",
            "enqueue",
            "--payload-base64",
            encoded,
            "--jobs-dir",
            str(tmp_path / "jobs"),
        ],
    )

    assert result.exit_code == 0
    response = json.loads(result.stdout)
    assert response["status"] == "queued"
    assert response["topic"] == "Fé em tempos difíceis"


def test_not_before_delays_immediate_job(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    now = datetime.now(UTC)
    job = store.enqueue(_request(not_before=now + timedelta(hours=1)))

    assert job_is_due(job, now) is False
    assert job_is_due(job, now + timedelta(hours=2)) is True
