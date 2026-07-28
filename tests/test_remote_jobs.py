from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from pipeline_videos.cli import app
from pipeline_videos.codex_rate_limits import CodexRateLimitSnapshot, CodexRateLimitWindow
from pipeline_videos.models import (
    DEFAULT_ELEVENLABS_MODEL_ID,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_VOICE_VARIANTS,
    Briefing,
    SourceLibrary,
)
from pipeline_videos.remote_jobs import (
    MAX_AUTOMATIC_RECOVERIES,
    REMOTE_MUSIC_TRACK_PATHS,
    CodexPhaseSpec,
    RemoteCodexRun,
    RemoteJobStore,
    RemoteVideoRequest,
    _automatic_recovery_delay_seconds,
    _biblical_review_phase_spec,
    _codex_agent_command,
    _editorial_sources_phase_spec,
    _ensure_scheduled_usage_before,
    _execute_agent_checkpoint,
    _failure_with_codex_context,
    _finalize_scheduled_usage_if_ready,
    _growth_correction_checkpoint,
    _growth_correction_is_available,
    _growth_phase_spec,
    _image_phase_specs,
    _import_editorial_source_selection,
    _long_script_phase_spec,
    _normalize_shorts_script_payload,
    _recover_generated_image_outputs,
    _schedule_automatic_recovery,
    _shorts_script_correction_checkpoint,
    _shorts_script_correction_is_available,
    _shorts_script_phase_spec,
    codex_checkpoint_prompt,
    codex_usage_summary,
    job_is_due,
    job_summary,
    prepare_job_project,
    wait_for_terminal_job,
    work_once,
)
from pipeline_videos.storage import read_json, read_model, write_json
from pipeline_videos.utils import sha256_value


def _request(**updates):
    payload = {
        "video_format": "short",
        "external_services_authorized": True,
    }
    payload.update(updates)
    return RemoteVideoRequest.model_validate(payload)


def _rate_limit_snapshot(used_percent: float) -> CodexRateLimitSnapshot:
    window = CodexRateLimitWindow(
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_duration_minutes=10080,
        resets_at="2026-08-03T17:31:00Z",
    )
    return CodexRateLimitSnapshot(
        captured_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        secondary=window,
        weekly=window,
    )


def test_remote_request_applies_safe_defaults_and_requires_explicit_external_authorization(
    tmp_path,
) -> None:
    request = _request()

    assert request.duration_seconds == 50
    assert request.exact_duration is False
    assert request.publication_mode == "private_review"
    assert request.publish_at is None
    assert request.creative_mode == "auto"
    assert request.hook_policy == "auto"
    assert request.hook_family == "auto"
    assert request.editorial_profile == "verse_led_v1"
    assert request.music_track is None
    assert request.voice_variant == "jesus_1"

    store = RemoteJobStore(tmp_path / "jobs")
    with pytest.raises(ValueError, match="autorizar explicitamente"):
        store.enqueue(RemoteVideoRequest(video_format="short"))

    with pytest.raises(ValueError, match="Shorts exigem hook inicial"):
        _request(hook_policy="none")

    with pytest.raises(ValueError, match="seis famílias oficiais"):
        _request(hook_family="consequence_first")

    assert _request(duration_seconds=15).duration_seconds == 15
    with pytest.raises(ValueError, match="modos devocional aplicado ou pastoral"):
        _request(duration_seconds=15, creative_mode="character_portrait")
    with pytest.raises(ValueError, match="hook emocional de interrupção"):
        _request(duration_seconds=15, hook_family="nobody_talks_but")
    with pytest.raises(ValueError, match="Histórias de personagens exigem"):
        _request(duration_seconds=15, character="Pedro")
    with pytest.raises(ValueError, match="Vídeos longos precisam"):
        _request(video_format="long", duration_seconds=15)
    with pytest.raises(ValueError, match="45 e 60"):
        _request(editorial_profile="high_stakes_biblical_question_v1", duration_seconds=30)
    with pytest.raises(ValueError, match="pergunta curada automaticamente"):
        _request(editorial_profile="high_stakes_biblical_question_v1", topic="Tema manual")
    with pytest.raises(ValueError, match="família investigativa"):
        _request(
            editorial_profile="high_stakes_biblical_question_v1",
            hook_family="never_do_this",
        )
    with pytest.raises(ValueError, match="exclusivas de Shorts"):
        _request(video_format="long", duration_seconds=540, music_track="leaning")


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
    assert read_json(store.terminal_event_path(queued.job_id))["status"] == "cancelled"

    second = store.enqueue(_request())
    failed = store.transition(second, "failed", error="falha de teste")
    assert read_json(store.terminal_event_path(second.job_id))["agent_attention_required"] is True
    retried = store.retry(failed.job_id)
    assert retried.status == "queued"
    assert retried.error is None
    assert retried.automatic_recovery_count == 0
    assert retried.last_failure_fingerprint is None
    assert retried.consecutive_failure_count == 0
    assert not store.terminal_event_path(second.job_id).exists()


def test_wait_for_terminal_job_returns_persisted_terminal_event_without_model(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())
    completed = store.transition(
        queued,
        "completed",
        youtube_url="https://youtu.be/video-123",
    )
    store.terminal_event_path(completed.job_id).unlink()

    waited = wait_for_terminal_job(store, completed.job_id, timeout_seconds=0)

    assert waited.status == "completed"
    event = read_json(store.terminal_event_path(completed.job_id))
    assert event["terminal"] is True
    assert event["youtube_url"] == "https://youtu.be/video-123"
    assert event["agent_attention_required"] is False


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
    assert manifest.config.production.image_workers == 2
    assert manifest.config.production.optimization_profile == "token_optimized_v1"
    assert manifest.config.shorts_script.use_specialist_subagent is True
    assert manifest.config.shorts_script.narrative_standard == "creative_retention_v3"
    assert manifest.config.youtube_growth.use_specialist_subagent is True
    assert manifest.config.youtube_growth.title_standard == "creative_pipe_passage_v3"
    assert manifest.config.youtube_growth.title_variants == 1
    assert manifest.config.audio.voice_id == DEFAULT_ELEVENLABS_VOICE_ID
    assert manifest.config.audio.model_id == DEFAULT_ELEVENLABS_MODEL_ID
    assert manifest.duration_policy == "preferred_target_v1"
    assert manifest.requested_duration_min_seconds == 42.5
    assert manifest.requested_duration_max_seconds == 57.5
    assert manifest.config.youtube_publication.allow_automated_private_review_upload is True
    assert all(record.status.value == "pending" for record in manifest.approvals.values())
    prompt = codex_checkpoint_prompt(Path("/tmp/checkpoint.json"))
    assert "mesma sessão" in prompt
    assert "AGENTS.md" in prompt
    assert "crie exatamente os subagentes" in prompt.casefold()
    worker_prompt = codex_checkpoint_prompt(
        Path("/tmp/image-worker.json"),
        session_scope="isolated_worker",
    )
    assert "image_worker isolado" in worker_prompt
    assert "Não crie subagentes" in worker_prompt
    assert "Leia somente o JSON do checkpoint" in worker_prompt


def test_remote_duration_is_exact_only_when_explicitly_requested(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(
        _request(
            topic="Duração declaradamente exata",
            duration_seconds=50,
            exact_duration=True,
        )
    )

    manifest = prepare_job_project(job, projects_root=tmp_path / "projects").load_manifest()

    assert manifest.duration_policy == "exact_target_v1"
    assert manifest.requested_duration_min_seconds == 50
    assert manifest.requested_duration_max_seconds == 50


def test_prepare_remote_micro_short_uses_two_parallel_image_workers(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Confiança", duration_seconds=15))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.target_duration_seconds == 15
    assert manifest.config.shorts_script.duration_profile == "adaptive_compact_v1"
    assert manifest.config.production.image_workers == 2
    assert manifest.config.shorts_script.generation_mode == ("deterministic_compact_visual_plan_v1")
    assert manifest.config.shorts_script.biblical_review_mode == (
        "deterministic_verified_compact_v1"
    )
    assert manifest.config.music.library_profile == "adaptive_compact_devotional_v1"
    briefing = read_model(layout.briefing, Briefing)
    assert briefing.creative_mode == "pastoral_encouragement"
    assert briefing.hook_family == "emotional_interruption"
    assert briefing.hook_contract == "cinematic_interruption_v1"
    assert briefing.call_to_action == ""


def test_remote_job_can_select_second_jesus_voice(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(voice_variant="jesus_2"))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.config.audio.voice_id == ELEVENLABS_VOICE_VARIANTS["jesus_2"]
    decisions = layout.decisions.read_text(encoding="utf-8")
    assert '"voice_variant": "jesus_2"' in decisions


@pytest.mark.parametrize(
    ("music_track", "expected_filename"),
    (("leaning", "Leaning.mp3"), ("aloboi", "Aloboi .mp3")),
)
def test_remote_high_stakes_job_persists_profile_voice_and_requested_music(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    music_track: str,
    expected_filename: str,
) -> None:
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "local")
    music_source = tmp_path / expected_filename
    music_source.write_bytes(b"ID3-test-track")
    monkeypatch.setitem(REMOTE_MUSIC_TRACK_PATHS, music_track, music_source)
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(
        _request(
            duration_seconds=50,
            editorial_profile="high_stakes_biblical_question_v1",
            voice_variant="jesus_2",
            music_track=music_track,
        )
    )

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    manifest = layout.load_manifest()

    assert manifest.config.shorts_script.editorial_profile == ("high_stakes_biblical_question_v1")
    assert manifest.config.shorts_script.require_independent_biblical_review is False
    assert manifest.config.audio.voice_id == ELEVENLABS_VOICE_VARIANTS["jesus_2"]
    assert manifest.config.render.background_music_path is not None
    assert manifest.config.render.background_music_path.endswith(expected_filename)
    summary = job_summary(job)
    assert summary["editorial_profile"] == "high_stakes_biblical_question_v1"
    assert summary["music_track"] == music_track


def test_remote_character_request_bootstraps_verified_storytelling_sources(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(character="Judas", duration_seconds=60))

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    briefing = read_model(layout.briefing, Briefing)
    sources = read_model(layout.sources, SourceLibrary)

    assert briefing.creative_mode == "biblical_storytelling"
    assert briefing.main_passage == "Mateus 26:14-16"
    assert len(sources.entries) == 4
    assert all(source.verified for source in sources.entries)


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


def test_character_long_script_phase_requires_human_storytelling_voice(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(video_format="long", duration_seconds=420, character="Mateus"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")

    spec = _long_script_phase_spec(layout)
    task = spec.payload["task"]
    instructions = " ".join(task["instructions"]).casefold()

    assert task["creative_contract"]["storytelling_profile"] == (
        "biblical_character_storytelling_v1"
    )
    assert task["briefing"]["creative_mode"] == "biblical_storytelling"
    assert "conte a história em voz alta" in instructions
    assert "não use analysis nem comparison" in instructions
    assert "o relato não explica" in instructions


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


def test_image_checkpoint_completes_from_hook_receipt_after_agent_exit(
    tmp_path,
    monkeypatch,
) -> None:
    session_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    prompt = "Cena concluída por subagente"
    repo_root = tmp_path / "repo"
    hook = repo_root / "scripts" / "remote_codex_hook.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("", encoding="utf-8")
    codex_home = tmp_path / ".codex"
    generated = codex_home / "generated_images" / "isolated-worker-thread" / "exec-image.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"\x89PNG\r\n\x1a\nworker image")
    destination = tmp_path / "project" / "images" / "staging" / "scene.png"
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request())
    coordinator_session_id = "019f699a-1111-7092-983b-26e5e8937c16"
    job = job.model_copy(update={"codex_session_id": coordinator_session_id})
    store.save(job)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(
            json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n"
        )
        hook_log = Path(kwargs["env"]["PIPELINE_VIDEOS_CODEX_HOOK_LOG"])
        hook_log.write_text(
            json.dumps(
                {
                    "event": "PostToolUse",
                    "image_job_id": "scene-001-v001",
                    "image_prompt_hash": sha256_value(prompt),
                    "image_saved_path": str(generated),
                    "artifact_materialized": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("pipeline_videos.remote_jobs.shutil.which", lambda _: "codex")
    monkeypatch.setattr("pipeline_videos.remote_jobs.subprocess.run", fake_run)
    spec = CodexPhaseSpec(
        phase="image_workers",
        item_id=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={
            "image_artifacts": [
                {
                    "job_id": "scene-001-v001",
                    "prompt_hash": sha256_value(prompt),
                    "staging_path": str(destination),
                }
            ]
        },
        expected_outputs=(destination,),
        session_scope="isolated_worker",
    )

    completed = _execute_agent_checkpoint(
        job,
        spec,
        store,
        repo_root=repo_root,
        timeout_seconds=30,
    )

    assert destination.read_bytes() == generated.read_bytes()
    assert completed.codex_runs[-1].exit_code == 0
    assert completed.codex_session_id == coordinator_session_id
    assert "resume" not in calls[0]
    assert completed.recovery_events[-1].outcome == "recovered"
    assert completed.recovery_events[-1].action == "artifact_receipt_recovery_v1"


def test_image_checkpoint_recovers_single_new_png_from_isolated_worker(
    tmp_path,
    monkeypatch,
) -> None:
    session_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    repo_root = tmp_path / "repo"
    hook = repo_root / "scripts" / "remote_codex_hook.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("", encoding="utf-8")
    codex_home = tmp_path / ".codex"
    old_image = codex_home / "generated_images" / "old-thread" / "exec-old.png"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"\x89PNG\r\n\x1a\nold image")
    generated = codex_home / "generated_images" / "worker-thread" / "exec-new.png"
    destination = tmp_path / "project" / "images" / "staging" / "scene.png"
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request())

    def fake_run(command, **kwargs):
        kwargs["stdout"].write(
            json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n"
        )
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nnew worker image")
        return SimpleNamespace(returncode=1)

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("pipeline_videos.remote_jobs.shutil.which", lambda _: "codex")
    monkeypatch.setattr("pipeline_videos.remote_jobs.subprocess.run", fake_run)
    spec = CodexPhaseSpec(
        phase="image_workers",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={
            "artifact_isolation": "single_expected_output_v1",
            "image_artifacts": [
                {
                    "job_id": "scene-001-v001",
                    "prompt_hash": sha256_value("prompt"),
                    "staging_path": str(destination),
                }
            ],
        },
        expected_outputs=(destination,),
        session_scope="isolated_worker",
    )

    completed = _execute_agent_checkpoint(
        job,
        spec,
        store,
        repo_root=repo_root,
        timeout_seconds=30,
    )

    assert destination.read_bytes() == generated.read_bytes()
    assert completed.codex_runs[-1].exit_code == 0
    assert completed.codex_session_id is None
    assert completed.recovery_events[-1].outcome == "recovered"
    assert completed.recovery_events[-1].action == ("cross_session_single_artifact_recovery_v1")


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


def test_remote_compact_visual_planner_keeps_context_during_correction(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Esperança", duration_seconds=15))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    write_json(layout.shorts_script_context, {"opening_attention_contract": {"standard": "x"}})
    write_json(
        layout.shorts_script_queue,
        {
            "job_id": "compact-visual-test",
            "generation_mode": "deterministic_compact_visual_plan_v1",
            "orchestration": {"reasoning_effort": "medium"},
            "inputs": {"compact_context": "script/shorts-context.json"},
            "outputs": {
                "package": "script/staging/shorts-script-specialist/package.json",
            },
        },
    )

    phase = _shorts_script_phase_spec(layout)
    correction = _shorts_script_correction_checkpoint(layout, ValueError("abertura estática"))

    assert phase.payload["delegation"]["role"] == "shorts_visual_planner"
    assert correction.payload["delegation"]["role"] == "shorts_visual_planner"
    assert str(layout.shorts_script_context) in correction.payload["allowed_inputs"]
    assert correction.payload["task"]["compact_context_path"] == str(layout.shorts_script_context)


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


def test_shorts_correction_allows_one_checkpoint_then_fallback(tmp_path) -> None:
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

    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    no_correction = job.model_copy(update={"attempts": 1})
    one_correction = no_correction.model_copy(update={"codex_runs": [correction_run]})

    assert _shorts_script_correction_is_available(no_correction, layout) is True
    assert _shorts_script_correction_is_available(one_correction, layout) is False
    next_attempt = one_correction.model_copy(update={"attempts": 2})
    assert _shorts_script_correction_is_available(next_attempt, layout) is True


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
    package = layout.growth_package_staging
    write_json(context, {"project_id": job.job_id})
    write_json(package, {"invalid": True})
    write_json(
        layout.growth_queue,
        {
            "job_id": "growth-test",
            "inputs": {"context": "youtube/growth-context.json"},
            "outputs": {"package": "youtube/staging/youtube-growth-specialist/package.json"},
        },
    )

    spec = _growth_correction_checkpoint(layout, ValueError("estilo fotográfico inválido"))

    assert spec.phase == "youtube_growth_correction"
    assert spec.expected_outputs == (package,)
    assert str(package) in spec.payload["allowed_inputs"]
    assert "estilo fotográfico inválido" in spec.payload["task"]["validation_error"]


def test_automatic_recovery_backoff_starts_fast_and_is_capped() -> None:
    assert _automatic_recovery_delay_seconds(1) == 60
    assert _automatic_recovery_delay_seconds(2) == 120
    assert _automatic_recovery_delay_seconds(20) == 21_600


def test_automatic_recovery_stops_and_emits_attention_event_at_limit(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())
    nearly_exhausted = store.transition(
        queued,
        "running",
        automatic_recovery_count=MAX_AUTOMATIC_RECOVERIES - 1,
    )

    failed = _schedule_automatic_recovery(
        store,
        nearly_exhausted,
        RuntimeError("falha permanente"),
        status="queued",
    )

    assert failed.status == "failed"
    assert failed.next_retry_at is None
    assert failed.automatic_recovery_count == MAX_AUTOMATIC_RECOVERIES
    assert read_json(store.terminal_event_path(failed.job_id))["agent_attention_required"] is True


def test_identical_image_handoff_failure_stops_after_two_occurrences(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())
    codex_run = RemoteCodexRun(
        phase="image_workers",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        started_at="2026-07-28T07:00:00Z",
        finished_at="2026-07-28T07:01:00Z",
        exit_code=2,
        error="Saídas obrigatórias ausentes: images/staging/scene.png",
    )
    running = store.transition(queued, "running", codex_runs=[codex_run])

    first = _schedule_automatic_recovery(
        store,
        running,
        RuntimeError("Saídas obrigatórias ausentes: images/staging/scene.png"),
        status="queued",
    )
    failed = _schedule_automatic_recovery(
        store,
        first,
        RuntimeError("Saídas obrigatórias ausentes: images/staging/scene.png"),
        status="queued",
    )

    assert first.status == "queued"
    assert first.last_failure_fingerprint == "image_artifact_materialization_missing_v1"
    assert first.consecutive_failure_count == 1
    assert failed.status == "failed"
    assert failed.automatic_recovery_count == 2
    assert failed.consecutive_failure_count == 2
    assert failed.recovery_events[-1].action == "stop_repeated_identical_failure_v1"
    learnings = read_json(store.root / "recovery-learnings.json")
    learned = learnings["fingerprints"]["image_artifact_materialization_missing_v1"]
    assert learned["occurrences"] == 2
    assert learned["terminal_failures"] == 1
    assert learned["affected_jobs"] == [failed.job_id]


def test_revoked_youtube_oauth_is_not_retried(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    queued = store.enqueue(_request())

    failed = _schedule_automatic_recovery(
        store,
        queued,
        RuntimeError("invalid_grant: OAuth token expired or revoked"),
        status="queued",
    )

    assert failed.status == "failed"
    assert failed.automatic_recovery_count == 1
    assert failed.last_failure_fingerprint == "youtube_oauth_invalid_grant_v1"
    assert failed.next_retry_at is None


def test_remote_image_phases_isolate_one_artifact_per_worker_checkpoint(tmp_path) -> None:
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
            "visual_standard": {"id": "biblical-midcentury-gouache-v1"},
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
    assert all(spec.phase == "image_workers" for spec in specs)
    assert all(spec.model == "gpt-5.6-sol" for spec in specs)
    assert all(spec.reasoning_effort == "medium" for spec in specs)
    assert all(spec.session_scope == "isolated_worker" for spec in specs)
    assert all(spec.payload["artifact_isolation"] == "single_expected_output_v1" for spec in specs)
    assert all(spec.payload["execution_role"] == "image_worker" for spec in specs)
    assert all(spec.payload["delegation"] == {"required": False} for spec in specs)
    assert all(len(spec.payload["allowed_inputs"]) == 1 for spec in specs)
    assert all(len(spec.payload["image_artifacts"]) == 1 for spec in specs)
    assert {spec.payload["image_artifacts"][0]["prompt_hash"] for spec in specs} == {
        sha256_value("PROMPT EXCLUSIVO UM"),
        sha256_value("PROMPT EXCLUSIVO DOIS"),
    }
    first_job = json.loads(Path(specs[0].payload["allowed_inputs"][0]).read_text(encoding="utf-8"))
    second_job = json.loads(Path(specs[1].payload["allowed_inputs"][0]).read_text(encoding="utf-8"))
    assert first_job["artifact_isolation"] == "single_expected_output_v1"
    assert second_job["artifact_isolation"] == "single_expected_output_v1"
    assert len(first_job["jobs"]) == 1
    assert len(second_job["jobs"]) == 1
    assert first_job["jobs"][0]["prompt"] == "PROMPT EXCLUSIVO UM"
    assert second_job["jobs"][0]["prompt"] == "PROMPT EXCLUSIVO DOIS"


def test_remote_growth_produces_metadata_without_image_inputs(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="Confiança"))
    layout = prepare_job_project(job, projects_root=tmp_path / "projects")
    context = layout.root / "youtube" / "growth-context.json"
    package = layout.root / "youtube" / "staging" / "youtube-growth-specialist" / "package.json"
    write_json(context, {"opening_scene": {"scene_id": "scene-001"}})
    write_json(
        layout.growth_queue,
        {
            "job_id": "youtube-growth-test",
            "orchestration": {
                "recommended_model": "gpt-5.6-sol",
                "reasoning_effort": "low",
            },
            "inputs": {"context": "youtube/growth-context.json"},
            "outputs": {"package": "youtube/staging/youtube-growth-specialist/package.json"},
        },
    )

    spec = _growth_phase_spec(layout)

    assert spec.payload["allowed_inputs"] == [str(layout.growth_queue), str(context)]
    assert spec.payload["task"]["kind"] == "produce_youtube_metadata"
    assert spec.payload["task"]["tool"] is None
    assert spec.expected_outputs == (package,)


def test_specific_topic_uses_compact_catalog_selection_phase(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(_request(topic="coragem para conversas difíceis", duration_seconds=15))
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
    assert all(
        item["compact_hook_prior"] == "concrete_scroll_stop_v1"
        and item["compact_hook_editorial_features"]
        and item["recent_exact_hook_count"] == 0
        and item["recent_exact_hook_penalty"] == 0
        for item in spec.payload["task"]["candidates"]
    )


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


def test_remote_image_recovery_uses_subagent_hook_receipt(tmp_path) -> None:
    prompt = "Prompt produzido por um trabalhador isolado"
    thread_id = "019f699a-9ff9-7092-983b-26e5e8937c16"
    phase_log = tmp_path / "phase.jsonl"
    phase_log.write_text(
        json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n",
        encoding="utf-8",
    )
    generated = (
        tmp_path / ".codex" / "generated_images" / "different-worker-thread" / "exec-image.png"
    )
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"\x89PNG\r\n\x1a\nworker image")
    destination = tmp_path / "project" / "images" / "staging" / "scene-001.png"
    hook_log = tmp_path / "hook.jsonl"
    hook_log.write_text(
        json.dumps(
            {
                "event": "PostToolUse",
                "image_prompt_hash": sha256_value(prompt),
                "image_saved_path": str(generated),
                "artifact_materialized": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = CodexPhaseSpec(
        phase="image_workers",
        item_id=None,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={
            "image_artifacts": [
                {
                    "job_id": "scene-001-v001",
                    "prompt_hash": sha256_value(prompt),
                    "staging_path": str(destination),
                }
            ]
        },
        expected_outputs=(destination,),
    )

    recovered = _recover_generated_image_outputs(
        spec,
        phase_log,
        codex_home=tmp_path / ".codex",
        hook_log=hook_log,
    )

    assert recovered == [(generated, destination)]
    assert destination.read_bytes() == generated.read_bytes()


def test_remote_image_recovery_finds_only_new_png_across_worker_sessions(tmp_path) -> None:
    phase_log = tmp_path / "phase.jsonl"
    phase_log.write_text("", encoding="utf-8")
    codex_home = tmp_path / ".codex"
    old_image = codex_home / "generated_images" / "old-worker-thread" / "exec-old-image.png"
    old_image.parent.mkdir(parents=True)
    old_image.write_bytes(b"\x89PNG\r\n\x1a\nold image")
    generated_before = frozenset({old_image.resolve()})
    generated = codex_home / "generated_images" / "new-worker-thread" / "exec-new-image.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"\x89PNG\r\n\x1a\nnew image")
    destination = tmp_path / "project" / "images" / "staging" / "scene-001.png"
    spec = CodexPhaseSpec(
        phase="image_workers",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={"artifact_isolation": "single_expected_output_v1"},
        expected_outputs=(destination,),
    )

    recovered = _recover_generated_image_outputs(
        spec,
        phase_log,
        codex_home=codex_home,
        generated_after_ns=10**30,
        generated_before=generated_before,
    )

    assert recovered == [(generated, destination)]
    assert destination.read_bytes() == generated.read_bytes()


def test_remote_cross_session_recovery_rejects_ambiguous_new_pngs(tmp_path) -> None:
    phase_log = tmp_path / "phase.jsonl"
    phase_log.write_text("", encoding="utf-8")
    codex_home = tmp_path / ".codex"
    generated_root = codex_home / "generated_images"
    for worker_id in ("worker-one", "worker-two"):
        generated = generated_root / worker_id / f"exec-{worker_id}.png"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"\x89PNG\r\n\x1a\nnew image")
    destination = tmp_path / "project" / "images" / "staging" / "scene-001.png"
    spec = CodexPhaseSpec(
        phase="image_workers",
        item_id="scene-001-v001",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        payload={"artifact_isolation": "single_expected_output_v1"},
        expected_outputs=(destination,),
    )

    recovered = _recover_generated_image_outputs(
        spec,
        phase_log,
        codex_home=codex_home,
        generated_before=frozenset(),
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


def test_scheduled_jobs_measure_weekly_usage_before_first_and_after_last(
    tmp_path,
    monkeypatch,
) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    local_date = "2026-07-28"
    first = store.enqueue(
        _request(),
        job_id="video-20260728-040000-11111111",
        schedule_occurrence_id=f"shorts-compacto-12345678:{local_date}:1",
    )
    second = store.enqueue(
        _request(),
        job_id="video-20260728-060000-22222222",
        schedule_occurrence_id=f"shorts-depth-12345678:{local_date}:1",
    )
    snapshots = iter([_rate_limit_snapshot(10.0), _rate_limit_snapshot(12.5)])
    monkeypatch.setattr(
        "pipeline_videos.remote_jobs._capture_codex_rate_limits",
        lambda: next(snapshots),
    )

    first = _ensure_scheduled_usage_before(store, first)

    assert first.codex_account_usage_before is not None
    persisted_second, _ = store.find(second.job_id)
    assert persisted_second.codex_account_usage_before is not None
    assert persisted_second.codex_account_usage_after is None

    first = store.transition(first, "completed")
    _finalize_scheduled_usage_if_ready(store, first)
    first, _ = store.find(first.job_id)
    assert first.codex_account_usage_after is None

    second, _ = store.find(second.job_id)
    second = store.transition(second, "completed")
    second = _finalize_scheduled_usage_if_ready(store, second)

    assert second.codex_account_usage_after is not None
    assert second.codex_weekly_usage_delta_percent == 2.5
    first, _ = store.find(first.job_id)
    assert first.codex_weekly_usage_delta_percent == 2.5
    assert job_summary(first)["codex_account_usage"]["weekly_usage_delta_percent"] == 2.5


def test_usage_measurement_failure_does_not_block_scheduled_job(tmp_path, monkeypatch) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    job = store.enqueue(
        _request(),
        job_id="video-20260728-040000-33333333",
        schedule_occurrence_id="shorts-compacto-12345678:2026-07-28:1",
    )
    monkeypatch.setattr(
        "pipeline_videos.remote_jobs._capture_codex_rate_limits",
        lambda: (_ for _ in ()).throw(RuntimeError("telemetria indisponível")),
    )

    measured = _ensure_scheduled_usage_before(store, job)

    assert measured.status == "queued"
    assert measured.codex_account_usage_before is None
    assert measured.codex_account_usage_before_attempted_at is not None
    assert "telemetria indisponível" in (measured.codex_account_usage_measurement_error or "")


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
    summary = job_summary(job)
    assert summary["duration_seconds"] == 50
    assert summary["not_before"] == (now + timedelta(hours=1)).isoformat()
    assert summary["voice_variant"] == "jesus_1"
    assert summary["editorial_profile"] == "verse_led_v1"
    assert summary["music_track"] is None


def test_next_retry_at_delays_automatic_recovery(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "jobs")
    now = datetime.now(UTC)
    job = store.enqueue(_request())
    waiting = store.transition(job, "queued", next_retry_at=now + timedelta(minutes=5))

    assert job_is_due(waiting, now) is False
    assert job_is_due(waiting, now + timedelta(minutes=6)) is True
