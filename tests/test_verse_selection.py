import subprocess
from collections import Counter
from datetime import date

import pytest

from pipeline_videos.creative_direction import hook_matches_family
from pipeline_videos.models import (
    USER_SIX_HOOK_FAMILIES,
    ApprovalStatus,
    Briefing,
    SourceLibrary,
    StageStatus,
    VerseCandidate,
    VerseCatalog,
    VerseUsageIndex,
    VerseUsageRecord,
)
from pipeline_videos.project import compact_narration_word_range, short_literal_verse_word_limit
from pipeline_videos.shorts_script import spoken_biblical_reference
from pipeline_videos.storage import read_json, read_model, write_json
from pipeline_videos.utils import tokenize_words
from pipeline_videos.verse_selection import (
    DEFAULT_CURATED_VERSE_BANK,
    DEFAULT_NARRATIVE_CHARACTER_BANK,
    build_usage_index,
    catalog_capacity_summary,
    compact_hook_editorial_features,
    compact_package_word_count,
    create_auto_short,
    create_auto_video,
    eligible_main_candidate_ids,
    load_verse_catalog,
    merge_usage_indexes,
    recent_compact_hook_count,
    reference_tokens,
    references_overlap,
    reserve_project_candidates,
    select_creative_mode,
    select_hook_family,
    select_retention_archetype,
    select_unused_verse,
)


def test_compact_hook_prior_prefers_concrete_tension_over_generic_imperative() -> None:
    generic = compact_hook_editorial_features(
        "Olhe para mim. Você precisa lembrar quem está diante de você."
    )
    concrete = compact_hook_editorial_features(
        "Antes de ceder ao medo, lembre-se de quem segura você."
    )

    assert generic["generic_opener"] is True
    assert generic["has_tension"] is False
    assert concrete["has_situation"] is True
    assert concrete["has_tension"] is True
    assert concrete["quality"] > generic["quality"]
    assert concrete["concreteness"] >= 0.7


def test_compact_selector_penalizes_the_persisted_hook_not_a_catalog_guess() -> None:
    base = next(
        candidate
        for candidate in load_verse_catalog().candidates
        if candidate.compact_hook and candidate.spoken_by == "jesus"
    )
    first_hook = "Antes de ceder ao medo, lembre-se de quem segura você."
    second_hook = "Antes de ceder à culpa, lembre-se de quem chama você."
    first = base.model_copy(
        update={
            "candidate_id": "first-hook",
            "source_id": "first-hook",
            "reference": "Mateus 99:1",
            "context_reference": "Mateus 99:1",
            "compact_hook": first_hook,
        }
    )
    second = base.model_copy(
        update={
            "candidate_id": "second-hook",
            "source_id": "second-hook",
            "reference": "João 99:1",
            "context_reference": "João 99:1",
            "compact_hook": second_hook,
        }
    )
    catalog = VerseCatalog(
        catalog_id="hook-repeat-test",
        translation=base.translation,
        publisher="fixture",
        source_url="https://example.invalid",
        rights_note=base.rights_note,
        accessed_at=base.accessed_at,
        candidates=[first, second],
    )
    usage = VerseUsageIndex(
        generated_at="2026-07-21T12:00:00Z",
        records=[
            VerseUsageRecord(
                project_id="previous-compact",
                project_path="shorts/previous-compact",
                video_format="short",
                duration_profile="adaptive_compact_v1",
                status="used",
                opening_hook=first_hook,
                created_at="2026-07-21T11:00:00Z",
            )
        ],
    )

    selected, _score = select_unused_verse(
        catalog,
        usage,
        video_format="short",
        minimum_package_words=1,
        maximum_package_words=200,
    )

    assert selected.candidate_id == "second-hook"


def test_usage_merge_preserves_reservation_fields_until_real_values_replace_them() -> None:
    shared_record = VerseUsageRecord.model_validate(
        {
            "registry_id": "prod:compact-01",
            "environment": "prod",
            "project_id": "compact-01",
            "project_path": "shorts/compact-01",
            "candidate_id": "reserved-candidate",
            "video_format": "short",
            "duration_profile": "adaptive_compact_v1",
            "status": "reserved",
            "main_passage": "João 14:1-2",
            "opening_hook": "Antes de ceder ao medo, lembre-se de quem segura você.",
            "created_at": "2026-07-21T10:00:00Z",
            "future_registry_field": "preserve-me",
        }
    )
    incomplete_local = VerseUsageRecord(
        registry_id="prod:compact-01",
        environment="prod",
        project_id="compact-01",
        project_path="shorts/compact-01",
        video_format="short",
        status="reserved",
        created_at="2026-07-21T10:01:00Z",
    )

    preserved = merge_usage_indexes(
        VerseUsageIndex(generated_at="2026-07-21T10:00:00Z", records=[shared_record]),
        VerseUsageIndex(generated_at="2026-07-21T10:01:00Z", records=[incomplete_local]),
    ).records[0]

    assert preserved.candidate_id == "reserved-candidate"
    assert preserved.main_passage == "João 14:1-2"
    assert preserved.opening_hook == shared_record.opening_hook
    assert preserved.duration_profile == "adaptive_compact_v1"
    assert preserved.model_extra["future_registry_field"] == "preserve-me"

    completed_local = incomplete_local.model_copy(
        update={
            "candidate_id": "used-candidate",
            "main_passage": "João 15:1-2",
            "opening_hook": "Uma nova abertura concreta.",
            "status": "used",
        }
    )
    updated = merge_usage_indexes(
        VerseUsageIndex(generated_at="2026-07-21T10:00:00Z", records=[preserved]),
        VerseUsageIndex(generated_at="2026-07-21T10:02:00Z", records=[completed_local]),
    ).records[0]

    assert updated.status == "used"
    assert updated.candidate_id == "used-candidate"
    assert updated.main_passage == "João 15:1-2"
    assert updated.opening_hook == "Uma nova abertura concreta."


def test_usage_merge_preserves_forward_compatible_envelope_fields() -> None:
    shared = VerseUsageIndex.model_validate(
        {
            "generated_at": "2026-07-21T10:00:00Z",
            "records": [],
            "future_envelope_field": {"revision": 2},
        }
    )
    incomplete_local = VerseUsageIndex.model_validate(
        {
            "generated_at": "2026-07-21T10:01:00Z",
            "records": [],
            "future_envelope_field": {},
            "local_registry_hint": "projects/shorts",
        }
    )

    merged = merge_usage_indexes(shared, incomplete_local)

    assert merged.model_extra["future_envelope_field"] == {"revision": 2}
    assert merged.model_extra["local_registry_hint"] == "projects/shorts"


def test_compact_reservation_persists_duration_profile(tmp_path) -> None:
    candidate = next(
        item for item in load_verse_catalog().candidates if item.catalog_segment == "compact_jesus"
    )

    usage = reserve_project_candidates(
        tmp_path,
        project_id="compact-reservation",
        video_format="short",
        candidates=[candidate],
        duration_profile="adaptive_compact_v1",
    )
    record = next(item for item in usage.records if item.project_id == "compact-reservation")

    assert record.duration_profile == "adaptive_compact_v1"
    assert record.opening_hook == candidate.compact_hook


def test_recent_compact_hook_window_ignores_incomplete_reservations() -> None:
    target_hook = "Antes de ceder ao medo, lembre-se de quem segura você."
    records = [
        VerseUsageRecord(
            project_id="blank-reservation",
            project_path="shorts/blank-reservation",
            video_format="short",
            status="reserved",
            created_at="2026-07-21T12:10:00Z",
        )
    ]
    records.extend(
        VerseUsageRecord(
            project_id=f"compact-{index}",
            project_path=f"shorts/compact-{index}",
            video_format="short",
            duration_profile="adaptive_compact_v1",
            status="used",
            opening_hook=target_hook if index == 7 else f"Hook compacto distinto {index}",
            created_at=f"2026-07-21T12:0{9 - index}:00Z",
        )
        for index in range(8)
    )
    usage = VerseUsageIndex(generated_at="2026-07-21T12:11:00Z", records=records)

    assert recent_compact_hook_count(usage, target_hook, limit=8) == 1


def test_shared_git_registry_prevents_repetition_between_project_roots(
    tmp_path, monkeypatch
) -> None:
    remote = tmp_path / "registry.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(remote)],
        capture_output=True,
        check=True,
        shell=False,
    )
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "git")
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_REMOTE", str(remote))
    monkeypatch.setenv(
        "PIPELINE_VIDEOS_CONTENT_REGISTRY_REF",
        "refs/heads/test-content-registry",
    )
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_ENVIRONMENT", "test-host")

    first, first_candidate, _ = create_auto_short(
        projects_dir=tmp_path / "local-projects",
        project_date=date(2026, 7, 14),
    )
    second, second_candidate, _ = create_auto_short(
        projects_dir=tmp_path / "remote-projects",
        project_date=date(2026, 7, 14),
    )

    assert first_candidate.reference != second_candidate.reference
    assert first.root.parent.parent != second.root.parent.parent
    registry = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "show",
            "test-content-registry:content-registry.json",
        ],
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    ).stdout
    usage = VerseUsageIndex.model_validate_json(registry)
    assert {item.project_id for item in usage.records} == {
        first.root.name,
        second.root.name,
    }


def test_reference_normalization_expands_ranges_and_chapters() -> None:
    assert reference_tokens("1 Samuel 17:32,37,45-47") == {
        "1-samuel|17|32",
        "1-samuel|17|37",
        "1-samuel|17|45",
        "1-samuel|17|46",
        "1-samuel|17|47",
    }
    assert reference_tokens("Salmo 56") == {"salmos|56|*"}
    assert references_overlap("Salmos 56", "Salmo 56:3")
    assert references_overlap("Josué 1:1-9", "Josue 1:9")
    assert not references_overlap("João 14:27", "João 8:12")
    assert reference_tokens("Levítico 19:18") == {"levitico|19|18"}
    assert reference_tokens("2 Crônicas 20:12-13") == {
        "2-cronicas|20|12",
        "2-cronicas|20|13",
    }
    assert reference_tokens("3 João 1:4") == {"3-joao|1|4"}


def test_auto_short_reserves_different_verses_and_populates_verified_source(tmp_path) -> None:
    first, first_candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        target_duration_seconds=55,
        project_date=date(2026, 7, 14),
    )
    second, second_candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        target_duration_seconds=55,
        project_date=date(2026, 7, 14),
    )

    assert first_candidate.reference != second_candidate.reference
    briefing = read_model(first.briefing, Briefing)
    sources = read_model(first.sources, SourceLibrary)
    manifest = first.load_manifest()
    assert briefing.main_passage == first_candidate.reference
    assert briefing.bible_translation == "Bíblia Portuguesa Mundial (BPM)"
    assert "exatamente uma ação" in briefing.call_to_action
    assert briefing.retention_archetype in first_candidate.retention_archetypes
    assert briefing.human_conflict == first_candidate.human_conflict
    assert briefing.central_question == first_candidate.central_question
    assert briefing.visual_progression == first_candidate.visual_progression
    assert briefing.possible_reveal == first_candidate.possible_reveal
    assert briefing.title_seed == first_candidate.title_seed
    assert briefing.creative_mode != "auto"
    assert briefing.hook_policy == "required"
    assert briefing.hook_contract == "user_six_v1"
    assert briefing.hook_family in USER_SIX_HOOK_FAMILIES
    assert briefing.viewer_outcome
    assert sources.entries[0].verified is True
    assert sources.entries[0].excerpt == first_candidate.verse_text
    assert "Domínio público" in (sources.entries[0].rights_note or "")
    assert manifest.video_format == "short"
    assert manifest.stages["briefing"].status == StageStatus.COMPLETED
    assert manifest.approvals["briefing"].status == ApprovalStatus.PENDING
    assert manifest.config.shorts_script.narrative_standard == "creative_retention_v3"
    assert manifest.config.youtube_growth.title_standard == "creative_pipe_passage_v3"
    assert manifest.config.images.visual_standard_id == "biblical-midcentury-gouache-v1"
    assert manifest.config.images.scene_planning_standard == "narration_block_visual_v1"
    assert manifest.config.youtube_growth.thumbnail_generation_mode == "youtube_auto_frame_v1"
    assert (tmp_path / "verse-usage.json").is_file()
    assert first.root != second.root


def test_auto_micro_short_selects_a_verse_that_fits_fifteen_seconds(tmp_path) -> None:
    layout, candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        target_duration_seconds=15,
        project_date=date(2026, 7, 20),
    )

    config = layout.load_manifest().config.shorts_script
    briefing = read_model(layout.briefing, Briefing)
    source = read_model(layout.sources, SourceLibrary).entries[0]
    assert config.duration_profile == "adaptive_compact_v1"
    assert len(candidate.verse_text.split()) <= config.max_literal_verse_words
    assert candidate.spoken_by == "jesus"
    assert candidate.candidate_id != "bpm-lucas-6-31"
    assert config.min_words <= compact_package_word_count(candidate) <= config.max_words
    assert candidate.compact_hook
    assert candidate.compact_hook_strength_score >= config.minimum_hook_strength_score
    assert candidate.compact_message_value_score >= config.minimum_message_value_score
    assert candidate.compact_rewatch_value_score >= config.minimum_rewatch_value_score
    assert source.excerpt == candidate.verse_text
    assert source.passage == candidate.reference
    assert source.context_passage == candidate.context_reference
    assert source.spoken_by == "jesus"
    assert source.compact_hook == candidate.compact_hook
    assert briefing.creative_mode in {"devotional_application", "pastoral_encouragement"}
    assert briefing.hook_family == "emotional_interruption"
    assert briefing.hook_contract == "cinematic_interruption_v1"
    assert briefing.call_to_action == ""


def test_compact_catalog_contains_clean_direct_speech_that_fits_the_contract(tmp_path) -> None:
    layout, _, _ = create_auto_short(
        projects_dir=tmp_path,
        target_duration_seconds=15,
        project_date=date(2026, 7, 20),
    )
    config = layout.load_manifest().config.shorts_script
    direct_speech = [
        candidate
        for candidate in load_verse_catalog().candidates
        if candidate.spoken_by == "jesus"
        and len(tokenize_words(candidate.verse_text)) <= config.max_literal_verse_words
        and candidate.compact_hook_strength_score >= config.minimum_hook_strength_score
        and candidate.compact_message_value_score >= config.minimum_message_value_score
        and candidate.compact_rewatch_value_score >= config.minimum_rewatch_value_score
        and config.min_words <= compact_package_word_count(candidate) <= config.max_words
    ]

    assert len(direct_speech) >= 4
    for candidate in direct_speech:
        assert not candidate.verse_text.startswith(("“", '"'))
        assert not candidate.verse_text.endswith(("”", '"'))
        assert not candidate.verse_text.casefold().startswith(
            ("jesus lhe disse", "jesus lhes falou", "novamente, portanto, jesus")
        )
        narration = (
            f"{candidate.compact_hook} “{candidate.verse_text}” "
            f"{spoken_biblical_reference(candidate.reference)}."
        )
        assert config.min_words <= len(tokenize_words(narration)) <= config.max_words

    lucas = next(
        candidate
        for candidate in load_verse_catalog().candidates
        if candidate.candidate_id == "bpm-lucas-6-31"
    )
    assert compact_package_word_count(lucas) < config.min_words


def test_selector_prefers_short_familiar_clear_verse_when_other_factors_match() -> None:
    common = {
        "source_id": "source",
        "book": "Salmos",
        "testament": "old",
        "translation": "BPM",
        "source_url": "https://example.invalid",
        "accessed_at": "2026-07-14",
        "rights_note": "Domínio público",
        "title": "Título",
        "central_theme": "Tema",
        "practical_application": "Aplicação",
    }
    concise = VerseCandidate(
        **common,
        candidate_id="concise",
        reference="Salmos 1:1",
        context_reference="Salmos 1:1",
        verse_text="Confie em Deus hoje.",
        slug="concise",
        familiarity_score=0.95,
        clarity_score=0.95,
    )
    long_obscure = VerseCandidate(
        **common,
        candidate_id="long",
        reference="Salmos 2:1",
        context_reference="Salmos 2:1",
        verse_text=" ".join(["palavra"] * 36),
        slug="long",
        familiarity_score=0.2,
        clarity_score=0.2,
    )
    catalog = VerseCatalog(
        catalog_id="test",
        translation="BPM",
        publisher="Teste",
        source_url="https://example.invalid",
        rights_note="Domínio público",
        accessed_at="2026-07-14",
        candidates=[long_obscure, concise],
    )

    selected, _ = select_unused_verse(catalog, VerseUsageIndex(generated_at="now"))

    assert selected.candidate_id == "concise"


def test_expansion_bank_is_loaded_with_verified_unique_candidates() -> None:
    catalog = load_verse_catalog()

    assert len(catalog.candidates) >= 70
    assert len({candidate.candidate_id for candidate in catalog.candidates}) == len(
        catalog.candidates
    )
    assert all(candidate.verse_text.strip() for candidate in catalog.candidates)
    assert all("Domínio público" in candidate.rights_note for candidate in catalog.candidates)


def test_curated_multiformat_bank_has_the_promised_400_candidates() -> None:
    bank = read_model(DEFAULT_CURATED_VERSE_BANK, VerseCatalog)
    counts = Counter(candidate.catalog_segment for candidate in bank.candidates)

    assert len(bank.candidates) == 400
    assert counts == {
        "compact_jesus": 120,
        "general": 180,
        "long_form_anchor": 100,
    }
    assert len({candidate.candidate_id for candidate in bank.candidates}) == 400
    assert len(bank.source_artifact_sha256) == 64
    assert bank.source_revision == "2026-04-25"
    assert {candidate.testament for candidate in bank.candidates} == {"old", "new"}

    compact = [
        candidate for candidate in bank.candidates if candidate.catalog_segment == "compact_jesus"
    ]
    assert all(candidate.spoken_by == "jesus" for candidate in compact)
    assert all(candidate.source_text_scope == "direct_speech_excerpt" for candidate in compact)
    assert all(not candidate.reference.startswith("João 17:") for candidate in compact)
    assert all(candidate.themes[0] != "kingdom" for candidate in compact)
    assert all(10 <= len(tokenize_words(candidate.compact_hook)) <= 12 for candidate in compact)
    assert all(
        hook_matches_family(candidate.compact_hook, "emotional_interruption")
        for candidate in compact
    )
    assert all(candidate.compact_hook_strength_score >= 0.9 for candidate in compact)
    assert all(candidate.compact_message_value_score >= 0.9 for candidate in compact)
    assert all(candidate.compact_rewatch_value_score >= 0.88 for candidate in compact)
    assert all("short" in candidate.recommended_formats for candidate in compact)
    assert all(
        compact_package_word_count(candidate)
        == len(
            tokenize_words(
                f"{candidate.compact_hook} {candidate.verse_text} "
                f"{spoken_biblical_reference(candidate.reference)}"
            )
        )
        for candidate in compact
    )
    blocked_fragments = (
        "raça de víboras",
        "espírito imundo",
        "ranger de dentes",
        "um homem, dono de uma casa",
    )
    assert all(
        not any(fragment in candidate.verse_text.casefold() for fragment in blocked_fragments)
        for candidate in compact
    )

    long_form = [
        candidate
        for candidate in bank.candidates
        if candidate.catalog_segment == "long_form_anchor"
    ]
    assert all(candidate.recommended_formats == ["long"] for candidate in long_form)
    assert len({candidate.book for candidate in long_form}) >= 60
    assert len({candidate.book for candidate in bank.candidates}) == 66


def test_narrative_character_bank_has_verified_story_packages() -> None:
    bank = read_model(DEFAULT_NARRATIVE_CHARACTER_BANK, VerseCatalog)

    assert len(bank.candidates) == 81
    assert len({character for item in bank.candidates for character in item.characters}) == 88
    assert sum(1 + len(item.related_passages) for item in bank.candidates) == 324
    assert len(bank.source_artifact_sha256) == 64
    assert bank.source_revision == "2026-04-25"
    assert all(item.catalog_segment == "narrative_story" for item in bank.candidates)
    assert all(item.source_text_scope == "pericope_excerpt" for item in bank.candidates)
    assert all(item.recommended_formats == ["short", "long"] for item in bank.candidates)
    assert all(len(item.related_passages) >= 3 for item in bank.candidates)
    assert all(
        passage.verse_text.strip() and passage.source_url.startswith("https://ebible.org/")
        for item in bank.candidates
        for passage in item.related_passages
    )


def test_narrative_candidates_are_opt_in_and_aliases_select_the_story() -> None:
    catalog = load_verse_catalog()
    usage = VerseUsageIndex(generated_at="2026-07-21T00:00:00Z")
    default_ids = eligible_main_candidate_ids(catalog, usage, video_format="landscape")
    narrative_ids = {
        item.candidate_id
        for item in catalog.candidates
        if item.catalog_segment == "narrative_story"
    }

    assert len(narrative_ids) == 81
    assert default_ids.isdisjoint(narrative_ids)

    selected, _ = select_unused_verse(
        catalog,
        usage,
        video_format="landscape",
        requested_character="mulher siro-fenícia",
    )

    assert selected.candidate_id == "bpm-narrative-mulher-cananeia"
    assert selected.context_reference == "Mateus 15:21-28"
    assert {item.reference for item in selected.related_passages} == {
        "Mateus 15:24-28",
        "Marcos 7:24-26",
        "Marcos 7:27-30",
    }


def test_character_project_receives_related_sources_and_portrait_mode(tmp_path) -> None:
    layout, candidate, _ = create_auto_video(
        projects_dir=tmp_path,
        target_duration_seconds=540,
        video_format="landscape",
        project_date=date(2026, 7, 21),
        requested_character="Judas",
    )
    briefing = read_model(layout.briefing, Briefing)
    sources = read_model(layout.sources, SourceLibrary)

    assert candidate.candidate_id == "bpm-narrative-judas-iscariotes"
    assert briefing.creative_mode == "character_portrait"
    assert briefing.main_passage == "Mateus 26:14-16"
    assert briefing.complementary_references == [
        "João 13:21-30",
        "Mateus 26:47-50",
        "Mateus 27:3-5",
    ]
    assert len(sources.entries) == 4
    assert all(source.verified and source.excerpt for source in sources.entries)


def test_compact_short_rejects_character_story_before_selection(tmp_path) -> None:
    with pytest.raises(ValueError, match="não entram no perfil compacto"):
        create_auto_short(
            projects_dir=tmp_path,
            target_duration_seconds=20,
            requested_character="Pedro",
        )


def test_compact_bank_covers_every_duration_from_15_to_20_seconds() -> None:
    candidates = [
        candidate
        for candidate in load_verse_catalog().candidates
        if candidate.catalog_segment == "compact_jesus"
    ]

    for duration in range(15, 21):
        minimum, maximum = compact_narration_word_range(duration)
        verse_limit = short_literal_verse_word_limit(duration)
        assert verse_limit is not None
        eligible = [
            candidate
            for candidate in candidates
            if minimum <= compact_package_word_count(candidate) <= maximum
            and len(tokenize_words(candidate.verse_text)) <= verse_limit
        ]
        assert len(eligible) >= 20, duration


def test_long_only_candidates_are_not_eligible_as_short_main_passages() -> None:
    catalog = load_verse_catalog()
    usage = VerseUsageIndex(generated_at="2026-07-20T00:00:00Z")
    short_ids = eligible_main_candidate_ids(catalog, usage, video_format="short")
    long_only_ids = {
        candidate.candidate_id
        for candidate in catalog.candidates
        if candidate.recommended_formats == ["long"]
    }

    assert long_only_ids
    assert short_ids.isdisjoint(long_only_ids)


def test_main_passage_eligibility_is_scoped_by_format_and_ignores_supporting_refs() -> None:
    catalog = load_verse_catalog()
    first, second = catalog.candidates[:2]
    usage = VerseUsageIndex(
        generated_at="2026-07-17T00:00:00Z",
        records=[
            VerseUsageRecord(
                project_id="long-1",
                project_path="videos-longos/long-1",
                video_format="landscape",
                status="used",
                main_passage=first.reference,
                references=[first.reference, second.reference],
                supporting_references=[second.reference],
                created_at="2026-07-17T00:00:00Z",
            )
        ],
    )

    short_eligible = eligible_main_candidate_ids(catalog, usage, video_format="short")
    long_eligible = eligible_main_candidate_ids(catalog, usage, video_format="landscape")

    assert first.candidate_id in short_eligible
    assert second.candidate_id in short_eligible
    assert first.candidate_id not in long_eligible
    assert second.candidate_id in long_eligible


def test_selector_starts_new_cycle_after_all_main_passages_were_used() -> None:
    candidate = load_verse_catalog().candidates[0]
    catalog = VerseCatalog(
        catalog_id="single",
        translation=candidate.translation,
        publisher="eBible.org",
        source_url="https://example.invalid",
        rights_note=candidate.rights_note,
        accessed_at=candidate.accessed_at,
        candidates=[candidate],
    )
    usage = VerseUsageIndex(
        generated_at="2026-07-17T00:00:00Z",
        records=[
            VerseUsageRecord(
                project_id="short-old",
                project_path="shorts/short-old",
                video_format="short",
                status="used",
                main_passage=candidate.reference,
                references=[candidate.reference],
                created_at="2026-01-01T00:00:00Z",
            )
        ],
    )

    selected, _ = select_unused_verse(catalog, usage, video_format="short")
    capacity = catalog_capacity_summary(catalog, usage, video_format="short")

    assert selected.candidate_id == candidate.candidate_id
    assert capacity["selection_cycle"] == 2
    assert capacity["available_in_cycle"] == 1


def test_selector_never_reuses_main_passage_reserved_by_active_job() -> None:
    candidate = load_verse_catalog().candidates[0]
    catalog = VerseCatalog(
        catalog_id="single",
        translation=candidate.translation,
        publisher="eBible.org",
        source_url="https://example.invalid",
        rights_note=candidate.rights_note,
        accessed_at=candidate.accessed_at,
        candidates=[candidate],
    )
    usage = VerseUsageIndex(
        generated_at="2026-07-17T00:00:00Z",
        records=[
            VerseUsageRecord(
                project_id="short-active",
                project_path="shorts/short-active",
                video_format="short",
                status="reserved",
                main_passage=candidate.reference,
                references=[candidate.reference],
                created_at="2026-07-17T00:00:00Z",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="reservadas por jobs ativos"):
        select_unused_verse(catalog, usage, video_format="short")


def test_retention_archetype_does_not_repeat_either_of_last_two_shorts() -> None:
    candidate = VerseCandidate(
        candidate_id="candidate",
        source_id="source",
        reference="Salmos 1:1",
        context_reference="Salmos 1:1",
        book="Salmos",
        testament="old",
        translation="BPM",
        verse_text="Confie em Deus hoje.",
        source_url="https://example.invalid",
        accessed_at="2026-07-16",
        rights_note="Domínio público",
        title="Título",
        slug="candidate",
        central_theme="Tema",
        practical_application="Aplicação",
        retention_archetypes=["conflict_first", "counterintuitive_reveal"],
    )
    usage = VerseUsageIndex(
        generated_at="2026-07-16T12:00:00Z",
        records=[
            VerseUsageRecord(
                project_id="first",
                project_path="shorts/first",
                video_format="short",
                status="used",
                retention_archetype="conflict_first",
                created_at="2026-07-16T10:00:00Z",
            ),
            VerseUsageRecord(
                project_id="second",
                project_path="shorts/second",
                video_format="short",
                status="used",
                retention_archetype="counterintuitive_reveal",
                created_at="2026-07-16T11:00:00Z",
            ),
        ],
    )

    selected = select_retention_archetype(candidate, usage)

    assert selected == "stair_step_progression"


def test_creative_selector_avoids_recent_modes_and_alternates_optional_long_hook() -> None:
    candidate = VerseCandidate(
        candidate_id="creative-candidate",
        source_id="creative-source",
        reference="1 Samuel 17:45",
        context_reference="1 Samuel 17:40-47",
        book="1 Samuel",
        testament="old",
        translation="BPM",
        verse_text="Eu vou contra ti em nome do Senhor dos Exércitos.",
        source_url="https://example.invalid",
        accessed_at="2026-07-19",
        rights_note="Domínio público",
        title="Davi diante de Golias",
        slug="davi-golias-criativo",
        central_theme="A sequência das decisões de Davi",
        practical_application="Agir com coragem.",
        characters=["Davi", "Golias"],
        preferred_development_modes=["biblical_story"],
        possible_reveal="A decisão precede o confronto.",
    )
    recent_modes = [
        "biblical_storytelling",
        "character_portrait",
        "chronological_reconstruction",
        "cause_and_consequence",
    ]
    usage = VerseUsageIndex(
        generated_at="2026-07-19T12:00:00Z",
        records=[
            VerseUsageRecord(
                project_id=f"long-{index}",
                project_path=f"videos-longos/long-{index}",
                video_format="landscape",
                status="used",
                creative_mode=mode,
                hook_family="none" if index == 4 else "start_in_scene",
                created_at=f"2026-07-19T0{index}:00:00Z",
            )
            for index, mode in enumerate(recent_modes, start=1)
        ],
    )

    selected_mode = select_creative_mode(candidate, usage, video_format="landscape")
    selected_hook = select_hook_family(
        "biblical_storytelling",
        usage,
        video_format="landscape",
        hook_policy="optional",
    )

    assert selected_mode not in set(recent_modes)
    assert selected_hook == "consequence_first"


def test_usage_scan_includes_complementary_sources_and_script_basis(tmp_path) -> None:
    layout, candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        project_date=date(2026, 7, 14),
    )
    briefing = read_model(layout.briefing, Briefing)
    briefing.complementary_references = ["Romanos 12:21"]
    write_json(layout.briefing, briefing)
    source_data = read_model(layout.sources, SourceLibrary)
    source_data.entries[0].passage = "Mateus 5:9"
    write_json(layout.sources, source_data)
    write_json(
        layout.script,
        {
            "version": 1,
            "language": "pt-BR",
            "title": "Teste",
            "sections": [
                {
                    "section_id": "opening",
                    "role": "opening",
                    "title": "Abertura",
                    "narration": "Texto",
                    "biblical_basis": ["João 14:27"],
                    "claim_classification": ["biblical_text"],
                }
            ],
        },
    )

    usage = build_usage_index(tmp_path)
    record = next(item for item in usage.records if item.project_id == layout.root.name)
    assert record.status == "used"
    assert "Romanos 12:21" in record.references
    assert "Mateus 5:9" in record.references
    assert "João 14:27" in record.references

    catalog = load_verse_catalog()
    selected, _ = select_unused_verse(catalog, usage)
    assert record.supporting_references
    assert selected.reference != candidate.reference


def test_usage_scan_accepts_legacy_manifest_fields(tmp_path) -> None:
    layout, candidate, _ = create_auto_short(
        projects_dir=tmp_path,
        project_date=date(2026, 7, 14),
    )
    manifest = read_json(layout.manifest)
    manifest["config"]["production"]["manual_image_batch_size"] = 10
    manifest["config"]["youtube_growth"]["reuse_scene_images_for_thumbnails"] = False
    write_json(layout.manifest, manifest)

    usage = build_usage_index(tmp_path)

    record = next(item for item in usage.records if item.project_id == layout.root.name)
    assert record.main_passage == candidate.reference
    selected, _ = select_unused_verse(load_verse_catalog(), usage)
    assert selected.reference != candidate.reference


def test_usage_scan_blocks_selection_when_project_json_is_invalid(tmp_path) -> None:
    project = tmp_path / "shorts" / "broken-project"
    project.mkdir(parents=True)
    (project / "project.json").write_text("{invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="seleção foi bloqueada para evitar repetição"):
        build_usage_index(tmp_path)
