from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from pipeline_videos.approved_scripts import (
    APPROVED_SCRIPT_ID_PATTERN,
    CONTENT_HASH_PATTERN,
    ApprovedScriptStore,
    ApprovedShortScript,
    approved_script_summary,
)
from pipeline_videos.models import MIN_SHORT_DURATION_SECONDS, PipelineModel
from pipeline_videos.remote_jobs import (
    RemoteJobStore,
    RemoteVideoRequest,
    job_summary,
)
from pipeline_videos.storage import read_json, write_json
from pipeline_videos.utils import sha256_value, utc_now

APPROVED_RELEASE_BUNDLE_SCHEMA_VERSION = "1.0"
APPROVED_RELEASE_ID_PATTERN = re.compile(r"^approved-release-[a-f0-9]{16}$")


class ApprovedReleaseBundleItem(PipelineModel):
    approved_script_id: str = Field(pattern=APPROVED_SCRIPT_ID_PATTERN.pattern)
    content_hash: str = Field(pattern=CONTENT_HASH_PATTERN.pattern)
    snapshot: ApprovedShortScript | None = None

    @model_validator(mode="after")
    def validate_snapshot_identity(self) -> ApprovedReleaseBundleItem:
        if self.snapshot is not None:
            if self.snapshot.status != "approved":
                raise ValueError("O bundle aceita somente snapshots aprovados.")
            if self.snapshot.approved_script_id != self.approved_script_id:
                raise ValueError("O ID do snapshot não corresponde ao item do bundle.")
            if self.snapshot.content_hash != self.content_hash:
                raise ValueError("O hash do snapshot não corresponde ao item do bundle.")
        return self


class ApprovedReleaseBundle(PipelineModel):
    schema_version: Literal["1.0"] = APPROVED_RELEASE_BUNDLE_SCHEMA_VERSION
    release_id: str
    items: list[ApprovedReleaseBundleItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_release(self) -> ApprovedReleaseBundle:
        if not APPROVED_RELEASE_ID_PATTERN.fullmatch(self.release_id):
            raise ValueError("ID de liberação aprovada inválido.")
        identities = [
            (item.approved_script_id, item.content_hash)
            for item in sorted(self.items, key=lambda item: item.approved_script_id)
        ]
        if len({script_id for script_id, _ in identities}) != len(identities):
            raise ValueError("O bundle não pode repetir approved_script_id.")
        if self.release_id != approved_release_id(identities):
            raise ValueError("O release_id não corresponde aos IDs e hashes do bundle.")
        return self


def approved_release_id(identities: list[tuple[str, str]]) -> str:
    canonical = "\n".join(
        f"{script_id}:{content_hash}" for script_id, content_hash in sorted(identities)
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"approved-release-{digest[:16]}"


def approved_release_bundle_path(jobs_root: Path, release_id: str) -> Path:
    if not APPROVED_RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValueError("ID de liberação aprovada inválido.")
    return jobs_root.expanduser().resolve() / f"{release_id}.approved-bundle.json"


def execute_approved_release_bundle(
    bundle: ApprovedReleaseBundle,
    *,
    job_store: RemoteJobStore,
) -> dict:
    script_store = ApprovedScriptStore(job_store.root)
    existing_by_id: dict[str, ApprovedShortScript] = {}

    # Validate the complete bundle before writing imports, reservations, intents, or jobs.
    for item in bundle.items:
        try:
            existing, _ = script_store.find(item.approved_script_id)
        except FileNotFoundError:
            if item.snapshot is None:
                raise RuntimeError(
                    f"O servidor não possui {item.approved_script_id} e o bundle não trouxe "
                    "o snapshot."
                ) from None
            continue
        if existing.content_hash != item.content_hash:
            raise RuntimeError(
                f"Hash divergente para {item.approved_script_id}; nenhuma alteração foi aplicada."
            )
        existing_by_id[item.approved_script_id] = existing

    intents_root = job_store.root / "approved-releases"
    intent_path = intents_root / f"{bundle.release_id}.json"
    bundle_items = [
        {
            "approved_script_id": item.approved_script_id,
            "content_hash": item.content_hash,
        }
        for item in sorted(bundle.items, key=lambda item: item.approved_script_id)
    ]
    signature = sha256_value(bundle_items)
    if intent_path.is_file():
        intent = read_json(intent_path)
        if intent.get("signature") != signature or intent.get("items") != bundle_items:
            raise RuntimeError("A intenção persistida existe com outro conteúdo.")
    else:
        now = datetime.now(UTC)
        jobs: dict[str, str] = {}
        for item in bundle.items:
            existing = existing_by_id.get(item.approved_script_id)
            if existing is not None and existing.status in {"reserved", "produced"}:
                if not existing.reserved_by_job_id:
                    raise RuntimeError(
                        f"{item.approved_script_id} não registra o job que o reservou."
                    )
                job_store.find(existing.reserved_by_job_id)
                jobs[item.approved_script_id] = existing.reserved_by_job_id
                continue
            suffix = sha256_value(
                {
                    "release_id": bundle.release_id,
                    "approved_script_id": item.approved_script_id,
                }
            )[:8]
            jobs[item.approved_script_id] = f"video-{now:%Y%m%d-%H%M%S}-{suffix}"
        intent = {
            "schema_version": "1.0",
            "release_id": bundle.release_id,
            "signature": signature,
            "status": "preparing",
            "items": bundle_items,
            "jobs": jobs,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        write_json(intent_path, intent)

    results: list[dict] = []
    for item in bundle.items:
        try:
            approved, _ = script_store.find(item.approved_script_id)
        except FileNotFoundError:
            if item.snapshot is None:  # Guarded by the complete preflight above.
                raise RuntimeError(f"Snapshot ausente para {item.approved_script_id}.") from None
            approved, _ = script_store.import_approved_snapshot(item.snapshot)
        job_id = intent["jobs"][item.approved_script_id]
        if approved.status == "approved":
            approved = script_store.reserve(job_id, script_id=approved.approved_script_id)
        elif approved.status in {"reserved", "produced"}:
            if approved.reserved_by_job_id != job_id:
                raise RuntimeError(f"{approved.approved_script_id} já pertence a outro job.")
        else:
            raise RuntimeError(f"{approved.approved_script_id} não está disponível para produção.")
        request = RemoteVideoRequest(
            video_format="short",
            production_profile="approved_fast_v1",
            approved_script_id=approved.approved_script_id,
            approved_script_hash=approved.content_hash,
            duration_seconds=max(
                MIN_SHORT_DURATION_SECONDS,
                approved.target_duration_seconds,
            ),
            editorial_profile=approved.editorial_profile,
            music_track=approved.music_track,
            voice_variant=approved.voice_variant,
            publication_mode="private_review",
            external_services_authorized=True,
        )
        job = job_store.enqueue(request, job_id=job_id)
        results.append(
            {
                "job": job_summary(job),
                "script": approved_script_summary(approved),
            }
        )

    intent.update(
        {
            "status": "completed",
            "updated_at": utc_now(),
            "results": results,
        }
    )
    write_json(intent_path, intent)
    return {
        "release_id": bundle.release_id,
        "status": "completed",
        "jobs": results,
    }
