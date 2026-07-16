from datetime import date

import pytest

from pipeline_videos.analytics import compact_metric_learning, import_experiment_file
from pipeline_videos.models import Experiment
from pipeline_videos.project import create_project
from pipeline_videos.storage import write_json


def _project(tmp_path, slug: str, *, video_format: str):
    return create_project(
        slug,
        slug.replace("-", " ").title(),
        projects_dir=tmp_path,
        project_date=date(2026, 7, 16),
        video_format=video_format,
        target_duration_seconds=60 if video_format == "short" else 420,
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
