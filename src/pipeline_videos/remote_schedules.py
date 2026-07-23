from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from pipeline_videos.models import MIN_SHORT_DURATION_SECONDS, PipelineModel
from pipeline_videos.remote_jobs import RemoteJobStore, RemoteVideoJob, RemoteVideoRequest
from pipeline_videos.storage import write_json
from pipeline_videos.utils import sha256_value, slugify, utc_now

REMOTE_SCHEDULE_SCHEMA_VERSION = "1.0"
SCHEDULE_ID_PATTERN = re.compile(r"^shorts-[a-z0-9][a-z0-9-]{0,70}-[a-f0-9]{8}$")


class DailyShortSlot(PipelineModel):
    production_time: time
    publication_time: time | None = None
    publication_day_offset: int = Field(default=1, ge=0, le=30)

    @field_validator("production_time", "publication_time")
    @classmethod
    def require_exact_minute(cls, value: time | None) -> time | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            raise ValueError("Os horários usam o fuso da agenda e não devem incluir offset.")
        if value.second or value.microsecond:
            raise ValueError("Os horários precisam usar precisão de minuto.")
        return value

    @model_validator(mode="after")
    def publication_must_follow_production(self) -> DailyShortSlot:
        if (
            self.publication_time is not None
            and self.publication_day_offset == 0
            and self.publication_time <= self.production_time
        ):
            raise ValueError(
                "No mesmo dia, o horário de publicação precisa ser posterior ao de produção."
            )
        return self


class DailyShortSchedule(PipelineModel):
    schema_version: str = REMOTE_SCHEDULE_SCHEMA_VERSION
    schedule_id: str
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    timezone: str = "America/Sao_Paulo"
    duration_seconds: int = Field(default=50, ge=MIN_SHORT_DURATION_SECONDS, le=180)
    script_source: Literal["automatic_catalog", "approved_pool"] = "automatic_catalog"
    publication_mode: Literal["private_review", "scheduled"] = "scheduled"
    slots: list[DailyShortSlot] = Field(min_length=1, max_length=2)
    external_services_authorized: bool
    public_scheduling_authorized: bool
    last_depletion_at: str | None = None
    last_depletion_reason: str | None = None
    created_at: str
    updated_at: str

    @field_validator("schedule_id")
    @classmethod
    def validate_schedule_id(cls, value: str) -> str:
        if not SCHEDULE_ID_PATTERN.fullmatch(value):
            raise ValueError("ID de agenda diária inválido.")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Fuso horário desconhecido: {value}") from exc
        return value

    @model_validator(mode="after")
    def require_explicit_authorization(self) -> DailyShortSchedule:
        if not self.external_services_authorized:
            raise ValueError("A agenda precisa autorizar Imagegen, ElevenLabs e upload privado.")
        if self.publication_mode == "scheduled" and not self.public_scheduling_authorized:
            raise ValueError("A agenda precisa autorizar explicitamente a publicação agendada.")
        if self.publication_mode == "scheduled" and any(
            slot.publication_time is None for slot in self.slots
        ):
            raise ValueError("Cada produção agendada precisa de horário de publicação.")
        if len({slot.production_time for slot in self.slots}) != len(self.slots):
            raise ValueError("Cada Short diário precisa ter um horário de produção diferente.")
        return self


class RemoteScheduleStore:
    def __init__(self, jobs_root: Path) -> None:
        self.root = jobs_root.expanduser().resolve() / "schedules"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, schedule_id: str) -> Path:
        if not SCHEDULE_ID_PATTERN.fullmatch(schedule_id):
            raise ValueError("ID de agenda diária inválido.")
        return self.root / f"{schedule_id}.json"

    def create(
        self,
        *,
        name: str,
        slots: list[DailyShortSlot],
        timezone: str = "America/Sao_Paulo",
        duration_seconds: int = 50,
        enabled: bool = True,
        external_services_authorized: bool,
        public_scheduling_authorized: bool,
        script_source: Literal["automatic_catalog", "approved_pool"] = "automatic_catalog",
        publication_mode: Literal["private_review", "scheduled"] = "scheduled",
    ) -> DailyShortSchedule:
        now = utc_now()
        name_slug = slugify(name)[:60] or "diarios"
        schedule = DailyShortSchedule(
            schedule_id=f"shorts-{name_slug}-{uuid4().hex[:8]}",
            name=name,
            enabled=enabled,
            timezone=timezone,
            duration_seconds=duration_seconds,
            script_source=script_source,
            publication_mode=publication_mode,
            slots=slots,
            external_services_authorized=external_services_authorized,
            public_scheduling_authorized=public_scheduling_authorized,
            created_at=now,
            updated_at=now,
        )
        self.save(schedule)
        return schedule

    def save(self, schedule: DailyShortSchedule) -> Path:
        path = self.path(schedule.schedule_id)
        write_json(path, schedule)
        with suppress(OSError):
            path.chmod(0o600)
        return path

    def find(self, schedule_id: str) -> DailyShortSchedule:
        path = self.path(schedule_id)
        if not path.is_file():
            raise FileNotFoundError(f"Agenda diária não encontrada: {schedule_id}")
        return DailyShortSchedule.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[DailyShortSchedule]:
        schedules = [
            DailyShortSchedule.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.root.glob("shorts-*.json")
        ]
        return sorted(schedules, key=lambda item: item.created_at, reverse=True)

    def set_enabled(self, schedule_id: str, enabled: bool) -> DailyShortSchedule:
        schedule = self.find(schedule_id).model_copy(
            update={"enabled": enabled, "updated_at": utc_now()}
        )
        self.save(schedule)
        return schedule

    def delete(self, schedule_id: str) -> DailyShortSchedule:
        schedule = self.find(schedule_id)
        self.path(schedule_id).unlink()
        return schedule


def _occurrence_id(schedule: DailyShortSchedule, local_date: date, slot_index: int) -> str:
    return f"{schedule.schedule_id}:{local_date.isoformat()}:{slot_index + 1}"


def _occurrence_job_id(occurrence_id: str, production_at: datetime) -> str:
    utc_production = production_at.astimezone(UTC)
    return f"video-{utc_production:%Y%m%d-%H%M%S}-{sha256_value(occurrence_id)[:8]}"


def materialize_daily_schedules(
    *,
    store: RemoteJobStore,
    schedule_store: RemoteScheduleStore,
    approved_store: Any | None = None,
    now: datetime | None = None,
) -> list[RemoteVideoJob]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    materialized: list[RemoteVideoJob] = []
    for schedule in schedule_store.list():
        if not schedule.enabled:
            continue
        timezone = ZoneInfo(schedule.timezone)
        local_date = current.astimezone(timezone).date()
        for slot_index, slot in enumerate(schedule.slots):
            production_at = datetime.combine(local_date, slot.production_time, timezone)
            occurrence_id = _occurrence_id(schedule, local_date, slot_index)
            resolved_job_id = _occurrence_job_id(occurrence_id, production_at)
            with suppress(FileNotFoundError):
                existing, _ = store.find(resolved_job_id)
                materialized.append(existing)
                continue
            publication_at = (
                datetime.combine(
                    local_date + timedelta(days=slot.publication_day_offset),
                    slot.publication_time,
                    timezone,
                )
                if slot.publication_time is not None
                else None
            )
            if publication_at is not None and publication_at.astimezone(UTC) <= current:
                continue
            approved = None
            if schedule.script_source == "approved_pool":
                if approved_store is None:
                    raise RuntimeError("A agenda de roteiros aprovados exige a fila editorial.")
                try:
                    approved = approved_store.reserve(resolved_job_id)
                except RuntimeError as exc:
                    if "Não há roteiros aprovados" in str(exc):
                        if schedule.last_depletion_reason is None:
                            schedule = schedule.model_copy(
                                update={
                                    "last_depletion_at": utc_now(),
                                    "last_depletion_reason": (
                                        "Fila de roteiros aprovados esgotada; "
                                        "nenhum tema automático foi criado."
                                    ),
                                    "updated_at": utc_now(),
                                }
                            )
                            schedule_store.save(schedule)
                        continue
                    raise
                if schedule.last_depletion_reason is not None:
                    schedule = schedule.model_copy(
                        update={
                            "last_depletion_at": None,
                            "last_depletion_reason": None,
                            "updated_at": utc_now(),
                        }
                    )
                    schedule_store.save(schedule)
            request = RemoteVideoRequest(
                video_format="short",
                production_profile=("approved_fast_v1" if approved is not None else "standard_v1"),
                approved_script_id=(approved.approved_script_id if approved is not None else None),
                approved_script_hash=approved.content_hash if approved is not None else None,
                duration_seconds=(
                    max(MIN_SHORT_DURATION_SECONDS, approved.target_duration_seconds)
                    if approved is not None
                    else schedule.duration_seconds
                ),
                editorial_profile=(
                    approved.editorial_profile if approved is not None else "verse_led_v1"
                ),
                music_track=approved.music_track if approved is not None else None,
                voice_variant=approved.voice_variant if approved is not None else "jesus_1",
                start_policy="immediate",
                not_before=production_at,
                publication_mode=schedule.publication_mode,
                publish_at=publication_at,
                audio_provider="elevenlabs",
                external_services_authorized=True,
            )
            try:
                job = store.enqueue(
                    request,
                    job_id=resolved_job_id,
                    schedule_occurrence_id=occurrence_id,
                    now=current,
                )
            except Exception:
                if approved is not None:
                    approved_store.release_reservation(
                        approved.approved_script_id,
                        job_id=resolved_job_id,
                    )
                raise
            materialized.append(job)
    return materialized


def schedule_summary(schedule: DailyShortSchedule) -> dict[str, Any]:
    return {
        "schedule_id": schedule.schedule_id,
        "name": schedule.name,
        "enabled": schedule.enabled,
        "timezone": schedule.timezone,
        "duration_seconds": schedule.duration_seconds,
        "script_source": schedule.script_source,
        "publication_mode": schedule.publication_mode,
        "slots": [
            {
                "production_time": slot.production_time.strftime("%H:%M"),
                "publication_time": (
                    slot.publication_time.strftime("%H:%M")
                    if slot.publication_time is not None
                    else None
                ),
                "publication_day_offset": slot.publication_day_offset,
            }
            for slot in schedule.slots
        ],
        "external_services_authorized": schedule.external_services_authorized,
        "public_scheduling_authorized": schedule.public_scheduling_authorized,
        "last_depletion_at": schedule.last_depletion_at,
        "last_depletion_reason": schedule.last_depletion_reason,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
    }
