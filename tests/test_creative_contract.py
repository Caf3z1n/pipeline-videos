from datetime import date

from pipeline_videos.models import Briefing, ScriptDocument, ScriptSection
from pipeline_videos.project import create_project
from pipeline_videos.scenes import build_scenes
from pipeline_videos.script import ensure_creative_contract
from pipeline_videos.storage import write_json
from pipeline_videos.utils import tokenize_words


def test_long_contract_is_idempotent_and_drives_a_balanced_scene_profile(tmp_path) -> None:
    layout = create_project(
        "jose-perdoa",
        "José perdoa seus irmãos",
        projects_dir=tmp_path,
        project_date=date(2026, 7, 16),
        target_duration_seconds=420,
    )
    write_json(
        layout.briefing,
        Briefing(
            title="José perdoa seus irmãos",
            main_passage="Gênesis 45",
            central_theme="O perdão interrompe o ciclo da vingança",
            practical_application="Responder à dor com verdade, limites e perdão.",
            audience="pessoas feridas por conflitos familiares",
        ),
    )
    roles = [
        "opening",
        "biblical_narrative",
        "reflection",
        "biblical_narrative",
        "reflection",
        "biblical_narrative",
        "reflection",
        "application",
        "closing",
    ]
    sections = [
        ScriptSection(
            section_id=f"section-{index:03d}",
            role=role,
            title=f"Movimento {index}",
            narration=" ".join(
                f"A narrativa avança com verdade e propósito no movimento {index}."
                for _ in range(6)
            ),
            retention_goal=f"Entregar valor específico no movimento {index}.",
        )
        for index, role in enumerate(roles, start=1)
    ]
    script = ScriptDocument(title="José perdoa seus irmãos", sections=sections)
    write_json(layout.script, script)

    contract = ensure_creative_contract(layout, script)
    contract_bytes = layout.creative_contract.read_bytes()
    repeated = ensure_creative_contract(layout, script)

    assert repeated == contract
    assert layout.creative_contract.read_bytes() == contract_bytes
    assert contract.content_promise == "O perdão interrompe o ciclo da vingança"
    assert contract.opening_hook == sections[0].narration.split(". ", 1)[0] + "."
    assert contract.pacing.profile_id == "balanced_long_v1"
    assert sum("reengagement" in beat.functions for beat in contract.beats) == 2
    assert contract.section_ids == [section.section_id for section in sections]

    balanced = build_scenes(layout, skip_approvals=True)
    eight_seconds = build_scenes(
        layout,
        target_scene_seconds=8.0,
        force=True,
        skip_approvals=True,
    )

    assert len(balanced.scenes) < len(eight_seconds.scenes)
    assert tokenize_words(" ".join(scene.narration for scene in balanced.scenes)) == tokenize_words(
        script.narration_text()
    )
