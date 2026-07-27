from __future__ import annotations

import json
from datetime import UTC, datetime, time

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.remote_jobs import RemoteJobStore
from pipeline_videos.remote_schedules import (
    DailyShortSlot,
    RemoteScheduleStore,
    materialize_daily_schedules,
)


def _schedule_store(tmp_path):
    job_store = RemoteJobStore(tmp_path / "jobs")
    return job_store, RemoteScheduleStore(job_store.root)


def test_daily_schedule_requires_explicit_external_and_public_authorization(tmp_path) -> None:
    _, schedule_store = _schedule_store(tmp_path)
    slot = DailyShortSlot(
        production_time=time(1, 0),
        publication_time=time(12, 0),
        publication_day_offset=1,
    )

    with pytest.raises(ValueError, match="Imagegen"):
        schedule_store.create(
            name="Madrugada",
            slots=[slot],
            external_services_authorized=False,
            public_scheduling_authorized=True,
        )
    with pytest.raises(ValueError, match="publicação agendada"):
        schedule_store.create(
            name="Madrugada",
            slots=[slot],
            external_services_authorized=True,
            public_scheduling_authorized=False,
        )


def test_daily_schedule_accepts_fifteen_second_shorts(tmp_path) -> None:
    _, schedule_store = _schedule_store(tmp_path)
    schedule = schedule_store.create(
        name="Micros",
        duration_seconds=15,
        voice_variant="jesus_2",
        music_track="aloboi",
        slots=[
            DailyShortSlot(
                production_time=time(1, 0),
                publication_time=time(12, 0),
                publication_day_offset=1,
            )
        ],
        external_services_authorized=True,
        public_scheduling_authorized=True,
    )

    assert schedule.duration_seconds == 15
    assert schedule.voice_variant == "jesus_2"
    assert schedule.music_track == "aloboi"


def test_same_day_publication_must_be_after_production() -> None:
    with pytest.raises(ValueError, match="posterior"):
        DailyShortSlot(
            production_time=time(3, 0),
            publication_time=time(2, 0),
            publication_day_offset=0,
        )


def test_two_daily_slots_materialize_idempotent_separate_scheduled_jobs(tmp_path) -> None:
    job_store, schedule_store = _schedule_store(tmp_path)
    schedule = schedule_store.create(
        name="Dois Shorts na madrugada",
        slots=[
            DailyShortSlot(
                production_time=time(1, 0),
                publication_time=time(12, 0),
                publication_day_offset=1,
            ),
            DailyShortSlot(
                production_time=time(3, 0),
                publication_time=time(19, 0),
                publication_day_offset=1,
            ),
        ],
        external_services_authorized=True,
        public_scheduling_authorized=True,
    )
    now = datetime(2026, 7, 16, 3, 30, tzinfo=UTC)

    first = materialize_daily_schedules(store=job_store, schedule_store=schedule_store, now=now)
    second = materialize_daily_schedules(store=job_store, schedule_store=schedule_store, now=now)

    assert len(first) == 2
    assert [job.job_id for job in second] == [job.job_id for job in first]
    assert len(job_store.list(status="queued")) == 2
    assert len({job.schedule_occurrence_id for job in first}) == 2
    assert all(job.request.publication_mode == "scheduled" for job in first)
    assert all(job.request.external_services_authorized for job in first)
    assert all(job.request.voice_variant == "jesus_1" for job in first)
    assert all(job.request.music_track == "leaning" for job in first)
    assert first[0].request.not_before.isoformat() == "2026-07-16T01:00:00-03:00"
    assert first[1].request.not_before.isoformat() == "2026-07-16T03:00:00-03:00"
    assert first[0].request.publish_at.isoformat() == "2026-07-17T12:00:00-03:00"
    assert first[1].request.publish_at.isoformat() == "2026-07-17T19:00:00-03:00"
    assert schedule.enabled is True


def test_disabled_schedule_does_not_materialize_jobs(tmp_path) -> None:
    job_store, schedule_store = _schedule_store(tmp_path)
    schedule = schedule_store.create(
        name="Pausada",
        slots=[
            DailyShortSlot(
                production_time=time(1, 0),
                publication_time=time(12, 0),
                publication_day_offset=1,
            )
        ],
        enabled=False,
        external_services_authorized=True,
        public_scheduling_authorized=True,
    )

    jobs = materialize_daily_schedules(
        store=job_store,
        schedule_store=schedule_store,
        now=datetime(2026, 7, 16, 3, 30, tzinfo=UTC),
    )

    assert jobs == []
    assert schedule.enabled is False


def test_schedule_cli_creates_two_slots_with_explicit_authorization(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "remote",
            "schedule-create",
            "--name",
            "Madrugada",
            "--production-time",
            "01:00",
            "--production-time",
            "03:00",
            "--publication-time",
            "12:00",
            "--publication-time",
            "19:00",
            "--publish-days-after",
            "1",
            "--duration",
            "18",
            "--profile",
            "verse-led",
            "--voice-variant",
            "jesus_2",
            "--music-track",
            "aloboi",
            "--authorize-external",
            "--authorize-public-schedule",
            "--jobs-dir",
            str(tmp_path / "jobs"),
        ],
    )

    assert result.exit_code == 0
    response = json.loads(result.stdout)
    assert response["enabled"] is True
    assert response["timezone"] == "America/Sao_Paulo"
    assert response["duration_seconds"] == 18
    assert response["performance_profile"] == "feed_compact_15_20_v1"
    assert response["editorial_profile"] == "verse_led_v1"
    assert response["voice_variant"] == "jesus_2"
    assert response["music_track"] == "aloboi"
    assert response["slots"] == [
        {
            "production_time": "01:00",
            "publication_time": "12:00",
            "publication_day_offset": 1,
        },
        {
            "production_time": "03:00",
            "publication_time": "19:00",
            "publication_day_offset": 1,
        },
    ]
