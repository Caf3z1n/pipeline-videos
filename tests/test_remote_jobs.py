from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.remote_jobs import (
    CodexPhaseSpec,
    RemoteCodexRun,
    RemoteJobStore,
    RemoteVideoRequest,
    _codex_phase_command,
    _editorial_sources_phase_spec,
    _failure_with_codex_context,
    _image_phase_specs,
    _import_editorial_source_selection,
    _normalize_shorts_script_payload,
    _shorts_script_repair_phase_spec,
    codex_phase_prompt,
    codex_usage_summary,
    job_is_due,
    prepare_job_project,
    work_once,
)
from pipeline_videos.storage import write_json


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
    prompt = codex_phase_prompt(Path("/tmp/phase-request.json"))
    assert "uma fase isolada" in prompt
    assert "Não leia README, AGENTS.md" in prompt
    assert "não crie" in prompt.casefold()


def test_codex_phase_command_is_ephemeral_compact_and_hooked(tmp_path) -> None:
    spec = CodexPhaseSpec(
        phase="image",
        item_id="scene-001-v001",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        payload={},
        expected_outputs=(tmp_path / "scene.png",),
    )

    command = _codex_phase_command(
        codex_binary="codex",
        spec=spec,
        context_dir=tmp_path / "context",
        output_message=tmp_path / "last-message.txt",
        hook_script=tmp_path / "remote_codex_hook.py",
    )

    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "multi_agent" in command
    assert 'web_search="disabled"' in command
    assert 'model_reasoning_effort="low"' in command
    assert any("hooks.PostToolUse" in value for value in command)
    assert any("hooks.PreCompact" in value for value in command)
    assert any("hooks.Stop" in value for value in command)


def test_shorts_hook_type_confusion_is_normalized_without_another_codex_run() -> None:
    payload = {
        "strategy": {
            "hook_type": "emotional_mirroring",
            "hook_techniques": ["self_relevance", "emotional_mirroring", "specificity"],
        }
    }

    normalized, change = _normalize_shorts_script_payload(payload)

    assert normalized["strategy"]["hook_type"] == "direct_tension"
    assert normalized["strategy"]["hook_techniques"] == [
        "self_relevance",
        "emotional_mirroring",
        "specificity",
    ]
    assert change == "strategy.hook_type: emotional_mirroring -> direct_tension"


def test_shorts_narrative_spine_is_aligned_to_mapped_sections_without_codex() -> None:
    payload = {
        "strategy": {
            "hook_type": "direct_tension",
            "hook_techniques": ["self_relevance"],
            "open_loop": "Resumo que não aparece no roteiro",
            "verse_opening": {"source_id": "bpm-salmos-34-18"},
            "narrative_spine": {
                "tension_section_id": "s1",
                "bridge_section_id": "s3",
                "meaning_section_id": "s4",
                "application_section_id": "s5",
                "tension_question": "Outra formulação",
                "bridge_statement": "Outro resumo",
                "central_truth": "Outra verdade",
                "practical_response": "Outra aplicação",
            },
        },
        "script": {
            "sections": [
                {
                    "section_id": "s1",
                    "narration": "Você sorri, mas está cansado de fingir.",
                    "biblical_basis": None,
                    "claim_classification": [],
                },
                {"section_id": "s3", "narration": "A Bíblia não manda esconder a dor."},
                {"section_id": "s4", "narration": "Isso não promete alívio imediato."},
                {"section_id": "s5", "narration": "Converse hoje com uma pessoa segura."},
            ]
        },
    }

    normalized, change = _normalize_shorts_script_payload(payload)
    spine = normalized["strategy"]["narrative_spine"]

    assert spine["tension_question"] == "Você sorri, mas está cansado de fingir."
    assert spine["bridge_statement"] == "A Bíblia não manda esconder a dor."
    assert spine["central_truth"] == "Isso não promete alívio imediato."
    assert spine["practical_response"] == "Converse hoje com uma pessoa segura."
    assert normalized["strategy"]["open_loop"] == spine["tension_question"]
    assert normalized["strategy"]["section_ids"] == ["s1", "s3", "s4", "s5"]
    assert normalized["script"]["sections"][0]["biblical_basis"] == ["bpm-salmos-34-18"]
    assert normalized["script"]["sections"][0]["claim_classification"] == ["interpretation"]
    assert "strategy.narrative_spine.tension_question" in change


def test_shorts_repair_is_single_compact_terra_phase(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Esperança"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    write_json(
        layout.shorts_script_queue,
        {
            "job_id": "shorts-script-test",
            "orchestration": {},
            "outputs": {
                "package": "script/staging/shorts-script-specialist/package.json",
            },
        },
    )

    spec = _shorts_script_repair_phase_spec(
        layout,
        ValueError("strategy.hook_type inválido"),
    )

    assert spec.phase == "shorts_script_repair"
    assert spec.model == "gpt-5.6-terra"
    assert spec.reasoning_effort == "low"
    assert spec.payload["allowed_inputs"] == [
        str(layout.shorts_script_queue),
        str(layout.shorts_script_package_staging),
    ]
    assert "strategy.hook_type inválido" in spec.payload["task"]["validation_error"]


def test_image_phases_receive_one_item_and_previous_staging_only(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Coragem"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    write_json(
        layout.image_queue,
        {
            "orchestration": {
                "recommended_model": "gpt-5.6-terra",
                "reasoning_effort": "low",
            },
            "visual_standard": {"id": "cinematic-family-3d-v1"},
            "generation_retry_policy": {"maximum_attempts_per_item": 3},
            "items": [
                {
                    "job_id": "scene-001-v001",
                    "status": "waiting_external",
                    "prompt": "PROMPT EXCLUSIVO UM",
                    "staging_path": "images/staging/scene-001/v001.png",
                },
                {
                    "job_id": "scene-002-v001",
                    "status": "waiting_external",
                    "prompt": "PROMPT EXCLUSIVO DOIS",
                    "staging_path": "images/staging/scene-002/v001.png",
                },
            ],
        },
    )

    specs = _image_phase_specs(layout)

    assert len(specs) == 2
    assert specs[0].payload["task"]["item"]["prompt"] == "PROMPT EXCLUSIVO UM"
    assert "PROMPT EXCLUSIVO DOIS" not in json.dumps(specs[0].payload, ensure_ascii=False)
    assert specs[0].payload["allowed_inputs"] == []
    assert specs[1].payload["allowed_inputs"] == [str(specs[0].expected_outputs[0])]


def test_specific_topic_uses_compact_catalog_selection_phase(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="coragem para conversas difíceis"))
    projects_root = tmp_path / "projects"
    layout = prepare_job_project(job, projects_root=projects_root)

    spec = _editorial_sources_phase_spec(
        job,
        layout,
        projects_root=projects_root,
    )

    assert spec.phase == "editorial_sources"
    assert spec.model == "gpt-5.6-terra"
    assert spec.reasoning_effort == "low"
    assert spec.payload["allowed_inputs"] == []
    assert spec.payload["task"]["desired_candidate_count"] == 1
    assert spec.payload["task"]["candidates"]


def test_long_job_materializes_enough_verified_sources_without_human_approval(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(video_format="long", duration_seconds=540))
    projects_root = tmp_path / "projects"
    layout = prepare_job_project(job, projects_root=projects_root)
    spec = _editorial_sources_phase_spec(
        job,
        layout,
        projects_root=projects_root,
    )
    task = spec.payload["task"]
    main_id = task["required_main_candidate_id"]
    supporting = [
        item["candidate_id"] for item in task["candidates"] if item["candidate_id"] != main_id
    ][: task["desired_candidate_count"] - 1]
    write_json(
        spec.expected_outputs[0],
        {
            "main_candidate_id": main_id,
            "supporting_candidate_ids": supporting,
            "rationale": "Passagens complementares verificadas para o tema.",
        },
    )

    _import_editorial_source_selection(
        job,
        layout,
        spec.expected_outputs[0],
        projects_root=projects_root,
    )

    briefing = json.loads(layout.briefing.read_text(encoding="utf-8"))
    sources = json.loads(layout.sources.read_text(encoding="utf-8"))["entries"]
    assert len([source for source in sources if source["verified"]]) == 7
    assert len(briefing["complementary_references"]) == 6
    assert all(
        record.status.value == "pending" for record in layout.load_manifest().approvals.values()
    )


def test_codex_usage_summary_separates_cached_and_fresh_tokens(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request())
    run = {
        "phase": "image",
        "item_id": "scene-001-v001",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "started_at": "2026-07-16T00:00:00Z",
        "finished_at": "2026-07-16T00:01:00Z",
        "exit_code": 0,
        "input_tokens": 1000,
        "cached_input_tokens": 800,
        "output_tokens": 100,
        "reasoning_output_tokens": 20,
    }
    job = job.model_copy(update={"codex_runs": [RemoteCodexRun.model_validate(run)]})

    usage = codex_usage_summary(job)

    assert usage["fresh_input_tokens"] == 200
    assert usage["total_tokens"] == 1100
    assert usage["fresh_plus_output_tokens"] == 300


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
