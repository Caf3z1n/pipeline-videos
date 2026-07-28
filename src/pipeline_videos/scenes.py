from __future__ import annotations

import shutil

from pipeline_videos.models import (
    ApprovalStatus,
    Briefing,
    ContinuityBible,
    Palette,
    PromptBlocks,
    Scene,
    SceneManifest,
    ScriptDocument,
    ScriptSection,
    ShortsOpeningVisualContract,
    ShortsScriptStrategy,
    StyleProfile,
)
from pipeline_videos.project import load_style
from pipeline_videos.script import ensure_creative_contract
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    require_approval,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json
from pipeline_videos.utils import aspect_ratio_label, relative_posix, sha256_value, tokenize_words

UNRESTRICTED_SHOTS = (
    "plano geral que revele com clareza a ação e o ambiente",
    "plano médio lateral com interação natural entre as figuras",
    "close sereno no rosto, gesto ou objeto central da cena",
    "plano alto mostrando a relação entre personagens e ambiente",
    "plano sobre o ombro com profundidade natural",
    "plano baixo suave que valorize a ação principal",
)

SCENE_ALGORITHM_VERSION = "7.0"
FIXED_SPOKEN_REFERENCE_VISUAL_BRIEF = (
    "A imagem padrão do perfil mostra uma Bíblia aberta sobre uma mesa de madeira diante de "
    "uma paisagem serena, em pintura editorial bíblica de guache."
)


ROLE_DIRECTIONS = {
    "opening": (
        "uma figura humana em pausa diante de uma paisagem ampla",
        "ambiente contemplativo entre noite e amanhecer",
        "despertar curiosidade e identificação",
    ),
    "biblical_narrative": (
        "uma cena do mundo bíblico representada com sobriedade e sem detalhes não atestados",
        "paisagem bíblica estilizada",
        "situar a passagem sem transformar interpretação em fato",
    ),
    "reflection": (
        "uma pessoa contemplando um caminho iluminado à distância",
        "paisagem simbólica e atemporal",
        "dar espaço visual para a reflexão",
    ),
    "application": (
        "uma pessoa contemporânea realizando um gesto simples de cuidado e confiança",
        "ambiente cotidiano simplificado",
        "aproximar a mensagem da vida prática",
    ),
    "closing": (
        "um caminho que segue em direção à luz, com presença humana discreta",
        "horizonte aberto e sereno",
        "encerrar com esperança e reverência",
    ),
}


def _palette_description(palette: Palette) -> str:
    return ", ".join(f"{color.name} {color.hex} para {color.role}" for color in palette.colors)


def _continuity_lines(continuity: ContinuityBible) -> list[str]:
    lines = list(continuity.global_rules)
    for character in continuity.characters:
        lines.append(
            f"{character.name}: {character.visual_description}; roupa: {character.clothing}; "
            + "; ".join(character.invariants)
        )
    return lines


def _resolved_style(layout: ProjectLayout) -> StyleProfile:
    style = read_model(layout.style_profile, StyleProfile)
    project = layout.load_manifest()
    image_config = project.config.images
    if not image_config.enforce_visual_standard:
        return style

    style_by_standard = {
        "biblical-midcentury-gouache-v1": "biblical-midcentury-gouache",
        "biblical-cutout-clay-v1": "biblical-cutout-clay",
    }
    style_id = style_by_standard.get(image_config.visual_standard_id)
    if style_id is None:
        raise RuntimeError(
            f"Padrão visual oficial desconhecido: {image_config.visual_standard_id}."
        )
    canonical = load_style(style_id)
    if style == canonical:
        return style

    archive = (
        layout.style_profile.parent / "versions" / f"style-profile-{sha256_value(style)[:12]}.json"
    )
    if not archive.exists():
        write_json(archive, style)
    write_json(layout.style_profile, canonical)
    layout.append_event(
        {
            "event": "visual_standard_restored",
            "visual_standard_id": canonical.visual_standard_id,
            "previous_style_archive": relative_posix(archive, layout.root),
        }
    )
    return canonical


def assemble_prompt(
    *,
    style: StyleProfile,
    palette: Palette,
    continuity: list[str],
    scene_description: str,
    composition: str,
    emotion: str,
    canvas_width: int,
    canvas_height: int,
) -> PromptBlocks:
    negative = list(style.negative_constraints)
    negative.extend(
        constraint
        for constraint in (
            "sem colagem, quadrinhos, storyboard, díptico, tríptico ou múltiplos painéis",
            "sem tela dividida, cenas empilhadas ou momentos diferentes na mesma imagem",
            "sem faixa branca, retângulo vazio ou divisor horizontal separando a composição",
        )
        if constraint not in negative
    )
    aspect = aspect_ratio_label(canvas_width, canvas_height)
    is_vertical = canvas_height > canvas_width
    orientation = "vertical" if is_vertical else "horizontal"
    asset_type = (
        f"quadro de cena vertical {aspect} para Short bíblico em celular"
        if is_vertical
        else f"quadro de cena horizontal {aspect} para vídeo reflexivo no YouTube"
    )
    constraints = (
        "imagem sem texto; composição artística livre, sem reservar áreas artificiais para "
        "legenda ou interface"
    )
    assembled = "\n".join(
        [
            "Use case: illustration-story",
            f"Asset type: {asset_type}",
            (
                f"Visual standard: {style.visual_standard_id}. "
                f"{style.reference_note or 'Siga literalmente o perfil visual do projeto.'} "
                "A fidelidade a esse padrão tem prioridade sobre qualquer estilo conflitante."
                if style.visual_standard_id
                else "Visual standard: seguir literalmente o perfil de estilo do projeto."
            ),
            f"Style/medium: {style.medium}. " + "; ".join(style.principles),
            f"Color palette: {_palette_description(palette)}. Clima: {palette.mood}.",
            f"Continuity: {'; '.join(continuity) if continuity else 'sem personagem recorrente'}",
            f"Constraints: {constraints}",
            f"Avoid: {'; '.join(negative)}",
            (
                "Primary request: uma única composição contínua, um único momento, um único "
                f"ambiente e uma ação principal. {scene_description}"
            ),
            f"Composition/framing: {composition}; composição {orientation} "
            f"cinematográfica {aspect}",
            f"Lighting/mood: {emotion}",
        ]
    )
    return PromptBlocks(
        style_profile_id=style.style_id,
        palette_id=palette.palette_id,
        continuity=continuity,
        scene=scene_description,
        composition=composition,
        emotion=emotion,
        negative=negative,
        assembled_prompt=assembled,
    )


def _scene_from_chunk(
    section: ScriptSection,
    narration: str,
    index: int,
    style: StyleProfile,
    palette: Palette,
    continuity: list[str],
    canvas_width: int,
    canvas_height: int,
    *,
    high_impact_opening: bool = False,
    scroll_stop_opening: bool = False,
    retention_reset: bool = False,
    visual_brief: str | None = None,
    visual_relation: str = "contextual",
    image_source_mode: str = "generated",
    image_source_path: str | None = None,
) -> Scene:
    subject, environment, function = ROLE_DIRECTIONS[section.role]
    if visual_brief:
        subject = visual_brief
    elif section.visual_direction:
        subject = section.visual_direction
    if section.retention_goal:
        function = section.retention_goal
    shots = UNRESTRICTED_SHOTS
    shot = shots[index % len(shots)]
    emotional_intent = {
        "opening": "silêncio, pergunta e acolhimento",
        "biblical_narrative": "reverência e atenção",
        "reflection": "proximidade e contemplação",
        "application": "clareza, coragem e esperança prática",
        "closing": "paz e continuidade",
    }[section.role]
    if scroll_stop_opening:
        subject = (
            f"{subject}. Quadro inicial de interrupção do scroll: um sujeito ou objeto dominante, "
            "uma ação observável já em curso e uma tensão concreta coerente com o hook; emoção "
            "legível imediatamente e um único foco visual, sem acolhimento genérico ou pose parada"
        )
        shot = (
            "plano médio ou close de alto impacto, sujeito e ação dominantes, gesto ou emoção "
            "imediatamente legível, tensão visual simples e enquadramento livre sem área vazia "
            "decorativa obrigatória"
        )
        emotional_intent = (
            "tensão e curiosidade imediatas, emoção clara e reverência; leitura visual em menos "
            "de um segundo, sem choque gratuito"
        )
        function = f"abrir com ação concreta alinhada ao hook; {function}"
    elif high_impact_opening:
        subject = (
            f"{subject}. Abertura do vídeo: um único personagem, objeto ou símbolo "
            "dominante, fiel ao trecho narrado neste quadro, com gesto ou emoção "
            "imediatamente legível; acabamento especialmente caprichado, limpo e intencional"
        )
        shot = (
            f"{UNRESTRICTED_SHOTS[index % len(UNRESTRICTED_SHOTS)]}, variação clara de escala e "
            "ângulo em relação ao primeiro quadro, ação ou símbolo diferente e leitura imediata"
        )
        emotional_intent = (
            "tensão e curiosidade imediatas, emoção clara e reverência; leitura visual em menos "
            "de um segundo, sem choque gratuito"
        )
        function = f"mudança visual dentro da abertura para sustentar retenção; {function}"
    elif retention_reset:
        shot = (
            f"{shots[(index + 2) % len(shots)]}, mudança deliberada de escala, direção de olhar "
            "ou profundidade em relação ao movimento anterior"
        )
        emotional_intent += "; renovação clara de curiosidade sem choque gratuito"
        function = f"reengajamento visual por nova informação ou reversão; {function}"
    if section.on_screen_emphasis:
        emotional_intent += f"; ênfase visual em {section.on_screen_emphasis}"
    prompt = assemble_prompt(
        style=style,
        palette=palette,
        continuity=continuity,
        scene_description=(
            f"{subject}. Represente somente o beat visual principal deste trecho, sem mostrar "
            f"antes e depois. Contexto narrativo para orientar a cena: {narration}"
        ),
        composition=shot,
        emotion=emotional_intent,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    scene_id = f"scene-{index + 1:03d}"
    narration_block_id = f"audio-block-{index + 1:03d}"
    return Scene(
        scene_id=scene_id,
        order=index + 1,
        section_id=section.section_id,
        narration=narration,
        estimated_duration_seconds=round(len(tokenize_words(narration)) / 145 * 60, 2),
        emotional_intent=emotional_intent,
        environment=environment,
        shot=shot,
        narrative_function=function,
        prompt=prompt,
        image_id=f"image-{index + 1:03d}",
        image_source_mode=image_source_mode,
        image_source_path=image_source_path,
        narration_block_id=narration_block_id,
        visual_relation=visual_relation,
        visual_brief=subject,
        approval=ApprovalStatus.PENDING,
    )


def _opening_contract_visual_brief(
    base_description: str | None,
    contract: ShortsOpeningVisualContract,
) -> str:
    return " ".join(
        (
            *((base_description.rstrip(" .") + ".",) if base_description else ()),
            f"Sujeito dominante: {contract.dominant_subject_or_object}.",
            f"Ação já em curso: {contract.observable_action}.",
            f"Mudança visível ({contract.action_kind}), em um único momento de transição: "
            f"{contract.initial_state} cedendo a {contract.resulting_state}.",
            f"Tensão visual concreta: {contract.concrete_tension}.",
            f"Enquadramento: {contract.shot_scale}.",
        )
    )


def build_scenes(
    layout: ProjectLayout,
    *,
    target_scene_seconds: float | None = None,
    force: bool = False,
    skip_approvals: bool = False,
) -> SceneManifest:
    if target_scene_seconds is not None:
        raise ValueError(
            "A divisão manual por segundos foi removida; as cenas seguem blocos contíguos "
            "planejados junto do roteiro."
        )
    if not skip_approvals:
        require_approval(layout, "briefing")
        require_approval(layout, "script")
        require_approval(layout, "biblical_review")
    script = read_model(layout.script, ScriptDocument)
    contract = ensure_creative_contract(layout, script)
    briefing = read_model(layout.briefing, Briefing)
    style = _resolved_style(layout)
    palette = read_model(layout.palette, Palette)
    continuity = read_model(layout.continuity, ContinuityBible)
    project = layout.load_manifest()
    render_config = project.config.render
    compact_visual_planning = (
        project.video_format == "short"
        and project.config.shorts_script.generation_mode == "deterministic_compact_visual_plan_v1"
        and project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
    )
    narration_block_planning = (
        project.config.images.scene_planning_standard == "narration_block_visual_v1"
        or compact_visual_planning
    )
    cutout_audio_only_planning = (
        project.config.long_form_profile.enabled
        and project.config.images.scene_planning_standard
        in {
            "script_audio_edit_plan_assets_v1",
            "script_visual_blueprint_compiler_v2",
        }
    )
    shorts_strategy = (
        read_model(layout.shorts_script_strategy, ShortsScriptStrategy)
        if project.video_format == "short" and layout.shorts_script_strategy.is_file()
        else None
    )
    visual_strategy = (
        shorts_strategy
        if project.video_format == "short"
        else script.long_form_strategy
        if narration_block_planning
        else None
    )
    opening_visual_contract = (
        shorts_strategy.creative_direction.opening_visual_contract
        if shorts_strategy is not None and shorts_strategy.creative_direction is not None
        else None
    )
    scroll_stop_contract = (
        project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
    )
    stable_inputs = {
        "algorithm": SCENE_ALGORITHM_VERSION,
        "script": script,
        "briefing": briefing,
        "style": style,
        "palette": palette,
        "continuity": continuity,
        "creative_contract": contract,
        "scene_planning_standard": project.config.images.scene_planning_standard,
        "cutout_audio_only_planning": cutout_audio_only_planning,
        "composition_safety_mode": project.config.images.composition_safety_mode,
        "spoken_reference_image_mode": (project.config.images.spoken_reference_image_mode),
        "spoken_reference_image_path": (project.config.images.spoken_reference_image_path),
        "visual_scenes": visual_strategy.visual_scenes if visual_strategy else None,
        "short_verse_visual_change_seconds": (
            project.config.shorts_script.verse_visual_change_seconds
            if project.video_format == "short"
            else None
        ),
        "render_canvas": [render_config.width, render_config.height],
    }
    if scroll_stop_contract:
        stable_inputs["opening_visual_standard"] = project.config.images.opening_visual_standard
        stable_inputs["opening_visual_contract"] = opening_visual_contract
    input_hash = sha256_value(stable_inputs)
    if stage_is_current(layout, "scenes", input_hash, force=force):
        return read_model(layout.scenes, SceneManifest)
    start_stage(layout, "scenes", input_hash, provider="deterministic-scene-builder")
    try:
        recurring = _continuity_lines(continuity)
        scenes: list[Scene] = []
        contract_beats = {beat.section_id: beat for beat in contract.beats}
        section_by_id = {section.section_id: section for section in script.sections}
        fixed_reference_section_id = (
            visual_strategy.cinematic_devotional.reference_section_id
            if project.video_format == "short"
            and visual_strategy is not None
            and visual_strategy.cinematic_devotional is not None
            and project.config.images.spoken_reference_image_mode == "fixed_profile_asset_v1"
            else None
        )
        if cutout_audio_only_planning:
            for section in script.sections:
                retention_reset = "reengagement" in contract_beats[section.section_id].functions
                scenes.append(
                    _scene_from_chunk(
                        section,
                        section.narration,
                        len(scenes),
                        style,
                        palette,
                        recurring,
                        render_config.width,
                        render_config.height,
                        retention_reset=retention_reset,
                        visual_brief=(
                            "Marcador técnico exclusivo da segmentação do áudio contínuo; "
                            "não autoriza imagem, asset ou decisão de edição"
                        ),
                        visual_relation="contextual",
                    )
                )
        elif visual_strategy is not None:
            if not visual_strategy.visual_scenes:
                raise ValueError("O padrão narration_block_visual_v1 exige strategy.visual_scenes.")
            for planned in visual_strategy.visual_scenes:
                section = section_by_id.get(planned.section_id)
                if section is None:
                    raise ValueError(
                        f"O plano visual referencia uma seção inexistente: {planned.section_id}."
                    )
                retention_reset = "reengagement" in contract_beats[section.section_id].functions
                uses_fixed_reference_image = planned.section_id == fixed_reference_section_id
                scroll_stop_opening = (
                    len(scenes) == 0
                    and section.role == "opening"
                    and project.config.images.opening_visual_standard == "scroll_stop_concrete_v1"
                )
                planned_visual_brief = (
                    FIXED_SPOKEN_REFERENCE_VISUAL_BRIEF
                    if uses_fixed_reference_image
                    else planned.scene_description
                )
                if scroll_stop_opening and opening_visual_contract is not None:
                    planned_visual_brief = _opening_contract_visual_brief(
                        planned_visual_brief,
                        opening_visual_contract,
                    )
                scenes.append(
                    _scene_from_chunk(
                        section,
                        planned.narration,
                        len(scenes),
                        style,
                        palette,
                        recurring,
                        render_config.width,
                        render_config.height,
                        retention_reset=retention_reset,
                        scroll_stop_opening=scroll_stop_opening,
                        visual_brief=planned_visual_brief,
                        visual_relation=(
                            "contextual" if uses_fixed_reference_image else planned.visual_relation
                        ),
                        image_source_mode=(
                            "fixed_profile_asset_v1" if uses_fixed_reference_image else "generated"
                        ),
                        image_source_path=(
                            project.config.images.spoken_reference_image_path
                            if uses_fixed_reference_image
                            else None
                        ),
                    )
                )
        else:
            raise ValueError(
                "O roteiro precisa fornecer visual_scenes no padrão narration_block_visual_v1."
            )
        if not scenes:
            raise ValueError("O roteiro não produziu nenhuma cena.")
        if (
            project.video_format == "short"
            and project.config.shorts_script.narrative_standard
            in {"retention_story_v2", "creative_retention_v3"}
            and target_scene_seconds is None
            and layout.shorts_script_strategy.is_file()
            and not (
                (
                    3
                    if project.config.shorts_script.generation_mode
                    == "deterministic_compact_visual_plan_v1"
                    else project.config.shorts_script.target_scene_min
                )
                <= len(scenes)
                <= project.config.shorts_script.target_scene_max
            )
        ):
            minimum_scene_count = (
                3
                if project.config.shorts_script.generation_mode
                == "deterministic_compact_visual_plan_v1"
                else project.config.shorts_script.target_scene_min
            )
            raise ValueError(
                "O padrão criativo de Shorts precisa produzir de "
                f"{minimum_scene_count} a "
                f"{project.config.shorts_script.target_scene_max} cenas; "
                f"o roteiro atual produziu {len(scenes)}."
            )
        manifest = SceneManifest(
            script_version=script.version,
            style_profile_id=style.style_id,
            palette_id=palette.palette_id,
            total_estimated_duration_seconds=round(
                sum(scene.estimated_duration_seconds for scene in scenes), 2
            ),
            scenes=scenes,
        )
        if layout.scenes.exists():
            old_hash = sha256_value(layout.scenes.read_text(encoding="utf-8"))[:10]
            archive = layout.scenes.parent / "versions" / f"manifest-{old_hash}.json"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                shutil.copy2(layout.scenes, archive)
        write_json(layout.scenes, manifest)
        complete_stage(
            layout,
            "scenes",
            input_hash,
            [relative_posix(layout.scenes, layout.root)],
            provider="deterministic-scene-builder",
            usage={
                "scene_count": len(scenes),
                "pacing_profile": (
                    "explicit_override"
                    if target_scene_seconds is not None
                    else contract.pacing.profile_id
                ),
            },
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "scenes", exc)
        raise
