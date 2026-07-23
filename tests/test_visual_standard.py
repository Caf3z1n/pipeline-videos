from datetime import date

from pipeline_videos.models import (
    Palette,
    StyleProfile,
)
from pipeline_videos.project import create_project, load_style
from pipeline_videos.scenes import _resolved_style, assemble_prompt
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
    ).assembled_prompt
    assert "Visual standard: biblical-midcentury-gouache-v1" in prompt
    assert "guache opaco e têmpera" in prompt
    assert "sem fotografia" in prompt
    assert "uma única composição contínua" in prompt
    assert "42% e 58%" not in prompt
    assert "bordas direita e inferior" not in prompt
    assert "sem tela dividida" in prompt


def test_long_and_short_share_the_same_visual_contract(tmp_path) -> None:
    long_layout = create_project(
        "longo-guache",
        "Longo em guache",
        projects_dir=tmp_path,
        target_duration_seconds=420,
        project_date=date(2026, 7, 14),
    )
    layout = create_project(
        "short-guache",
        "Short em guache",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        video_format="short",
        project_date=date(2026, 7, 14),
    )

    long_manifest = long_layout.load_manifest()
    short_manifest = layout.load_manifest()
    assert long_manifest.config.images.visual_standard_id == (
        short_manifest.config.images.visual_standard_id
    )
    assert long_manifest.config.images.scene_planning_standard == (
        short_manifest.config.images.scene_planning_standard
    )
    assert read_model(long_layout.style_profile, StyleProfile) == read_model(
        layout.style_profile, StyleProfile
    )
