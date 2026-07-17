import subprocess
from datetime import date

import pytest

from pipeline_videos.models import (
    ApprovalStatus,
    Briefing,
    SourceLibrary,
    StageStatus,
    VerseCandidate,
    VerseCatalog,
    VerseUsageIndex,
    VerseUsageRecord,
)
from pipeline_videos.storage import read_json, read_model, write_json
from pipeline_videos.verse_selection import (
    build_usage_index,
    create_auto_short,
    load_verse_catalog,
    reference_tokens,
    references_overlap,
    select_retention_archetype,
    select_unused_verse,
)


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
    assert sources.entries[0].verified is True
    assert sources.entries[0].excerpt == first_candidate.verse_text
    assert "Domínio público" in (sources.entries[0].rights_note or "")
    assert manifest.video_format == "short"
    assert manifest.stages["briefing"].status == StageStatus.COMPLETED
    assert manifest.approvals["briefing"].status == ApprovalStatus.PENDING
    assert manifest.config.shorts_script.narrative_standard == "retention_story_v2"
    assert manifest.config.youtube_growth.title_standard == "tension_pipe_passage_v2"
    assert manifest.config.images.visual_standard_id == "cinematic-family-3d-v1"
    assert (tmp_path / "verse-usage.json").is_file()
    assert first.root != second.root


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
    assert selected.reference not in {
        candidate.reference,
        "Romanos 12:21",
        "Mateus 5:9",
        "João 14:27",
    }


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
