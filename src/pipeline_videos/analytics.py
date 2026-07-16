from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pipeline_videos.models import Experiment, ExperimentMemory
from pipeline_videos.storage import (
    PROJECT_CATEGORY_DIRS,
    ProjectLayout,
    read_json,
    read_model,
    write_json,
)
from pipeline_videos.utils import relative_posix, utc_now

PERCENT_METRICS = {
    "ctr_percent",
    "average_percentage_viewed",
    "retention_30s_percent",
    "retention_60s_percent",
}
NONNEGATIVE_METRICS = {
    "impressions",
    "views",
    "watch_time_hours",
    "average_view_duration_seconds",
    "subscribers_gained",
}


def _validate_metrics(experiment: Experiment) -> None:
    for name, value in experiment.metrics.items():
        if value is None or isinstance(value, str):
            continue
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"Métrica inválida em {experiment.experiment_id}: {name}.")
        numeric = float(value)
        if name in PERCENT_METRICS and not 0 <= numeric <= 100:
            raise ValueError(f"{name} precisa estar entre 0 e 100.")
        if name in NONNEGATIVE_METRICS and numeric < 0:
            raise ValueError(f"{name} não pode ser negativa.")


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


def _projects_root(layout: ProjectLayout) -> Path:
    parent = layout.root.parent
    return parent.parent if parent.name in PROJECT_CATEGORY_DIRS else parent


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
