from __future__ import annotations

from datetime import date

import pytest

from pipeline_videos.growth import (
    OFFICIAL_YOUTUBE_GUIDELINES,
    _validate_package,
    check_growth_package_staging,
    import_growth_package,
    materialize_compact_growth_fallback,
    prepare_growth_queue,
)
from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    CompactGrowthDraft,
    PromptBlocks,
    Scene,
    SceneManifest,
    ScriptDocument,
    ScriptSection,
    YouTubeGrowthPackage,
    YouTubeGrowthStrategy,
    YouTubeMetadata,
    YouTubePromiseMatch,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import write_json
from pipeline_videos.utils import utc_now


def _project_with_inputs(tmp_path, *, compact: bool = False):
    duration = 18 if compact else 60
    layout = create_project(
        "davi-e-golias",
        "Davi e Golias",
        projects_dir=tmp_path,
        target_duration_seconds=duration,
        video_format="short",
        project_date=date(2026, 7, 14),
    )
    write_json(
        layout.briefing,
        Briefing(
            title="Davi e Golias",
            main_passage="1 Samuel 17",
            central_theme="Coragem diante do medo",
            audience="pessoas que buscam coragem",
            viewer_outcome="Compreender como a confiança muda a resposta ao medo.",
            practical_application="Confiar em Deus diante do medo.",
            creative_mode="pastoral_encouragement",
            interpretive_stance="devotional",
            hook_policy="required",
            hook_family="if_you_want_do_this",
        ),
    )
    script = ScriptDocument(
        title="Coragem diante do medo | 1 Samuel 17",
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="O desafio",
                narration="Eu vi Davi avançar quando todos recuavam diante do medo.",
                biblical_basis=["1 Samuel 17"],
                claim_classification=["biblical_text"],
            )
        ],
    )
    write_json(layout.script, script)
    write_json(
        layout.biblical_review,
        BiblicalReview(
            script_version=1,
            reviewed_at=utc_now(),
            reviewer="teste",
            summary="Sem bloqueios.",
            approved=True,
        ),
    )
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="biblical-midcentury-gouache",
            palette_id="midcentury-devotional-print",
            total_estimated_duration_seconds=4.0,
            scenes=[
                Scene(
                    scene_id="scene-001",
                    order=1,
                    section_id="section-001",
                    narration=script.sections[0].narration,
                    estimated_duration_seconds=4.0,
                    emotional_intent="coragem",
                    environment="vale",
                    shot="plano médio",
                    narrative_function="abertura",
                    prompt=PromptBlocks(
                        style_profile_id="biblical-midcentury-gouache",
                        palette_id="midcentury-devotional-print",
                        continuity=[],
                        scene="Davi avançando pelo vale",
                        composition="contraste de escala",
                        emotion="coragem",
                        negative=[],
                        assembled_prompt="guache editorial bíblico",
                    ),
                    image_id="image-001",
                )
            ],
        ),
    )
    return layout


def _package(layout, queue: dict) -> YouTubeGrowthPackage:
    guard = queue["contract_guard"]
    title = "Coragem diante do medo | 1 Samuel 17"
    description = (
        "Coragem diante do medo em 1 Samuel 17 revela como a confiança em Deus muda a forma "
        "de enfrentar uma ameaça concreta. Davi avança com convicção e mantém o foco naquele "
        "que o sustenta, enquanto todo o exército permanece paralisado.\n\n"
        "A passagem acompanha uma escolha que transforma temor em ação responsável. A história "
        "preserva o sentido bíblico e oferece uma resposta clara para quem procura coragem, "
        "confiança e fidelidade diante de desafios reais."
    )
    return YouTubeGrowthPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        metadata=YouTubeMetadata(
            title=title,
            alternative_titles=[],
            description=description,
            chapters=[],
            topics=[
                "coragem diante do medo",
                "1 Samuel 17",
                "Davi e Golias",
                "coragem na Bíblia",
                "confiança em Deus",
                "fé em tempos difíceis",
                "história de Davi",
                "Jesus",
            ],
            pinned_comment="Como 1 Samuel 17 fortalece sua coragem diante do medo?",
        ),
        strategy=YouTubeGrowthStrategy(
            format="short",
            target_viewer="pessoas que buscam coragem",
            search_intent="compreender a coragem de Davi",
            primary_keyword="coragem diante do medo",
            secondary_keywords=["Davi e Golias", "confiança em Deus"],
            content_promise=guard["content_promise"],
            opening_hook=guard["opening_hook"],
            title_rationale="Enfoque concreto seguido da passagem.",
            description_rationale="Assunto e passagem aparecem na abertura.",
            tag_strategy="Tema, passagem, personagem e buscas relacionadas.",
            discovery_hypothesis="Clareza pode facilitar a escolha de assistir.",
            promise_match=YouTubePromiseMatch(
                content_promise=guard["content_promise"],
                opening_line=guard["opening_hook"],
                opening_scene_id=guard["opening_scene_id"],
                title_delivery="Coragem diante do medo",
            ),
            source_ids=[],
            section_ids=["section-001"],
            official_guidelines=list(OFFICIAL_YOUTUBE_GUIDELINES),
        ),
    )


def test_new_growth_queue_is_metadata_only(tmp_path) -> None:
    layout = _project_with_inputs(tmp_path)
    queue = prepare_growth_queue(layout)

    assert queue["outputs"].keys() == {
        "package",
        "title_count",
        "title_standard",
        "description_standard",
    }
    assert not any("imagegen" in item.casefold() for item in queue["instructions"])


def test_growth_package_imports_without_visual_artifacts(tmp_path) -> None:
    layout = _project_with_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    write_json(layout.growth_package_staging, _package(layout, queue))

    imported = import_growth_package(layout)

    assert imported.metadata.title == "Coragem diante do medo | 1 Samuel 17"
    assert layout.metadata.is_file()
    assert layout.growth_strategy.is_file()


def test_growth_validation_rejects_changed_promise(tmp_path) -> None:
    layout = _project_with_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(layout, queue)
    package.strategy.content_promise = "Outra promessa"

    with pytest.raises(ValueError, match="content_promise"):
        _validate_package(layout, package, queue)


def test_compact_fallback_produces_valid_staging_file(tmp_path) -> None:
    layout = _project_with_inputs(tmp_path, compact=True)
    queue = prepare_growth_queue(layout)
    assert queue["generation_mode"] == "compact_metadata_draft_v1"

    draft = materialize_compact_growth_fallback(layout)
    check = check_growth_package_staging(layout)

    assert isinstance(draft, CompactGrowthDraft)
    assert check.valid


def test_staging_check_reports_wrong_job_id(tmp_path) -> None:
    layout = _project_with_inputs(tmp_path)
    queue = prepare_growth_queue(layout)
    package = _package(layout, queue)
    package.job_id = "fila-antiga"
    write_json(layout.growth_package_staging, package)

    check = check_growth_package_staging(layout)

    assert not check.valid
    assert "fila antiga" in " ".join(check.errors)
