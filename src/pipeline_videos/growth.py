from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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
    YouTubeGrowthStrategy,
    YouTubeMetadata,
    YouTubeThumbnailDesign,
)
from pipeline_videos.state import (
    complete_stage,
    invalidate,
    stage_is_current,
    start_stage,
    wait_stage,
)
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

GROWTH_QUEUE_SCHEMA_VERSION = "1.9"
GROWTH_ALGORITHM_VERSION = "1.9"
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


def _thumbnail_scene_candidates(scenes: SceneManifest, *, limit: int = 8) -> list[dict[str, Any]]:
    ordered = sorted(scenes.scenes, key=lambda item: item.order)
    if len(ordered) <= limit:
        selected = ordered
    else:
        indexes = {round(index * (len(ordered) - 1) / (limit - 1)) for index in range(limit)}
        selected = [ordered[index] for index in sorted(indexes)]
    return [
        {
            "scene_id": scene.scene_id,
            "section_id": scene.section_id,
            "order": scene.order,
            "narrative_function": scene.narrative_function,
            "visual_subject": scene.prompt.scene,
            "composition": scene.prompt.composition,
            "emotion": scene.prompt.emotion,
        }
        for scene in selected
    ]


def _growth_context_payload(layout: ProjectLayout) -> dict[str, Any]:
    required = {
        "briefing": layout.briefing,
        "sources": layout.sources,
        "script": layout.script,
        "biblical_review": layout.biblical_review,
        "scenes": layout.scenes,
        "experiments": layout.experiments,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Entradas ausentes para o especialista: " + ", ".join(missing))
    project = layout.load_manifest()
    light_mode = project.config.production.mode in {
        "light_manual",
        "light_automatic",
        "remote_review",
    }
    script = read_model(layout.script, ScriptDocument)
    sources = read_model(layout.sources, SourceLibrary)
    scenes = read_model(layout.scenes, SceneManifest)
    opening_scene = min(scenes.scenes, key=lambda item: item.order)
    experiments = read_json(layout.experiments)
    compact_experiments = (
        experiments.get("experiments", []) if isinstance(experiments, dict) else []
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "algorithm": GROWTH_ALGORITHM_VERSION,
        "project": {
            "project_id": project.project_id,
            "title": project.title,
            "video_format": project.video_format,
            "target_duration_seconds": project.target_duration_seconds,
            "title_variants": project.config.youtube_growth.title_variants,
            "production_mode": project.config.production.mode,
            "reasoning_effort": "low" if light_mode else "medium",
        },
        "briefing": read_json(layout.briefing),
        "sources": [
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "title": source.title,
                "passage": source.passage,
                "translation": source.translation,
                "excerpt": source.excerpt,
                "use_in_video": source.use_in_video,
                "claim_scope": source.claim_scope,
                "verified": source.verified,
            }
            for source in sources.entries
        ],
        "script": {
            "title": script.title,
            "sections": [
                {
                    "section_id": section.section_id,
                    "role": section.role,
                    "title": section.title,
                    "narration": section.narration,
                    "biblical_basis": section.biblical_basis,
                    "retention_goal": section.retention_goal,
                    "visual_direction": section.visual_direction,
                }
                for section in script.sections
            ],
        },
        "biblical_review": read_json(layout.biblical_review),
        "opening_scene": {
            "scene_id": opening_scene.scene_id,
            "section_id": opening_scene.section_id,
            "narration": opening_scene.narration,
            "narrative_function": opening_scene.narrative_function,
            "visual_subject": opening_scene.prompt.scene,
            "composition": opening_scene.prompt.composition,
            "emotion": opening_scene.prompt.emotion,
        },
        "experiments": compact_experiments[-12:],
    }
    if project.video_format == "short" and layout.shorts_script_strategy.is_file():
        payload["retention_strategy"] = read_json(layout.shorts_script_strategy)
    if project.video_format != "short":
        payload["visual_system"] = {
            "style": read_json(layout.style_profile),
            "palette": read_json(layout.palette),
        }
        if project.config.youtube_growth.reuse_scene_images_for_thumbnails:
            payload["thumbnail_scene_candidates"] = _thumbnail_scene_candidates(scenes)
    return payload


def growth_input_hash(layout: ProjectLayout) -> str:
    return sha256_value(_growth_context_payload(layout))


def _queue_without_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "generated_at"}


def prepare_growth_queue(layout: ProjectLayout, *, force: bool = False) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.youtube_growth
    if not config.enabled or not config.use_specialist_subagent:
        raise RuntimeError("O subagente especialista está desativado neste projeto.")

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
    light_mode = project.config.production.mode in {
        "light_manual",
        "light_automatic",
        "remote_review",
    }
    single_agent_mode = project.config.production.mode == "remote_review"
    integrated_thumbnails = config.thumbnail_generation_mode == "integrated_imagegen_v1"
    reuse_scene_images = (
        not integrated_thumbnails
        and not is_short
        and project.config.youtube_growth.reuse_scene_images_for_thumbnails
    )
    thumbnail_count = config.thumbnail_variants if integrated_thumbnails or not is_short else 1
    thumbnail_size = (
        [project.config.render.width, project.config.render.height] if is_short else [1280, 720]
    )
    thumbnail_paths = (
        []
        if (is_short and not integrated_thumbnails) or reuse_scene_images
        else [
            relative_posix(layout.growth_staging / f"thumbnail-{index:03d}.png", layout.root)
            for index in range(1, thumbnail_count + 1)
        ]
    )
    growth_context = _growth_context_payload(layout)
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
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "orchestration": {
            "mode": "single_agent" if single_agent_mode else "subagent",
            "worker_id": "main" if single_agent_mode else "youtube-growth-specialist",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "low" if light_mode else "medium",
            "recommended_model": "gpt-5.6-terra",
            "context_policy": "compact_only",
            "fork_turns": None if single_agent_mode else "none",
        },
        "inputs": {
            "compact_context": relative_posix(layout.growth_context, layout.root),
        },
        "outputs": {
            "package": relative_posix(layout.growth_package_staging, layout.root),
            "thumbnails": thumbnail_paths,
            "thumbnail_count": thumbnail_count,
            "thumbnail_design_count": config.title_variants,
            "thumbnail_scene_ids": [
                item["scene_id"] for item in growth_context.get("thumbnail_scene_candidates", [])
            ],
            "thumbnail_source_mode": (
                "generated_integrated_art"
                if integrated_thumbnails
                else "opening_scene_active_image"
                if is_short
                else "selected_scene_images"
                if reuse_scene_images
                else "generated_staging_art"
            ),
            "thumbnail_size": thumbnail_size,
            "thumbnail_role": "short_first_frame" if is_short else "custom_thumbnail",
            "base_art_without_text": not integrated_thumbnails,
            "external_image_generation_required": (
                integrated_thumbnails or (not is_short and not reuse_scene_images)
            ),
            "text_overlay_required": not integrated_thumbnails,
            "imagegen_text_authority": integrated_thumbnails,
            "title_standard": config.title_standard,
            "description_standard": config.description_standard,
            "thumbnail_standard": config.thumbnail_standard,
            "title_thumbnail_pairing_standard": config.title_thumbnail_pairing_standard,
            "design_contract": {
                "text_word_count": [2, 5],
                "maximum_lines": 3,
                "allowed_text_colors": ["white", "yellow", "red"],
                "allowed_text_alignments": ["left", "center", "right"],
                "strong_outline": True,
                "strong_shadow": True,
                "texture": "subtle",
                "safe_areas_required": True,
                "optional_narrative_elements": ["arrow", "x", "underline", "brush"],
                "pillow_is_text_authority": not integrated_thumbnails,
                "text_must_be_baked_into_bitmap": integrated_thumbnails,
                "paired_variants_required": True,
                "title_thumbnail_repetition_allowed": (
                    config.title_thumbnail_pairing_standard != "complementary_v1"
                ),
            },
        },
        "generation_retry_policy": imagegen_retry_policy(),
        "schemas": {
            "package": compact_json_schema(YouTubeGrowthPackage.model_json_schema()),
        },
        "official_guidelines": list(OFFICIAL_YOUTUBE_GUIDELINES),
        "instructions": [
            (
                "Leia somente inputs.compact_context; ele já contém os fatos, passagens, roteiro, "
                "revisão, abertura visual e experimentos necessários."
            ),
            "Preserve rigorosamente fatos, passagens e fontes do projeto.",
            f"Entregue {config.title_variants} combinações distintas de título para teste.",
            "Use títulos precisos e curtos, com termos importantes no início e no máximo "
            "100 caracteres.",
            "Crie descrição única; explique o vídeo e use uma ou duas expressões centrais "
            "nas primeiras linhas.",
            "Trate SEO, CTR e alcance como hipóteses; nunca prometa viralização ou invente "
            "métricas.",
            "Use dados reais de analytics/experiments.json quando existirem; nunca invente "
            "volume de busca.",
            "Faça título e thumbnail se complementarem sem repetir mecanicamente a mesma frase.",
            "Associe título e thumbnail pelo mesmo índice: a primeira imagem acompanha o título "
            "principal.",
            "Evite clickbait enganoso, excesso de elementos, choque gratuito e violações "
            "das políticas.",
            "Preencha package.json no schema fornecido, repetindo job_id e input_hash da fila.",
            (
                "Construa um conflito visual simples com escala dramática, um personagem ou "
                "objeto dominante, emoção legível, luz direcional e área negativa planejada "
                "para o texto."
            ),
            (
                "Preencha thumbnail_designs com um design por candidata, no mesmo índice do "
                "título: texto honesto de duas a cinco palavras, hierarquia em até três linhas, "
                "cores white/yellow/red, alinhamento coerente com o espaço negativo, contorno e "
                "sombra fortes e textura sutil."
            ),
            (
                "Respeite safe areas e verifique a legibilidade em tela pequena sem cobrir "
                "rostos, ação ou o objeto dominante."
            ),
            (
                "Use arrow, x, underline ou brush somente quando o elemento narrativo for "
                "honesto e relevante; identifique o alvo e registre a justificativa editorial."
            ),
            "Não altere project.json, fila, aprovações, ativos ou arquivos fora de staging.",
        ],
    }
    if config.title_standard == "passage_pipe_topic_v1":
        main_passage = str(growth_context.get("briefing", {}).get("main_passage", "")).strip()
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
                    "Em cada par, distribua a promessa: o título deve carregar referência, tema "
                    "e intenção de busca; o texto da thumbnail deve acrescentar tensão, decisão, "
                    "contraste, consequência ou pergunta visual que o título ainda não disse."
                ),
                (
                    "É proibido repetir na thumbnail a frase temática que aparece depois da barra "
                    "vertical do título. Também não use essa frase inteira como trecho dentro da "
                    "thumbnail, nem a frase inteira da thumbnail dentro do título. Compartilhar "
                    "uma palavra-chave ou nome próprio continua permitido quando necessário."
                ),
                (
                    "Preencha title_thumbnail_complement explicando de forma concisa a função "
                    f"complementar {pair_scope_label}."
                ),
            ]
        )
    if integrated_thumbnails:
        payload["instructions"].extend(
            [
                (
                    "Gere cada thumbnail final com uma chamada independente de imagegen e salve "
                    "o bitmap completo exatamente no caminho de staging do mesmo índice."
                ),
                *imagegen_retry_instructions(asset_label="thumbnail"),
                (
                    "Inclua na própria geração o texto exato de thumbnail_design, sua palavra de "
                    "destaque vermelha e o elemento narrativo vermelho. Texto e símbolo precisam "
                    "participar da iluminação, profundidade, perspectiva e composição da arte; "
                    "não entregue arte-base limpa nem aparência de camada solta."
                ),
                (
                    "Confirme visualmente a grafia e os acentos antes de concluir. Não aceite "
                    "texto adicional, duplicado, deformado ou diferente de "
                    "thumbnail_design.text."
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
                    f"{image_count_label} {image_pairing_verb} aos títulos pelo "
                    "índice. Em Shorts, a primeira candidata integrada ocupará o frame zero; "
                    "eventuais candidatas adicionais ficam como alternativas versionadas."
                ),
            ]
        )
    elif is_short:
        payload["instructions"].extend(
            [
                (
                    "Para Shorts, não chame imagegen nem gere outra arte. A imagem ativa da "
                    "primeira cena do gancho será reutilizada como arte-base da capa e já "
                    "permanece visível durante a abertura do vídeo."
                ),
                (
                    f"O título principal deve ter no máximo {SHORT_TITLE_MAX_CHARACTERS} "
                    "caracteres, conter primary_keyword de forma natural e combinar intenção "
                    "de busca com curiosidade honesta."
                ),
                (
                    "Inclua a passagem principal do briefing em pelo menos uma opção de título "
                    "e prefira a formulação que uma pessoa realmente buscaria, como referência, "
                    "personagem, lugar ou frase bíblica reconhecível."
                ),
                (
                    f"Mantenha a descrição em até {SHORT_DESCRIPTION_MAX_CHARACTERS} caracteres, "
                    f"com primary_keyword nos primeiros {SHORT_DESCRIPTION_SEARCH_WINDOW}, no "
                    "máximo dois parágrafos curtos e até três hashtags relevantes. Não crie "
                    "capítulos para Shorts."
                ),
                (
                    "A capa é secundária no feed automático, mas precisa ser específica na busca "
                    "e no canal. Evite frases genéricas de autoajuda; use duas a cinco palavras "
                    "ligadas ao conflito bíblico e complemente, sem repetir, o título."
                ),
                (
                    "Planeje thumbnail_design sobre opening_scene do contexto, respeitando o "
                    "assunto dominante e o espaço negativo já definidos; o compositor Pillow "
                    "aplicará o texto."
                ),
                (
                    "A primeira thumbnail_design deve manter short_cover_timestamp_seconds=0, "
                    "pois a capa composta ocupará somente o frame zero."
                ),
            ]
        )
    elif reuse_scene_images:
        payload["instructions"].extend(
            [
                (
                    "Não gere novas artes de thumbnail. Escolha source_scene_id somente entre "
                    "thumbnail_scene_candidates do contexto compacto."
                ),
                (
                    "Use cenas distintas quando isso preservar a promessa de cada candidata; "
                    "o compositor local aplicará tipografia e enquadramento."
                ),
            ]
        )
    else:
        payload["instructions"].extend(
            [
                (
                    "Gere cada arte-base de thumbnail com uma chamada de imagegen e salve "
                    "somente no staging."
                ),
                (
                    "Gere a arte-base sempre sem letras, palavras, números, logotipos ou "
                    "marca-d'água; o compositor Pillow é a autoridade final da tipografia."
                ),
                (
                    f"Entregue {pair_count_label}; quando houver mais de uma combinação, elas "
                    "devem ser diferentes e adequadas ao A/B test do YouTube Studio."
                ),
                (
                    "Para 16:9, entregue um thumbnail_design por arte-base; cada candidata "
                    "continua pareada ao título do mesmo índice."
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
        "Aguardando o subagente youtube_growth_specialist escrever somente na pasta de staging.",
        [
            relative_posix(layout.growth_queue, layout.root),
            relative_posix(layout.growth_context, layout.root),
        ],
    )
    return payload


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


def _title_topic(title: str) -> str:
    return title.split("|", 1)[1].strip() if "|" in title else title.strip()


def _pairing_content_tokens(value: str) -> list[str]:
    words = _normalized_search_text(value).split()
    return [
        "contra" if word in _PAIRING_CONFLICT_WORDS else word
        for word in words
        if word not in _PAIRING_STOP_WORDS
    ]


def _title_repeats_thumbnail(title: str, thumbnail_text: str) -> bool:
    topic = _normalized_search_text(_title_topic(title))
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
    if package.job_id != queue.get("job_id") or package.input_hash != queue.get("input_hash"):
        raise RuntimeError("O pacote do especialista pertence a uma fila antiga.")
    project = layout.load_manifest()
    metadata = package.metadata
    titles = [metadata.title, *metadata.alternative_titles]
    required_titles = project.config.youtube_growth.title_variants
    if len({title.strip().casefold() for title in titles if title.strip()}) < required_titles:
        raise ValueError(f"O pacote precisa de {required_titles} títulos distintos.")
    outputs = queue.get("outputs", {})
    expected_designs = int(outputs.get("thumbnail_design_count", 0))
    if not expected_designs:
        # Filas 1.3 de Shorts registravam thumbnail_count=1 para a única
        # arte-base reutilizada, embora ainda exigissem um overlay por título.
        expected_designs = (
            required_titles
            if project.video_format == "short"
            else int(outputs.get("thumbnail_count", 0))
        )
    if not expected_designs:
        expected_designs = len(outputs.get("thumbnails", []))
    if len(titles) < expected_designs:
        raise ValueError("Cada thumbnail precisa de um título correspondente no mesmo índice.")
    if any(len(title) > 100 for title in titles):
        raise ValueError("Todos os títulos precisam ter no máximo 100 caracteres.")
    if not metadata.description.strip():
        raise ValueError("A descrição do especialista está vazia.")
    expected_format = "short" if project.video_format == "short" else "long_form"
    if package.strategy.format != expected_format:
        raise ValueError("A estratégia não corresponde ao formato do projeto.")
    if package.strategy.thumbnail_text and len(package.strategy.thumbnail_text.split()) > 5:
        raise ValueError("O texto sugerido para a thumbnail deve ter no máximo cinco palavras.")
    uses_design_contract = queue.get("schema_version") == GROWTH_QUEUE_SCHEMA_VERSION
    if uses_design_contract and len(package.strategy.thumbnail_designs) != expected_designs:
        raise ValueError("O pacote precisa de um thumbnail_design por candidata.")
    if uses_design_contract and any(
        design.texture != "subtle_grain" for design in package.strategy.thumbnail_designs
    ):
        raise ValueError("O novo padrão de thumbnail exige textura sutil no texto.")
    if (
        uses_design_contract
        and outputs.get("title_thumbnail_pairing_standard") == "complementary_v1"
    ):
        designs = package.strategy.thumbnail_designs
        normalized_design_texts = {_normalized_search_text(design.text) for design in designs}
        if len(normalized_design_texts) != expected_designs:
            raise ValueError("Cada título precisa de uma frase de thumbnail distinta.")
        for index, (title, design) in enumerate(
            zip(titles[:expected_designs], designs, strict=True),
            start=1,
        ):
            if _title_repeats_thumbnail(title, design.text):
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
    if source_mode == "generated_integrated_art":
        declared_paths = outputs.get("thumbnails", [])
        if len(declared_paths) != int(outputs.get("thumbnail_count", 0)):
            raise ValueError("A fila integrada precisa declarar um arquivo por thumbnail.")
        if outputs.get("text_overlay_required") is not False:
            raise ValueError("A fila integrada não pode solicitar overlay local de texto.")
        if outputs.get("imagegen_text_authority") is not True:
            raise ValueError(
                "A fila integrada precisa declarar o imagegen como autoridade textual."
            )
    if source_mode == "selected_scene_images":
        context = read_json(layout.growth_context)
        candidates = context.get("thumbnail_scene_candidates", [])
        allowed_scene_ids = {item.get("scene_id") for item in candidates if isinstance(item, dict)}
        selected_scene_ids = [
            design.source_scene_id for design in package.strategy.thumbnail_designs
        ]
        if any(
            not scene_id or scene_id not in allowed_scene_ids for scene_id in selected_scene_ids
        ):
            raise ValueError(
                "Cada thumbnail reutilizada precisa escolher um source_scene_id "
                "declarado no contexto."
            )
        if len(allowed_scene_ids) >= len(selected_scene_ids) and len(
            set(selected_scene_ids)
        ) != len(selected_scene_ids):
            raise ValueError("As candidatas de thumbnail precisam reutilizar cenas distintas.")
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
        if not package.strategy.thumbnail_designs:
            thumbnail_words = (package.strategy.thumbnail_text or "").split()
            if not 2 <= len(thumbnail_words) <= 5:
                raise ValueError("A capa do Short precisa de duas a cinco palavras.")
            accent_words = (package.strategy.thumbnail_accent_word or "").split()
            if len(accent_words) != 1:
                raise ValueError("A capa do Short precisa de uma única palavra de destaque.")
            normalized_words = {word.casefold() for word in thumbnail_words}
            if accent_words[0].casefold() not in normalized_words:
                raise ValueError("A palavra de destaque precisa existir no texto da capa.")
        if package.strategy.short_cover_timestamp_seconds != 0:
            raise ValueError("A capa do Short precisa ocupar o frame zero.")
    if outputs.get("title_standard") == "passage_pipe_topic_v1":
        briefing = read_model(layout.briefing, Briefing)
        prefix = f"{briefing.main_passage.strip()} | "
        if not prefix.strip(" |") or any(not title.startswith(prefix) for title in titles):
            raise ValueError(
                "Todos os títulos precisam seguir 'Passagem principal | Tema principal'."
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


def _short_opening_thumbnail_source(
    layout: ProjectLayout,
    scenes: SceneManifest,
) -> Path:
    if not layout.images.is_file():
        raise FileNotFoundError(
            "O manifesto de imagens ainda não existe; importe as imagens antes do pacote "
            "de crescimento do Short."
        )
    images = read_model(layout.images, ImageManifest)
    opening_scene = min(scenes.scenes, key=lambda item: item.order)
    image_item = next(
        (item for item in images.items if item.scene_id == opening_scene.scene_id),
        None,
    )
    if image_item is None or image_item.active_version is None:
        raise RuntimeError(
            "A primeira cena do gancho ainda não possui imagem ativa para reutilizar na capa."
        )
    version = next(
        (item for item in image_item.versions if item.version == image_item.active_version),
        None,
    )
    if version is None:
        raise RuntimeError("A versão ativa da imagem de abertura não foi encontrada.")
    source = layout.root / version.path
    if not source.is_file():
        raise FileNotFoundError(f"Imagem ativa da abertura ausente: {source}")
    return source


def _long_scene_thumbnail_sources(
    layout: ProjectLayout,
    strategy: YouTubeGrowthStrategy,
    count: int,
) -> list[Path]:
    if not layout.images.is_file():
        raise FileNotFoundError("Importe as imagens antes de compor as thumbnails reutilizadas.")
    images = read_model(layout.images, ImageManifest)
    items_by_scene = {item.scene_id: item for item in images.items}
    sources: list[Path] = []
    for design in strategy.thumbnail_designs[:count]:
        scene_id = design.source_scene_id
        item = items_by_scene.get(str(scene_id))
        if item is None or item.active_version is None:
            raise RuntimeError(f"A cena {scene_id} ainda não possui imagem ativa para a capa.")
        version = next(
            (candidate for candidate in item.versions if candidate.version == item.active_version),
            None,
        )
        if version is None:
            raise RuntimeError(f"A versão ativa de {scene_id} não foi encontrada.")
        source = layout.root / version.path
        if not source.is_file():
            raise FileNotFoundError(f"Imagem ativa da cena ausente: {source}")
        sources.append(source)
    if len(sources) != count:
        raise ValueError("Faltam cenas selecionadas para as candidatas de thumbnail.")
    return sources


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


def _load_thumbnail_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    candidates = [font_path] if font_path else []
    candidates.extend(
        [
            Path(__file__).resolve().parent / "assets/fonts/Montserrat-ExtraBold.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError("Nenhuma fonte TrueType disponível para compor a thumbnail.")


def _project_font_path(layout: ProjectLayout) -> Path | None:
    configured = layout.load_manifest().config.captions.font_file
    font_path = layout.root / configured if configured else None
    return font_path if font_path is not None and font_path.is_file() else None


def _thumbnail_text_layout(
    draw: ImageDraw.ImageDraw,
    design: YouTubeThumbnailDesign,
    font_path: Path | None,
    width: int,
    height: int,
) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, list[list[str]], int]:
    words = design.text.upper().split()
    maximum_width = round(width * (1.0 - 2 * design.safe_margin_x_ratio))
    font_size = max(36, min(round(width * 0.14), round(height * 0.14)))
    minimum_size = max(24, min(round(width * 0.065), round(height * 0.065)))
    while font_size >= minimum_size:
        font = _load_thumbnail_font(font_path, font_size)
        accent_font = _load_thumbnail_font(font_path, round(font_size * 1.12))
        gap = max(10, round(font_size * 0.18))
        if design.text_lines:
            lines = [line.upper().split() for line in design.text_lines]
        else:
            lines = [[]]
            line_width = 0
            for word in words:
                word_font = (
                    accent_font if word.casefold() == design.accent_word.casefold() else font
                )
                box = draw.textbbox((0, 0), word, font=word_font, anchor="lt")
                word_width = box[2] - box[0]
                proposed = word_width if not lines[-1] else line_width + gap + word_width
                if lines[-1] and proposed > maximum_width:
                    lines.append([word])
                    line_width = word_width
                else:
                    lines[-1].append(word)
                    line_width = proposed
        line_widths = []
        line_heights = []
        for line in lines:
            metrics = []
            for word in line:
                word_font = (
                    accent_font if word.casefold() == design.accent_word.casefold() else font
                )
                box = draw.textbbox((0, 0), word, font=word_font, anchor="lt")
                metrics.append((box[2] - box[0], box[3] - box[1]))
            line_widths.append(sum(item[0] for item in metrics) + gap * max(0, len(line) - 1))
            line_heights.append(max(item[1] for item in metrics))
        line_gap = max(10, round(font_size * 0.2))
        total_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
        available_height = round(height * (1.0 - 2 * design.safe_margin_y_ratio))
        if (
            len(lines) <= design.max_lines
            and max(line_widths, default=0) <= maximum_width
            and total_height <= available_height
        ):
            return font, accent_font, lines, gap
        font_size -= 4
    raise ValueError("Não foi possível ajustar o texto da thumbnail em até três linhas.")


def _darken_text_region(image: Image.Image, position: str) -> Image.Image:
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(height):
        normalized = y / max(1, height - 1)
        if position == "top":
            strength = max(0.0, 1.0 - normalized / 0.48)
        elif position == "bottom":
            strength = max(0.0, (normalized - 0.52) / 0.48)
        else:
            strength = max(0.0, 1.0 - abs(normalized - 0.5) / 0.34)
        alpha = round(155 * min(1.0, strength))
        for x in range(width):
            pixels[x, y] = (0, 0, 0, alpha)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


_THUMBNAIL_COLORS = {
    "white": (255, 255, 255, 255),
    "yellow": (255, 211, 64, 255),
    "red": (232, 55, 62, 255),
}


def _legacy_thumbnail_design(strategy: YouTubeGrowthStrategy) -> YouTubeThumbnailDesign | None:
    text = " ".join((strategy.thumbnail_text or "").split())
    accent = " ".join((strategy.thumbnail_accent_word or "").split())
    if not text or not accent:
        return None
    return YouTubeThumbnailDesign(
        text=text,
        accent_word=accent,
        text_position=strategy.thumbnail_text_position,
        primary_color="white",
        accent_color="red",
        texture="none",
    )


def _thumbnail_design_for_candidate(
    strategy: YouTubeGrowthStrategy, index: int
) -> YouTubeThumbnailDesign | None:
    if index < len(strategy.thumbnail_designs):
        return strategy.thumbnail_designs[index]
    return _legacy_thumbnail_design(strategy)


def _narrative_anchor(design: YouTubeThumbnailDesign, width: int, height: int) -> tuple[int, int]:
    margin_x = round(width * design.safe_margin_x_ratio)
    margin_y = round(height * design.safe_margin_y_ratio)
    positions = {
        "top_left": (margin_x + round(width * 0.08), margin_y + round(height * 0.1)),
        "top_right": (width - margin_x - round(width * 0.08), margin_y + round(height * 0.1)),
        "center_left": (margin_x + round(width * 0.08), height // 2),
        "center_right": (width - margin_x - round(width * 0.08), height // 2),
        "bottom_left": (margin_x + round(width * 0.08), height - margin_y - round(height * 0.1)),
        "bottom_right": (
            width - margin_x - round(width * 0.08),
            height - margin_y - round(height * 0.1),
        ),
    }
    return positions[design.narrative_element_anchor]


def _draw_free_narrative_element(
    draw: ImageDraw.ImageDraw,
    design: YouTubeThumbnailDesign,
    width: int,
    height: int,
) -> None:
    if design.narrative_element not in {"arrow", "x"}:
        return
    target_x, target_y = _narrative_anchor(design, width, height)
    color = _THUMBNAIL_COLORS[design.accent_color]
    stroke = max(7, round(width * 0.009))
    if design.narrative_element == "x":
        radius = max(20, round(width * 0.035))
        draw.line(
            (target_x - radius, target_y - radius, target_x + radius, target_y + radius),
            fill=color,
            width=stroke,
        )
        draw.line(
            (target_x - radius, target_y + radius, target_x + radius, target_y - radius),
            fill=color,
            width=stroke,
        )
        return
    points_left = design.narrative_element_anchor.endswith("left")
    tail_x = target_x + round(width * 0.12) * (1 if points_left else -1)
    tail_y = target_y - round(height * 0.07)
    draw.line((tail_x, tail_y, target_x, target_y), fill=color, width=stroke)
    head = max(18, round(width * 0.028))
    direction = 1 if points_left else -1
    draw.line(
        (target_x, target_y, target_x + direction * head, target_y - head),
        fill=color,
        width=stroke,
    )
    draw.line(
        (target_x, target_y, target_x + direction * head, target_y + head),
        fill=color,
        width=stroke,
    )


def _apply_subtle_text_texture(layer: Image.Image, text_mask: Image.Image, width: int) -> None:
    pattern = Image.new("L", layer.size, 0)
    pattern_draw = ImageDraw.Draw(pattern)
    step = max(12, round(width * 0.018))
    for grid_y, y in enumerate(range(0, layer.height, step)):
        for grid_x, x in enumerate(range(0, layer.width, step)):
            seed = (grid_x * 73_856_093) ^ (grid_y * 19_349_663)
            if seed % 5:
                continue
            jitter_x = seed % step
            jitter_y = (seed // 17) % step
            radius = 1 + seed % max(2, round(width * 0.0025))
            alpha = 18 + seed % 30
            center_x = x + jitter_x
            center_y = y + jitter_y
            pattern_draw.ellipse(
                (
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                ),
                fill=alpha,
            )
    scratch_count = max(5, round(width / 180))
    for index in range(scratch_count):
        seed = (index + 1) * 2_654_435_761
        start_x = seed % max(1, layer.width)
        start_y = (seed // 97) % max(1, layer.height)
        length = max(10, round(width * (0.018 + (seed % 7) * 0.002)))
        pattern_draw.line(
            (start_x, start_y, min(layer.width, start_x + length), start_y + index % 3 - 1),
            fill=28,
            width=max(1, round(width * 0.0015)),
        )
    clipped = ImageChops.multiply(text_mask, pattern)
    texture = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    texture.putalpha(clipped)
    layer.alpha_composite(texture)


def _compose_thumbnail(
    image: Image.Image,
    design: YouTubeThumbnailDesign,
    font_path: Path | None,
) -> Image.Image:
    image = ImageEnhance.Contrast(image).enhance(1.12)
    image = ImageEnhance.Color(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.18)
    image = _darken_text_region(image, design.text_position)

    width, height = image.size
    measurement = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font, accent_font, lines, gap = _thumbnail_text_layout(
        measurement, design, font_path, width, height
    )
    stroke = max(4, round(width * 0.009))
    line_gap = max(12, round(font.size * 0.2))
    metrics: list[list[tuple[str, ImageFont.FreeTypeFont, int, int]]] = []
    line_heights = []
    for line in lines:
        line_metrics = []
        for word in line:
            word_font = accent_font if word.casefold() == design.accent_word.casefold() else font
            box = measurement.textbbox((0, 0), word, font=word_font, anchor="lt")
            line_metrics.append((word, word_font, box[2] - box[0], box[3] - box[1]))
        metrics.append(line_metrics)
        line_heights.append(max(item[3] for item in line_metrics))
    total_height = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    safe_y = round(height * design.safe_margin_y_ratio)
    if design.text_position == "top":
        y = safe_y
    elif design.text_position == "bottom":
        y = height - safe_y - total_height
    else:
        y = round((height - total_height) / 2)

    shadow_mask = Image.new("L", image.size, 0)
    shadow_draw = ImageDraw.Draw(shadow_mask)
    decorations = Image.new("RGBA", image.size, (0, 0, 0, 0))
    decoration_draw = ImageDraw.Draw(decorations)
    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(text_mask)
    _draw_free_narrative_element(decoration_draw, design, width, height)
    accent = design.accent_word.casefold()
    for line_metrics, line_height in zip(metrics, line_heights, strict=True):
        line_width = sum(item[2] for item in line_metrics) + gap * max(0, len(line_metrics) - 1)
        safe_x = round(width * design.safe_margin_x_ratio)
        if design.text_alignment == "left":
            x = safe_x
        elif design.text_alignment == "right":
            x = width - safe_x - line_width
        else:
            x = round((width - line_width) / 2)
        for word, word_font, word_width, word_height in line_metrics:
            word_y = y + round((line_height - word_height) / 2)
            is_accent = word.casefold() == accent
            if is_accent and design.narrative_element == "brush":
                padding_x = max(10, round(width * 0.014))
                padding_y = max(6, round(width * 0.008))
                decoration_draw.rounded_rectangle(
                    (
                        x - padding_x,
                        word_y - padding_y,
                        x + word_width + padding_x,
                        word_y + word_height + padding_y,
                    ),
                    radius=max(8, round(width * 0.012)),
                    fill=_THUMBNAIL_COLORS["red"],
                )
            if is_accent and design.narrative_element == "underline":
                underline_y = word_y + word_height + max(5, round(stroke * 0.8))
                decoration_draw.line(
                    (x, underline_y, x + word_width, underline_y),
                    fill=_THUMBNAIL_COLORS["red"],
                    width=max(6, round(stroke * 1.1)),
                )
            shadow_draw.text(
                (x + stroke * 2, word_y + stroke * 2),
                word,
                font=word_font,
                fill=230,
                anchor="lt",
                stroke_width=stroke,
                stroke_fill=230,
            )
            fill_name = design.accent_color if is_accent else design.primary_color
            fill = _THUMBNAIL_COLORS[fill_name]
            if is_accent and design.narrative_element == "brush":
                fill = _THUMBNAIL_COLORS["white"]
            text_draw.text(
                (x, word_y),
                word,
                font=word_font,
                fill=fill,
                anchor="lt",
                stroke_width=stroke,
                stroke_fill=(8, 10, 14, 255),
            )
            mask_draw.text((x, word_y), word, font=word_font, fill=255, anchor="lt")
            x += word_width + gap
        y += line_height + line_gap
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(max(2, stroke * 0.55)))
    shadow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow_mask)
    if design.texture == "subtle_grain":
        _apply_subtle_text_texture(text_layer, text_mask, width)
    composed = Image.alpha_composite(image.convert("RGBA"), shadow_layer)
    composed = Image.alpha_composite(composed, decorations)
    return Image.alpha_composite(composed, text_layer).convert("RGB")


def _normalize_thumbnail(
    source: Path,
    destination: Path,
    size: tuple[int, int],
    *,
    design: YouTubeThumbnailDesign | None,
    font_path: Path | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with Image.open(source) as image:
        normalized = ImageOps.fit(
            image.convert("RGB"),
            size,
            method=Image.Resampling.LANCZOS,
        )
        if design is not None:
            normalized = _compose_thumbnail(normalized, design, font_path)
        normalized.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, destination)


def retouch_youtube_package(layout: ProjectLayout, spec_path: Path) -> YouTubeMetadata:
    """Aplica correções editoriais locais sem regenerar roteiro, áudio, imagens ou render."""

    spec_path = spec_path.expanduser().resolve()
    if not spec_path.is_file():
        raise FileNotFoundError(spec_path)
    spec = read_json(spec_path)
    metadata = read_model(layout.metadata, YouTubeMetadata)
    metadata_updates = spec.get("metadata", {})
    if not isinstance(metadata_updates, dict):
        raise ValueError("metadata precisa ser um objeto JSON.")
    metadata = metadata.model_copy(update=metadata_updates)
    metadata = YouTubeMetadata.model_validate(metadata.model_dump(mode="json"))

    manifest_path = layout.thumbnail.parent / "thumbnail-manifest.json"
    thumbnail_manifest = read_json(manifest_path)
    candidates = thumbnail_manifest.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("O projeto não possui candidatas de thumbnail para retocar.")
    updates = spec.get("thumbnail_updates", [])
    if not isinstance(updates, list):
        raise ValueError("thumbnail_updates precisa ser uma lista.")
    paired_titles = [metadata.title, *metadata.alternative_titles]
    font_path = _project_font_path(layout)
    next_version = _next_thumbnail_version(layout)
    changed_indexes: set[int] = set()
    package = (
        read_model(layout.growth_package, YouTubeGrowthPackage)
        if layout.growth_package.is_file()
        else None
    )
    designs = list(package.strategy.thumbnail_designs) if package else []

    for raw_update in updates:
        if not isinstance(raw_update, dict):
            raise ValueError("Cada retoque de thumbnail precisa ser um objeto.")
        candidate_index = int(raw_update.get("candidate_index", 0)) - 1
        if not 0 <= candidate_index < len(candidates):
            raise ValueError("candidate_index aponta para uma thumbnail inexistente.")
        design = YouTubeThumbnailDesign.model_validate(raw_update.get("design", {}))
        source_relative = candidates[candidate_index].get("source_path")
        if not isinstance(source_relative, str):
            raise ValueError("A candidata não registra sua arte-base.")
        source = (layout.root / source_relative).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = layout.thumbnail.parent / f"thumbnail-v{next_version:03d}.png"
        next_version += 1
        _normalize_thumbnail(
            source,
            destination,
            (1280, 720),
            design=design,
            font_path=font_path,
        )
        candidates[candidate_index] = {
            "path": relative_posix(destination, layout.root),
            "hash": sha256_file(destination),
            "source_path": source_relative,
            "source_hash": sha256_file(source),
            "paired_title": paired_titles[candidate_index],
            "design": design.model_dump(mode="json"),
        }
        while len(designs) <= candidate_index:
            designs.append(design)
        designs[candidate_index] = design
        changed_indexes.add(candidate_index)

    for index, candidate in enumerate(candidates):
        if index < len(paired_titles):
            candidate["paired_title"] = paired_titles[index]
    active_index = int(spec.get("active_candidate_index", 1)) - 1
    if not 0 <= active_index < len(candidates):
        raise ValueError("active_candidate_index aponta para uma thumbnail inexistente.")
    active_path = (layout.root / candidates[active_index]["path"]).resolve()
    shutil.copy2(active_path, layout.thumbnail)

    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="Título, descrição e thumbnails foram retocados sem alterar o vídeo.",
    )
    write_json(layout.metadata, metadata)
    write_text(layout.description, _description_document(metadata))
    if package is not None:
        strategy = package.strategy.model_copy(update={"thumbnail_designs": designs})
        package = package.model_copy(update={"metadata": metadata, "strategy": strategy})
        write_json(layout.growth_package, package)
        write_json(layout.growth_strategy, strategy)
        if layout.growth_package_staging.is_file():
            write_json(layout.growth_package_staging, package)

    thumbnail_manifest.update(
        {
            "active_path": candidates[active_index]["path"],
            "active_hash": candidates[active_index]["hash"],
            "candidates": candidates,
            "approved": False,
            "updated_at": utc_now(),
            "provider": "deterministic-manual-retouch",
            "text_overlay": candidates[active_index]["design"]["text"],
            "accent_word": candidates[active_index]["design"]["accent_word"],
            "text_position": candidates[active_index]["design"]["text_position"],
            "designs": [candidate["design"] for candidate in candidates],
        }
    )
    write_json(manifest_path, thumbnail_manifest)

    metadata_hash = sha256_value(metadata.model_dump(mode="json"))
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
        provider="deterministic-manual-retouch",
        note="Título e descrição corrigidos sem alterar o vídeo.",
    )
    thumbnail_hash = sha256_value([candidate["hash"] for candidate in candidates])
    complete_stage(
        layout,
        "thumbnail",
        thumbnail_hash,
        [
            *[candidate["path"] for candidate in candidates],
            relative_posix(layout.thumbnail, layout.root),
            relative_posix(manifest_path, layout.root),
        ],
        provider="deterministic-manual-retouch",
        usage={
            "candidates": len(candidates),
            "retouched": len(changed_indexes),
            "preserved": len(candidates) - len(changed_indexes),
            "external_image_generations": 0,
        },
        note=(
            "Duas candidatas recompostas; a arte-base e a candidata aprovada pelo usuário "
            "foram preservadas."
        ),
    )
    return metadata


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
        _normalize_thumbnail(source, destination, size, design=None)

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
    source_mode = queue["outputs"].get("thumbnail_source_mode", "generated_staging_art")
    if project.video_format == "short" and source_mode == "opening_scene_active_image":
        thumbnail_paths = [_short_opening_thumbnail_source(layout, scenes)]
    elif source_mode == "selected_scene_images":
        thumbnail_paths = _long_scene_thumbnail_sources(
            layout,
            package.strategy,
            int(queue["outputs"].get("thumbnail_count", 0)),
        )
    else:
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
    configured_font = project.config.captions.font_file
    font_path = layout.root / configured_font if configured_font else None
    if font_path is not None and not font_path.is_file():
        font_path = None
    candidate_designs = [
        _thumbnail_design_for_candidate(package.strategy, index)
        for index in range(len(thumbnail_paths))
    ]
    apply_local_overlay = source_mode != "generated_integrated_art"
    for source, destination, design in zip(
        thumbnail_paths, version_paths, candidate_designs, strict=True
    ):
        _normalize_thumbnail(
            source,
            destination,
            size,
            design=design if apply_local_overlay else None,
            font_path=font_path,
        )

    first_design = candidate_designs[0]
    first_text = first_design.text if first_design else package.strategy.thumbnail_text
    first_accent = (
        first_design.accent_word if first_design else package.strategy.thumbnail_accent_word
    )
    first_position = (
        first_design.text_position if first_design else package.strategy.thumbnail_text_position
    )

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
                else "Candidatas para A/B test no YouTube Studio."
            ),
            "text_overlay": first_text,
            "accent_word": first_accent,
            "text_position": first_position,
            "thumbnail_designs": [
                design.model_dump(mode="json") if design else None for design in candidate_designs
            ],
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
                    "design": (
                        candidate_designs[index].model_dump(mode="json")
                        if candidate_designs[index]
                        else None
                    ),
                }
                for index, (path, digest) in enumerate(
                    zip(version_paths, thumbnail_hashes, strict=True)
                )
            ],
            "approved": False,
            "updated_at": utc_now(),
            "provider": (
                "opening-scene+youtube-growth-specialist"
                if source_mode == "opening_scene_active_image"
                else "scene-reuse+youtube-growth-specialist"
                if source_mode == "selected_scene_images"
                else "imagegen-integrated+youtube-growth-specialist"
                if source_mode == "generated_integrated_art"
                else "youtube-growth-specialist"
            ),
            "text_baked_into_bitmap": not apply_local_overlay,
            "text_overlay": first_text,
            "accent_word": first_accent,
            "text_position": first_position,
            "overlay_style": package.strategy.thumbnail_overlay_style,
            "designs": [
                design.model_dump(mode="json") if design else None for design in candidate_designs
            ],
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
            "external_image_generations": (
                0
                if source_mode in {"opening_scene_active_image", "selected_scene_images"}
                else len(version_paths)
            ),
            "integrated_image_generations": (
                len(version_paths) if source_mode == "generated_integrated_art" else 0
            ),
            "reused_opening_image": int(source_mode == "opening_scene_active_image"),
            "reused_scene_images": (
                len(version_paths) if source_mode == "selected_scene_images" else 0
            ),
        },
        note=(
            "Imagem do versículo reutilizada na capa e composta no primeiro frame do Short."
            if project.video_format == "short"
            else f"{len(version_paths)} candidatas prontas para teste."
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


def _light_cover_design(script: ScriptDocument) -> YouTubeThumbnailDesign:
    opening = min(
        script.sections,
        key=lambda section: 0 if section.role == "opening" else 1,
    )
    candidate = (opening.on_screen_emphasis or script.title).strip()
    words = re.findall(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+", candidate)
    if len(words) < 2:
        words.extend(["PARA", "HOJE"])
    selected = words[:5]
    text = " ".join(selected)
    return YouTubeThumbnailDesign(
        text=text,
        accent_word=selected[-1],
        text_position="top",
        text_alignment="center",
        primary_color="white",
        accent_color="yellow",
        max_lines=3,
        texture="subtle_grain",
        narrative_element="none",
    )


def compose_light_short_cover(layout: ProjectLayout, *, force: bool = False) -> Path:
    """Compose the opening scene as the Short cover that render places at frame zero."""
    project = layout.load_manifest()
    if project.video_format != "short" or project.config.production.mode not in {
        "light_manual",
        "light_automatic",
    }:
        raise ValueError("A capa Light só pode ser composta em um projeto Short Light.")
    metadata = read_model(layout.metadata, YouTubeMetadata)
    script = read_model(layout.script, ScriptDocument)
    scenes = read_model(layout.scenes, SceneManifest)
    source = _short_opening_thumbnail_source(layout, scenes)
    design = _light_cover_design(script)
    size = (project.config.render.width, project.config.render.height)
    input_hash = sha256_value(
        {
            "algorithm": "light-opening-cover-v1",
            "source_hash": sha256_file(source),
            "metadata": metadata,
            "design": design,
            "size": size,
        }
    )
    if (
        not force
        and stage_is_current(layout, "thumbnail", input_hash)
        and layout.thumbnail.is_file()
    ):
        return layout.thumbnail

    invalidate(
        layout,
        stages=("render", "validation"),
        approvals=("metadata", "final_render"),
        note="A capa Light incorporada ao primeiro frame foi atualizada.",
    )
    version = _next_thumbnail_version(layout)
    version_path = layout.thumbnail.parent / f"thumbnail-v{version:03d}.png"
    configured_font = project.config.captions.font_file
    font_path = layout.root / configured_font if configured_font else None
    if font_path is not None and not font_path.is_file():
        font_path = None
    _normalize_thumbnail(
        source,
        version_path,
        size,
        design=design,
        font_path=font_path,
    )
    shutil.copy2(version_path, layout.thumbnail)
    digest = sha256_file(version_path)
    write_json(
        layout.thumbnail_prompt,
        {
            "schema_version": "2.0",
            "status": "completed",
            "concept": metadata.thumbnail_concept,
            "prompt": metadata.thumbnail_prompt,
            "format_note": "Capa vertical composta e incorporada ao início do Short.",
            "text_overlay": design.text,
            "accent_word": design.accent_word,
            "text_position": design.text_position,
            "thumbnail_designs": [design.model_dump(mode="json")],
        },
    )
    write_json(
        layout.thumbnail.parent / "thumbnail-manifest.json",
        {
            "active_path": relative_posix(version_path, layout.root),
            "active_hash": digest,
            "candidates": [
                {
                    "path": relative_posix(version_path, layout.root),
                    "hash": digest,
                    "source_path": relative_posix(source, layout.root),
                    "source_hash": sha256_file(source),
                    "paired_title": metadata.title,
                    "design": design.model_dump(mode="json"),
                }
            ],
            "approved": False,
            "updated_at": utc_now(),
            "provider": "light-opening-scene",
            "text_overlay": design.text,
            "accent_word": design.accent_word,
            "text_position": design.text_position,
            "designs": [design.model_dump(mode="json")],
        },
    )
    complete_stage(
        layout,
        "thumbnail",
        input_hash,
        [
            relative_posix(version_path, layout.root),
            relative_posix(layout.thumbnail, layout.root),
            relative_posix(layout.thumbnail_prompt, layout.root),
            "youtube/thumbnail-manifest.json",
        ],
        provider="light-opening-scene",
        usage={"candidates": 1, "external_image_generations": 0, "reused_opening_image": 1},
        note="Imagem da primeira cena reutilizada na capa e composta no primeiro frame do Short.",
    )
    layout.append_event(
        {
            "event": "light_short_cover_composed",
            "source": relative_posix(source, layout.root),
            "thumbnail": relative_posix(layout.thumbnail, layout.root),
            "frame_zero": True,
        }
    )
    return layout.thumbnail
