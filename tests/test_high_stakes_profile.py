from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.creative_direction import hook_matches_family
from pipeline_videos.models import Briefing, SourceLibrary, VerseCatalog
from pipeline_videos.shorts_script import prepare_shorts_script_queue
from pipeline_videos.storage import REPO_ROOT, read_model
from pipeline_videos.verse_selection import (
    HighStakesQuestionCatalogExhaustedError,
    create_auto_short,
)

QUESTION_BANK = REPO_ROOT / "config" / "bible" / "high-stakes-question-bank-v1.json"


def test_high_stakes_question_bank_is_verified_and_complete() -> None:
    catalog = VerseCatalog.model_validate(json.loads(QUESTION_BANK.read_text(encoding="utf-8")))

    assert catalog.catalog_id == "high-stakes-biblical-question-bank-v1"
    assert len(catalog.candidates) >= 8
    for candidate in catalog.candidates:
        assert candidate.editorial_profile == "high_stakes_biblical_question_v1"
        assert candidate.catalog_segment == "high_stakes_question"
        assert candidate.opening_hooks
        assert candidate.required_answer_points
        assert len(candidate.related_passages) >= 2
        assert len(candidate.verse_text.split()) <= 40
        assert all(
            hook_matches_family(hook, family) for family, hook in candidate.opening_hooks.items()
        )


def test_high_stakes_short_is_question_first_with_adaptive_scripture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "local")

    layout, candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        target_duration_seconds=50,
        project_date=date(2026, 7, 21),
        editorial_profile="high_stakes_biblical_question_v1",
    )

    manifest = layout.load_manifest()
    briefing = read_model(layout.briefing, Briefing)
    sources = read_model(layout.sources, SourceLibrary)
    assert manifest.config.shorts_script.editorial_profile == ("high_stakes_biblical_question_v1")
    assert manifest.config.shorts_script.scripture_citation_timing_policy == "adaptive_payoff_v1"
    assert manifest.config.shorts_script.scripture_citation_min_start_seconds == 0
    assert manifest.config.shorts_script.scripture_citation_max_end_seconds == 44
    assert manifest.config.shorts_script.require_independent_biblical_review is True
    assert briefing.question_id == candidate.candidate_id
    assert briefing.central_question == candidate.central_question
    assert briefing.hook_contract == "high_stakes_opening_v1"
    assert briefing.opening_hook == candidate.opening_hooks[briefing.hook_family]
    assert briefing.opening_hook.endswith(".")
    assert not briefing.opening_hook.endswith("?")
    assert briefing.primary_literal_source_id == candidate.source_id
    assert briefing.required_answer_points == candidate.required_answer_points
    assert len(sources.entries) >= 3

    queue = prepare_shorts_script_queue(layout)
    assert queue["scripture_candidates"] == [candidate.source_id]
    assert queue["editorial_profile_contract"]["question_id"] == candidate.candidate_id
    assert queue["editorial_profile_contract"]["scripture_timing_policy"] == ("adaptive_payoff_v1")
    assert queue["retention_contract"]["timeline_seconds"]["scripture_adaptive"] == [0, 44]
    assert queue["preflight"]["independent_biblical_review_required"] is True
    assert any("Não existe posição fixa de 8–20" in item for item in queue["instructions"])


def test_high_stakes_profile_rejects_compact_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "local")

    with pytest.raises(ValueError, match="45 e 60"):
        create_auto_short(
            projects_dir=tmp_path,
            target_duration_seconds=30,
            editorial_profile="high_stakes_biblical_question_v1",
        )


def test_high_stakes_catalog_blocks_instead_of_starting_a_second_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "local")
    selected_ids = set()

    for day in range(1, 9):
        _, candidate, _ = create_auto_short(
            projects_dir=tmp_path,
            target_duration_seconds=50,
            project_date=date(2026, 8, day),
            editorial_profile="high_stakes_biblical_question_v1",
        )
        selected_ids.add(candidate.candidate_id)

    assert len(selected_ids) == 8
    before = sorted(path.name for path in (tmp_path / "shorts").iterdir())
    with pytest.raises(
        HighStakesQuestionCatalogExhaustedError,
        match="Nenhum novo Short foi criado",
    ):
        create_auto_short(
            projects_dir=tmp_path,
            target_duration_seconds=50,
            project_date=date(2026, 8, 9),
            editorial_profile="high_stakes_biblical_question_v1",
        )
    after = sorted(path.name for path in (tmp_path / "shorts").iterdir())

    assert after == before

    result = CliRunner().invoke(
        app,
        [
            "new-short",
            "--profile",
            "high-stakes",
            "--duration",
            "50",
            "--projects-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    normalized_output = " ".join(result.output.split())
    assert "Nenhum novo Short foi criado" in normalized_output
