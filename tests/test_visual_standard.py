from datetime import date

from pipeline_videos.models import (
    Palette,
    SceneManifest,
    ScriptDocument,
    ScriptSection,
    StyleProfile,
)
from pipeline_videos.project import create_project, load_style
from pipeline_videos.scenes import _resolved_style, assemble_prompt, build_scenes
from pipeline_videos.storage import read_model, write_json


def test_visual_standard_restores_the_project_profile_and_hardens_prompt(tmp_path) -> None:
    layout = create_project(
        "padrao-visual-short",
        "Padrão visual",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )
    canonical = load_style("biblical-midcentury-gouache")
    tampered = canonical.model_copy(update={"medium": "estilo adulterado"})
    write_json(layout.style_profile, tampered)

    restored = _resolved_style(layout)

    assert restored == canonical
    assert layout.load_manifest().config.images.visual_standard_id == (
        "biblical-midcentury-gouache-v1"
    )
    assert read_model(layout.style_profile, StyleProfile) == canonical
    archived = list((layout.style_profile.parent / "versions").glob("style-profile-*.json"))
    assert len(archived) == 1
    assert read_model(archived[0], StyleProfile) == tampered

    palette = read_model(layout.palette, Palette)
    prompt = assemble_prompt(
        style=restored,
        palette=palette,
        continuity=[],
        scene_description="Davi abaixa a lâmina diante do manto de Saul",
        composition="close simbólico",
        emotion="tensão contida",
        canvas_width=1080,
        canvas_height=1920,
        reserve_caption_safe_area=False,
    ).assembled_prompt
    assert "Visual standard: biblical-midcentury-gouache-v1" in prompt
    assert "guache opaco e têmpera" in prompt
    assert "sem fotografia" in prompt
    assert "uma única composição contínua" in prompt
    assert "42% e 58%" not in prompt
    assert "bordas direita e inferior" not in prompt
    assert "sem tela dividida" in prompt


def test_short_verse_opening_can_change_images_and_keeps_first_anchor(tmp_path) -> None:
    layout = create_project(
        "versiculo-abertura",
        "Versículo na abertura",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        style_id="cinematic-family-3d-animation",
        project_date=date(2026, 7, 14),
    )
    write_json(
        layout.script,
        ScriptDocument(
            title="Coragem em Josué",
            sections=[
                ScriptSection(
                    section_id="verse",
                    role="opening",
                    title="Josué 1:9",
                    narration=(
                        "Em Josué, capítulo 1, versículo 9, lemos: “Seja forte e corajoso. "
                        "Não se apavore nem "
                        "desanime, pois o Senhor, o seu Deus, estará com você por onde andar.”"
                    ),
                    biblical_basis=["Josué 1:9"],
                    claim_classification=["biblical_text"],
                    visual_direction=(
                        "Josué em primeiro plano antes de conduzir o povo, com decisão no gesto"
                    ),
                ),
                ScriptSection(
                    section_id="reflection",
                    role="reflection",
                    title="Comentário",
                    narration="O medo ainda existe, mas você não precisa resolver tudo hoje.",
                    biblical_basis=["Josué 1:9"],
                    claim_classification=["interpretation"],
                ),
            ],
        ),
    )

    manifest = build_scenes(layout, target_scene_seconds=3, skip_approvals=True)
    persisted = read_model(layout.scenes, SceneManifest)
    opening_scenes = [scene for scene in manifest.scenes if scene.section_id == "verse"]

    assert persisted == manifest
    assert len(opening_scenes) >= 2
    opening = opening_scenes[0]
    continuation = opening_scenes[1]
    assert "imagem-âncora da abertura" in opening.narrative_function
    assert "alto impacto" in opening.prompt.composition
    assert "45% a 65%" in opening.prompt.assembled_prompt
    assert "uma área do mesmo fundo contínuo" in opening.prompt.assembled_prompt
    assert "nunca transformar essa área em faixa branca" in opening.prompt.assembled_prompt
    assert "mudança visual dentro da abertura" in continuation.narrative_function
    assert "variação clara de escala e ângulo" in continuation.prompt.composition
    assert "leitura visual em menos de um segundo" in opening.prompt.emotion
