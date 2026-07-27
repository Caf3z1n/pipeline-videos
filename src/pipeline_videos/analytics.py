from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline_videos.models import (
    Experiment,
    ExperimentMemory,
    ShortMetricSnapshot,
    ShortMetricSnapshotMemory,
    ShortPerformanceDiagnostic,
    ShortPerformanceDiagnostics,
    ShortPerformanceNotification,
    YouTubePublicationRecord,
)
from pipeline_videos.storage import (
    PROJECT_CATEGORY_DIRS,
    ProjectLayout,
    read_json,
    read_model,
    write_json,
)
from pipeline_videos.utils import relative_posix, utc_now

BOUNDED_PERCENT_METRICS = {
    "ctr_percent",
    "stayed_to_watch_percent",
    "swiped_away_percent",
}
NONNEGATIVE_METRICS = {
    "impressions",
    "shown_in_feed",
    "views",
    "engaged_views",
    "likes",
    "comments",
    "shares",
    "watch_time_hours",
    "average_view_duration_seconds",
    "average_percentage_viewed",
    "retention_1s_percent",
    "retention_3s_percent",
    "retention_30s_percent",
    "retention_60s_percent",
    "subscribers_gained",
    "subscribers_lost",
}


@dataclass(frozen=True)
class ShortsDiagnosticPolicy:
    version: str = "appeal_engagement_satisfaction_v1"
    diagnostic_algorithm_revision: int = 4
    appeal_baseline_provenance: str = "channel_snapshot_2026-06-27_to_2026-07-24"
    minimum_sample_size: int = 200
    confirmation_hours: float = 48.0
    appeal_critical_below_percent: float = 22.0
    appeal_baseline_percent: float = 32.4
    appeal_healthy_percent: float = 35.0
    appeal_reference_percent: float = 38.0
    engagement_weak_below_percent: float = 75.0
    engagement_strong_percent: float = 90.0
    engagement_replay_strong_percent: float = 100.0

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "version": self.version,
            "diagnostic_algorithm_revision": self.diagnostic_algorithm_revision,
            "appeal_baseline_provenance": self.appeal_baseline_provenance,
            "minimum_sample_size": self.minimum_sample_size,
            "confirmation_hours": self.confirmation_hours,
            "appeal_critical_below_percent": self.appeal_critical_below_percent,
            "appeal_baseline_percent": self.appeal_baseline_percent,
            "appeal_healthy_percent": self.appeal_healthy_percent,
            "appeal_reference_percent": self.appeal_reference_percent,
            "engagement_weak_below_percent": self.engagement_weak_below_percent,
            "engagement_strong_percent": self.engagement_strong_percent,
            "engagement_replay_strong_percent": self.engagement_replay_strong_percent,
        }


DEFAULT_SHORTS_DIAGNOSTIC_POLICY = ShortsDiagnosticPolicy()


def _validate_metrics(experiment: Experiment) -> None:
    for name, value in experiment.metrics.items():
        if value is None or isinstance(value, str):
            continue
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"Métrica inválida em {experiment.experiment_id}: {name}.")
        numeric = float(value)
        if name in BOUNDED_PERCENT_METRICS and not 0 <= numeric <= 100:
            raise ValueError(f"{name} precisa estar entre 0 e 100.")
        if name in NONNEGATIVE_METRICS and numeric < 0:
            raise ValueError(f"{name} não pode ser negativa.")
    stayed = experiment.metrics.get("stayed_to_watch_percent")
    swiped = experiment.metrics.get("swiped_away_percent")
    if (
        isinstance(stayed, (int, float))
        and isinstance(swiped, (int, float))
        and abs(float(stayed) + float(swiped) - 100.0) > 1.5
    ):
        raise ValueError(
            "stayed_to_watch_percent + swiped_away_percent precisa totalizar aproximadamente 100."
        )


def import_experiment_file(layout: ProjectLayout, source: Path) -> ExperimentMemory:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo de experimento não encontrado: {source}")
    raw = read_json(source)
    if isinstance(raw, dict) and "experiments" in raw:
        incoming = ExperimentMemory.model_validate(raw).experiments
    else:
        incoming = [Experiment.model_validate(raw)]
    if not incoming:
        raise ValueError("O arquivo não contém experimentos.")
    for experiment in incoming:
        _validate_metrics(experiment)

    memory = (
        read_model(layout.experiments, ExperimentMemory)
        if layout.experiments.is_file()
        else ExperimentMemory()
    )
    previous = memory.model_dump(mode="json")
    by_id = {experiment.experiment_id: experiment for experiment in memory.experiments}
    for experiment in incoming:
        by_id[experiment.experiment_id] = experiment
    memory.experiments = list(by_id.values())
    if memory.model_dump(mode="json") == previous:
        return memory
    write_json(layout.experiments, memory)
    layout.append_event(
        {
            "event": "analytics_experiments_imported",
            "source": str(source),
            "experiment_ids": [experiment.experiment_id for experiment in incoming],
            "imported_at": utc_now(),
        }
    )
    return memory


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(numerator / denominator * 100.0, 4)


def _effective_average_percentage_viewed(snapshot: ShortMetricSnapshot) -> float | None:
    if snapshot.average_percentage_viewed is not None:
        return snapshot.average_percentage_viewed
    if (
        snapshot.average_view_duration_seconds is not None
        and snapshot.video_duration_seconds is not None
    ):
        return round(
            snapshot.average_view_duration_seconds / snapshot.video_duration_seconds * 100.0,
            4,
        )
    return None


def _duration_cohort(duration_seconds: float | None) -> str | None:
    if duration_seconds is None:
        return None
    for upper_bound, label in (
        (20.0, "15_20s"),
        (30.0, "21_30s"),
        (44.0, "31_44s"),
        (60.0, "45_60s"),
        (90.0, "61_90s"),
        (120.0, "91_120s"),
        (180.0, "121_180s"),
    ):
        if duration_seconds <= upper_bound + 0.5:
            return label
    return "over_180s"


def _appeal_sample(snapshot: ShortMetricSnapshot) -> tuple[int | None, str]:
    if snapshot.shown_in_feed is not None:
        return snapshot.shown_in_feed, "shown_in_feed"
    if snapshot.views is not None:
        # Views include starts outside the Shorts feed and may include replays. Preserve the
        # provenance, but never promote that proxy to confirmed feed-exposure evidence.
        return None, "views_proxy"
    return None, "unavailable"


def _engagement_sample(snapshot: ShortMetricSnapshot) -> tuple[int | None, str]:
    if snapshot.engaged_views is not None:
        return snapshot.engaged_views, "engaged_views"
    return None, "unavailable"


def _satisfaction_sample(snapshot: ShortMetricSnapshot) -> tuple[int | None, str]:
    if snapshot.engaged_views is not None:
        return snapshot.engaged_views, "engaged_views"
    return None, "unavailable"


def _snapshot_maturity(
    snapshot: ShortMetricSnapshot,
    *,
    policy: ShortsDiagnosticPolicy,
) -> str:
    if snapshot.processing_status != "final":
        return "provisional"
    if snapshot.published_at is None:
        return "provisional"
    evidence_end = snapshot.window_end or snapshot.observed_at
    age_hours = (evidence_end - snapshot.published_at).total_seconds() / 3600
    if age_hours < policy.confirmation_hours:
        return "provisional"
    return "confirmed"


def _stage_evidence_status(
    maturity: str,
    *,
    sample_size: int | None,
    policy: ShortsDiagnosticPolicy,
) -> str:
    if sample_size is None or sample_size < policy.minimum_sample_size:
        return "insufficient_sample"
    return maturity


def _appeal_status(stayed: float | None, policy: ShortsDiagnosticPolicy) -> str:
    if stayed is None:
        return "unknown"
    if stayed < policy.appeal_critical_below_percent:
        return "critical"
    if stayed < policy.appeal_baseline_percent:
        return "below_baseline"
    if stayed < policy.appeal_healthy_percent:
        return "monitor"
    if stayed < policy.appeal_reference_percent:
        return "healthy"
    return "reference"


def _engagement_status(
    average_percentage_viewed: float | None,
    policy: ShortsDiagnosticPolicy,
) -> str:
    if average_percentage_viewed is None:
        return "unknown"
    if average_percentage_viewed < policy.engagement_weak_below_percent:
        return "weak"
    if average_percentage_viewed < policy.engagement_strong_percent:
        return "developing"
    if average_percentage_viewed < policy.engagement_replay_strong_percent:
        return "strong"
    return "replay_strong"


def _notification(
    *,
    code: str,
    severity: str,
    stage: str,
    title: str,
    evidence: list[str],
    actions: list[str],
) -> ShortPerformanceNotification:
    return ShortPerformanceNotification(
        code=code,
        severity=severity,
        stage=stage,
        title=title,
        evidence=evidence,
        recommended_actions=actions,
    )


def diagnose_short_snapshot(
    snapshot: ShortMetricSnapshot,
    *,
    policy: ShortsDiagnosticPolicy = DEFAULT_SHORTS_DIAGNOSTIC_POLICY,
) -> ShortPerformanceDiagnostic:
    """Separate feed appeal, post-hook engagement and satisfaction without a total score."""

    appeal_sample_size, appeal_sample_basis = _appeal_sample(snapshot)
    engagement_sample_size, engagement_sample_basis = _engagement_sample(snapshot)
    satisfaction_sample_size, satisfaction_sample_basis = _satisfaction_sample(snapshot)
    maturity = _snapshot_maturity(snapshot, policy=policy)
    stage_evidence = {
        "appeal": _stage_evidence_status(
            maturity,
            sample_size=appeal_sample_size,
            policy=policy,
        ),
        "engagement": _stage_evidence_status(
            maturity,
            sample_size=engagement_sample_size,
            policy=policy,
        ),
        "satisfaction": _stage_evidence_status(
            maturity,
            sample_size=satisfaction_sample_size,
            policy=policy,
        ),
    }
    raw_stayed = snapshot.stayed_to_watch_percent
    raw_average_percentage_viewed = _effective_average_percentage_viewed(snapshot)
    appeal_status = _appeal_status(raw_stayed, policy)
    engagement_status = _engagement_status(raw_average_percentage_viewed, policy)
    satisfaction_available = any(
        value is not None
        for value in (
            snapshot.likes,
            snapshot.comments,
            snapshot.shares,
            snapshot.subscribers_gained,
            snapshot.subscribers_lost,
        )
    )
    satisfaction_status = "observed" if satisfaction_available else "unknown"
    relevant_stages = []
    if raw_stayed is not None:
        relevant_stages.append("appeal")
    if raw_average_percentage_viewed is not None:
        relevant_stages.append("engagement")
    if satisfaction_available:
        relevant_stages.append("satisfaction")
    if not relevant_stages:
        relevant_stages.append("appeal")
    relevant_evidence = [stage_evidence[stage] for stage in relevant_stages]
    evidence_status = (
        "confirmed"
        if "confirmed" in relevant_evidence
        else "provisional"
        if "provisional" in relevant_evidence
        else "insufficient_sample"
    )
    stayed = raw_stayed if stage_evidence["appeal"] != "insufficient_sample" else None
    average_percentage_viewed = (
        raw_average_percentage_viewed
        if stage_evidence["engagement"] != "insufficient_sample"
        else None
    )
    derived: dict[str, float] = {}
    if raw_stayed is not None:
        derived["stayed_to_watch_percent"] = round(raw_stayed, 4)
    if raw_average_percentage_viewed is not None:
        derived["effective_average_percentage_viewed"] = round(
            raw_average_percentage_viewed,
            4,
        )
    if snapshot.engaged_views is not None and snapshot.views:
        derived["engaged_views_per_view_percent"] = round(
            snapshot.engaged_views / snapshot.views * 100.0,
            4,
        )
    for name, value in (
        ("likes_per_engaged_view_percent", _rate(snapshot.likes, snapshot.engaged_views)),
        ("comments_per_engaged_view_percent", _rate(snapshot.comments, snapshot.engaged_views)),
        ("shares_per_engaged_view_percent", _rate(snapshot.shares, snapshot.engaged_views)),
        (
            "subscribers_gained_per_engaged_view_percent",
            _rate(snapshot.subscribers_gained, snapshot.engaged_views),
        ),
        (
            "subscribers_lost_per_engaged_view_percent",
            _rate(snapshot.subscribers_lost, snapshot.engaged_views),
        ),
        ("likes_per_view_percent", _rate(snapshot.likes, snapshot.views)),
        ("shares_per_view_percent", _rate(snapshot.shares, snapshot.views)),
        (
            "subscribers_gained_per_view_percent",
            _rate(snapshot.subscribers_gained, snapshot.views),
        ),
        (
            "subscribers_lost_per_view_percent",
            _rate(snapshot.subscribers_lost, snapshot.views),
        ),
    ):
        if value is not None:
            derived[name] = value

    notifications: list[ShortPerformanceNotification] = []
    if evidence_status == "insufficient_sample":
        primary = "insufficient_evidence"
        notifications.append(
            _notification(
                code="SHORTS_SAMPLE_INSUFFICIENT",
                severity="info",
                stage="evidence",
                title="Amostra ainda insuficiente para acionar mudança editorial",
                evidence=[
                    "Amostras disponíveis por estágio: "
                    f"apelo={appeal_sample_size if appeal_sample_size is not None else 'ausente'}, "
                    "engajamento="
                    + (
                        str(engagement_sample_size)
                        if engagement_sample_size is not None
                        else "ausente"
                    )
                    + ".",
                    f"Mínimo operacional da política: {policy.minimum_sample_size}.",
                ],
                actions=["Aguardar processamento ou importar um snapshot posterior do Studio."],
            )
        )
    elif stayed is None:
        weak_progression = (
            average_percentage_viewed is not None
            and average_percentage_viewed < policy.engagement_weak_below_percent
        )
        primary = (
            "progression_bottleneck"
            if weak_progression
            else "appeal_metric_missing"
            if raw_stayed is None
            else "appeal_sample_insufficient"
        )
        notifications.append(
            _notification(
                code=(
                    "SHORTS_APPEAL_METRIC_MISSING"
                    if raw_stayed is None
                    else "SHORTS_APPEAL_SAMPLE_INSUFFICIENT"
                ),
                severity="info",
                stage="evidence",
                title=(
                    "Continuaram assistindo não está disponível neste snapshot"
                    if raw_stayed is None
                    else "Continuaram assistindo ainda não tem amostra suficiente"
                ),
                evidence=[
                    (
                        "engaged_views/views não substitui Stayed to watch e não será usado "
                        "como se fosse."
                        if raw_stayed is None
                        else f"Amostra de apelo: {appeal_sample_size or 0}; "
                        f"mínimo operacional: {policy.minimum_sample_size}."
                    )
                ],
                actions=[
                    "Importar Continuaram assistindo e Deslizaram do YouTube Studio "
                    "quando disponíveis."
                    if raw_stayed is None
                    else "Aguardar uma janela com amostra suficiente antes de alterar a abertura."
                ],
            )
        )
        if weak_progression:
            notifications.append(
                _notification(
                    code="SHORTS_PROGRESSION_BOTTLENECK",
                    severity="warning",
                    stage="engagement",
                    title="Progressão perde atenção depois da entrada no Short",
                    evidence=[f"Percentual médio assistido: {average_percentage_viewed:.1f}%."],
                    actions=[
                        "Verificar se cada cena acrescenta informação, mudança ou "
                        "consequência real.",
                        "Entregar o payoff exato da abertura sem adiar valor artificialmente.",
                        "Remover repetição, preâmbulo e cortes decorativos que não avançam "
                        "a promessa.",
                    ],
                )
            )
    elif (
        stayed < policy.appeal_baseline_percent
        and average_percentage_viewed is not None
        and average_percentage_viewed < policy.engagement_weak_below_percent
    ):
        primary = "opening_and_progression"
        notifications.append(
            _notification(
                code="SHORTS_APPEAL_BELOW_BASELINE",
                severity="critical" if appeal_status == "critical" else "warning",
                stage="appeal",
                title="Escolha inicial no feed abaixo da linha de base operacional",
                evidence=[f"Continuaram assistindo: {stayed:.1f}%."],
                actions=[
                    "Trocar abertura abstrata por situação concreta e primeiro quadro com "
                    "sujeito, ação e tensão.",
                    "Manter precisão bíblica, duração, voz e música estáveis para isolar "
                    "o experimento.",
                ],
            )
        )
        notifications.append(
            _notification(
                code="SHORTS_PROGRESSION_BELOW_BASELINE",
                severity="warning",
                stage="engagement",
                title="Progressão perde atenção depois da entrada no Short",
                evidence=[f"Percentual médio assistido: {average_percentage_viewed:.1f}%."],
                actions=[
                    "Garantir informação nova em cada beat e payoff alinhado à promessa, "
                    "sem filler.",
                    "Remover cenas ou frases que não alteram informação, tensão ou consequência.",
                ],
            )
        )
    elif stayed < policy.appeal_baseline_percent and (
        average_percentage_viewed is None
        or average_percentage_viewed >= policy.engagement_weak_below_percent
    ):
        primary = "opening_bottleneck"
        notifications.append(
            _notification(
                code="SHORTS_OPENING_BOTTLENECK",
                severity="critical" if appeal_status == "critical" else "warning",
                stage="appeal",
                title="Escolha inicial no feed abaixo da linha de base operacional",
                evidence=[f"Continuaram assistindo: {stayed:.1f}%."],
                actions=[
                    "Priorizar situação humana específica em vez de imperativo ou emoção abstrata.",
                    "Planejar o primeiro frame como ação já em curso, com um foco dominante "
                    "e conflito legível.",
                    "Fazer imagem e fala abrirem a mesma pergunta que o versículo resolve "
                    "imediatamente.",
                ],
            )
        )
    elif (
        stayed >= policy.appeal_healthy_percent
        and average_percentage_viewed is not None
        and average_percentage_viewed < policy.engagement_weak_below_percent
    ):
        primary = "progression_bottleneck"
        notifications.append(
            _notification(
                code="SHORTS_PROGRESSION_BOTTLENECK",
                severity="warning",
                stage="engagement",
                title="Progressão perde atenção depois da entrada no Short",
                evidence=[f"Percentual médio assistido: {average_percentage_viewed:.1f}%."],
                actions=[
                    "Verificar se cada cena acrescenta informação, mudança ou consequência real.",
                    "Entregar o payoff exato da abertura sem adiar valor artificialmente.",
                    "Remover repetição, preâmbulo e cortes decorativos que não avançam a promessa.",
                ],
            )
        )
    elif (
        stayed >= policy.appeal_reference_percent
        and average_percentage_viewed is not None
        and average_percentage_viewed >= policy.engagement_replay_strong_percent
    ):
        primary = "editorial_reference_candidate"
        notifications.append(
            _notification(
                code="SHORTS_EDITORIAL_REFERENCE_CANDIDATE",
                severity="info",
                stage="engagement",
                title="Candidato a referência editorial, ainda sem inferência causal",
                evidence=[
                    f"Continuaram assistindo: {stayed:.1f}%.",
                    f"Percentual médio assistido: {average_percentage_viewed:.1f}%.",
                ],
                actions=[
                    "Registrar hook, primeiro frame, passagem e perfil para testes "
                    "controlados futuros.",
                    "Confirmar o padrão em uma coorte, sem copiar um único outlier.",
                ],
            )
        )
    else:
        primary = "mixed_signals"
        if appeal_status in {"critical", "below_baseline"}:
            notifications.append(
                _notification(
                    code="SHORTS_APPEAL_BELOW_BASELINE",
                    severity="critical" if appeal_status == "critical" else "warning",
                    stage="appeal",
                    title="Apelo no feed abaixo da linha de base operacional",
                    evidence=[f"Continuaram assistindo: {stayed:.1f}%."],
                    actions=[
                        "Testar concretude verbal e conflito visual como variáveis separadas."
                    ],
                )
            )

    if appeal_sample_basis == "views_proxy":
        notifications.append(
            _notification(
                code="SHORTS_FEED_SAMPLE_MISSING",
                severity="info",
                stage="evidence",
                title="Exibido no feed não está disponível para confirmar o apelo",
                evidence=[
                    "Views não foi usado como substituto de exposição no feed neste diagnóstico."
                ],
                actions=[
                    "Importar Exibido no feed quando estiver disponível para qualificar "
                    "a confiança."
                ],
            )
        )

    confidence_note = (
        "Diagnóstico determinístico por apelo, engajamento e satisfação; não atribui causalidade "
        "a um único Short. "
        + (
            "Resultado confirmado pela amostra e maturidade declaradas."
            if evidence_status == "confirmed"
            else "Resultado preliminar; não promover padrão antes de uma coorte comparável."
        )
    )
    return ShortPerformanceDiagnostic(
        snapshot_id=snapshot.snapshot_id,
        video_id=snapshot.video_id,
        observed_at=snapshot.observed_at,
        evidence_end_at=snapshot.window_end or snapshot.observed_at,
        video_duration_seconds=snapshot.video_duration_seconds,
        duration_cohort=_duration_cohort(snapshot.video_duration_seconds),
        duration_profile=snapshot.duration_profile,
        performance_profile=snapshot.performance_profile,
        hook_contract=snapshot.hook_contract,
        visual_standard_id=snapshot.visual_standard_id,
        hook_text=snapshot.hook_text,
        hook_mechanism=snapshot.hook_mechanism,
        opening_visual_standard=snapshot.opening_visual_standard,
        first_frame_pattern=snapshot.first_frame_pattern,
        evidence_status=evidence_status,
        stage_evidence=stage_evidence,
        stage_sample_sizes={
            stage: size
            for stage, size in (
                ("appeal", appeal_sample_size),
                ("engagement", engagement_sample_size),
                ("satisfaction", satisfaction_sample_size),
            )
            if size is not None
        },
        stage_sample_basis={
            "appeal": appeal_sample_basis,
            "engagement": engagement_sample_basis,
            "satisfaction": satisfaction_sample_basis,
        },
        sample_size=appeal_sample_size,
        sample_basis=appeal_sample_basis,
        appeal_status=appeal_status,
        engagement_status=engagement_status,
        satisfaction_status=satisfaction_status,
        primary_diagnosis=primary,
        derived_metrics=derived,
        notifications=notifications,
        confidence_note=confidence_note,
    )


def diagnose_short_snapshots(
    memory: ShortMetricSnapshotMemory,
    *,
    policy: ShortsDiagnosticPolicy = DEFAULT_SHORTS_DIAGNOSTIC_POLICY,
) -> ShortPerformanceDiagnostics:
    diagnostics = [
        diagnose_short_snapshot(snapshot, policy=policy)
        for snapshot in sorted(
            memory.snapshots,
            key=lambda item: (
                item.window_end or item.observed_at,
                item.observed_at,
                item.snapshot_id,
            ),
        )
    ]
    return ShortPerformanceDiagnostics(policy=policy.as_dict(), diagnostics=diagnostics)


def _persisted_short_performance_diagnostics(
    layout: ProjectLayout,
) -> ShortPerformanceDiagnostics | None:
    if not layout.short_performance_diagnostics.is_file():
        return None
    try:
        return read_model(
            layout.short_performance_diagnostics,
            ShortPerformanceDiagnostics,
        )
    except (OSError, TypeError, ValueError):
        return None


def load_short_performance_diagnostics(layout: ProjectLayout) -> ShortPerformanceDiagnostics:
    # Diagnostics are a cheap deterministic projection. Always rebuild from the immutable
    # snapshots so an interruption between the two atomic writes cannot leave stale alerts.
    memory = (
        read_model(layout.short_metric_snapshots, ShortMetricSnapshotMemory)
        if layout.short_metric_snapshots.is_file()
        else ShortMetricSnapshotMemory()
    )
    return diagnose_short_snapshots(memory)


def latest_short_diagnostics(
    diagnostics: ShortPerformanceDiagnostics,
) -> list[ShortPerformanceDiagnostic]:
    """Return the newest observation per video; historical alerts are not kept active."""

    latest: dict[str, ShortPerformanceDiagnostic] = {}
    for diagnostic in diagnostics.diagnostics:
        current = latest.get(diagnostic.video_id)
        if current is None or (
            diagnostic.evidence_end_at,
            diagnostic.observed_at,
            diagnostic.snapshot_id,
        ) > (
            current.evidence_end_at,
            current.observed_at,
            current.snapshot_id,
        ):
            latest[diagnostic.video_id] = diagnostic
    return sorted(
        latest.values(),
        key=lambda item: (item.evidence_end_at, item.observed_at, item.snapshot_id),
        reverse=True,
    )


def active_short_notifications(
    diagnostics: ShortPerformanceDiagnostics,
) -> list[tuple[ShortPerformanceDiagnostic, ShortPerformanceNotification]]:
    """Resolve alerts only with newer confirmed evidence for the same metric stage."""

    newest_by_stage: dict[tuple[str, str], ShortPerformanceDiagnostic] = {}
    for diagnostic in diagnostics.diagnostics:
        available_stages: list[str] = []
        if diagnostic.appeal_status != "unknown":
            available_stages.append("appeal")
        if diagnostic.engagement_status != "unknown":
            available_stages.append("engagement")
        if diagnostic.satisfaction_status != "unknown":
            available_stages.append("satisfaction")
        for stage in available_stages:
            if diagnostic.stage_evidence.get(stage, diagnostic.evidence_status) != "confirmed":
                continue
            key = (diagnostic.video_id, stage)
            current = newest_by_stage.get(key)
            if current is None or (
                diagnostic.evidence_end_at,
                diagnostic.observed_at,
                diagnostic.snapshot_id,
            ) > (
                current.evidence_end_at,
                current.observed_at,
                current.snapshot_id,
            ):
                newest_by_stage[key] = diagnostic

    active: list[tuple[ShortPerformanceDiagnostic, ShortPerformanceNotification]] = []
    for (_video_id, stage), diagnostic in newest_by_stage.items():
        active.extend(
            (diagnostic, notification)
            for notification in diagnostic.notifications
            if notification.stage == stage and notification.severity in {"warning", "critical"}
        )
    return sorted(
        active,
        key=lambda item: (
            item[0].evidence_end_at,
            item[0].observed_at,
            item[0].snapshot_id,
            item[1].stage,
            item[1].code,
        ),
        reverse=True,
    )


def _notification_signatures(diagnostics: ShortPerformanceDiagnostics) -> set[tuple[str, str, str]]:
    return {
        (diagnostic.snapshot_id, notification.code, notification.severity)
        for diagnostic, notification in active_short_notifications(diagnostics)
    }


def _enrich_short_snapshot(
    layout: ProjectLayout,
    snapshot: ShortMetricSnapshot,
) -> ShortMetricSnapshot:
    """Freeze comparable project attributes into the observation at import time."""

    publication_paths = [layout.publication]
    if layout.publication_archive.is_dir():
        publication_paths.extend(sorted(layout.publication_archive.glob("*.json")))
    context = None
    for path in publication_paths:
        if not path.is_file():
            continue
        try:
            publication = read_model(path, YouTubePublicationRecord)
        except (OSError, TypeError, ValueError):
            continue
        if publication.video_id == snapshot.video_id:
            context = publication.short_analytics_context
            break
    if context is None:
        return snapshot
    authoritative = {
        "video_duration_seconds": context.video_duration_seconds,
        "duration_profile": context.duration_profile,
        "performance_profile": context.performance_profile,
        "visual_standard_id": context.visual_standard_id,
        "opening_visual_standard": context.opening_visual_standard,
        "first_frame_pattern": context.first_frame_pattern,
        "hook_contract": context.hook_contract,
        "hook_text": context.hook_text,
        "hook_mechanism": context.hook_mechanism,
    }
    updates: dict[str, str | float] = {}
    for field_name, frozen_value in authoritative.items():
        if not frozen_value:
            continue
        supplied_value = getattr(snapshot, field_name)
        if supplied_value is not None:
            if field_name == "video_duration_seconds":
                conflicts = abs(float(supplied_value) - float(frozen_value)) > 0.5
            else:
                conflicts = supplied_value != frozen_value
            if conflicts:
                raise ValueError(
                    f"{field_name} diverge do fingerprint congelado no upload do vídeo."
                )
        updates[field_name] = frozen_value
    return snapshot.model_copy(update=updates) if updates else snapshot


def import_short_metric_file(
    layout: ProjectLayout,
    source: Path,
) -> tuple[ShortMetricSnapshotMemory, ShortPerformanceDiagnostics]:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo de snapshots não encontrado: {source}")
    if layout.load_manifest().video_format != "short":
        raise ValueError("Snapshots de Shorts só podem ser importados em um projeto Short.")
    raw = read_json(source)
    if isinstance(raw, dict) and "snapshots" in raw:
        incoming = ShortMetricSnapshotMemory.model_validate(raw).snapshots
    elif isinstance(raw, list):
        incoming = [ShortMetricSnapshot.model_validate(item) for item in raw]
    else:
        incoming = [ShortMetricSnapshot.model_validate(raw)]
    if not incoming:
        raise ValueError("O arquivo não contém snapshots.")
    memory = (
        read_model(layout.short_metric_snapshots, ShortMetricSnapshotMemory)
        if layout.short_metric_snapshots.is_file()
        else ShortMetricSnapshotMemory()
    )
    existing_by_id = {snapshot.snapshot_id: snapshot for snapshot in memory.snapshots}
    observation_ids = {
        (snapshot.video_id, snapshot.observed_at): snapshot.snapshot_id
        for snapshot in memory.snapshots
    }
    inserted: list[ShortMetricSnapshot] = []
    for snapshot in incoming:
        enriched = _enrich_short_snapshot(layout, snapshot)
        previous = existing_by_id.get(snapshot.snapshot_id)
        if previous is not None:
            supplied_fields = {
                field_name
                for field_name in snapshot.model_fields_set
                if not (
                    field_name
                    in {
                        "duration_profile",
                        "performance_profile",
                        "visual_standard_id",
                        "opening_visual_standard",
                        "first_frame_pattern",
                        "hook_contract",
                        "hook_text",
                        "hook_mechanism",
                    }
                    and getattr(snapshot, field_name) is None
                )
            }
            if any(
                getattr(previous, field_name) != getattr(enriched, field_name)
                for field_name in supplied_fields
            ):
                raise ValueError(
                    f"snapshot_id={snapshot.snapshot_id} é imutável; use um novo id para "
                    "outra observação."
                )
            continue
        observation_key = (snapshot.video_id, snapshot.observed_at)
        other_snapshot_id = observation_ids.get(observation_key)
        if other_snapshot_id is not None:
            raise ValueError(
                "Já existe outra observação para o mesmo video_id e observed_at: "
                f"{other_snapshot_id}. Una as fontes em um snapshot source=combined."
            )
        existing_by_id[snapshot.snapshot_id] = enriched
        observation_ids[observation_key] = snapshot.snapshot_id
        inserted.append(enriched)
    persisted_diagnostics = _persisted_short_performance_diagnostics(layout)
    previous_diagnostics = load_short_performance_diagnostics(layout)
    if inserted:
        memory.snapshots = sorted(
            existing_by_id.values(),
            key=lambda item: (item.observed_at, item.snapshot_id),
        )
        write_json(layout.short_metric_snapshots, memory)
        layout.append_event(
            {
                "event": "analytics_short_snapshots_imported",
                "source": str(source),
                "snapshot_ids": [snapshot.snapshot_id for snapshot in inserted],
                "imported_at": utc_now(),
            }
        )
    diagnostics = diagnose_short_snapshots(memory)
    if persisted_diagnostics is None or diagnostics.model_dump(
        mode="json"
    ) != persisted_diagnostics.model_dump(mode="json"):
        write_json(layout.short_performance_diagnostics, diagnostics)
    previous_alerts = _notification_signatures(previous_diagnostics)
    current_alerts = _notification_signatures(diagnostics)
    if inserted and previous_alerts != current_alerts:
        layout.append_event(
            {
                "event": "analytics_short_alert_state_changed",
                "active_alerts": [
                    {"snapshot_id": item[0], "code": item[1], "severity": item[2]}
                    for item in sorted(current_alerts)
                ],
            }
        )
    return memory, diagnostics


def _projects_root(layout: ProjectLayout) -> Path:
    parent = layout.root.parent
    return parent.parent if parent.name in PROJECT_CATEGORY_DIRS else parent


def compact_short_performance_advisories(
    layout: ProjectLayout,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return only confirmed, comparable actions for the next Short planning queue."""

    target = layout.load_manifest()
    if target.video_format != "short":
        return []
    target_profile = target.config.shorts_script.duration_profile
    target_performance_profile = target.config.shorts_script.performance_profile
    target_duration_cohort = _duration_cohort(float(target.target_duration_seconds))
    target_visual = target.config.images.visual_standard_id
    target_opening = target.config.images.opening_visual_standard
    target_briefing = read_json(layout.briefing) if layout.briefing.is_file() else {}
    target_hook_contract = (
        target_briefing.get("hook_contract") if isinstance(target_briefing, dict) else None
    )
    root = _projects_root(layout)
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for category in PROJECT_CATEGORY_DIRS:
        for project_root in sorted((root / category).glob("*")):
            snapshots_path = project_root / "analytics" / "shorts-snapshots.json"
            if not snapshots_path.is_file() or not (project_root / "project.json").is_file():
                continue
            try:
                project_layout = ProjectLayout(project_root)
                diagnostics = load_short_performance_diagnostics(project_layout)
            except (OSError, TypeError, ValueError):
                continue
            active = active_short_notifications(diagnostics)
            grouped: dict[tuple[str, str], list[ShortPerformanceNotification]] = {}
            diagnostic_by_key: dict[tuple[str, str], ShortPerformanceDiagnostic] = {}
            for diagnostic, notification in active:
                key = (diagnostic.video_id, diagnostic.snapshot_id)
                grouped.setdefault(key, []).append(notification)
                diagnostic_by_key[key] = diagnostic
            for key, actionable in grouped.items():
                diagnostic = diagnostic_by_key[key]
                if diagnostic.duration_profile != target_profile:
                    continue
                if diagnostic.performance_profile != target_performance_profile:
                    continue
                if diagnostic.duration_cohort != target_duration_cohort:
                    continue
                if diagnostic.visual_standard_id != target_visual:
                    continue
                if diagnostic.opening_visual_standard != target_opening:
                    continue
                if diagnostic.hook_contract != target_hook_contract:
                    continue
                active_stages = {item.stage for item in actionable}
                active_diagnosis = (
                    "opening_and_progression"
                    if active_stages == {"appeal", "engagement"}
                    else "opening_bottleneck"
                    if active_stages == {"appeal"}
                    else "progression_bottleneck"
                    if active_stages == {"engagement"}
                    else diagnostic.primary_diagnosis
                )
                candidates.append(
                    (
                        (
                            diagnostic.evidence_end_at,
                            diagnostic.observed_at,
                            diagnostic.snapshot_id,
                        ),
                        {
                            "source_project": relative_posix(project_root, root),
                            "snapshot_id": diagnostic.snapshot_id,
                            "primary_diagnosis": active_diagnosis,
                            "evidence": [line for item in actionable for line in item.evidence],
                            "recommended_actions": list(
                                dict.fromkeys(
                                    action
                                    for item in actionable
                                    for action in item.recommended_actions
                                )
                            ),
                            "confidence_note": diagnostic.confidence_note,
                        },
                    )
                )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [payload for _evidence_order, payload in candidates[:limit]]


def compact_metric_learning(layout: ProjectLayout, *, limit: int = 6) -> list[dict[str, Any]]:
    """Return bounded, cross-project evidence; never turn sparse metrics into causality."""
    target_format = layout.load_manifest().video_format
    root = _projects_root(layout)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    experiment_paths = [
        project / "analytics" / "experiments.json"
        for category in PROJECT_CATEGORY_DIRS
        for project in sorted((root / category).glob("*"))
        if project.is_dir()
    ]
    if layout.experiments not in experiment_paths:
        experiment_paths.insert(0, layout.experiments)
    for path in experiment_paths:
        if not path.is_file() or not (path.parents[1] / "project.json").is_file():
            continue
        try:
            project_layout = ProjectLayout(path.parents[1])
            project = project_layout.load_manifest()
            memory = read_model(path, ExperimentMemory)
        except (OSError, TypeError, ValueError):
            continue
        for experiment in memory.experiments:
            if experiment.experiment_id in seen or not experiment.metrics:
                continue
            seen.add(experiment.experiment_id)
            candidates.append(
                {
                    "experiment_id": experiment.experiment_id,
                    "video_format": project.video_format,
                    "hypothesis": experiment.hypothesis,
                    "change": experiment.change,
                    "metrics": experiment.metrics,
                    "conclusion": experiment.conclusion,
                    "confidence_note": experiment.confidence_note,
                    "source_project": relative_posix(project_layout.root, root),
                }
            )
    candidates.sort(
        key=lambda item: (
            item["video_format"] == target_format,
            bool(item["conclusion"]),
            len(item["metrics"]),
            item["experiment_id"],
        ),
        reverse=True,
    )
    return candidates[:limit]
