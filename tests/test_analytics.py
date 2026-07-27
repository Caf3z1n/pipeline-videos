import json
from datetime import date

import pytest
from typer.testing import CliRunner

from pipeline_videos import cli
from pipeline_videos.analytics import (
    active_short_notifications,
    compact_metric_learning,
    compact_short_performance_advisories,
    diagnose_short_snapshot,
    import_experiment_file,
    import_short_metric_file,
    latest_short_diagnostics,
    load_short_performance_diagnostics,
)
from pipeline_videos.models import (
    Experiment,
    ShortMetricSnapshot,
    ShortPublicationAnalyticsContext,
    YouTubePublicationAsset,
    YouTubePublicationRecord,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import read_json, write_json


def _project(tmp_path, slug: str, *, video_format: str, target_duration_seconds: int | None = None):
    return create_project(
        slug,
        slug.replace("-", " ").title(),
        projects_dir=tmp_path,
        project_date=date(2026, 7, 16),
        video_format=video_format,
        target_duration_seconds=(
            target_duration_seconds
            if target_duration_seconds is not None
            else 60
            if video_format == "short"
            else 420
        ),
    )


def test_metrics_import_is_idempotent_and_learning_prefers_same_format(tmp_path) -> None:
    long_layout = _project(tmp_path, "longo-base", video_format="landscape")
    short_layout = _project(tmp_path, "short-base", video_format="short")
    target = _project(tmp_path, "longo-alvo", video_format="landscape")
    long_source = tmp_path / "long-experiment.json"
    short_source = tmp_path / "short-experiment.json"
    write_json(
        long_source,
        Experiment(
            experiment_id="long-hook-01",
            hypothesis="Uma abertura mais direta melhora a retenção inicial.",
            change="Gancho literal antes do contexto.",
            project_ids=[long_layout.load_manifest().project_id],
            metrics={"ctr_percent": 6.4, "retention_30s_percent": 71.0},
            conclusion="Manter como hipótese para o próximo longo.",
        ),
    )
    write_json(
        short_source,
        Experiment(
            experiment_id="short-hook-01",
            hypothesis="Verso primeiro melhora conclusão.",
            change="Verso no primeiro beat.",
            project_ids=[short_layout.load_manifest().project_id],
            metrics={"average_percentage_viewed": 88.0},
        ),
    )

    first = import_experiment_file(long_layout, long_source)
    experiment_bytes = long_layout.experiments.read_bytes()
    event_bytes = long_layout.events.read_bytes()
    repeated = import_experiment_file(long_layout, long_source)
    import_experiment_file(short_layout, short_source)
    learning = compact_metric_learning(target)

    assert first == repeated
    assert long_layout.experiments.read_bytes() == experiment_bytes
    assert long_layout.events.read_bytes() == event_bytes
    assert len(repeated.experiments) == 1
    assert [item["experiment_id"] for item in learning] == ["long-hook-01", "short-hook-01"]
    assert all("confidence_note" in item for item in learning)


def test_metrics_import_rejects_invalid_percentages(tmp_path) -> None:
    layout = _project(tmp_path, "metricas-invalidas", video_format="landscape")
    source = tmp_path / "invalid.json"
    write_json(
        source,
        Experiment(
            experiment_id="invalid-ctr",
            hypothesis="Teste inválido.",
            change="Nenhuma.",
            project_ids=[layout.load_manifest().project_id],
            metrics={"ctr_percent": 120.0},
        ),
    )

    with pytest.raises(ValueError, match="entre 0 e 100"):
        import_experiment_file(layout, source)


def _snapshot(**updates) -> ShortMetricSnapshot:
    payload = {
        "snapshot_id": "short-01-72h",
        "video_id": "youtube-short-01",
        "published_at": "2026-07-17T09:00:00-03:00",
        "observed_at": "2026-07-20T09:00:00-03:00",
        "source": "youtube_studio",
        "processing_status": "final",
        "view_counting_standard": "shorts_starts_2025",
        "shown_in_feed": 1_000,
        "views": 886,
        "engaged_views": 280,
        "stayed_to_watch_percent": 28.9,
        "swiped_away_percent": 71.1,
        "average_view_duration_seconds": 32.0,
        "average_percentage_viewed": 85.1,
        "video_duration_seconds": 38.0,
        "likes": 119,
        "shares": 8,
        "subscribers_gained": 33,
        "duration_profile": "adaptive_compact_v1",
        "hook_contract": "cinematic_interruption_v1",
        "visual_standard_id": "biblical-midcentury-gouache-v1",
        "opening_visual_standard": "scroll_stop_concrete_v1",
        "first_frame_pattern": "close_action_in_progress",
    }
    payload.update(updates)
    return ShortMetricSnapshot.model_validate(payload)


def test_short_snapshot_import_diagnoses_opening_and_is_idempotent(tmp_path) -> None:
    layout = _project(tmp_path, "short-diagnostico", video_format="short")
    source = tmp_path / "short-snapshot.json"
    write_json(source, _snapshot())

    memory, diagnostics = import_short_metric_file(layout, source)
    snapshot_bytes = layout.short_metric_snapshots.read_bytes()
    diagnosis_bytes = layout.short_performance_diagnostics.read_bytes()
    event_bytes = layout.events.read_bytes()
    repeated_memory, repeated_diagnostics = import_short_metric_file(layout, source)

    assert memory == repeated_memory
    assert diagnostics == repeated_diagnostics
    assert layout.short_metric_snapshots.read_bytes() == snapshot_bytes
    assert layout.short_performance_diagnostics.read_bytes() == diagnosis_bytes
    assert layout.events.read_bytes() == event_bytes
    assert len(memory.snapshots) == 1
    assert memory.snapshots[0].opening_visual_standard == "scroll_stop_concrete_v1"
    assert memory.snapshots[0].first_frame_pattern == "close_action_in_progress"
    diagnosis = diagnostics.diagnostics[0]
    assert diagnosis.evidence_status == "confirmed"
    assert diagnosis.appeal_status == "below_baseline"
    assert diagnosis.engagement_status == "developing"
    assert diagnosis.primary_diagnosis == "opening_bottleneck"
    assert diagnosis.derived_metrics["likes_per_engaged_view_percent"] == 42.5
    assert {item.code for item in diagnosis.notifications} >= {"SHORTS_OPENING_BOTTLENECK"}


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        (
            {
                "stayed_to_watch_percent": 19.1,
                "swiped_away_percent": 80.9,
                "average_percentage_viewed": 65.7,
            },
            "opening_and_progression",
        ),
        (
            {
                "stayed_to_watch_percent": 26.6,
                "swiped_away_percent": 73.4,
                "average_percentage_viewed": 64.0,
            },
            "opening_and_progression",
        ),
        (
            {
                "stayed_to_watch_percent": 40.2,
                "swiped_away_percent": 59.8,
                "average_percentage_viewed": 167.6,
            },
            "editorial_reference_candidate",
        ),
        (
            {
                "stayed_to_watch_percent": 36.0,
                "swiped_away_percent": 64.0,
                "average_percentage_viewed": 68.0,
            },
            "progression_bottleneck",
        ),
    ],
)
def test_short_diagnosis_separates_appeal_and_engagement(updates, expected) -> None:
    assert diagnose_short_snapshot(_snapshot(**updates)).primary_diagnosis == expected


def test_satisfaction_preserves_subscriber_loss_signal() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(subscribers_gained=None, subscribers_lost=7, engaged_views=280)
    )

    assert diagnosis.satisfaction_status == "observed"
    assert diagnosis.derived_metrics["subscribers_lost_per_engaged_view_percent"] == 2.5


def test_short_diagnosis_waits_for_minimum_sample() -> None:
    diagnosis = diagnose_short_snapshot(_snapshot(shown_in_feed=120, views=110, engaged_views=40))

    assert diagnosis.evidence_status == "insufficient_sample"
    assert diagnosis.primary_diagnosis == "insufficient_evidence"
    assert not any(item.severity in {"warning", "critical"} for item in diagnosis.notifications)


def test_views_never_confirm_missing_shorts_feed_exposure() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(shown_in_feed=None, views=10_000, engaged_views=300)
    )

    assert diagnosis.stage_evidence["appeal"] == "insufficient_sample"
    assert diagnosis.primary_diagnosis == "appeal_sample_insufficient"
    assert not any(
        item.stage == "appeal" and item.severity in {"warning", "critical"}
        for item in diagnosis.notifications
    )
    assert any(item.code == "SHORTS_FEED_SAMPLE_MISSING" for item in diagnosis.notifications)


def test_api_snapshot_cannot_claim_stayed_to_watch() -> None:
    with pytest.raises(ValueError, match="não fornece Stayed to watch"):
        _snapshot(source="youtube_analytics_api")


def test_experiment_accepts_replay_percentage_above_one_hundred(tmp_path) -> None:
    layout = _project(tmp_path, "replay-valido", video_format="short")
    source = tmp_path / "replay.json"
    write_json(
        source,
        Experiment(
            experiment_id="replay-01",
            hypothesis="Loops elevam o percentual médio assistido.",
            change="Nenhuma.",
            project_ids=[layout.load_manifest().project_id],
            metrics={"average_percentage_viewed": 175.4},
        ),
    )

    imported = import_experiment_file(layout, source)

    assert imported.experiments[0].metrics["average_percentage_viewed"] == 175.4


def test_snapshot_percentages_must_be_complementary() -> None:
    with pytest.raises(ValueError, match="aproximadamente 100"):
        _snapshot(stayed_to_watch_percent=30.0, swiped_away_percent=60.0)


def test_new_snapshot_resolves_old_active_alert_for_same_video(tmp_path) -> None:
    layout = _project(tmp_path, "short-alerta-resolvido", video_format="short")
    first_source = tmp_path / "first.json"
    latest_source = tmp_path / "latest.json"
    write_json(first_source, _snapshot())
    write_json(
        latest_source,
        _snapshot(
            snapshot_id="short-01-96h",
            observed_at="2026-07-21T09:00:00-03:00",
            stayed_to_watch_percent=36.0,
            swiped_away_percent=64.0,
            average_percentage_viewed=92.0,
        ),
    )

    import_short_metric_file(layout, first_source)
    _memory, diagnostics = import_short_metric_file(layout, latest_source)
    latest = latest_short_diagnostics(diagnostics)

    assert len(latest) == 1
    assert latest[0].snapshot_id == "short-01-96h"
    assert not any(item.severity in {"warning", "critical"} for item in latest[0].notifications)


def test_only_confirmed_compatible_advisories_reach_next_short(tmp_path) -> None:
    source_layout = _project(tmp_path, "short-fonte", video_format="short")
    target_layout = _project(tmp_path, "short-alvo", video_format="short")
    source = tmp_path / "confirmed.json"
    source_manifest = source_layout.load_manifest()
    write_json(
        source,
        _snapshot(
            duration_profile=source_manifest.config.shorts_script.duration_profile,
            performance_profile=source_manifest.config.shorts_script.performance_profile,
            hook_contract="user_six_v1",
            visual_standard_id=source_manifest.config.images.visual_standard_id,
            opening_visual_standard=source_manifest.config.images.opening_visual_standard,
            video_duration_seconds=60.03,
        ),
    )
    import_short_metric_file(source_layout, source)

    advisories = compact_short_performance_advisories(target_layout)

    assert len(advisories) == 1
    assert advisories[0]["primary_diagnosis"] == "opening_bottleneck"
    assert any("primeiro frame" in item for item in advisories[0]["recommended_actions"])


def test_missing_publication_time_never_confirms_snapshot() -> None:
    diagnosis = diagnose_short_snapshot(_snapshot(published_at=None))

    assert diagnosis.evidence_status == "provisional"
    assert not active_short_notifications(type("Diagnostics", (), {"diagnostics": [diagnosis]})())


def test_newer_provisional_snapshot_does_not_clear_confirmed_appeal_alert(tmp_path) -> None:
    layout = _project(tmp_path, "short-provisional", video_format="short")
    confirmed = tmp_path / "confirmed.json"
    provisional = tmp_path / "provisional.json"
    write_json(confirmed, _snapshot())
    write_json(
        provisional,
        _snapshot(
            snapshot_id="short-processing",
            observed_at="2026-07-21T09:00:00-03:00",
            processing_status="processing",
            stayed_to_watch_percent=40.0,
            swiped_away_percent=60.0,
        ),
    )

    import_short_metric_file(layout, confirmed)
    _memory, diagnostics = import_short_metric_file(layout, provisional)
    active = active_short_notifications(diagnostics)

    assert any(diagnostic.snapshot_id == "short-01-72h" for diagnostic, _item in active)


def test_confirmed_api_snapshot_without_appeal_metric_preserves_studio_alert(tmp_path) -> None:
    layout = _project(tmp_path, "short-api-gap", video_format="short")
    studio = tmp_path / "studio.json"
    api = tmp_path / "api.json"
    write_json(studio, _snapshot())
    write_json(
        api,
        _snapshot(
            snapshot_id="short-api-96h",
            observed_at="2026-07-21T09:00:00-03:00",
            source="youtube_analytics_api",
            stayed_to_watch_percent=None,
            swiped_away_percent=None,
            average_percentage_viewed=95.0,
        ),
    )

    import_short_metric_file(layout, studio)
    _memory, diagnostics = import_short_metric_file(layout, api)
    active = active_short_notifications(diagnostics)

    assert any(
        diagnostic.snapshot_id == "short-01-72h" and notification.stage == "appeal"
        for diagnostic, notification in active
    )


def test_api_only_snapshot_can_raise_independent_progression_alert() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(
            source="youtube_analytics_api",
            stayed_to_watch_percent=None,
            swiped_away_percent=None,
            views=1_000,
            engaged_views=300,
            average_percentage_viewed=60.0,
        )
    )

    assert diagnosis.primary_diagnosis == "progression_bottleneck"
    assert any(
        item.code == "SHORTS_PROGRESSION_BOTTLENECK" and item.stage == "engagement"
        for item in diagnosis.notifications
    )


def test_stage_notifications_do_not_mix_evidence_from_another_stage() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(
            stayed_to_watch_percent=20.0,
            swiped_away_percent=80.0,
            average_percentage_viewed=65.0,
        )
    )
    appeal = next(item for item in diagnosis.notifications if item.stage == "appeal")
    engagement = next(item for item in diagnosis.notifications if item.stage == "engagement")

    assert diagnosis.primary_diagnosis == "opening_and_progression"
    assert all("Percentual médio" not in line for line in appeal.evidence)
    assert all("Continuaram assistindo" not in line for line in engagement.evidence)


def test_late_export_of_older_window_cannot_clear_newer_appeal_evidence() -> None:
    newer_window = diagnose_short_snapshot(
        _snapshot(
            snapshot_id="newer-window",
            observed_at="2026-07-20T10:00:00-03:00",
            window_end="2026-07-20T09:00:00-03:00",
            stayed_to_watch_percent=25.0,
            swiped_away_percent=75.0,
        )
    )
    late_old_export = diagnose_short_snapshot(
        _snapshot(
            snapshot_id="late-old-export",
            observed_at="2026-07-21T10:00:00-03:00",
            window_end="2026-07-19T09:00:00-03:00",
            stayed_to_watch_percent=40.0,
            swiped_away_percent=60.0,
        )
    )
    diagnostics = type(
        "Diagnostics",
        (),
        {"diagnostics": [newer_window, late_old_export]},
    )()

    assert latest_short_diagnostics(diagnostics)[0].snapshot_id == "newer-window"
    assert any(
        diagnostic.snapshot_id == "newer-window" and item.stage == "appeal"
        for diagnostic, item in active_short_notifications(diagnostics)
    )


@pytest.mark.parametrize("value", [True, float("inf"), float("nan")])
def test_snapshot_rejects_boolean_or_non_finite_metrics(value) -> None:
    with pytest.raises(ValueError, match="booleanos|finitas"):
        _snapshot(average_percentage_viewed=value)


def test_engagement_stage_uses_engaged_views_sample() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(
            shown_in_feed=1_000,
            views=900,
            engaged_views=10,
            stayed_to_watch_percent=28.0,
            swiped_away_percent=72.0,
            average_percentage_viewed=60.0,
        )
    )

    assert diagnosis.stage_evidence["appeal"] == "confirmed"
    assert diagnosis.stage_evidence["engagement"] == "insufficient_sample"
    assert diagnosis.stage_evidence["satisfaction"] == "insufficient_sample"
    assert diagnosis.primary_diagnosis == "opening_bottleneck"
    assert not any(item.stage == "engagement" for item in diagnosis.notifications)


def test_delayed_export_uses_metric_window_end_for_maturity() -> None:
    diagnosis = diagnose_short_snapshot(
        _snapshot(
            window_start="2026-07-17T09:00:00-03:00",
            window_end="2026-07-17T10:00:00-03:00",
        )
    )

    assert diagnosis.evidence_status == "provisional"


def test_same_video_and_observed_at_requires_combined_snapshot(tmp_path) -> None:
    layout = _project(tmp_path, "short-same-time", video_format="short")
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_json(first, _snapshot())
    write_json(second, _snapshot(snapshot_id="other-id", source="combined"))
    import_short_metric_file(layout, first)

    with pytest.raises(ValueError, match="source=combined"):
        import_short_metric_file(layout, second)


def test_stale_diagnostic_policy_is_recomputed_and_migrated_on_import(tmp_path) -> None:
    layout = _project(tmp_path, "short-policy", video_format="short")
    source = tmp_path / "source.json"
    write_json(source, _snapshot())
    import_short_metric_file(layout, source)
    stale = read_json(layout.short_performance_diagnostics)
    stale["policy"]["diagnostic_algorithm_revision"] = 0
    stale["diagnostics"] = []
    write_json(layout.short_performance_diagnostics, stale)

    current = load_short_performance_diagnostics(layout)
    assert len(current.diagnostics) == 1
    import_short_metric_file(layout, source)

    migrated = read_json(layout.short_performance_diagnostics)
    assert migrated["policy"]["diagnostic_algorithm_revision"] == 4
    assert len(migrated["diagnostics"]) == 1


def _write_publication_fingerprint(layout, *, video_id: str = "youtube-short-01") -> None:
    project = layout.load_manifest()
    now = "2026-07-17T09:00:00-03:00"
    asset = YouTubePublicationAsset(status="uploaded", updated_at=now)
    write_json(
        layout.publication,
        YouTubePublicationRecord(
            project_id=project.project_id,
            video_id=video_id,
            youtube_url=f"https://youtu.be/{video_id}",
            channel_id="channel",
            channel_title="Canal",
            title=project.title,
            privacy_status="private",
            short_analytics_context=ShortPublicationAnalyticsContext(
                duration_profile=project.config.shorts_script.duration_profile,
                performance_profile=project.config.shorts_script.performance_profile,
                video_duration_seconds=60.0,
                video_duration_source="ffprobe",
                hook_contract="user_six_v1",
                visual_standard_id=project.config.images.visual_standard_id,
                hook_text="Nunca faça da pressa a voz que decide por você.",
                hook_mechanism="consequence_first",
                opening_visual_standard=project.config.images.opening_visual_standard,
            ),
            video=asset,
            metadata=asset,
            captions=asset,
            created_at=now,
            updated_at=now,
        ),
    )


def test_publication_fingerprint_enrichment_is_idempotent_with_explicit_nulls(tmp_path) -> None:
    layout = _project(tmp_path, "short-fingerprint", video_format="short")
    _write_publication_fingerprint(layout)
    source = tmp_path / "snapshot.json"
    snapshot = _snapshot(
        duration_profile=None,
        performance_profile=None,
        hook_contract=None,
        visual_standard_id=None,
        hook_text=None,
        hook_mechanism=None,
        opening_visual_standard=None,
        first_frame_pattern=None,
        video_duration_seconds=None,
    )
    write_json(source, snapshot)

    first, _diagnostics = import_short_metric_file(layout, source)
    second, _diagnostics = import_short_metric_file(layout, source)

    assert first == second
    assert first.snapshots[0].opening_visual_standard == "scroll_stop_concrete_v1"
    assert first.snapshots[0].performance_profile == "feed_depth_45_60_v1"
    assert first.snapshots[0].video_duration_seconds == 60.0
    assert first.snapshots[0].first_frame_pattern is None


def test_publication_fingerprint_rejects_conflicting_import_metadata(tmp_path) -> None:
    layout = _project(tmp_path, "short-fingerprint-conflict", video_format="short")
    _write_publication_fingerprint(layout)
    source = tmp_path / "snapshot.json"
    write_json(source, _snapshot(opening_visual_standard="legacy_v1"))

    with pytest.raises(ValueError, match="fingerprint congelado"):
        import_short_metric_file(layout, source)


def test_duration_cohort_blocks_compact_extremes(tmp_path) -> None:
    source_layout = _project(
        tmp_path,
        "short-44s",
        video_format="short",
        target_duration_seconds=44,
    )
    target_layout = _project(
        tmp_path,
        "short-15s",
        video_format="short",
        target_duration_seconds=15,
    )
    source = tmp_path / "short-44s.json"
    write_json(source, _snapshot(video_duration_seconds=44.0))
    import_short_metric_file(source_layout, source)

    assert compact_short_performance_advisories(target_layout) == []


def test_status_json_identifies_each_alerted_youtube_video(tmp_path) -> None:
    layout = _project(tmp_path, "short-multiple-uploads", video_format="short")
    source = tmp_path / "snapshots.json"
    write_json(
        source,
        [
            _snapshot().model_dump(mode="json"),
            _snapshot(
                snapshot_id="second-video-72h",
                video_id="youtube-short-02",
            ).model_dump(mode="json"),
        ],
    )
    import_short_metric_file(layout, source)

    result = CliRunner().invoke(
        cli.app,
        [
            "status",
            layout.load_manifest().project_id,
            "--projects-dir",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert {item["video_id"] for item in payload["analytics"]["active_alerts"]} == {
        "youtube-short-01",
        "youtube-short-02",
    }
    assert {item["stage"] for item in payload["analytics"]["active_alerts"]} == {"appeal"}
