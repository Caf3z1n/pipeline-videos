from __future__ import annotations

from pathlib import Path

from pipeline_videos.models import (
    BiblicalReview,
    Briefing,
    CharacterSheet,
    ContinuityBible,
    ReviewFinding,
    ScriptDocument,
    ScriptSection,
    SourceEntry,
    SourceLibrary,
)
from pipeline_videos.project import create_project
from pipeline_videos.review import approve_biblical_review, approve_briefing, approve_script
from pipeline_videos.scenes import build_scenes
from pipeline_videos.script import save_script
from pipeline_videos.state import complete_stage
from pipeline_videos.storage import DEFAULT_PROJECTS_DIR, ProjectLayout, resolve_project, write_json
from pipeline_videos.utils import relative_posix, sha256_value, today_iso, utc_now

DEMO_ID = "demo-salmo-23"


def _demo_script() -> ScriptDocument:
    return ScriptDocument(
        title="Quando o caminho atravessa o vale",
        source_ids=["source-salmo-23"],
        notes=[
            "Demonstração curta sem citação literal longa.",
            "O texto diferencia a imagem do salmo de sua aplicação devocional.",
        ],
        sections=[
            ScriptSection(
                section_id="section-001",
                role="opening",
                title="Quando falta um mapa",
                narration=(
                    "Há dias em que a alma parece caminhar sem mapa. O futuro fica nebuloso, "
                    "e até os passos simples pesam mais do que deveriam."
                ),
                biblical_basis=["Salmo 23:1-4"],
                claim_classification=["interpretation", "creative_elaboration"],
            ),
            ScriptSection(
                section_id="section-002",
                role="biblical_narrative",
                title="A imagem do pastor",
                narration=(
                    "No Salmo 23, o salmista apresenta Deus pela imagem de um pastor. A passagem "
                    "fala de cuidado, direção, descanso e presença, inclusive quando o caminho "
                    "atravessa um vale escuro."
                ),
                biblical_basis=["Salmo 23:1-4"],
                claim_classification=["biblical_text", "interpretation"],
            ),
            ScriptSection(
                section_id="section-003",
                role="reflection",
                title="Presença no vale",
                narration=(
                    "A promessa não é uma vida sem vales. A esperança nasce da presença que não "
                    "abandona. O medo pode existir, mas já não precisa ocupar sozinho todo o "
                    "horizonte."
                ),
                biblical_basis=["Salmo 23:4"],
                claim_classification=["interpretation"],
            ),
            ScriptSection(
                section_id="section-004",
                role="application",
                title="O próximo passo",
                narration=(
                    "Hoje, talvez você não enxergue a estrada inteira. Respire, reconheça o que "
                    "está fora do seu controle e escolha o próximo passo possível. Procure apoio, "
                    "ore com honestidade e não caminhe isolado."
                ),
                biblical_basis=["Salmo 23:4"],
                claim_classification=["interpretation", "creative_elaboration"],
            ),
            ScriptSection(
                section_id="section-005",
                role="closing",
                title="Caminhar acompanhado",
                narration=(
                    "Que esta imagem antiga devolva serenidade ao seu coração: mesmo quando a luz "
                    "parece distante, você pode atravessar o vale um passo de cada vez, "
                    "acompanhado."
                ),
                biblical_basis=["Salmo 23:4"],
                claim_classification=["interpretation"],
            ),
        ],
    )


def _initialize_demo_content(layout: ProjectLayout) -> None:
    briefing = Briefing(
        title="Quando o caminho atravessa o vale",
        main_passage="Salmo 23:1-4",
        complementary_references=[],
        central_theme="a presença de Deus nos caminhos difíceis",
        practical_application=(
            "Reconhecer limites, buscar apoio e dar o próximo passo possível sem confundir a "
            "aplicação devocional com uma promessa de ausência de sofrimento."
        ),
        audience="adultos vivendo incerteza, cansaço ou medo",
        emotional_intent="acolher a ansiedade e conduzir à serenidade esperançosa",
        narration_tone="reflexivo, sóbrio, acolhedor e cinematográfico",
        target_duration_seconds=45,
        bible_translation=None,
        interpretation_limits=[
            "Não afirmar que a passagem promete ausência de sofrimento ou sucesso material.",
            "Apresentar conselhos práticos como aplicação, não como citação do salmo.",
        ],
        claims_to_avoid=[
            "Não atribuir ao salmista detalhes biográficos ou históricos ausentes da passagem.",
            "Não apresentar a paráfrase como tradução bíblica literal.",
        ],
        visual_notes=[
            "Alternar paisagem bíblica simbólica e aplicação contemporânea.",
            "Manter espaço inferior seguro para legendas.",
        ],
        call_to_action="Convidar a uma reflexão respeitosa, sem promessa de resultado instantâneo.",
    )
    sources = SourceLibrary(
        entries=[
            SourceEntry(
                source_id="source-salmo-23",
                kind="biblical_text",
                title="Salmo 23:1-4 — referência canônica",
                passage="Salmo 23:1-4",
                accessed_at=today_iso(),
                use_in_video="Base temática; o roteiro usa paráfrase, não transcrição longa.",
                claim_scope="Imagem do pastor, cuidado, descanso, direção, vale e presença.",
                rights_note=(
                    "Nenhuma tradução moderna é reproduzida literalmente nesta demonstração. "
                    "Definir tradução e licença antes de inserir citações públicas."
                ),
                verified=True,
            )
        ]
    )
    continuity = ContinuityBible(
        characters=[
            CharacterSheet(
                character_id="traveler",
                name="Viajante",
                visual_description="figura adulta de proporções simples e postura serena",
                clothing="túnica longa vinho escuro e manto marfim, sem ornamentos",
                invariants=[
                    "silhueta reconhecível",
                    "roupa idêntica em toda a sequência bíblica",
                    "sem detalhes faciais complexos",
                ],
            )
        ],
        environments={
            "vale": "camadas de colinas azul-petróleo e caminho dourado estreito",
            "amanhecer": "horizonte marfim com luz dourada discreta",
        },
        global_rules=[
            "Manter viajante, roupas e proporções consistentes.",
            "Usar a paleta do projeto independentemente do estilo-base.",
            "Não inserir texto ou símbolos religiosos decorativos sem função narrativa.",
        ],
    )
    write_json(layout.briefing, briefing)
    write_json(layout.sources, sources)
    write_json(layout.continuity, continuity)
    briefing_hash = sha256_value(briefing)
    complete_stage(
        layout,
        "briefing",
        briefing_hash,
        [
            relative_posix(layout.briefing, layout.root),
            relative_posix(layout.sources, layout.root),
        ],
        provider="demo-fixture",
    )
    approve_briefing(layout, decided_by="demo-fixture", note="Conteúdo seguro para a demonstração.")

    script = save_script(layout, _demo_script(), provider="demo-fixture")
    review = BiblicalReview(
        script_version=script.version,
        reviewed_at=utc_now(),
        reviewer="revisão de demonstração",
        summary=(
            "O roteiro permanece no campo devocional, não usa citação literal longa e identifica "
            "como aplicação os conselhos que não pertencem ao texto do salmo."
        ),
        findings=[
            ReviewFinding(
                finding_id="finding-001",
                severity="info",
                category="interpretation",
                statement="A passagem não promete uma vida sem vales.",
                evidence=(
                    "Trata-se de uma inferência devocional coerente com a própria menção ao vale, "
                    "não de uma citação literal."
                ),
                resolved=True,
            ),
            ReviewFinding(
                finding_id="finding-002",
                severity="warning",
                category="creative_elaboration",
                statement="Respirar, buscar apoio e não caminhar isolado são aplicações modernas.",
                evidence="Essas ações não são apresentadas como palavras do Salmo 23.",
                recommendation="Manter a distinção explícita no roteiro e na descrição.",
                resolved=True,
            ),
            ReviewFinding(
                finding_id="finding-003",
                severity="info",
                category="rights",
                statement="O roteiro não reproduz uma tradução bíblica moderna.",
                evidence="A fonte registra referência e paráfrase sem trecho literal longo.",
                resolved=True,
            ),
        ],
    )
    write_json(layout.biblical_review, review)
    review_hash = sha256_value(review)
    complete_stage(
        layout,
        "biblical_review",
        review_hash,
        [relative_posix(layout.biblical_review, layout.root)],
        provider="demo-fixture",
    )
    approve_biblical_review(layout, decided_by="demo-fixture")
    approve_script(layout, decided_by="demo-fixture")


def run_demo(
    *,
    projects_dir: Path | None = None,
    force: bool = False,
) -> tuple[ProjectLayout, object]:
    base = (projects_dir or DEFAULT_PROJECTS_DIR).expanduser().resolve()
    try:
        layout = resolve_project(DEMO_ID, base)
        if projects_dir is not None:
            try:
                layout.root.resolve().relative_to(base)
            except ValueError as exc:
                raise FileNotFoundError from exc
    except FileNotFoundError:
        layout = create_project(
            DEMO_ID,
            "Quando o caminho atravessa o vale",
            projects_dir=base,
            target_duration_seconds=45,
            demo=True,
        )
        _initialize_demo_content(layout)

    build_scenes(layout, target_scene_seconds=7.5, force=force, skip_approvals=True)

    from pipeline_videos.audio import generate_audio
    from pipeline_videos.captions import generate_captions
    from pipeline_videos.images import approve_image, build_image_manifest, generate_fixture_images
    from pipeline_videos.metadata import build_metadata
    from pipeline_videos.music import select_music
    from pipeline_videos.render import render_project
    from pipeline_videos.validation import validate_project

    image_manifest = build_image_manifest(layout, force=force)
    image_manifest = generate_fixture_images(layout, force=force)
    for item in image_manifest.items:
        if item.approved_version is None:
            approve_image(layout, item.scene_id, decided_by="demo-fixture")
    generate_audio(layout, force=force)
    select_music(layout, force=force)
    generate_captions(layout, force=force)
    build_metadata(layout, force=force)
    result = render_project(layout, force=force)
    report = validate_project(layout, require_render=True)
    return layout, {"render": result, "validation": report}
