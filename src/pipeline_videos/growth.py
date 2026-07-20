from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from pipeline_videos.analytics import compact_metric_learning
from pipeline_videos.imagegen_retry import (
    imagegen_retry_instructions,
    imagegen_retry_policy,
)
from pipeline_videos.metadata import _build_chapters
from pipeline_videos.models import (
    ApprovalStatus,
    AudioManifest,
    Briefing,
    ImageManifest,
    SceneManifest,
    ScriptDocument,
    SourceLibrary,
    StageStatus,
    YouTubeGrowthPackage,
    YouTubeMetadata,
    YouTubeThumbnailDesign,
)
from pipeline_videos.script import ensure_creative_contract
from pipeline_videos.state import (
    complete_stage,
    invalidate,
    start_stage,
    wait_stage,
)
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.thumbnail_prompts import build_long_form_thumbnail_prompt
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

GROWTH_QUEUE_SCHEMA_VERSION = "1.13"
GROWTH_ALGORITHM_VERSION = "1.13"
SHORT_TITLE_MAX_CHARACTERS = 70
SHORT_DESCRIPTION_MAX_CHARACTERS = 600
SHORT_DESCRIPTION_SEARCH_WINDOW = 240
OFFICIAL_YOUTUBE_GUIDELINES = (
    "https://support.google.com/youtube/answer/12340300",
    "https://support.google.com/youtube/answer/12948449",
    "https://support.google.com/youtube/answer/16090438",
    "https://support.google.com/youtube/answer/16391400",
    "https://support.google.com/youtube/answer/72431",
    "https://support.google.com/youtube/answer/9229980",
)

_PHOTOGRAPHIC_THUMBNAIL_STYLE_MARKERS = (
    "editorial photography",
    "cinematic photography",
    "realistic photography",
    "photorealistic portrait",
    "photo-realistic portrait",
    "fotografia editorial",
    "retrato fotorrealista",
    "live-action",
)


def _growth_context_payload(layout: ProjectLayout) -> dict[str, Any]:
    required = {
        "briefing": layout.briefing,
        "sources": layout.sources,
        "script": layout.script,
        "biblical_review": layout.biblical_review,
        "scenes": layout.scenes,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Entradas ausentes para o especialista: " + ", ".join(missing))
    project = layout.load_manifest()
    briefing = read_model(layout.briefing, Briefing)
    script = read_model(layout.script, ScriptDocument)
    contract = ensure_creative_contract(layout, script)
    sources = read_model(layout.sources, SourceLibrary)
    scenes = read_model(layout.scenes, SceneManifest)
    opening_scene = min(scenes.scenes, key=lambda item: item.order)
    style = read_json(layout.style_profile)
    palette = read_json(layout.palette)
    review = read_json(layout.biblical_review)
    used_source_ids = set(contract.source_ids or script.source_ids)
    compact_sources = [
        {
            "source_id": source.source_id,
            "kind": source.kind,
            "title": source.title,
            "passage": source.passage,
            "translation": source.translation,
            "use_in_video": source.use_in_video,
            "claim_scope": source.claim_scope,
            "verified": source.verified,
        }
        for source in sources.entries
        if not used_source_ids or source.source_id in used_source_ids
    ]
    open_findings = [
        {
            "severity": finding.get("severity"),
            "category": finding.get("category"),
            "statement": finding.get("statement"),
            "evidence": finding.get("evidence"),
            "recommendation": finding.get("recommendation"),
        }
        for finding in review.get("findings", [])
        if isinstance(finding, dict) and not finding.get("resolved", False)
    ]
    compact_contract = {
        "input_hash": contract.input_hash,
        "format": contract.format,
        "target_viewer": contract.target_viewer,
        "content_promise": contract.content_promise,
        "opening_hook": contract.opening_hook,
        "hook_used": contract.hook_used,
        "hook_family": contract.hook_family,
        "creative_mode": contract.creative_mode,
        "interpretive_stance": contract.interpretive_stance,
        "payoff": contract.payoff,
        "resolution": contract.resolution,
        "viewer_takeaway": contract.viewer_takeaway,
        "practical_application": contract.practical_application,
        "main_passage": contract.main_passage,
        "source_ids": contract.source_ids,
        "claims_to_avoid": contract.claims_to_avoid,
        "beats": [
            {
                "section_id": beat.section_id,
                "functions": beat.functions,
                "viewer_value": beat.viewer_value,
            }
            for beat in contract.beats
        ],
    }
    payload: dict[str, Any] = {
        "schema_version": "2.0",
        "algorithm": GROWTH_ALGORITHM_VERSION,
        "project": {
            "project_id": project.project_id,
            "title": project.title,
            "video_format": project.video_format,
            "target_duration_seconds": project.target_duration_seconds,
            "title_variants": project.config.youtube_growth.title_variants,
            "thumbnail_reference_mode": (project.config.youtube_growth.thumbnail_reference_mode),
            "production_mode": project.config.production.mode,
            "reasoning_effort": "medium",
        },
        "thumbnail_theme": briefing.central_theme.strip() or project.title,
        "creative_contract": compact_contract,
        "sources": compact_sources,
        "script_outline": {
            "title": script.title,
            "sections": [
                {
                    "section_id": section.section_id,
                    "role": section.role,
                    "title": section.title,
                    "biblical_basis": section.biblical_basis,
                }
                for section in script.sections
            ],
        },
        "biblical_review": {
            "summary": review.get("summary", ""),
            "open_findings": open_findings,
        },
        "opening_scene": {
            "scene_id": opening_scene.scene_id,
            "section_id": opening_scene.section_id,
            "narrative_function": opening_scene.narrative_function,
            "visual_subject": opening_scene.prompt.scene,
            "composition": opening_scene.prompt.composition,
            "emotion": opening_scene.prompt.emotion,
        },
        "visual_system": {
            "style": {
                key: style.get(key)
                for key in (
                    "style_id",
                    "name",
                    "medium",
                    "principles",
                    "negative_constraints",
                    "visual_standard_id",
                )
            },
            "palette": {key: palette.get(key) for key in ("palette_id", "name", "mood", "colors")},
        },
        "metric_learning": compact_metric_learning(layout),
    }
    return payload


def growth_input_hash(layout: ProjectLayout) -> str:
    return sha256_value(_growth_context_payload(layout))


def _queue_without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "generated_at"}


def _active_scene_image_source(layout: ProjectLayout, scene_id: str) -> Path:
    if not layout.images.is_file():
        raise RuntimeError(
            "A thumbnail exige a imagem ativa da abertura; importe as imagens primeiro."
        )
    images = read_model(layout.images, ImageManifest)
    item = next((candidate for candidate in images.items if candidate.scene_id == scene_id), None)
    if item is None or item.active_version is None:
        raise RuntimeError("A cena de abertura ainda não possui imagem ativa para editar.")
    version = next(
        (candidate for candidate in item.versions if candidate.version == item.active_version),
        None,
    )
    if version is None:
        raise RuntimeError("A versão ativa da imagem de abertura não foi encontrada.")
    source = (layout.root / version.path).resolve()
    try:
        source.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError("A imagem de abertura está fora da pasta portátil do projeto.") from exc
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"Imagem ativa da abertura ausente: {source}")
    return source


def prepare_growth_queue(layout: ProjectLayout, *, force: bool = False) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.youtube_growth
    if not config.enabled:
        raise RuntimeError("A etapa de crescimento para YouTube está desativada neste projeto.")

    input_hash = growth_input_hash(layout)
    stage = project.stages["youtube_growth"]
    if (
        not force
        and layout.growth_queue.is_file()
        and layout.growth_context.is_file()
        and stage.input_hash == input_hash
    ):
        queue = read_json(layout.growth_queue)
        if stage.status in {StageStatus.WAITING_EXTERNAL, StageStatus.COMPLETED}:
            return queue
    if project.approvals["metadata"].status == ApprovalStatus.APPROVED and not force:
        raise RuntimeError(
            "Os metadados estão aprovados; use --force para solicitar novas versões."
        )

    is_short = project.video_format == "short"
    is_theme_based_long = (
        not is_short
        and config.thumbnail_generation_mode == "theme_prompt_imagegen_v1"
        and config.thumbnail_reference_mode == "theme_only_v1"
        and config.thumbnail_standard == "biblical_cinematic_landscape_v1"
        and config.thumbnail_typography_standard == "no_text_v1"
    )
    single_agent_mode = not config.use_specialist_subagent
    requires_opening_reference = not is_theme_based_long
    thumbnail_count = config.thumbnail_variants
    thumbnail_size = (
        [project.config.render.width, project.config.render.height] if is_short else [1280, 720]
    )
    thumbnail_paths = [
        relative_posix(layout.growth_staging / f"thumbnail-{index:03d}.png", layout.root)
        for index in range(1, thumbnail_count + 1)
    ]
    growth_context = _growth_context_payload(layout)
    long_form_thumbnail_prompt = (
        build_long_form_thumbnail_prompt(str(growth_context["thumbnail_theme"]))
        if is_theme_based_long
        else None
    )
    opening_reference = (
        _active_scene_image_source(
            layout,
            str(growth_context["opening_scene"]["scene_id"]),
        )
        if requires_opening_reference
        else None
    )
    write_json(layout.growth_context, growth_context)
    job_id = f"youtube-growth-{input_hash[:16]}"
    pair_count_label = (
        "um par editorial completo"
        if config.title_variants == 1
        else "três pares editoriais completos"
        if config.title_variants == 3
        else f"{config.title_variants} pares editoriais completos"
    )
    pair_scope_label = "do par" if config.title_variants == 1 else "de cada par"
    image_count_label = (
        "A imagem" if config.thumbnail_variants == 1 else f"As {config.thumbnail_variants} imagens"
    )
    image_pairing_verb = (
        "continua pareada" if config.thumbnail_variants == 1 else "continuam pareadas"
    )
    payload: dict[str, Any] = {
        "schema_version": GROWTH_QUEUE_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "job_id": job_id,
        "input_hash": input_hash,
        "contract_guard": {
            **{
                key: growth_context["creative_contract"][key]
                for key in ("input_hash", "content_promise", "opening_hook")
            },
            "hook_used": growth_context["creative_contract"].get("hook_used", True),
            "opening_scene_id": growth_context["opening_scene"]["scene_id"],
        },
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "orchestration": {
            "mode": "single_agent" if single_agent_mode else "subagent",
            "worker_id": "main" if single_agent_mode else "youtube-growth-specialist",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "medium",
            "recommended_model": "gpt-5.6-sol",
            "context_policy": "compact_only",
            "fork_turns": None if single_agent_mode else "none",
        },
        "inputs": {
            "compact_context": relative_posix(layout.growth_context, layout.root),
            "thumbnail_visual_reference_scene_id": (
                None if is_theme_based_long else growth_context["opening_scene"]["scene_id"]
            ),
            "thumbnail_visual_reference_path": (
                relative_posix(opening_reference, layout.root) if opening_reference else None
            ),
            "thumbnail_theme": (
                str(growth_context["thumbnail_theme"]) if is_theme_based_long else None
            ),
            "thumbnail_prompt": long_form_thumbnail_prompt,
        },
        "visual_standard": {
            "id": project.config.images.visual_standard_id,
            "enforced": project.config.images.enforce_visual_standard,
            "thumbnail_must_match_scene_images": not is_theme_based_long,
            "reference_policy": (
                "theme_only_no_reference" if is_theme_based_long else "required_opening_scene_image"
            ),
            "required_prompt_markers": [
                project.config.images.visual_standard_id,
                "animação 3D cinematográfica familiar premium",
            ],
            "required_typography_prompt_markers": (
                []
                if is_theme_based_long
                else [
                    "integrated_extruded_3d_v1",
                    "tipografia 3D extrudada integrada",
                    "bordas chanfradas",
                    "sombras de contato",
                    "perspectiva coerente",
                    "material coerente com a cena",
                ]
            ),
            "forbidden_positive_style_markers": list(_PHOTOGRAPHIC_THUMBNAIL_STYLE_MARKERS),
        },
        "outputs": {
            "package": relative_posix(layout.growth_package_staging, layout.root),
            "thumbnails": thumbnail_paths,
            "thumbnail_count": thumbnail_count,
            "thumbnail_design_count": config.title_variants,
            "thumbnail_source_mode": (
                "generated_theme_art" if is_theme_based_long else "generated_integrated_art"
            ),
            "thumbnail_reference_mode": config.thumbnail_reference_mode,
            "thumbnail_size": thumbnail_size,
            "thumbnail_role": "short_first_frame" if is_short else "custom_thumbnail",
            "base_art_without_text": is_theme_based_long,
            "external_image_generation_required": True,
            "text_overlay_required": False,
            "imagegen_text_authority": not is_theme_based_long,
            "text_forbidden": is_theme_based_long,
            "title_standard": config.title_standard,
            "description_standard": config.description_standard,
            "thumbnail_standard": config.thumbnail_standard,
            "thumbnail_typography_standard": config.thumbnail_typography_standard,
            "title_thumbnail_pairing_standard": config.title_thumbnail_pairing_standard,
            "design_contract": (
                {
                    "text_word_count": [0, 0],
                    "maximum_lines": 0,
                    "allowed_text_colors": [],
                    "allowed_text_alignments": [],
                    "typography_standard": "no_text_v1",
                    "typography_treatment": "none",
                    "safe_areas_required": True,
                    "clean_negative_space_required": True,
                    "character_frame_ratio": [0.30, 0.40],
                    "character_side": ["left", "right"],
                    "text_must_be_baked_into_bitmap": False,
                    "text_forbidden": True,
                    "paired_variants_required": True,
                }
                if is_theme_based_long
                else {
                    "text_word_count": [2, 5],
                    "maximum_lines": 3,
                    "allowed_text_colors": ["white", "yellow", "red"],
                    "allowed_text_alignments": ["left", "center", "right"],
                    "strong_outline": True,
                    "strong_shadow": True,
                    "texture": "subtle",
                    "typography_standard": config.thumbnail_typography_standard,
                    "typography_treatment": "integrated_extruded_3d",
                    "visible_extrusion": True,
                    "beveled_edges": True,
                    "contact_and_occlusion_shadows": True,
                    "scene_coherent_material_and_lighting": True,
                    "perspective_matches_scene": True,
                    "hierarchy_by_scale": "dominant_keyword_scale",
                    "safe_areas_required": True,
                    "optional_narrative_elements": ["arrow", "x", "underline", "brush"],
                    "pillow_is_text_authority": False,
                    "text_must_be_baked_into_bitmap": True,
                    "paired_variants_required": True,
                    "title_thumbnail_repetition_allowed": (
                        config.title_thumbnail_pairing_standard != "complementary_v1"
                    ),
                }
            ),
        },
        "generation_retry_policy": imagegen_retry_policy(),
        "schemas": {
            "package": compact_json_schema(YouTubeGrowthPackage.model_json_schema()),
        },
        "official_guidelines": list(OFFICIAL_YOUTUBE_GUIDELINES),
        "instructions": [
            (
                "Leia somente inputs.compact_context; o creative_contract é a autoridade para "
                "promessa, primeira fala, resolução, modo criativo e função de cada trecho."
            ),
            (
                "Copie creative_contract.content_promise e creative_contract.opening_hook "
                "literalmente para os campos homônimos da strategy. Quando hook_used=false, "
                "opening_hook representa a primeira fala contratada, não uma autorização para "
                "transformá-la em hook sensacionalista."
            ),
            "Preserve rigorosamente fatos, passagens e fontes do projeto.",
            f"Entregue {config.title_variants} combinações distintas de título para teste.",
            "Use títulos precisos e curtos, com termos importantes no início e no máximo "
            "100 caracteres.",
            "Crie descrição única; explique o vídeo e use uma ou duas expressões centrais "
            "nas primeiras linhas.",
            "Trate SEO, CTR e alcance como hipóteses; nunca prometa viralização ou invente "
            "métricas.",
            "Use somente metric_learning quando houver dados; nunca invente volume de busca.",
            "Faça título e thumbnail se complementarem sem repetir mecanicamente a mesma frase.",
            "Associe título e thumbnail pelo mesmo índice: a primeira imagem acompanha o título "
            "principal.",
            "Evite clickbait enganoso, excesso de elementos, choque gratuito e violações "
            "das políticas.",
            "Preencha package.json no schema fornecido, repetindo job_id e input_hash da fila.",
            "Não altere project.json, fila, aprovações, ativos ou arquivos fora de staging.",
        ],
    }
    if is_theme_based_long:
        payload["instructions"].extend(
            [
                (
                    "Gere exatamente uma thumbnail horizontal 16:9 a partir de "
                    "inputs.thumbnail_prompt, sem usar imagem de referência. O tema em "
                    "inputs.thumbnail_theme é a única base narrativa da composição."
                ),
                (
                    "Copie inputs.thumbnail_prompt integralmente e sem acréscimos para "
                    "metadata.thumbnail_prompt e use esse mesmo texto na chamada de imagegen."
                ),
                (
                    "Entregue somente o título principal e a thumbnail principal: mantenha "
                    "metadata.alternative_titles e metadata.thumbnail_alternatives vazios."
                ),
                (
                    "A imagem final não pode conter texto, letras, números, logotipos, "
                    "marcas-d'água, seta, X, sublinhado, pincel ou qualquer marcação editorial."
                ),
                (
                    "Preencha um thumbnail_design sem tipografia: text e accent_word vazios, "
                    "text_lines vazio, texture=none, typography_treatment=none, "
                    "typography_hierarchy=none, typography_material=none e "
                    "narrative_element=none."
                ),
                (
                    "Use metadata.thumbnail_concept para resumir o único conceito visual e "
                    "copie esse conceito para strategy.promise_match.thumbnail_delivery."
                ),
                (
                    "Preserve a área limpa no lado oposto ao personagem, mas não insira texto "
                    "agora e não aplique overlay local depois."
                ),
            ]
        )
    else:
        payload["instructions"].extend(
            [
                (
                    "Construa um conflito visual simples com escala dramática, um personagem ou "
                    "objeto dominante, emoção legível, luz direcional e área negativa planejada "
                    "para o texto."
                ),
                (
                    "A thumbnail precisa pertencer exatamente ao mesmo universo visual das cenas. "
                    "Use visual_standard.id, visual_system.style, visual_system.palette e a "
                    "opening_scene como contrato, preservando animação 3D cinematográfica familiar "
                    "premium, materiais estilizados, proporções, desenho de personagem, paleta e "
                    "iluminação de longa-metragem."
                ),
                (
                    "Comece metadata.thumbnail_prompt com 'Visual standard: "
                    f"{project.config.images.visual_standard_id}. Style/medium: animação 3D "
                    "cinematográfica familiar premium.' Não descreva o estilo positivo como "
                    "photography, realistic, photorealistic, live action, fotografia editorial ou "
                    "retrato fotorrealista."
                ),
                (
                    "Preencha thumbnail_designs com um design por candidata, no mesmo índice do "
                    "título: texto honesto de duas a cinco palavras, hierarquia em até três "
                    "linhas, cores white/yellow/red, alinhamento coerente com o espaço negativo, "
                    "contorno e "
                    "sombra fortes e textura sutil."
                ),
                (
                    "Use obrigatoriamente thumbnail_typography_standard="
                    "integrated_extruded_3d_v1. Em cada design, mantenha "
                    "typography_treatment=integrated_extruded_3d, "
                    "typography_hierarchy=dominant_keyword_scale e "
                    "typography_material=scene_lit_text_material."
                ),
                (
                    "Inclua literalmente em metadata.thumbnail_prompt: 'Typography standard: "
                    "integrated_extruded_3d_v1. Tipografia 3D extrudada integrada, com espessura "
                    "visível, bordas chanfradas, sombras de contato e oclusão, perspectiva "
                    "coerente e material coerente com a cena.'"
                ),
                (
                    "Modele as letras como objetos 3D pertencentes à cena: volume espesso visível, "
                    "bevel nas bordas, textura material, highlights direcionais e sombras de "
                    "contato coerentes com a luz. A palavra ou linha dominante deve ser muito "
                    "maior que as "
                    "demais; não use todas as palavras no mesmo tamanho."
                ),
                (
                    "A palavra vermelha deve receber o mesmo volume, bevel, material e "
                    "profundidade das letras principais. A seta/X/sublinhado/pincel também deve "
                    "ter espessura, "
                    "perspectiva e sombra coerentes, nunca aparência de adesivo ou overlay plano."
                ),
                (
                    "Respeite safe areas e verifique a legibilidade em tela pequena sem cobrir "
                    "rostos, ação ou o objeto dominante."
                ),
                (
                    "Use arrow, x, underline ou brush somente quando o elemento narrativo for "
                    "honesto e relevante; identifique o alvo e registre a justificativa editorial."
                ),
            ]
        )
    if is_short:
        payload["outputs"]["design_contract"]["top_text_safe_margin_ratio"] = 0.10
        payload["instructions"].append(
            "Somente para esta thumbnail vertical de Short, reserve os 10% superiores do "
            "canvas como margem de segurança totalmente livre de tipografia. Inclua "
            "literalmente em metadata.thumbnail_prompt: 'Short top safe area: keep the top "
            "10% of the canvas completely free of text, letters, extrusion, outlines and "
            "shadows; place every word below this boundary.' Ao editar com imagegen, trate "
            "volume, extrusão, contorno e sombra como parte da tipografia: nenhum pixel "
            "desses elementos pode entrar nessa faixa. Esta regra não se aplica a "
            "thumbnails de vídeos longos."
        )
    if config.title_standard == "passage_pipe_topic_v1":
        main_passage = str(
            growth_context.get("creative_contract", {}).get("main_passage", "")
        ).strip()
        payload["instructions"].extend(
            [
                (
                    "Todos os títulos devem começar exatamente com a passagem principal, seguida "
                    f"de barra vertical: '{main_passage} | Tema principal'. Depois da barra, use "
                    "o personagem, confronto ou assunto que uma pessoa reconhece imediatamente."
                ),
                (
                    "Evite teses abstratas no título quando o nome do personagem ou do conflito "
                    "for mais forte. Mantenha a promessa fiel ao vídeo."
                ),
            ]
        )
    elif config.title_standard == "tension_pipe_passage_v2":
        main_passage = str(
            growth_context.get("creative_contract", {}).get("main_passage", "")
        ).strip()
        payload["instructions"].extend(
            [
                (
                    "Todos os títulos devem seguir exatamente 'Tensão ou promessa concreta | "
                    f"{main_passage}'. Coloque a passagem depois da barra e faça a primeira parte "
                    "entregar o conflito específico do cold open, sem pergunta genérica."
                ),
                (
                    "Preencha strategy.promise_match copiando content_promise, opening_hook como "
                    "opening_line e contract_guard.opening_scene_id. Em title_delivery, copie "
                    "exatamente a parte do título antes da barra. Em thumbnail_delivery, copie "
                    + (
                        "metadata.thumbnail_concept."
                        if is_theme_based_long
                        else "exatamente o texto do primeiro thumbnail_design."
                    )
                ),
            ]
        )
    elif config.title_standard == "creative_pipe_passage_v3":
        main_passage = str(
            growth_context.get("creative_contract", {}).get("main_passage", "")
        ).strip()
        payload["instructions"].extend(
            [
                (
                    "Todos os títulos devem seguir 'Enfoque concreto | "
                    f"{main_passage}'. Antes da barra, adapte a forma ao creative_mode: fato "
                    "narrativo, personagem, curiosidade, pergunta específica, comparação, dilema, "
                    "tese interpretativa ou promessa prática. Não force tensão motivacional."
                ),
                (
                    "Preencha strategy.promise_match copiando content_promise, opening_hook como "
                    "opening_line e contract_guard.opening_scene_id. Em title_delivery, copie "
                    "exatamente a parte do título antes da barra. Em thumbnail_delivery, copie "
                    + (
                        "metadata.thumbnail_concept."
                        if is_theme_based_long
                        else "exatamente o texto do primeiro thumbnail_design."
                    )
                ),
            ]
        )
    if config.description_standard == "scripture_timeline_v1" and not is_short:
        payload["instructions"].extend(
            [
                "Abra a descrição com um resumo curto e direto do conteúdo do vídeo.",
                (
                    "O pipeline montará deterministicamente a timeline das citações bíblicas. "
                    "Ela deve começar por '00:00 - Abertura'; não invente minutagens."
                ),
            ]
        )
    if config.thumbnail_standard == "direct_conflict_red_v1":
        payload["instructions"].extend(
            [
                (
                    "Use frases de thumbnail específicas e imediatamente reconhecíveis, com nome, "
                    "confronto, perigo ou decisão concreta. Prefira construções como 'DAVI VS "
                    "GOLIAS' a abstrações como 'ANTES DA PEDRA'."
                ),
                (
                    "Em cada design, use accent_color=red na palavra de maior impacto e inclua um "
                    "elemento narrativo vermelho — arrow, x, underline ou brush — com alvo e "
                    "justificativa claros. O elemento deve apontar ou marcar algo real na cena."
                ),
                (
                    "O texto deve ocupar área visual forte e legível, sem encobrir rosto, ação ou "
                    "objeto principal. Sensacionalismo visual é permitido; promessa enganosa não."
                ),
            ]
        )
    if config.title_thumbnail_pairing_standard == "complementary_v1":
        payload["instructions"].extend(
            [
                (
                    f"Crie {pair_count_label} por índice. O título principal acompanha a primeira "
                    "thumbnail; quando houver alternative_titles, cada um acompanha a thumbnail "
                    "seguinte."
                ),
                (
                    "Em cada par, o título deve carregar um enfoque concreto coerente com o "
                    "modo criativo, referência e busca; a imagem acrescenta emoção, gesto, "
                    "paisagem ou detalhe visual sem texto."
                    if is_theme_based_long and config.title_standard == "creative_pipe_passage_v3"
                    else "Em cada par, o título deve carregar tensão concreta, referência e "
                    "busca; a imagem acrescenta emoção, gesto, paisagem e conflito visual sem "
                    "texto."
                    if is_theme_based_long
                    else (
                        "Em cada par, o título deve carregar um enfoque concreto coerente com o "
                        "modo criativo, referência e busca; a thumbnail acrescenta conflito, "
                        "decisão, detalhe ou consequência visual."
                        if config.title_standard == "creative_pipe_passage_v3"
                        else "Em cada par, o título deve carregar tensão concreta, referência "
                        "e busca; a thumbnail acrescenta conflito, decisão ou consequência "
                        "visual."
                        if config.title_standard == "tension_pipe_passage_v2"
                        else "Em cada par, distribua a promessa: o título deve carregar "
                        "referência, tema e intenção de busca; o texto da thumbnail deve "
                        "acrescentar tensão, decisão, contraste, consequência ou pergunta visual "
                        "que o título ainda não disse."
                    )
                ),
                (
                    "Preencha title_thumbnail_complement explicando de forma concisa a função "
                    f"complementar {pair_scope_label}."
                ),
            ]
        )
        if not is_theme_based_long:
            payload["instructions"].append(
                "É proibido repetir na thumbnail a tensão escrita antes da barra vertical. "
                "Também não use a frase inteira da thumbnail dentro do título."
                if config.title_standard in {"tension_pipe_passage_v2", "creative_pipe_passage_v3"}
                else "É proibido repetir na thumbnail a frase temática que aparece depois da "
                "barra vertical do título. Também não use essa frase inteira como trecho "
                "dentro da thumbnail, nem a frase inteira da thumbnail dentro do título."
            )
    payload["instructions"].extend(
        [
            (
                "Gere cada thumbnail final com uma chamada independente de imagegen e salve "
                "o bitmap completo exatamente no caminho de staging do mesmo índice."
            ),
            *imagegen_retry_instructions(asset_label="thumbnail"),
        ]
    )
    if is_theme_based_long:
        payload["instructions"].extend(
            [
                "Não passe referenced_image_paths: esta geração usa somente o prompt temático.",
                (
                    "Depois que a primeira geração produzir um bitmap válido, aceite essa saída "
                    "sem repetir por qualidade ou deriva visual. Nova tentativa automática só é "
                    "permitida quando não houver saída por moderation_blocked."
                ),
            ]
        )
    else:
        payload["instructions"].extend(
            [
                (
                    "Inclua na própria geração o texto exato de thumbnail_design, sua palavra de "
                    "destaque vermelha e o elemento narrativo vermelho. Texto e símbolo precisam "
                    "participar da iluminação, profundidade, perspectiva e composição da arte; "
                    "não entregue arte-base limpa nem aparência de camada solta."
                ),
                (
                    "Componha a tipografia 3D como camada de primeiro ou médio plano da imagem, "
                    "com oclusão e sombra de contato convincentes. Preserve a leitura instantânea "
                    "em "
                    "tela pequena e a hierarquia dramática entre linha auxiliar, palavra dominante "
                    "e destaque vermelho."
                ),
                (
                    "Confirme visualmente a grafia e os acentos antes de concluir. Não aceite "
                    "texto adicional, duplicado, deformado ou diferente de thumbnail_design.text."
                ),
                (
                    "Reserve espaço negativo real para a tipografia e nunca cubra rosto, corpo, "
                    "ação principal ou o alvo do símbolo. O símbolo deve apontar ou marcar "
                    "precisamente o alvo declarado."
                ),
                (
                    "O importador apenas normalizará dimensões e formato; não aplicará texto, "
                    "seta, X, sublinhado ou pincel com Pillow."
                ),
                (
                    f"{image_count_label} {image_pairing_verb} aos títulos pelo índice. Em Shorts, "
                    "a primeira candidata integrada ocupará o frame zero; eventuais candidatas "
                    "adicionais ficam como alternativas versionadas."
                ),
                (
                    "Edite diretamente inputs.thumbnail_visual_reference_path com imagegen, "
                    "passando esse arquivo em referenced_image_paths. Use esse arquivo exato como "
                    "imagem de referência e preserve a cena, o personagem e a identidade visual; "
                    "acrescente somente a composição de thumbnail, o texto e a marcação narrativa "
                    "solicitados."
                ),
                (
                    "Depois que a primeira edição produzir um bitmap válido, aceite essa saída sem "
                    "repetir por qualidade, deriva visual ou imperfeição de texto. Nova tentativa "
                    "automática continua permitida somente quando não houver saída por "
                    "moderation_blocked, conforme generation_retry_policy."
                ),
            ]
        )

    invalidate(
        layout,
        stages=("metadata", "thumbnail", "validation"),
        approvals=("metadata", "final_render"),
        note="Um novo pacote de crescimento para YouTube foi solicitado.",
    )
    start_stage(layout, "youtube_growth", input_hash, provider=config.specialist_role)
    write_json(layout.growth_queue, payload)
    wait_stage(
        layout,
        "youtube_growth",
        input_hash,
        "Aguardando o agente configurado produzir o pacote e a thumbnail no staging.",
        [
            relative_posix(layout.growth_queue, layout.root),
            relative_posix(layout.growth_context, layout.root),
        ],
    )
    return payload


def _validate_thumbnail_visual_standard(
    package: YouTubeGrowthPackage,
    queue: dict[str, Any],
) -> None:
    outputs = queue.get("outputs", {})
    source_mode = outputs.get("thumbnail_source_mode")
    if source_mode not in {"generated_integrated_art", "generated_theme_art"}:
        return
    prompt = package.metadata.thumbnail_prompt.strip()
    if source_mode == "generated_theme_art":
        expected_prompt = str(queue.get("inputs", {}).get("thumbnail_prompt") or "").strip()
        if not expected_prompt or prompt != expected_prompt:
            raise ValueError(
                "O prompt da thumbnail longa precisa copiar integralmente o template temático."
            )
    contract = queue.get("visual_standard")
    if not isinstance(contract, dict) or not contract.get("enforced"):
        return
    positive_prompt = _prompt_without_negative_sections(prompt)
    normalized_prompt = _normalized_search_text(positive_prompt)
    standard_id = str(contract.get("id", "")).strip()
    if (
        source_mode == "generated_integrated_art"
        and standard_id
        and _normalized_search_text(standard_id) not in normalized_prompt
    ):
        raise ValueError(
            "O prompt da thumbnail não declara o visual_standard obrigatório das cenas."
        )
    photographic_markers = contract.get(
        "forbidden_positive_style_markers",
        _PHOTOGRAPHIC_THUMBNAIL_STYLE_MARKERS,
    )
    if any(
        _contains_positive_style_marker(normalized_prompt, str(marker))
        for marker in photographic_markers
        if str(marker).strip()
    ):
        raise ValueError(
            "O prompt da thumbnail tenta usar fotografia ou fotorrealismo em vez do padrão 3D."
        )
    typography_standard = str(outputs.get("thumbnail_typography_standard", "")).strip()
    if typography_standard == "integrated_extruded_3d_v1":
        required_typography_markers = contract.get(
            "required_typography_prompt_markers",
            ["integrated_extruded_3d_v1", "tipografia 3D extrudada integrada"],
        )
        missing_markers = [
            str(marker)
            for marker in required_typography_markers
            if str(marker).strip() and _normalized_search_text(str(marker)) not in normalized_prompt
        ]
        if missing_markers:
            raise ValueError(
                "O prompt da thumbnail não declara o tratamento obrigatório de tipografia 3D "
                f"integrada: {', '.join(missing_markers)}."
            )


def _contains_positive_style_marker(normalized_prompt: str, marker: str) -> bool:
    prompt_tokens = normalized_prompt.split()
    marker_tokens = _normalized_search_text(marker).split()
    if not marker_tokens:
        return False
    negations = {
        "avoid",
        "evitar",
        "evite",
        "nao",
        "não",
        "never",
        "not",
        "nunca",
        "sem",
        "without",
    }
    marker_length = len(marker_tokens)
    for index in range(len(prompt_tokens) - marker_length + 1):
        if prompt_tokens[index : index + marker_length] != marker_tokens:
            continue
        prefix = prompt_tokens[max(0, index - 4) : index]
        if not any(token in negations for token in prefix):
            return True
    return False


_NEGATIVE_PROMPT_SECTION_LABELS = {
    "avoid",
    "evitar",
    "evite",
    "negative constraints",
    "negative prompt",
    "restricoes negativas",
}


def _prompt_without_negative_sections(prompt: str) -> str:
    positive_lines: list[str] = []
    inside_negative_section = False
    for line in prompt.splitlines():
        section_match = re.match(r"^\s*([^:\n]{1,80})\s*:\s*(.*)$", line)
        if section_match:
            label = _normalized_search_text(section_match.group(1))
            inside_negative_section = label in _NEGATIVE_PROMPT_SECTION_LABELS
        if not inside_negative_section:
            positive_lines.append(line)
    return "\n".join(positive_lines)


def _safe_staging_file(layout: ProjectLayout, relative_path: str) -> Path:
    path = (layout.root / relative_path).resolve()
    try:
        path.relative_to(layout.growth_staging.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho fora do staging do especialista: {relative_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Saída do especialista ausente: {path}")
    return path


def _normalized_search_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


_PAIRING_STOP_WORDS = {
    "a",
    "as",
    "de",
    "da",
    "das",
    "do",
    "dos",
    "e",
    "em",
    "o",
    "os",
    "um",
    "uma",
}
_PAIRING_CONFLICT_WORDS = {
    "contra",
    "confronta",
    "confronto",
    "enfrenta",
    "enfrentando",
    "enfrentar",
    "enfrentou",
    "versus",
    "vs",
}


def _title_topic(title: str, title_standard: str) -> str:
    if "|" not in title:
        return title.strip()
    left, right = (part.strip() for part in title.split("|", 1))
    return (
        left if title_standard in {"tension_pipe_passage_v2", "creative_pipe_passage_v3"} else right
    )


def _pairing_content_tokens(value: str) -> list[str]:
    words = _normalized_search_text(value).split()
    return [
        "contra" if word in _PAIRING_CONFLICT_WORDS else word
        for word in words
        if word not in _PAIRING_STOP_WORDS
    ]


def _title_repeats_thumbnail(
    title: str,
    thumbnail_text: str,
    *,
    title_standard: str,
) -> bool:
    topic = _normalized_search_text(_title_topic(title, title_standard))
    thumbnail = _normalized_search_text(thumbnail_text)
    if not topic or not thumbnail:
        return False
    if topic == thumbnail:
        return True
    topic_words = topic.split()
    thumbnail_words = thumbnail.split()
    if len(thumbnail_words) >= 2 and thumbnail in topic:
        return True
    if len(topic_words) >= 2 and topic in thumbnail:
        return True
    topic_tokens = set(_pairing_content_tokens(topic))
    thumbnail_tokens = set(_pairing_content_tokens(thumbnail))
    if len(topic_tokens) < 2 or len(thumbnail_tokens) < 2:
        return False
    overlap = len(topic_tokens & thumbnail_tokens) / len(topic_tokens | thumbnail_tokens)
    return overlap >= 0.8


def _validate_package(
    layout: ProjectLayout, package: YouTubeGrowthPackage, queue: dict[str, Any]
) -> None:
    if queue.get("schema_version") != GROWTH_QUEUE_SCHEMA_VERSION:
        raise ValueError("A versão da fila de crescimento não é mais suportada.")
    if package.job_id != queue.get("job_id") or package.input_hash != queue.get("input_hash"):
        raise RuntimeError("O pacote do especialista pertence a uma fila antiga.")
    _validate_thumbnail_visual_standard(package, queue)
    project = layout.load_manifest()
    metadata = package.metadata
    titles = [metadata.title, *metadata.alternative_titles]
    required_titles = project.config.youtube_growth.title_variants
    distinct_titles = {title.strip().casefold() for title in titles if title.strip()}
    if len(distinct_titles) != required_titles or len(titles) != required_titles:
        raise ValueError(f"O pacote precisa de exatamente {required_titles} títulos distintos.")
    outputs = queue.get("outputs", {})
    expected_designs = int(outputs.get("thumbnail_design_count", 0))
    if not expected_designs:
        raise ValueError("A fila precisa declarar thumbnail_design_count.")
    if len(titles) < expected_designs:
        raise ValueError("Cada thumbnail precisa de um título correspondente no mesmo índice.")
    if any(len(title) > 100 for title in titles):
        raise ValueError("Todos os títulos precisam ter no máximo 100 caracteres.")
    if not metadata.description.strip():
        raise ValueError("A descrição do especialista está vazia.")
    expected_format = "short" if project.video_format == "short" else "long_form"
    if package.strategy.format != expected_format:
        raise ValueError("A estratégia não corresponde ao formato do projeto.")
    contract_guard = queue.get("contract_guard", {})
    if package.strategy.content_promise != contract_guard.get("content_promise"):
        raise ValueError(
            "A estratégia mudou a promessa do contrato criativo; "
            "copie content_promise literalmente."
        )
    if package.strategy.opening_hook != contract_guard.get("opening_hook"):
        raise ValueError(
            "A estratégia mudou o gancho do contrato criativo; copie opening_hook literalmente."
        )
    if len(package.strategy.thumbnail_designs) != expected_designs:
        raise ValueError("O pacote precisa de um thumbnail_design por candidata.")
    typography_standard = outputs.get("thumbnail_typography_standard")
    if typography_standard == "integrated_extruded_3d_v1":
        if any(design.texture != "subtle_grain" for design in package.strategy.thumbnail_designs):
            raise ValueError("O novo padrão de thumbnail exige textura sutil no texto.")
        for design in package.strategy.thumbnail_designs:
            if design.typography_treatment != "integrated_extruded_3d":
                raise ValueError("A thumbnail precisa usar tipografia 3D extrudada integrada.")
            if design.typography_hierarchy != "dominant_keyword_scale":
                raise ValueError("A thumbnail precisa hierarquizar a palavra dominante por escala.")
            if design.typography_material != "scene_lit_text_material":
                raise ValueError("A tipografia precisa compartilhar material e luz com a cena.")
    elif typography_standard == "no_text_v1":
        for design in package.strategy.thumbnail_designs:
            if (
                design.text.strip()
                or design.accent_word.strip()
                or design.text_lines
                or design.typography_treatment != "none"
                or design.typography_hierarchy != "none"
                or design.typography_material != "none"
                or design.narrative_element != "none"
            ):
                raise ValueError("A thumbnail longa precisa permanecer sem texto ou marcações.")
    if (
        outputs.get("title_thumbnail_pairing_standard") == "complementary_v1"
        and typography_standard != "no_text_v1"
    ):
        designs = package.strategy.thumbnail_designs
        normalized_design_texts = {_normalized_search_text(design.text) for design in designs}
        if len(normalized_design_texts) != expected_designs:
            raise ValueError("Cada título precisa de uma frase de thumbnail distinta.")
        for index, (title, design) in enumerate(
            zip(titles[:expected_designs], designs, strict=True),
            start=1,
        ):
            if _title_repeats_thumbnail(
                title,
                design.text,
                title_standard=str(outputs.get("title_standard", "")),
            ):
                raise ValueError(
                    f"O par {index} repete na thumbnail a promessa do título; "
                    "os dois textos precisam se complementar."
                )
        if not package.strategy.title_thumbnail_complement.strip():
            label = "o par" if expected_designs == 1 else "os pares"
            raise ValueError(f"Explique como {label} de título e thumbnail se complementam.")
    if outputs.get("thumbnail_standard") == "direct_conflict_red_v1":
        for design in package.strategy.thumbnail_designs:
            if design.accent_color != "red":
                raise ValueError("A palavra de destaque da thumbnail precisa usar vermelho.")
            if design.narrative_element == "none":
                raise ValueError(
                    "Cada thumbnail precisa de seta, X, sublinhado ou pincel narrativo."
                )
            if not (design.narrative_element_target or "").strip():
                raise ValueError("O elemento narrativo da thumbnail precisa declarar seu alvo.")
    source_mode = outputs.get("thumbnail_source_mode")
    if source_mode not in {"generated_integrated_art", "generated_theme_art"}:
        raise ValueError("A fila precisa usar thumbnail gerada pelo modelo.")
    declared_paths = outputs.get("thumbnails", [])
    if len(declared_paths) != int(outputs.get("thumbnail_count", 0)):
        raise ValueError("A fila integrada precisa declarar um arquivo por thumbnail.")
    if int(outputs.get("thumbnail_count", 0)) == 1 and metadata.thumbnail_alternatives:
        raise ValueError("O pacote de uma única thumbnail não pode declarar alternativas.")
    if outputs.get("text_overlay_required") is not False:
        raise ValueError("A fila integrada não pode solicitar overlay local de texto.")
    if (
        source_mode == "generated_integrated_art"
        and outputs.get("imagegen_text_authority") is not True
    ):
        raise ValueError("A fila integrada precisa declarar o imagegen como autoridade textual.")
    if source_mode == "generated_theme_art":
        if outputs.get("imagegen_text_authority") is not False:
            raise ValueError("A thumbnail sem texto não pode declarar autoridade textual.")
        if outputs.get("text_forbidden") is not True:
            raise ValueError("A fila temática precisa proibir texto no bitmap.")
    if project.video_format == "short":
        if len(metadata.title) > SHORT_TITLE_MAX_CHARACTERS:
            raise ValueError(
                f"O título principal do Short precisa ter no máximo "
                f"{SHORT_TITLE_MAX_CHARACTERS} caracteres."
            )
        if len(metadata.description) > SHORT_DESCRIPTION_MAX_CHARACTERS:
            raise ValueError(
                f"A descrição do Short precisa ter no máximo "
                f"{SHORT_DESCRIPTION_MAX_CHARACTERS} caracteres."
            )
        if metadata.chapters:
            raise ValueError("Shorts não devem trazer capítulos no pacote de crescimento.")
        primary_keyword = _normalized_search_text(package.strategy.primary_keyword)
        if not primary_keyword or primary_keyword not in _normalized_search_text(metadata.title):
            raise ValueError("O título principal do Short precisa conter primary_keyword.")
        description_opening = metadata.description[:SHORT_DESCRIPTION_SEARCH_WINDOW]
        if primary_keyword not in _normalized_search_text(description_opening):
            raise ValueError("A abertura da descrição do Short precisa conter primary_keyword.")
        briefing = read_model(layout.briefing, Briefing)
        passage = _normalized_search_text(briefing.main_passage)
        if passage and not any(passage in _normalized_search_text(title) for title in titles):
            raise ValueError(
                "Ao menos uma opção de título do Short precisa conter a passagem principal."
            )
        if package.strategy.short_cover_timestamp_seconds != 0:
            raise ValueError("A capa do Short precisa ocupar o frame zero.")
    if outputs.get("title_standard") == "passage_pipe_topic_v1":
        briefing = read_model(layout.briefing, Briefing)
        prefix = f"{briefing.main_passage.strip()} | "
        if not prefix.strip(" |") or any(not title.startswith(prefix) for title in titles):
            raise ValueError(
                "Todos os títulos precisam seguir 'Passagem principal | Tema principal'."
            )
    elif outputs.get("title_standard") in {
        "tension_pipe_passage_v2",
        "creative_pipe_passage_v3",
    }:
        briefing = read_model(layout.briefing, Briefing)
        suffix = f" | {briefing.main_passage.strip()}"
        if not suffix.strip(" |") or any(
            not title.endswith(suffix) or not title.removesuffix(suffix).strip() for title in titles
        ):
            expected = (
                "Enfoque concreto | Passagem principal"
                if outputs.get("title_standard") == "creative_pipe_passage_v3"
                else "Tensão concreta | Passagem principal"
            )
            raise ValueError(f"Todos os títulos precisam seguir '{expected}'.")
        promise_match = package.strategy.promise_match
        if promise_match is None:
            raise ValueError("Este padrão de título exige strategy.promise_match.")
        if promise_match.content_promise != contract_guard.get("content_promise"):
            raise ValueError("promise_match mudou a promessa do contrato criativo.")
        if promise_match.opening_line != contract_guard.get("opening_hook"):
            raise ValueError("promise_match mudou a primeira fala do contrato criativo.")
        if promise_match.opening_scene_id != contract_guard.get("opening_scene_id"):
            raise ValueError("promise_match não aponta para a cena de abertura ativa.")
        if promise_match.title_delivery != metadata.title.removesuffix(suffix).strip():
            raise ValueError("title_delivery precisa copiar o enfoque antes da barra.")
        expected_thumbnail_delivery = (
            metadata.thumbnail_concept
            if typography_standard == "no_text_v1"
            else package.strategy.thumbnail_designs[0].text
        )
        if promise_match.thumbnail_delivery != expected_thumbnail_delivery:
            raise ValueError(
                "thumbnail_delivery precisa copiar o conceito visual da thumbnail principal."
                if typography_standard == "no_text_v1"
                else "thumbnail_delivery precisa copiar o texto da thumbnail principal."
            )
    if len(package.strategy.official_guidelines) < 3 or not set(
        package.strategy.official_guidelines
    ).issubset(OFFICIAL_YOUTUBE_GUIDELINES):
        raise ValueError(
            "A estratégia deve citar somente as orientações oficiais fornecidas na fila."
        )

    sources = read_model(layout.sources, SourceLibrary)
    script = read_model(layout.script, ScriptDocument)
    source_ids = {item.source_id for item in sources.entries}
    section_ids = {item.section_id for item in script.sections}
    if not set(package.strategy.source_ids).issubset(source_ids):
        raise ValueError("A estratégia referencia source_id inexistente.")
    if not set(package.strategy.section_ids).issubset(section_ids):
        raise ValueError("A estratégia referencia section_id inexistente.")


def _next_thumbnail_version(layout: ProjectLayout) -> int:
    versions = []
    for path in layout.thumbnail.parent.glob("thumbnail-v*.png"):
        match = re.fullmatch(r"thumbnail-v(\d+)\.png", path.name)
        if match:
            versions.append(int(match.group(1)))
    return max(versions, default=0) + 1


def _validate_thumbnail_sources(paths: list[Path]) -> None:
    for path in paths:
        try:
            with Image.open(path) as image:
                image.load()
                if min(image.size) < 320:
                    raise ValueError(f"Thumbnail pequena demais: {path.name} ({image.size})")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Thumbnail inválida: {path}") from exc


def _chapters_for_project(
    layout: ProjectLayout,
    scenes: SceneManifest,
    audio: AudioManifest | None,
) -> list[str]:
    if layout.load_manifest().video_format == "short":
        return []
    project = layout.load_manifest()
    script = read_model(layout.script, ScriptDocument) if layout.script.is_file() else None
    return _build_chapters(
        scenes,
        audio,
        script,
        scripture_timeline=(
            project.config.youtube_growth.description_standard == "scripture_timeline_v1"
        ),
    )


def _description_document(metadata: YouTubeMetadata) -> str:
    parts = [metadata.description.strip()]
    if metadata.chapters:
        parts.append("\n".join(metadata.chapters))
    return "\n\n".join(parts)


def _normalize_thumbnail(
    source: Path,
    destination: Path,
    size: tuple[int, int],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with Image.open(source) as image:
        normalized = ImageOps.fit(
            image.convert("RGB"),
            size,
            method=Image.Resampling.LANCZOS,
        )
        normalized.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, destination)


def import_integrated_thumbnail_candidates(
    layout: ProjectLayout,
    sources: list[Path],
) -> list[Path]:
    """Importa bitmaps finais do imagegen sem adicionar camadas locais de texto ou símbolos."""

    manifest_path = layout.thumbnail.parent / "thumbnail-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("O projeto ainda não possui manifesto de thumbnails.")
    thumbnail_manifest = read_json(manifest_path)
    current_candidates = thumbnail_manifest.get("candidates", [])
    if not isinstance(current_candidates, list) or not current_candidates:
        raise ValueError("O projeto não possui candidatas de thumbnail para parear.")
    if len(sources) != len(current_candidates):
        raise ValueError("Informe exatamente uma imagem integrada para cada candidata existente.")

    normalized_sources = [source.expanduser().resolve() for source in sources]
    for source in normalized_sources:
        if not source.is_file():
            raise FileNotFoundError(source)
        try:
            source.relative_to(layout.root.resolve())
        except ValueError as exc:
            raise ValueError(
                "As thumbnails integradas precisam estar em staging dentro do projeto."
            ) from exc
    _validate_thumbnail_sources(normalized_sources)

    project = layout.load_manifest()
    size = (
        (project.config.render.width, project.config.render.height)
        if project.video_format == "short"
        else (1280, 720)
    )
    next_version = _next_thumbnail_version(layout)
    destinations = [
        layout.thumbnail.parent / f"thumbnail-v{next_version + index:03d}.png"
        for index in range(len(normalized_sources))
    ]
    for source, destination in zip(normalized_sources, destinations, strict=True):
        _normalize_thumbnail(source, destination, size)

    previous_paths = [
        candidate.get("path")
        for candidate in current_candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("path"), str)
    ]
    candidates: list[dict[str, Any]] = []
    for index, (source, destination) in enumerate(
        zip(normalized_sources, destinations, strict=True)
    ):
        current = current_candidates[index]
        if not isinstance(current, dict) or not isinstance(current.get("design"), dict):
            raise ValueError("Cada candidata existente precisa registrar seu design editorial.")
        design = YouTubeThumbnailDesign.model_validate(current["design"])
        candidates.append(
            {
                "path": relative_posix(destination, layout.root),
                "hash": sha256_file(destination),
                "source_path": relative_posix(source, layout.root),
                "source_hash": sha256_file(source),
                "paired_title": current.get("paired_title"),
                "design": design.model_dump(mode="json"),
                "text_baked_into_bitmap": True,
            }
        )

    active_relative = thumbnail_manifest.get("active_path")
    if not isinstance(active_relative, str):
        raise ValueError("O manifesto não registra a thumbnail ativa que deve ser preservada.")
    active_path = (layout.root / active_relative).resolve()
    if not active_path.is_file() or not layout.thumbnail.is_file():
        raise FileNotFoundError("A thumbnail ativa não está disponível para preservação.")

    thumbnail_manifest.update(
        {
            "candidates": candidates,
            "approved": False,
            "updated_at": utc_now(),
            "provider": "imagegen-integrated-retouch",
            "composition_mode": "integrated_imagegen_v1",
            "text_baked_into_bitmap": True,
            "previous_candidate_paths": previous_paths,
            "designs": [candidate["design"] for candidate in candidates],
        }
    )
    write_json(manifest_path, thumbnail_manifest)
    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Novas thumbnails integradas pelo imagegen aguardam escolha humana.",
    )
    hashes = [candidate["hash"] for candidate in candidates]
    complete_stage(
        layout,
        "thumbnail",
        sha256_value({"candidates": hashes, "active": sha256_file(active_path)}),
        [
            *[candidate["path"] for candidate in candidates],
            relative_posix(layout.thumbnail, layout.root),
            relative_posix(manifest_path, layout.root),
        ],
        provider="imagegen-integrated-retouch",
        usage={
            "candidates": len(candidates),
            "external_image_generations": len(candidates),
            "integrated_image_generations": len(candidates),
            "local_text_overlays": 0,
            "preserved_active_thumbnail": 1,
        },
        note=(
            "Candidatas com texto e símbolos incorporados pelo imagegen; a thumbnail ativa "
            "anterior foi preservada até a escolha do usuário."
        ),
    )
    layout.append_event(
        {
            "event": "integrated_thumbnail_candidates_imported",
            "candidates": len(candidates),
            "active_path": active_relative,
        }
    )
    return destinations


def refresh_growth_metadata(layout: ProjectLayout) -> YouTubeMetadata:
    if not layout.growth_package.is_file():
        raise FileNotFoundError("O pacote final do especialista ainda não foi importado.")
    package = read_model(layout.growth_package, YouTubeGrowthPackage)
    scenes = read_model(layout.scenes, SceneManifest)
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    metadata = package.metadata.model_copy(
        update={"chapters": _chapters_for_project(layout, scenes, audio)}
    )
    package = package.model_copy(update={"metadata": metadata})
    write_json(layout.metadata, metadata)
    write_json(layout.growth_package, package)
    write_text(layout.description, _description_document(metadata))
    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Os capítulos dos metadados foram sincronizados com o áudio atual.",
    )
    growth_hash = layout.load_manifest().stages["youtube_growth"].input_hash
    metadata_hash = sha256_value(
        {
            "growth_input_hash": growth_hash,
            "package": package,
            "audio_hash": sha256_file(layout.audio) if layout.audio.is_file() else None,
        }
    )
    complete_stage(
        layout,
        "metadata",
        metadata_hash,
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
            relative_posix(layout.thumbnail_prompt, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="youtube-growth-specialist",
        note="Capítulos sincronizados com a timeline de áudio.",
    )
    return metadata


def import_growth_package(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> YouTubeGrowthPackage:
    if not layout.growth_queue.is_file():
        raise FileNotFoundError(f"Fila do especialista ausente: {layout.growth_queue}")
    queue = read_json(layout.growth_queue)
    if not isinstance(queue, dict) or queue.get("role") != "youtube_growth_specialist":
        raise ValueError("Fila do especialista inválida.")
    current_hash = growth_input_hash(layout)
    if queue.get("input_hash") != current_hash:
        raise RuntimeError("A fila do especialista ficou obsoleta após mudanças no projeto.")
    project = layout.load_manifest()
    if project.approvals["metadata"].status == ApprovalStatus.APPROVED and not force:
        raise RuntimeError("Os metadados estão aprovados; use --force para importar nova versão.")

    package_path = _safe_staging_file(layout, queue["outputs"]["package"])
    package = read_model(package_path, YouTubeGrowthPackage)
    _validate_package(layout, package, queue)
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    scenes = read_model(layout.scenes, SceneManifest)
    source_mode = queue["outputs"].get("thumbnail_source_mode")
    if source_mode not in {"generated_integrated_art", "generated_theme_art"}:
        raise ValueError("Apenas thumbnails geradas pelo modelo são suportadas.")
    clean_theme_art = source_mode == "generated_theme_art"
    thumbnail_paths = [
        _safe_staging_file(layout, path) for path in queue["outputs"].get("thumbnails", [])
    ]
    if not thumbnail_paths:
        raise ValueError("A fila não declarou fonte de thumbnail para importação.")
    _validate_thumbnail_sources(thumbnail_paths)

    metadata = package.metadata.model_copy(
        update={"chapters": _chapters_for_project(layout, scenes, audio)}
    )
    if project.video_format == "short" and audio:
        timestamp = package.strategy.short_cover_timestamp_seconds
        if timestamp is None or timestamp > audio.duration:
            raise ValueError("O timestamp da capa do Short está fora da duração do vídeo.")

    size_values = queue["outputs"].get("thumbnail_size", [])
    if not (
        isinstance(size_values, list)
        and len(size_values) == 2
        and all(isinstance(value, int) and value > 0 for value in size_values)
    ):
        raise ValueError("A fila não definiu dimensões válidas para as thumbnails.")
    size = (size_values[0], size_values[1])
    first_version = _next_thumbnail_version(layout)
    version_paths = [
        layout.thumbnail.parent / f"thumbnail-v{first_version + index:03d}.png"
        for index in range(len(thumbnail_paths))
    ]
    candidate_designs = package.strategy.thumbnail_designs
    if len(candidate_designs) != len(thumbnail_paths):
        raise ValueError("Cada thumbnail integrada precisa de um design editorial correspondente.")
    for source, destination in zip(thumbnail_paths, version_paths, strict=True):
        _normalize_thumbnail(source, destination, size)

    first_design = candidate_designs[0]
    first_text = None if clean_theme_art else first_design.text
    first_accent = None if clean_theme_art else first_design.accent_word
    first_position = None if clean_theme_art else first_design.text_position

    write_json(layout.metadata, metadata)
    write_json(layout.growth_strategy, package.strategy)
    final_package = package.model_copy(update={"metadata": metadata})
    write_json(layout.growth_package, final_package)
    write_json(
        layout.thumbnail_prompt,
        {
            "schema_version": "2.0",
            "status": "completed",
            "concept": metadata.thumbnail_concept,
            "prompt": metadata.thumbnail_prompt,
            "alternatives": metadata.thumbnail_alternatives,
            "format_note": (
                "Capa vertical composta e incorporada ao início do Short."
                if project.video_format == "short"
                else "Thumbnail horizontal sem texto gerada a partir do tema do vídeo."
            ),
            "text_overlay": first_text,
            "accent_word": first_accent,
            "text_position": first_position,
            "thumbnail_designs": [design.model_dump(mode="json") for design in candidate_designs],
        },
    )
    write_text(layout.description, _description_document(metadata))
    shutil.copy2(version_paths[0], layout.thumbnail)
    thumbnail_hashes = [sha256_file(path) for path in version_paths]
    write_json(
        layout.thumbnail.parent / "thumbnail-manifest.json",
        {
            "active_path": relative_posix(version_paths[0], layout.root),
            "active_hash": thumbnail_hashes[0],
            "candidates": [
                {
                    "path": relative_posix(path, layout.root),
                    "hash": digest,
                    "source_path": relative_posix(thumbnail_paths[index], layout.root),
                    "source_hash": sha256_file(thumbnail_paths[index]),
                    "paired_title": [metadata.title, *metadata.alternative_titles][index],
                    "design": candidate_designs[index].model_dump(mode="json"),
                    "text_baked_into_bitmap": not clean_theme_art,
                }
                for index, (path, digest) in enumerate(
                    zip(version_paths, thumbnail_hashes, strict=True)
                )
            ],
            "approved": False,
            "updated_at": utc_now(),
            "provider": (
                "imagegen-theme+youtube-growth-specialist"
                if clean_theme_art
                else "imagegen-integrated+youtube-growth-specialist"
            ),
            "text_baked_into_bitmap": not clean_theme_art,
            "text_forbidden": clean_theme_art,
            "text_overlay": first_text,
            "accent_word": first_accent,
            "text_position": first_position,
            "designs": [design.model_dump(mode="json") for design in candidate_designs],
        },
    )
    completed_queue = dict(queue)
    completed_queue["status"] = StageStatus.COMPLETED.value
    completed_queue["imported_at"] = utc_now()
    write_json(layout.growth_queue, completed_queue)
    invalidate(
        layout,
        stages=(
            ("render", "validation")
            if project.video_format == "short" and project.config.render.short_cover_enabled
            else ("validation",)
        ),
        approvals=("metadata", "final_render"),
        note="O pacote do especialista de crescimento foi importado.",
    )

    growth_hash = str(queue["input_hash"])
    complete_stage(
        layout,
        "youtube_growth",
        growth_hash,
        [
            relative_posix(layout.growth_queue, layout.root),
            relative_posix(layout.growth_context, layout.root),
            relative_posix(layout.growth_package, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="youtube-growth-specialist",
        usage={
            "title_variants": len({metadata.title, *metadata.alternative_titles}),
            "context_bytes": layout.growth_context.stat().st_size,
            "schema_bytes": len(
                json.dumps(queue["schemas"]["package"], ensure_ascii=False).encode("utf-8")
            ),
        },
        note="Pacote estratégico do YouTube importado; aprovação humana ainda pendente.",
    )
    metadata_hash = sha256_value(
        {
            "growth_input_hash": growth_hash,
            "package": final_package,
            "audio_hash": sha256_file(layout.audio) if layout.audio.is_file() else None,
        }
    )
    complete_stage(
        layout,
        "metadata",
        metadata_hash,
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
            relative_posix(layout.thumbnail_prompt, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="youtube-growth-specialist",
    )
    thumbnail_hash = sha256_value(thumbnail_hashes)
    thumbnail_artifacts = [relative_posix(path, layout.root) for path in version_paths]
    thumbnail_artifacts.extend(
        [
            relative_posix(layout.thumbnail, layout.root),
            "youtube/thumbnail-manifest.json",
        ]
    )
    complete_stage(
        layout,
        "thumbnail",
        thumbnail_hash,
        thumbnail_artifacts,
        provider="youtube-growth-specialist",
        usage={
            "candidates": len(version_paths),
            "external_image_generations": len(version_paths),
            "integrated_image_generations": 0 if clean_theme_art else len(version_paths),
            "clean_image_generations": len(version_paths) if clean_theme_art else 0,
        },
        note=(
            "Imagem do versículo reutilizada na capa e composta no primeiro frame do Short."
            if project.video_format == "short"
            else "Thumbnail temática sem texto pronta para publicação."
        ),
    )
    layout.append_event(
        {
            "event": "youtube_growth_package_imported",
            "job_id": package.job_id,
            "thumbnail_candidates": len(version_paths),
            "format": project.video_format,
        }
    )
    return final_package
