from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta

import pytest

from pipeline_videos.approved_scripts import (
    ApprovedCtaOption,
    ApprovedScriptStore,
    ApprovedShortScript,
    approved_content_hash,
    materialize_approved_project,
)
from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    NarrationVisualScene,
    ScriptDocument,
    ScriptSection,
    ShortsScriptStrategy,
    SourceEntry,
    SourceLibrary,
)
from pipeline_videos.remote_jobs import RemoteJobStore
from pipeline_videos.remote_schedules import (
    DailyShortSlot,
    RemoteScheduleStore,
    materialize_daily_schedules,
)


def _approved_script() -> ApprovedShortScript:
    narration = (
        "Eu sabia que Judas me trairia e ainda assim o mantive entre os doze. "
        "Comente LIBERDADE se esta resposta fez sentido."
    )
    script = ScriptDocument(
        title="Por que Jesus não afastou Judas? | João 6:64",
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="A pergunta",
                narration=narration,
            )
        ],
    )
    strategy = ShortsScriptStrategy(
        target_viewer="Pessoas com dúvidas bíblicas",
        viewer_need="Entender a decisão de Jesus",
        core_value="Resposta bíblica clara",
        content_promise="Explicar a permanência de Judas",
        hook="Eu sabia que Judas me trairia.",
        hook_type="curiosity_gap",
        hook_target_end_seconds=3.0,
        open_loop="Por que Judas permaneceu?",
        payoff="Conhecimento não anulou escolha e responsabilidade.",
        practical_value="Distinguir presciência de coerção.",
        cta="Comente LIBERDADE.",
        reason_to_subscribe="Mais dúvidas bíblicas explicadas.",
        retention_beats=[],
        visual_scenes=[
            NarrationVisualScene(
                section_id="opening",
                narration=narration,
                scene_description="Jesus e Judas à mesa, em tensão silenciosa.",
                visual_relation="contextual",
            )
        ],
        section_ids=["opening"],
    )
    shell = ApprovedShortScript.model_construct(
        approved_script_id="approved-judas-00000000",
        label="Por que Jesus não afastou Judas?",
        source_project_id="23-07-2026_judas",
        source_project_path="C:/projeto",
        target_duration_seconds=50,
        editorial_profile="verse_led_v1",
        briefing=Briefing(title=script.title, target_duration_seconds=50),
        sources=SourceLibrary(entries=[]),
        script=script,
        biblical_review=BiblicalReview(
            script_version=1,
            reviewed_at="2026-07-23T12:00:00Z",
            reviewer="biblical_reviewer",
            summary="Sem bloqueios.",
        ),
        strategy=strategy,
        cta_options=[
            ApprovedCtaOption(
                option_id="liberdade",
                spoken_text="Comente LIBERDADE se esta resposta fez sentido.",
                mode="comment",
            )
        ],
        selected_cta_id="liberdade",
        voice_variant="jesus_1",
        music_track="leaning",
        metadata=None,
    )
    digest = approved_content_hash(shell)
    return ApprovedShortScript(
        approved_script_id=f"approved-judas-{digest[:8]}",
        status="draft",
        label=shell.label,
        source_project_id=shell.source_project_id,
        source_project_path=shell.source_project_path,
        target_duration_seconds=shell.target_duration_seconds,
        editorial_profile=shell.editorial_profile,
        briefing=shell.briefing,
        sources=shell.sources,
        script=shell.script,
        biblical_review=shell.biblical_review,
        strategy=shell.strategy,
        cta_options=shell.cta_options,
        selected_cta_id=shell.selected_cta_id,
        voice_variant=shell.voice_variant,
        music_track=shell.music_track,
        content_hash=digest,
        created_at="2026-07-23T12:00:00Z",
        updated_at="2026-07-23T12:00:00Z",
    )


def test_approval_hash_and_atomic_reservation(tmp_path) -> None:
    store = ApprovedScriptStore(tmp_path / "jobs")
    draft = _approved_script()
    store.save(draft)

    with pytest.raises(ValueError, match="hash"):
        store.approve(
            draft.approved_script_id,
            confirm_hash="wrong",
            approved_by="Pedro",
        )

    approved = store.approve(
        draft.approved_script_id,
        confirm_hash=draft.content_hash,
        approved_by="Pedro",
    )
    reserved = store.reserve("video-20260723-120000-12345678")

    assert approved.status == "approved"
    assert reserved.status == "reserved"
    assert reserved.reserved_by_job_id == "video-20260723-120000-12345678"
    with pytest.raises(RuntimeError, match="Não há roteiros"):
        store.reserve("video-20260723-120001-12345678")


def test_remote_production_state_merge_is_monotonic_and_idempotent(tmp_path) -> None:
    store = ApprovedScriptStore(tmp_path / "jobs")
    draft = _approved_script()
    store.save(draft)
    approved = store.approve(
        draft.approved_script_id,
        confirm_hash=draft.content_hash,
        approved_by="Pedro",
    )
    snapshot = store.production_state_snapshot()
    snapshot["items"][0].update(
        {
            "status": "reserved",
            "reserved_by_job_id": "video-20260723-120000-12345678",
            "reserved_at": "2026-07-23T12:00:00+00:00",
            "updated_at": "2026-07-23T12:00:00+00:00",
        }
    )

    first = store.merge_production_state(snapshot)
    second = store.merge_production_state(snapshot)
    reserved, reserved_path = store.find(approved.approved_script_id)

    assert first["advanced"] == 1
    assert second["unchanged"] == 1
    assert reserved.status == "reserved"
    assert reserved_path.parent.name == "reserved"

    produced_snapshot = json.loads(json.dumps(snapshot))
    produced_snapshot["exported_at"] = "2026-07-23T12:10:00+00:00"
    produced_snapshot["items"][0].update(
        {
            "status": "produced",
            "produced_at": "2026-07-23T12:09:00+00:00",
            "youtube_video_id": "youtube-123",
            "youtube_url": "https://youtu.be/youtube-123",
            "updated_at": "2026-07-23T12:09:00+00:00",
        }
    )
    result = store.merge_production_state(produced_snapshot)
    produced, _ = store.find(approved.approved_script_id)

    assert result["advanced"] == 1
    assert produced.status == "produced"
    assert produced.youtube_url == "https://youtu.be/youtube-123"

    stale_result = store.merge_production_state(snapshot)
    still_produced, _ = store.find(approved.approved_script_id)

    assert stale_result["regressions_ignored"] == 1
    assert still_produced.status == "produced"


def test_remote_production_state_hash_mismatch_is_atomic(tmp_path) -> None:
    store = ApprovedScriptStore(tmp_path / "jobs")
    first = _approved_script()
    second = first.model_copy(
        update={
            "approved_script_id": "approved-segundo-12345678",
            "label": "Segundo roteiro",
        }
    )
    second = second.model_copy(update={"content_hash": approved_content_hash(second)})
    store.save(first)
    store.save(second)
    for script in (first, second):
        store.approve(
            script.approved_script_id,
            confirm_hash=script.content_hash,
            approved_by="Pedro",
        )
    snapshot = store.production_state_snapshot()
    for item in snapshot["items"]:
        item["status"] = "reserved"
        item["reserved_by_job_id"] = "video-20260723-120000-12345678"
    snapshot["items"][1]["content_hash"] = "0" * 64

    with pytest.raises(RuntimeError, match="Hash divergente"):
        store.merge_production_state(snapshot)

    assert len(store.list(status="approved")) == 2
    assert store.list(status="reserved") == []
    assert not store.remote_state_path.exists()


def test_cta_can_be_replaced_before_hash_approval(tmp_path) -> None:
    store = ApprovedScriptStore(tmp_path / "jobs")
    draft = _approved_script()
    store.save(draft)

    updated = store.select_cta(
        draft.approved_script_id,
        option_id="compartilhar",
        spoken_text="Compartilhe com alguém que também faria essa pergunta.",
        mode="share",
    )

    assert updated.selected_cta_id == "compartilhar"
    assert updated.content_hash != draft.content_hash
    assert "Compartilhe com alguém" in updated.script.narration_text()
    assert "Comente LIBERDADE" not in updated.script.narration_text()
    with pytest.raises(ValueError, match="hash"):
        store.approve(
            updated.approved_script_id,
            confirm_hash=draft.content_hash,
            approved_by="Pedro",
        )


def test_seo_is_frozen_with_three_specific_and_two_standard_hashtags(tmp_path) -> None:
    store = ApprovedScriptStore(tmp_path / "jobs")
    draft = _approved_script()
    store.save(draft)

    updated = store.set_seo(
        draft.approved_script_id,
        title="Por que Jesus manteve Judas entre os doze? | João 6:70-71",
        description=(
            "Jesus sabia da futura traição de Judas. Entenda como conhecimento, escolha "
            "e responsabilidade aparecem nessa dúvida bíblica."
        ),
        specific_hashtags=["Judas Iscariotes", "Livre Arbítrio", "Traição de Judas"],
    )

    assert updated.metadata is not None
    assert updated.metadata.title.startswith("Por que Jesus manteve Judas")
    assert updated.metadata.description.endswith(
        "#JudasIscariotes #LivreArbitrio #TraicaoJudas #biblia #jesus"
    )
    assert 10 <= len(updated.metadata.topics) <= 12
    assert all("#" not in tag for tag in updated.metadata.topics)
    assert updated.content_hash != draft.content_hash
    assert len(updated.metadata.description.split("#")) == 6


def test_approved_script_materialization_uses_script_locked_duration(
    tmp_path,
    monkeypatch,
) -> None:
    import pipeline_videos.script as script_module

    monkeypatch.setattr(
        script_module,
        "validate_script_editorial_policy",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("A produção pré-aprovada não pode revalidar o roteiro.")
        ),
    )
    draft = _approved_script()
    narration_parts = [
        "Eu sabia que Judas me trairia e ainda assim o mantive entre os doze.",
        (
            'Em João, capítulo 6, versículo 64, lemos: "Jesus sabia, desde o '
            'princípio, quem o havia de trair."'
        ),
        'Comente "LIBERDADE" nos comentários.',
    ]
    narration = " ".join(narration_parts)
    script = ScriptDocument(
        title=draft.script.title,
        sections=[
            ScriptSection(
                section_id="opening",
                role="opening",
                title="A pergunta",
                narration=narration,
                biblical_basis=["João 6:64"],
                claim_classification=["biblical_text"],
            )
        ],
    )
    visual_scenes = [
        NarrationVisualScene(
            section_id="opening",
            narration=narration_parts[0],
            scene_description="Jesus observa Judas entre os doze.",
            visual_relation="contextual",
        ),
        NarrationVisualScene(
            section_id="opening",
            narration=narration_parts[1],
            scene_description="Judas permanece sentado à mesa com os discípulos.",
            visual_relation="contextual",
        ),
        NarrationVisualScene(
            section_id="opening",
            narration=narration_parts[2],
            scene_description="Uma Bíblia aberta passa de uma pessoa para outra.",
            visual_relation="symbolic",
        ),
    ]
    short_shell = draft.model_copy(
        update={
            "target_duration_seconds": 8,
            "briefing": draft.briefing.model_copy(
                update={
                    "main_passage": "João 6:64",
                    "central_theme": "Jesus conhecia a futura traição de Judas.",
                    "viewer_outcome": "Entender conhecimento prévio e responsabilidade.",
                }
            ),
            "script": script,
            "sources": SourceLibrary(
                entries=[
                    SourceEntry(
                        source_id="joao-6-64",
                        kind="biblical_text",
                        title="João 6:64",
                        passage="João 6:64",
                        excerpt="Jesus sabia, desde o princípio, quem o havia de trair.",
                        use_in_video="Citação literal",
                        claim_scope="Conhecimento prévio de Jesus",
                        verified=True,
                    )
                ]
            ),
            "strategy": draft.strategy.model_copy(
                update={
                    "visual_scenes": visual_scenes,
                    "cta": 'Comente "LIBERDADE" nos comentários.',
                }
            ),
            "cta_options": [
                ApprovedCtaOption(
                    option_id="liberdade",
                    spoken_text='Comente "LIBERDADE" nos comentários.',
                    mode="comment",
                )
            ],
        }
    )
    short = ApprovedShortScript.model_validate(
        short_shell.model_copy(update={"content_hash": approved_content_hash(short_shell)})
    )

    layout = materialize_approved_project(
        short,
        job_id="video-20260723-120000-12345678",
        projects_root=tmp_path / "projects",
        publication_mode="private_review",
        voice_id="voice-test",
        voice_model_id="model-test",
    )
    manifest = layout.load_manifest()

    assert manifest.target_duration_seconds == 8
    assert manifest.duration_policy == "approved_script_locked_v1"
    assert manifest.requested_duration_min_seconds is None
    assert manifest.requested_duration_max_seconds is None
    assert manifest.config.shorts_script.minimum_audio_duration_ratio == 0

    from pipeline_videos.metadata import build_metadata

    locked_metadata = layout.metadata.read_bytes()
    build_metadata(layout)
    assert layout.metadata.read_bytes() == locked_metadata

    from pipeline_videos.autopilot import prepare_local_coordinator_job

    coordinator = prepare_local_coordinator_job(layout)
    assert set(coordinator["external_roles"]) == {"image_workers"}


def test_private_daily_schedule_consumes_only_approved_pool_idempotently(tmp_path) -> None:
    job_store = RemoteJobStore(tmp_path / "jobs")
    approved_store = ApprovedScriptStore(job_store.root)
    draft = _approved_script()
    approved_store.save(draft)
    approved_store.approve(
        draft.approved_script_id,
        confirm_hash=draft.content_hash,
        approved_by="Pedro",
    )
    schedule_store = RemoteScheduleStore(job_store.root)
    schedule_store.create(
        name="Fila aprovada",
        slots=[DailyShortSlot(production_time=time(2, 0))],
        script_source="approved_pool",
        publication_mode="private_review",
        external_services_authorized=True,
        public_scheduling_authorized=False,
    )
    now = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)

    first = materialize_daily_schedules(
        store=job_store,
        schedule_store=schedule_store,
        approved_store=approved_store,
        now=now,
    )
    second = materialize_daily_schedules(
        store=job_store,
        schedule_store=schedule_store,
        approved_store=approved_store,
        now=now,
    )

    assert len(first) == 1
    assert [job.job_id for job in second] == [first[0].job_id]
    assert first[0].request.production_profile == "approved_fast_v1"
    assert first[0].request.publication_mode == "private_review"
    assert first[0].request.publish_at is None
    assert first[0].request.approved_script_id == draft.approved_script_id
    assert approved_store.list(status="approved") == []
    assert len(approved_store.list(status="reserved")) == 1

    empty_day = materialize_daily_schedules(
        store=job_store,
        schedule_store=schedule_store,
        approved_store=approved_store,
        now=now + timedelta(days=1),
    )
    persisted_schedule = schedule_store.list()[0]
    assert empty_day == []
    assert "esgotada" in (persisted_schedule.last_depletion_reason or "")
