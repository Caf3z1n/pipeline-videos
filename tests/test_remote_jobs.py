from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.models import (
    DEFAULT_ELEVENLABS_MODEL_ID,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_VOICE_VARIANTS,
)
from pipeline_videos.remote_jobs import (
    CodexPhaseSpec,
    RemoteCodexRun,
    RemoteJobStore,
    RemoteVideoRequest,
    _automatic_recovery_delay_seconds,
    _biblical_review_phase_spec,
    _codex_agent_command,
    _editorial_sources_phase_spec,
    _execute_agent_checkpoint,
    _failure_with_codex_context,
    _growth_correction_checkpoint,
    _growth_correction_is_available,
    _growth_phase_spec,
    _image_phase_specs,
    _import_editorial_source_selection,
    _long_script_phase_spec,
    _normalize_shorts_script_payload,
    _recover_generated_image_outputs,
    _shorts_script_correction_checkpoint,
    _shorts_script_correction_is_available,
    _shorts_script_phase_spec,
    codex_checkpoint_prompt,
    codex_usage_summary,
    job_is_due,
    job_summary,
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
    assert request.creative_mode == "auto"
    assert request.hook_policy == "auto"
    assert request.hook_family == "auto"
    assert request.voice_variant == "jesus_1"

    store = RemoteJobStore(tmp_path / "jobs")
    with pytest.raises(ValueError, match="autorizar explicitamente"):
        store.enqueue(RemoteVideoRequest(video_format="short"))

    with pytest.raises(ValueError, match="Shorts exigem hook inicial"):
        _request(hook_policy="none")

    with pytest.raises(ValueError, match="seis famílias oficiais"):
        _request(hook_family="consequence_first")

    assert _request(duration_seconds=15).duration_seconds == 15
    with pytest.raises(ValueError, match="Vídeos longos precisam"):
        _request(video_format="long", duration_seconds=15)


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


def test_overnight_job_recovery_can_resume_after_window(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request(start_policy="overnight"))
    retry_at = datetime(2026, 7, 16, 8, 10, tzinfo=UTC)
    recovering = queued.model_copy(
        update={
            "attempts": 1,
            "automatic_recovery_count": 1,
            "next_retry_at": retry_at,
        }
    )

    assert job_is_due(recovering, retry_at - timedelta(seconds=1)) is False
    assert job_is_due(recovering, retry_at) is True


def test_prepare_job_project_enables_local_specialists_without_human_approval(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Coragem em Josué", title="Coragem em Josué"))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.config.production.mode == "final_candidate_one_shot"
    assert manifest.config.production.use_subagents is True
    assert manifest.config.production.image_workers == 3
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.shorts_script.use_specialist_subagent is True
    assert manifest.config.shorts_script.narrative_standard == "creative_retention_v3"
    assert manifest.config.youtube_growth.use_specialist_subagent is True
    assert manifest.config.youtube_growth.title_standard == "creative_pipe_passage_v3"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.youtube_growth.thumbnail_variants == 1
    assert manifest.config.audio.voice_id == DEFAULT_ELEVENLABS_VOICE_ID
    assert manifest.config.audio.model_id == DEFAULT_ELEVENLABS_MODEL_ID
    assert manifest.config.youtube_publication.allow_automated_private_review_upload is True
    assert all(record.status.value == "pending" for record in manifest.approvals.values())
    prompt = codex_checkpoint_prompt(Path("/tmp/checkpoint.json"))
    assert "mesma sessão" in prompt
    assert "AGENTS.md" in prompt
    assert "crie exatamente os subagentes" in prompt.casefold()


def test_prepare_remote_micro_short_preserves_one_image_worker_profile(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Confiança", duration_seconds=15))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.target_duration_seconds == 15
    assert manifest.config.shorts_script.duration_profile == "adaptive_compact_v1"
    assert manifest.config.production.image_workers == 1


def test_remote_job_can_select_second_jesus_voice(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(voice_variant="jesus_2"))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.config.audio.voice_id == ELEVENLABS_VOICE_VARIANTS["jesus_2"]
    decisions = layout.decisions.read_text(encoding="utf-8")
    assert '"voice_variant": "jesus_2"' in decisions


def test_long_script_phase_declares_one_functional_pacing_contract(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(video_format="long", duration_seconds=540))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")

    spec = _long_script_phase_spec(layout)
    task = spec.payload["task"]

    assert task["creative_contract"]["narrative_standard"] == "creative_retention_long_v2"
    assert task["creative_contract"]["content_promise_source"] == "briefing.central_theme"
    assert task["creative_contract"]["resolution_source"] == "writer_synthesis_supported_by_sources"
    assert task["creative_contract"]["pacing_profile"] == "long_creative_v2"
    assert task["creative_contract"]["required_reengagements"] == 4
    assert task["creative_contract"]["maximum_reengagement_gap_seconds"] == 120.0
    assert task["scripture_cadence"] == {
        "first_citation_max_seconds": 75.0,
        "minimum_gap_seconds": 25.0,
        "maximum_gap_seconds": 105.0,
        "final_citation_max_distance_seconds": 120.0,
    }
    assert any("long_form_strategy" in item for item in task["instructions"])
    assert any("cada seção acrescenta" in item.casefold() for item in task["instructions"])


def test_codex_agent_command_starts_persistent_sol_medium_session(tmp_path) -> None:
    spec = CodexPhaseSpec(
        phase="image",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(tmp_path / "scene.png",),
    )

    command = _codex_agent_command(
        codex_binary="codex",
        spec=spec,
        repo_root=tmp_path / "repo",
        output_message=tmp_path / "last-message.txt",
        hook_script=tmp_path / "remote_codex_hook.py",
        session_id=None,
    )

    assert "--ephemeral" not in command
    assert "resume" not in command
    assert command[command.index("--cd") + 1] == str(tmp_path / "repo")
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "multi_agent" in command
    assert 'web_search="disabled"' in command
    assert 'model_reasoning_effort="medium"' in command
    assert any("hooks.PostToolUse" in value for value in command)
    assert any("hooks.PreCompact" in value for value in command)
    assert any("hooks.Stop" in value for value in command)


def test_remote_checkpoint_rejects_model_or_reasoning_override(tmp_path) -> None:
    with pytest.raises(ValueError, match="gpt-5.6-sol"):
        CodexPhaseSpec(
            phase="image",
            item_id=None,
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            payload={},
            expected_outputs=(tmp_path / "scene.png",),
        )
    with pytest.raises(ValueError, match="raciocínio medium"):
        CodexPhaseSpec(
            phase="image",
            item_id=None,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            payload={},
            expected_outputs=(tmp_path / "scene.png",),
        )


def test_codex_agent_command_resumes_the_job_session_without_ephemeral_mode(tmp_path) -> None:
    session_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    spec = CodexPhaseSpec(
        phase="youtube_growth",
        item_id=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(tmp_path / "package.json",),
    )

    command = _codex_agent_command(
        codex_binary="codex",
        spec=spec,
        repo_root=tmp_path / "repo",
        output_message=tmp_path / "last-message.txt",
        hook_script=tmp_path / "remote_codex_hook.py",
        session_id=session_id,
    )

    assert command[1:3] == ["exec", "resume"]
    assert session_id in command
    assert "--cd" not in command
    assert "--ephemeral" not in command


def test_agent_checkpoints_persist_and_resume_the_same_session(tmp_path, monkeypatch) -> None:
    session_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    repo_root = tmp_path / "repo"
    hook = repo_root / "scripts" / "remote_codex_hook.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("", encoding="utf-8")
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request())
    outputs = [tmp_path / "first.json", tmp_path / "second.json"]
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(json.dumps({"type": "thread.started", "thread_id": session_id}))
        kwargs["stdout"].write("\n")
        outputs[len(calls) - 1].write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("pipeline_videos.remote_jobs.shutil.which", lambda _: "codex")
    monkeypatch.setattr("pipeline_videos.remote_jobs.subprocess.run", fake_run)
    first = CodexPhaseSpec(
        phase="editorial_sources",
        item_id=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(outputs[0],),
    )
    second = CodexPhaseSpec(
        phase="shorts_script",
        item_id=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(outputs[1],),
    )

    job = _execute_agent_checkpoint(job, first, store, repo_root=repo_root, timeout_seconds=30)
    job = _execute_agent_checkpoint(job, second, store, repo_root=repo_root, timeout_seconds=30)

    assert job.codex_session_id == session_id
    assert len(job.codex_runs) == 2
    assert job_summary(job)["codex_session_id"] == session_id
    assert calls[0][1] == "exec"
    assert "resume" not in calls[0]
    assert calls[1][1:3] == ["exec", "resume"]
    assert session_id in calls[1]


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


def test_retention_story_fields_are_normalized_without_another_codex_run() -> None:
    payload = {
        "strategy": {
            "content_promise": "promessa antiga",
            "open_loop": "pergunta antiga",
            "payoff": "payoff antigo",
            "practical_value": "aplicação antiga",
            "cta": "cta antigo",
            "section_ids": [],
            "verse_opening": {"source_id": "bpm-salmos-56-3"},
            "retention_story": {
                "opening_promise": "Davi ouviu passos e congelou.",
                "central_question": "Sentir medo significava perder a fé?",
                "payoff": "Coragem bíblica não esconde o medo.",
                "practical_response": "Faça o próximo passo responsável.",
                "engagement": {"spoken_text": "Inscreva-se para lembrar desse passo."},
            },
        },
        "script": {
            "sections": [
                {
                    "section_id": "s1",
                    "narration": "Davi ouviu passos e congelou.",
                    "biblical_basis": [],
                    "claim_classification": [],
                },
                {"section_id": "s2", "narration": "O perigo era real."},
            ]
        },
    }

    normalized, change = _normalize_shorts_script_payload(payload)
    strategy = normalized["strategy"]

    assert strategy["content_promise"] == "Davi ouviu passos e congelou."
    assert strategy["open_loop"] == "Sentir medo significava perder a fé?"
    assert strategy["payoff"] == "Coragem bíblica não esconde o medo."
    assert strategy["practical_value"] == "Faça o próximo passo responsável."
    assert strategy["cta"] == "Inscreva-se para lembrar desse passo."
    assert strategy["section_ids"] == ["s1", "s2"]
    assert normalized["script"]["sections"][0]["biblical_basis"] == ["bpm-salmos-56-3"]
    assert normalized["script"]["sections"][0]["claim_classification"] == ["interpretation"]
    assert "retention_story.opening_promise" in change


def test_remote_shorts_script_uses_sol_with_medium_reasoning_even_for_legacy_queue(
    tmp_path,
) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Esperança"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    context = layout.root / "script" / "shorts-context.json"
    write_json(context, {"project_id": job.job_id})
    write_json(
        layout.shorts_script_queue,
        {
            "job_id": "shorts-script-test",
            "orchestration": {
                "recommended_model": "gpt-5.6-sol",
                "reasoning_effort": "low",
            },
            "inputs": {"compact_context": "script/shorts-context.json"},
            "outputs": {
                "package": "script/staging/shorts-script-specialist/package.json",
            },
        },
    )

    spec = _shorts_script_phase_spec(layout)

    assert spec.phase == "shorts_script"
    assert spec.model == "gpt-5.6-sol"
    assert spec.reasoning_effort == "medium"


def test_shorts_correction_is_a_checkpoint_in_the_same_sol_session(tmp_path) -> None:
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

    spec = _shorts_script_correction_checkpoint(
        layout,
        ValueError("strategy.hook_type inválido"),
    )

    assert spec.phase == "shorts_script_correction"
    assert spec.model == "gpt-5.6-sol"
    assert spec.reasoning_effort == "medium"
    assert spec.payload["allowed_inputs"] == [
        str(layout.shorts_script_queue),
        str(layout.shorts_script_package_staging),
    ]
    assert "strategy.hook_type inválido" in spec.payload["task"]["validation_error"]


def test_remote_biblical_review_uses_one_isolated_high_reasoning_reviewer(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Judas"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    write_json(layout.shorts_biblical_review_context, {"project_id": job.job_id})
    write_json(
        layout.shorts_biblical_review_queue,
        {
            "job_id": "biblical-review-test",
            "inputs": {"compact_context": "reviews/shorts-review-context.json"},
            "outputs": {"review": "reviews/staging/biblical-reviewer/review.json"},
        },
    )

    spec = _biblical_review_phase_spec(layout)

    assert spec.phase == "biblical_review"
    assert spec.model == "gpt-5.6-sol"
    assert spec.reasoning_effort == "medium"
    assert spec.payload["delegation"] == {
        "required": True,
        "role": "biblical_reviewer",
        "count": 1,
        "fork_turns": "none",
        "reasoning_effort": "high",
        "model": "gpt-5.6-sol",
    }
    assert spec.expected_outputs == (layout.shorts_biblical_review_staging_file,)


def test_shorts_correction_allows_two_checkpoints_in_the_persistent_session(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Esperança"))
    correction_run = RemoteCodexRun(
        phase="shorts_script_correction",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        job_attempt=1,
        started_at="2026-07-16T09:00:00Z",
        finished_at="2026-07-16T09:01:00Z",
        exit_code=0,
    )

    one_correction = job.model_copy(update={"attempts": 1, "codex_runs": [correction_run]})
    two_corrections = one_correction.model_copy(
        update={"codex_runs": [correction_run, correction_run]}
    )

    assert _shorts_script_correction_is_available(one_correction) is True
    assert _shorts_script_correction_is_available(two_corrections) is False
    next_attempt = two_corrections.model_copy(update={"attempts": 2})
    assert _shorts_script_correction_is_available(next_attempt) is True


def test_growth_correction_is_one_checkpoint_per_job_attempt(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Cuidado"))
    correction_run = RemoteCodexRun(
        phase="youtube_growth_correction",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        job_attempt=1,
        started_at="2026-07-17T10:00:00Z",
        finished_at="2026-07-17T10:01:00Z",
        exit_code=0,
    )

    assert _growth_correction_is_available(job.model_copy(update={"attempts": 1})) is True
    corrected = job.model_copy(update={"attempts": 1, "codex_runs": [correction_run]})
    assert _growth_correction_is_available(corrected) is False
    assert _growth_correction_is_available(corrected.model_copy(update={"attempts": 2})) is True


def test_growth_correction_checkpoint_preserves_queue_and_reports_error(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Cuidado"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    context = layout.root / "youtube" / "growth-context.json"
    reference = layout.root / "images" / "opening.png"
    package = layout.growth_package_staging
    thumbnail = layout.growth_staging / "thumbnail-001.png"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"reference")
    write_json(context, {"project_id": job.job_id})
    write_json(package, {"invalid": True})
    write_json(
        layout.growth_queue,
        {
            "job_id": "growth-test",
            "inputs": {
                "compact_context": "youtube/growth-context.json",
                "thumbnail_visual_reference_path": "images/opening.png",
                "thumbnail_visual_reference_scene_id": "scene-001",
            },
            "outputs": {
                "package": "youtube/staging/youtube-growth-specialist/package.json",
                "thumbnails": ["youtube/staging/youtube-growth-specialist/thumbnail-001.png"],
            },
            "visual_standard": {"reference_policy": "required_opening_scene_image"},
        },
    )

    spec = _growth_correction_checkpoint(layout, ValueError("estilo fotográfico inválido"))

    assert spec.phase == "youtube_growth_correction"
    assert spec.expected_outputs == (package, thumbnail)
    assert str(package) in spec.payload["allowed_inputs"]
    assert "estilo fotográfico inválido" in spec.payload["task"]["validation_error"]


def test_automatic_recovery_backoff_starts_fast_and_is_capped() -> None:
    assert _automatic_recovery_delay_seconds(1) == 60
    assert _automatic_recovery_delay_seconds(2) == 120
    assert _automatic_recovery_delay_seconds(20) == 21_600


def test_remote_image_phase_delegates_two_compact_workers_in_parallel(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Coragem"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    write_json(
        layout.image_queue,
        {
            "orchestration": {
                "recommended_model": "gpt-5.6-sol",
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

    assert len(specs) == 1
    spec = specs[0]
    assert spec.phase == "image_workers"
    assert spec.model == "gpt-5.6-sol"
    assert spec.reasoning_effort == "medium"
    delegation = spec.payload["delegation"]
    assert delegation["count"] == 2
    assert delegation["max_parallel"] == 2
    assert delegation["reasoning_effort"] == "low"
    assert delegation["model"] == "gpt-5.6-sol"
    assert len(spec.payload["allowed_inputs"]) == 2
    first_job = json.loads(Path(spec.payload["allowed_inputs"][0]).read_text(encoding="utf-8"))
    second_job = json.loads(Path(spec.payload["allowed_inputs"][1]).read_text(encoding="utf-8"))
    assert first_job["jobs"][0]["prompt"] == "PROMPT EXCLUSIVO UM"
    assert second_job["jobs"][0]["prompt"] == "PROMPT EXCLUSIVO DOIS"


def test_remote_growth_requires_opening_scene_as_visual_reference(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Confiança"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    context = layout.root / "youtube" / "growth-context.json"
    package = layout.root / "youtube" / "staging" / "youtube-growth-specialist" / "package.json"
    thumbnail = package.parent / "thumbnail-001.png"
    reference = layout.root / "images" / "staging" / "scene-001-v001.png"
    write_json(context, {"opening_scene": {"scene_id": "scene-001"}})
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"reference image")
    write_json(
        layout.growth_queue,
        {
            "job_id": "youtube-growth-test",
            "orchestration": {
                "recommended_model": "gpt-5.6-sol",
                "reasoning_effort": "low",
            },
            "inputs": {
                "compact_context": "youtube/growth-context.json",
                "thumbnail_visual_reference_scene_id": "scene-001",
            },
            "outputs": {
                "package": "youtube/staging/youtube-growth-specialist/package.json",
                "thumbnails": ["youtube/staging/youtube-growth-specialist/thumbnail-001.png"],
            },
            "visual_standard": {
                "id": "cinematic-family-3d-v1",
                "reference_policy": "required_opening_scene_image",
            },
        },
    )
    write_json(
        layout.image_queue,
        {
            "items": [
                {
                    "scene_id": "scene-001",
                    "staging_path": "images/staging/scene-001-v001.png",
                }
            ]
        },
    )

    spec = _growth_phase_spec(layout)

    assert spec.payload["allowed_inputs"] == [
        str(layout.growth_queue),
        str(context),
        str(reference),
    ]
    assert spec.payload["task"]["thumbnail_visual_reference_path"] == str(reference)
    assert "não a converta em fotografia" in spec.payload["task"]["instructions"][1]
    assert spec.expected_outputs == (package, thumbnail)


def test_remote_long_growth_uses_theme_prompt_without_visual_reference(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(video_format="long", topic="Confiança"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    context = layout.root / "youtube" / "growth-context.json"
    package = layout.root / "youtube" / "staging" / "youtube-growth-specialist" / "package.json"
    thumbnail = package.parent / "thumbnail-001.png"
    write_json(context, {"thumbnail_theme": "Confiança em Deus"})
    write_json(
        layout.growth_queue,
        {
            "job_id": "youtube-growth-long-test",
            "inputs": {
                "compact_context": "youtube/growth-context.json",
                "thumbnail_visual_reference_scene_id": None,
                "thumbnail_visual_reference_path": None,
                "thumbnail_theme": "Confiança em Deus",
                "thumbnail_prompt": "Prompt temático horizontal sem texto.",
            },
            "outputs": {
                "package": "youtube/staging/youtube-growth-specialist/package.json",
                "thumbnails": ["youtube/staging/youtube-growth-specialist/thumbnail-001.png"],
                "thumbnail_source_mode": "generated_theme_art",
            },
            "visual_standard": {
                "id": "cinematic-family-3d-v1",
                "reference_policy": "theme_only_no_reference",
            },
        },
    )

    spec = _growth_phase_spec(layout)

    assert spec.payload["allowed_inputs"] == [str(layout.growth_queue), str(context)]
    assert spec.payload["task"]["thumbnail_visual_reference_path"] is None
    assert "sem referenced_image_paths" in spec.payload["task"]["instructions"][1]
    assert spec.expected_outputs == (package, thumbnail)


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
    assert spec.model == "gpt-5.6-sol"
    assert spec.reasoning_effort == "medium"
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
        "model": "gpt-5.6-sol",
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


def test_remote_image_checkpoint_recovers_png_from_persistent_codex_session(tmp_path) -> None:
    thread_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    phase_log = tmp_path / "phase.jsonl"
    phase_log.write_text(
        json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n",
        encoding="utf-8",
    )
    generated = tmp_path / ".codex" / "generated_images" / thread_id / "exec-image.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"\x89PNG\r\n\x1a\nrecovered image")
    destination = tmp_path / "project" / "images" / "staging" / "scene-001.png"
    spec = CodexPhaseSpec(
        phase="image",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(destination,),
    )

    recovered = _recover_generated_image_outputs(
        spec,
        phase_log,
        codex_home=tmp_path / ".codex",
    )

    assert recovered == [(generated, destination)]
    assert destination.read_bytes() == generated.read_bytes()


def test_remote_image_recovery_rejects_non_png_cache_artifact(tmp_path) -> None:
    thread_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    phase_log = tmp_path / "phase.jsonl"
    phase_log.write_text(
        json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n",
        encoding="utf-8",
    )
    generated = tmp_path / ".codex" / "generated_images" / thread_id / "exec-image.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"not really a png")
    destination = tmp_path / "project" / "images" / "staging" / "scene-001.png"
    spec = CodexPhaseSpec(
        phase="image",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={},
        expected_outputs=(destination,),
    )

    recovered = _recover_generated_image_outputs(
        spec,
        phase_log,
        codex_home=tmp_path / ".codex",
    )

    assert recovered == []
    assert not destination.exists()


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


def test_work_once_requeues_failure_for_automatic_recovery(tmp_path, monkeypatch) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("falha recuperavel de teste")

    monkeypatch.setattr("pipeline_videos.remote_jobs.prepare_job_project", fail_prepare)

    recovered = work_once(
        store=store,
        projects_root=tmp_path / "projects",
        repo_root=tmp_path,
    )

    assert recovered is not None
    assert recovered.job_id == queued.job_id
    assert recovered.status == "queued"
    assert recovered.automatic_recovery_count == 1
    assert recovered.next_retry_at is not None
    assert "falha recuperavel de teste" in (recovered.error or "")


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


def test_next_retry_at_delays_automatic_recovery(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    now = datetime.now(UTC)
    job = store.enqueue(_request())
    waiting = store.transition(job, "queued", next_retry_at=now + timedelta(minutes=5))

    assert job_is_due(waiting, now) is False
    assert job_is_due(waiting, now + timedelta(minutes=6)) is True
