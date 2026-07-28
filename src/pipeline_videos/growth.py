from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pipeline_videos.analytics import compact_metric_learning
from pipeline_videos.metadata import _build_chapters
from pipeline_videos.models import (
    ApprovalStatus,
    AudioManifest,
    Briefing,
    CompactGrowthDraft,
    SceneManifest,
    ScriptDocument,
    SourceLibrary,
    StageStatus,
    YouTubeGrowthPackage,
    YouTubeGrowthStrategy,
    YouTubeMetadata,
    YouTubePromiseMatch,
)
from pipeline_videos.script import ensure_creative_contract
from pipeline_videos.state import complete_stage, invalidate, start_stage, wait_stage
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json, write_text
from pipeline_videos.utils import (
    compact_json_schema,
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

GROWTH_QUEUE_SCHEMA_VERSION = "2.0"
GROWTH_ALGORITHM_VERSION = "2.0"
SHORT_TITLE_MAX_CHARACTERS = 70
SHORT_DESCRIPTION_MAX_CHARACTERS = 600
SHORT_DESCRIPTION_MIN_CHARACTERS = 260
LONG_DESCRIPTION_MIN_CHARACTERS = 350
DESCRIPTION_SEARCH_WINDOW = 240
SEO_TAG_MIN_COUNT = 8
SEO_TAG_MAX_COUNT = 15
SEO_TAG_MAX_CHARACTERS = 350
OFFICIAL_YOUTUBE_GUIDELINES = (
    "https://support.google.com/youtube/answer/12340300",
    "https://support.google.com/youtube/answer/12948449",
    "https://support.google.com/youtube/answer/16090438",
    "https://support.google.com/youtube/answer/146402",
)

_GENERIC_DESCRIPTION_CTA_PATTERNS = (
    r"\bcompartilhe esta mensagem\b",
    r"\bcompartilhe este v[ií]deo\b",
    r"\binscreva-se no canal\b",
    r"\bdeixe seu like\b",
    r"\bcurta o v[ií]deo\b",
)
_SELF_REFERENTIAL_DESCRIPTION_PATTERNS = (
    r"\b(?:este|esse|aquele|neste|nesse|naquele)\s+(?:short|v[ií]deo|conte[uú]do)\b",
    r"\b(?:o|um)\s+(?:short|v[ií]deo|conte[uú]do)\s+"
    r"(?:mostra|explica|apresenta|revela|aborda|acompanha)\b",
)


@dataclass(frozen=True)
class GrowthStagingCheck:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _normalized_search_text(value: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", value.casefold(), flags=re.UNICODE))


def _normalized_biblical_reference_text(value: str) -> str:
    normalized = _normalized_search_text(value)
    normalized = re.sub(r"\bcap[ií]tulo\b", "", normalized)
    normalized = re.sub(r"\bvers[ií]culos?\b", "", normalized)
    return " ".join(normalized.split())


def _pairing_content_tokens(value: str) -> list[str]:
    stopwords = {
        "a",
        "as",
        "com",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "para",
        "por",
        "que",
        "um",
        "uma",
    }
    return [
        token
        for token in _normalized_search_text(value).split()
        if len(token) >= 3 and token not in stopwords
    ]


def _safe_staging_file(layout: ProjectLayout, relative_path: str) -> Path:
    candidate = (layout.root / relative_path).resolve()
    staging = layout.growth_staging.resolve()
    try:
        candidate.relative_to(staging)
    except ValueError as exc:
        raise ValueError(
            "A saída do especialista precisa permanecer no staging declarado."
        ) from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Saída do especialista ausente: {candidate}")
    return candidate


def _growth_context_payload(layout: ProjectLayout) -> dict[str, Any]:
    required = (
        layout.briefing,
        layout.sources,
        layout.script,
        layout.biblical_review,
        layout.scenes,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Entradas ausentes para o especialista: " + ", ".join(missing))

    project = layout.load_manifest()
    briefing = read_model(layout.briefing, Briefing)
    script = read_model(layout.script, ScriptDocument)
    sources = read_model(layout.sources, SourceLibrary)
    scenes = read_model(layout.scenes, SceneManifest)
    contract = ensure_creative_contract(layout, script)
    opening_scene = min(scenes.scenes, key=lambda item: item.order)
    used_source_ids = set(contract.source_ids or script.source_ids)
    return {
        "schema_version": "3.0",
        "algorithm": GROWTH_ALGORITHM_VERSION,
        "project": {
            "project_id": project.project_id,
            "title": project.title,
            "video_format": project.video_format,
            "target_duration_seconds": project.target_duration_seconds,
            "title_variants": project.config.youtube_growth.title_variants,
        },
        "briefing": {
            "title": briefing.title,
            "main_passage": briefing.main_passage,
            "central_theme": briefing.central_theme,
            "audience": briefing.audience,
        },
        "creative_contract": {
            "input_hash": contract.input_hash,
            "format": contract.format,
            "target_viewer": contract.target_viewer,
            "content_promise": contract.content_promise,
            "opening_hook": contract.opening_hook,
            "viewer_takeaway": contract.viewer_takeaway,
            "main_passage": contract.main_passage,
            "source_ids": contract.source_ids,
        },
        "sources": [
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "title": source.title,
                "passage": source.passage,
                "translation": source.translation,
                "verified": source.verified,
            }
            for source in sources.entries
            if not used_source_ids or source.source_id in used_source_ids
        ],
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
        "opening_scene": {
            "scene_id": opening_scene.scene_id,
            "section_id": opening_scene.section_id,
            "narrative_function": opening_scene.narrative_function,
        },
        "metric_learning": compact_metric_learning(layout),
    }


def growth_input_hash(layout: ProjectLayout) -> str:
    return sha256_value(_growth_context_payload(layout))


def materialize_cutout_growth_package(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> YouTubeGrowthPackage:
    """Materialize the YouTube package already decided in the long-form script."""

    project = layout.load_manifest()
    if project.config.youtube_growth.generation_mode != "script_package_local_v1":
        raise ValueError("O projeto não usa a embalagem local integrada ao roteiro.")
    script = read_model(layout.script, ScriptDocument)
    if script.long_form_strategy is None or script.long_form_strategy.youtube_package is None:
        raise ValueError(
            "O roteiro cutout precisa trazer título, descrição e conceito de thumbnail "
            "na mesma decisão editorial."
        )
    if (
        project.stages["youtube_growth"].status == StageStatus.COMPLETED
        and layout.growth_package.is_file()
        and not force
    ):
        return read_model(layout.growth_package, YouTubeGrowthPackage)
    seed = script.long_form_strategy.youtube_package
    scenes = read_model(layout.scenes, SceneManifest)
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    opening_scene = min(scenes.scenes, key=lambda item: item.order)
    input_hash = sha256_value(
        {
            "script": script,
            "generation_mode": "script_package_local_v1",
        }
    )
    metadata = YouTubeMetadata(
        title=seed.title,
        alternative_titles=[],
        description=seed.description,
        chapters=_chapters_for_project(layout, scenes, audio),
        topics=list(dict.fromkeys(seed.topics)),
        pinned_comment=seed.pinned_comment,
    )
    strategy = YouTubeGrowthStrategy(
        format="long_form",
        target_viewer=script.long_form_strategy.target_viewer,
        search_intent=seed.primary_keyword,
        primary_keyword=seed.primary_keyword,
        secondary_keywords=seed.secondary_keywords,
        content_promise=script.long_form_strategy.content_promise,
        opening_hook=script.long_form_strategy.opening_hook,
        title_rationale=(
            "O título entrega a promessa, usa linguagem de busca cristã quando natural e "
            "complementa a thumbnail sem repetir sua frase."
        ),
        description_rationale=(
            "A descrição foi decidida junto com a pesquisa e o roteiro, sem uma segunda "
            "interpretação do conteúdo."
        ),
        tag_strategy="Tema, passagem, livro e variações diretamente ligadas à pesquisa.",
        discovery_hypothesis=(
            "Uma promessa clara e fiel pode melhorar a escolha de assistir; o resultado "
            "continua sendo uma hipótese editorial."
        ),
        promise_match=YouTubePromiseMatch(
            content_promise=script.long_form_strategy.content_promise,
            opening_line=script.long_form_strategy.opening_line,
            opening_scene_id=opening_scene.scene_id,
            title_delivery=seed.title.split("|", 1)[0].strip(),
        ),
        source_ids=script.source_ids,
        section_ids=[section.section_id for section in script.sections],
        official_guidelines=list(OFFICIAL_YOUTUBE_GUIDELINES),
    )
    package = YouTubeGrowthPackage(
        job_id=f"script-package-{input_hash[:12]}",
        input_hash=input_hash,
        metadata=metadata,
        strategy=strategy,
    )
    write_json(layout.metadata, metadata)
    write_json(layout.growth_strategy, strategy)
    write_json(layout.growth_package, package)
    write_json(
        layout.growth_queue,
        {
            "schema_version": "3.0",
            "status": StageStatus.COMPLETED.value,
            "generation_mode": "script_package_local_v1",
            "input_hash": input_hash,
            "model_calls": 0,
            "source": relative_posix(layout.script, layout.root),
            "output": relative_posix(layout.growth_package, layout.root),
        },
    )
    write_json(
        layout.growth_warnings,
        {
            "generated_at": utc_now(),
            "validation_mode": "compiled_invariants_once_v2",
            "warnings": [],
        },
    )
    write_text(layout.description, _description_document(metadata))
    complete_stage(
        layout,
        "youtube_growth",
        input_hash,
        [
            relative_posix(layout.growth_queue, layout.root),
            relative_posix(layout.growth_package, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="script-package-local",
        usage={"model_calls": 0, "title_variants": 1},
    )
    complete_stage(
        layout,
        "metadata",
        sha256_value(package),
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="script-package-local",
    )
    return package


def _compact_growth_context(full_context: dict[str, Any]) -> dict[str, Any]:
    contract = full_context["creative_contract"]
    return {
        "schema_version": "2.0",
        "project": full_context["project"],
        "main_passage": contract["main_passage"],
        "target_viewer": contract["target_viewer"],
        "content_promise": contract["content_promise"],
        "opening_hook": contract["opening_hook"],
        "viewer_takeaway": contract["viewer_takeaway"],
        "source_ids": contract["source_ids"],
        "section_ids": [item["section_id"] for item in full_context["script_outline"]["sections"]],
        "sources": full_context["sources"],
        "requirements": {
            "title": "Enfoque concreto | Passagem.",
            "description": "Direta, única, natural e sem falar do próprio formato.",
            "topics": "De oito a quinze tags relevantes, distintas e sem #.",
            "pinned_comment": "Uma pergunta específica ligada ao tema ou passagem.",
        },
    }


def prepare_growth_queue(layout: ProjectLayout, *, force: bool = False) -> dict[str, Any]:
    project = layout.load_manifest()
    config = project.config.youtube_growth
    if not config.enabled:
        raise RuntimeError("A etapa de crescimento para YouTube está desativada neste projeto.")
    if config.generation_mode == "script_package_local_v1":
        package = materialize_cutout_growth_package(layout, force=force)
        return {
            "schema_version": "3.0",
            "status": StageStatus.COMPLETED.value,
            "generation_mode": config.generation_mode,
            "input_hash": package.input_hash,
            "model_calls": 0,
            "output": relative_posix(layout.growth_package, layout.root),
        }

    full_context = _growth_context_payload(layout)
    input_hash = sha256_value(full_context)
    stage = project.stages["youtube_growth"]
    if (
        not force
        and layout.growth_queue.is_file()
        and layout.growth_context.is_file()
        and stage.input_hash == input_hash
        and stage.status in {StageStatus.WAITING_EXTERNAL, StageStatus.COMPLETED}
    ):
        return read_json(layout.growth_queue)
    if project.approvals["metadata"].status == ApprovalStatus.APPROVED and not force:
        raise RuntimeError("Os metadados estão aprovados; use --force para solicitar nova versão.")

    compact_mode = config.generation_mode == "compact_metadata_draft_v1"
    context = _compact_growth_context(full_context) if compact_mode else full_context
    write_json(layout.growth_context, context)
    job_id = f"youtube-growth-{input_hash[:12]}"
    schema = (
        CompactGrowthDraft.model_json_schema()
        if compact_mode
        else YouTubeGrowthPackage.model_json_schema()
    )
    payload: dict[str, Any] = {
        "schema_version": GROWTH_QUEUE_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "project_id": project.project_id,
        "job_id": job_id,
        "input_hash": input_hash,
        "generation_mode": config.generation_mode,
        "validation_mode": config.validation_mode,
        "status": StageStatus.WAITING_EXTERNAL.value,
        "role": config.specialist_role,
        "contract_guard": {
            "input_hash": full_context["creative_contract"]["input_hash"],
            "content_promise": full_context["creative_contract"]["content_promise"],
            "opening_hook": full_context["creative_contract"]["opening_hook"],
            "opening_scene_id": full_context["opening_scene"]["scene_id"],
        },
        "orchestration": {
            "mode": "subagent",
            "worker_id": "youtube-growth-specialist",
            "single_writer": True,
            "may_modify_project_state": False,
            "may_publish": False,
            "reasoning_effort": "medium",
            "recommended_model": "gpt-5.6-sol",
            "context_policy": "metadata_only",
            "fork_turns": "none",
        },
        "inputs": {"context": relative_posix(layout.growth_context, layout.root)},
        "outputs": {
            "package": relative_posix(layout.growth_package_staging, layout.root),
            "title_count": config.title_variants,
            "title_standard": config.title_standard,
            "description_standard": config.description_standard,
        },
        "schemas": {"package": compact_json_schema(schema)},
        "preflight": {
            "read_only": True,
            "command": (
                f'uv run pipeline-videos metadata growth-check-staging "{project.project_id}"'
            ),
            "success_condition": "valid=true",
            "returns_all_detectable_errors": True,
            "max_model_repairs": project.config.production.max_model_repairs_per_stage,
            "fallback_command": (
                f'uv run pipeline-videos metadata growth-fallback "{project.project_id}"'
                if compact_mode
                else None
            ),
        },
        "instructions": [
            "Leia somente inputs.context.",
            "Crie título, descrição, palavras-chave, tags e comentário fixado.",
            "Não gere, edite, selecione ou descreva imagens.",
            "Trate SEO como hipótese; não invente volume de busca, CTR ou alcance.",
            "Preserve literalmente content_promise e opening_hook do contract_guard.",
            "Repita job_id e input_hash exatamente e grave somente outputs.package.",
            "Não altere arquivos fora do staging e não publique.",
        ],
    }
    invalidate(
        layout,
        stages=("metadata", "validation"),
        approvals=("metadata", "final_render"),
        note="Um novo pacote de metadados foi solicitado.",
    )
    start_stage(layout, "youtube_growth", input_hash, provider="youtube-growth-specialist")
    write_json(layout.growth_queue, payload)
    wait_stage(
        layout,
        "youtube_growth",
        input_hash,
        "Aguardando o pacote de metadados.",
        [
            relative_posix(layout.growth_queue, layout.root),
            relative_posix(layout.growth_context, layout.root),
        ],
    )
    return payload


def _load_compact_growth_draft(
    layout: ProjectLayout,
    queue: dict[str, Any],
) -> CompactGrowthDraft:
    path = _safe_staging_file(layout, queue["outputs"]["package"])
    draft = CompactGrowthDraft.model_validate(read_json(path))
    if draft.job_id != queue.get("job_id") or draft.input_hash != queue.get("input_hash"):
        raise ValueError("O rascunho pertence a uma fila antiga.")
    return draft


def _fit_compact_title(title: str, passage: str, fallback: str) -> str:
    suffix = f" | {passage.strip()}" if passage.strip() else ""
    left = (title.strip() or fallback.strip() or "Uma palavra de Jesus").split("|", 1)[0].strip()
    if passage.strip():
        left = re.sub(re.escape(passage.strip()), "", left, flags=re.IGNORECASE)
    left = left.strip(" -|:;,.!?") or "Uma palavra de Jesus"
    maximum = max(1, SHORT_TITLE_MAX_CHARACTERS - len(suffix))
    if len(left) > maximum:
        left = left[:maximum].rsplit(" ", 1)[0].strip() or left[:maximum]
    return f"{left}{suffix}"


def _compact_topics(
    draft: CompactGrowthDraft,
    *,
    passage: str,
    book: str,
) -> list[str]:
    candidates = [
        draft.primary_keyword,
        passage,
        book,
        *draft.secondary_keywords,
        *draft.topics,
        "Jesus",
        "Bíblia",
        "palavra de Jesus",
    ]
    topics: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.replace("#", "").strip(" ,")
        key = _normalized_search_text(value)
        if not key or key in seen:
            continue
        if len(",".join([*topics, value])) > SEO_TAG_MAX_CHARACTERS:
            continue
        topics.append(value)
        seen.add(key)
        if len(topics) == SEO_TAG_MAX_COUNT:
            break
    return topics


def _build_compact_growth_package(
    layout: ProjectLayout,
    queue: dict[str, Any],
    draft: CompactGrowthDraft,
) -> YouTubeGrowthPackage:
    briefing = read_model(layout.briefing, Briefing)
    script = read_model(layout.script, ScriptDocument)
    context = read_json(layout.growth_context)
    guard = queue["contract_guard"]
    passage = briefing.main_passage.strip()
    primary_keyword = draft.primary_keyword.strip()
    title = _fit_compact_title(draft.title, passage, briefing.title)
    metadata = YouTubeMetadata(
        status="draft",
        title=title,
        alternative_titles=[],
        description=draft.description.strip()[:5000],
        chapters=[],
        topics=_compact_topics(
            draft,
            passage=passage,
            book=passage.split()[0] if passage else "Bíblia",
        ),
        pinned_comment=(
            draft.pinned_comment.strip()
            or f"Qual frase de {passage or 'Jesus'} você quer guardar hoje?"
        ),
        publication_visibility="private",
    )
    suffix = f" | {passage}" if passage else ""
    strategy = YouTubeGrowthStrategy(
        format="short",
        target_viewer=str(context.get("target_viewer") or "público interessado na passagem"),
        search_intent=f"Compreender uma mensagem sobre {primary_keyword}.",
        primary_keyword=primary_keyword,
        secondary_keywords=draft.secondary_keywords,
        content_promise=str(guard["content_promise"]),
        opening_hook=str(guard["opening_hook"]),
        title_rationale=draft.title_rationale.strip() or "Enfoque concreto e passagem.",
        description_rationale=draft.description_rationale.strip() or "Resumo direto do assunto.",
        tag_strategy=draft.tag_strategy.strip() or "Tema, passagem, livro e buscas relacionadas.",
        discovery_hypothesis=(
            draft.discovery_hypothesis.strip()
            or "Clareza temática pode facilitar a escolha de assistir."
        ),
        promise_match=YouTubePromiseMatch(
            content_promise=str(guard["content_promise"]),
            opening_line=str(guard["opening_hook"]),
            opening_scene_id=str(guard["opening_scene_id"]),
            title_delivery=title.removesuffix(suffix).strip(),
        ),
        source_ids=[str(item) for item in context.get("source_ids", [])],
        section_ids=(
            [str(item) for item in context.get("section_ids", [])]
            or [section.section_id for section in script.sections]
        ),
        compliance_checks=["Sem promessa de viralização ou alcance."],
        official_guidelines=list(OFFICIAL_YOUTUBE_GUIDELINES),
    )
    return YouTubeGrowthPackage(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        metadata=metadata,
        strategy=strategy,
    )


def _compact_growth_advisories(
    layout: ProjectLayout,
    package: YouTubeGrowthPackage,
) -> list[str]:
    warnings: list[str] = []
    description = package.metadata.description.strip()
    if not SHORT_DESCRIPTION_MIN_CHARACTERS <= len(description) <= SHORT_DESCRIPTION_MAX_CHARACTERS:
        warnings.append("Descrição fora da faixa recomendada de 260 a 600 caracteres.")
    if "\n\n" not in description:
        warnings.append("Descrição sem dois parágrafos distintos.")
    if not SEO_TAG_MIN_COUNT <= len(package.metadata.topics) <= SEO_TAG_MAX_COUNT:
        warnings.append("Quantidade de tags fora da recomendação de 8 a 15.")
    if "?" not in package.metadata.pinned_comment:
        warnings.append("Comentário fixado não formula uma pergunta.")
    return warnings


def _validate_search_first_seo(
    layout: ProjectLayout,
    package: YouTubeGrowthPackage,
) -> None:
    metadata = package.metadata
    strategy = package.strategy
    briefing = read_model(layout.briefing, Briefing)
    minimum = (
        SHORT_DESCRIPTION_MIN_CHARACTERS
        if strategy.format == "short"
        else LONG_DESCRIPTION_MIN_CHARACTERS
    )
    maximum = SHORT_DESCRIPTION_MAX_CHARACTERS if strategy.format == "short" else 5000
    description = metadata.description.strip()
    if not minimum <= len(description) <= maximum:
        raise ValueError(f"A descrição precisa ter entre {minimum} e {maximum} caracteres.")
    if "\n\n" not in description:
        raise ValueError("A descrição precisa ter ao menos dois parágrafos.")
    opening = _normalized_search_text(description[:DESCRIPTION_SEARCH_WINDOW])
    primary = _normalized_search_text(strategy.primary_keyword)
    passage = _normalized_search_text(briefing.main_passage)
    if not primary or primary not in opening:
        raise ValueError("A abertura da descrição precisa conter primary_keyword.")
    if passage and passage not in opening:
        raise ValueError("A abertura da descrição precisa conter a passagem principal.")
    if primary not in _normalized_search_text(metadata.title[:55]):
        raise ValueError("primary_keyword precisa aparecer no início do título.")
    normalized_description = _normalized_search_text(description)
    if any(
        re.search(pattern, normalized_description) for pattern in _GENERIC_DESCRIPTION_CTA_PATTERNS
    ):
        raise ValueError("A descrição não pode usar CTA genérico.")
    if any(
        re.search(pattern, normalized_description)
        for pattern in _SELF_REFERENTIAL_DESCRIPTION_PATTERNS
    ):
        raise ValueError("A descrição não pode se referir ao próprio formato.")
    hashtags = re.findall(r"(?<!\w)#[\wÀ-ÿ]+", description, flags=re.UNICODE)
    if len(hashtags) > 3 or any(
        tag.casefold() in {"#viral", "#fyp", "#viralizar"} for tag in hashtags
    ):
        raise ValueError("A descrição usa hashtags inválidas.")
    tags = [tag.strip() for tag in metadata.topics if tag.strip()]
    normalized_tags = [_normalized_search_text(tag) for tag in tags]
    if not SEO_TAG_MIN_COUNT <= len(tags) <= SEO_TAG_MAX_COUNT:
        raise ValueError("metadata.topics precisa conter de oito a quinze tags.")
    if len(set(normalized_tags)) != len(normalized_tags):
        raise ValueError("metadata.topics precisa conter tags distintas.")
    if any("#" in tag for tag in tags):
        raise ValueError("metadata.topics usa tags sem #.")
    if len(",".join(tags)) > SEO_TAG_MAX_CHARACTERS:
        raise ValueError("metadata.topics excede 350 caracteres.")
    if primary not in normalized_tags:
        raise ValueError("metadata.topics precisa incluir primary_keyword.")
    if passage and passage not in normalized_tags:
        raise ValueError("metadata.topics precisa incluir a passagem principal.")
    if not all(
        value.strip()
        for value in (
            strategy.description_rationale,
            strategy.tag_strategy,
            strategy.discovery_hypothesis,
        )
    ):
        raise ValueError("A estratégia precisa justificar descrição, tags e descoberta.")
    if "?" not in metadata.pinned_comment:
        raise ValueError("O comentário fixado precisa formular uma pergunta.")
    anchor_tokens = set(
        _pairing_content_tokens(f"{strategy.primary_keyword} {briefing.main_passage}")
    )
    if anchor_tokens and not anchor_tokens.intersection(
        _pairing_content_tokens(metadata.pinned_comment)
    ):
        raise ValueError("O comentário fixado precisa se relacionar ao tema ou passagem.")


def _validate_package(
    layout: ProjectLayout,
    package: YouTubeGrowthPackage,
    queue: dict[str, Any],
) -> None:
    if queue.get("schema_version") != GROWTH_QUEUE_SCHEMA_VERSION:
        raise ValueError("A versão da fila de crescimento não é suportada.")
    if package.job_id != queue.get("job_id") or package.input_hash != queue.get("input_hash"):
        raise RuntimeError("O pacote pertence a uma fila antiga.")
    project = layout.load_manifest()
    expected_format = "short" if project.video_format == "short" else "long_form"
    if package.strategy.format != expected_format:
        raise ValueError("A estratégia não corresponde ao formato do projeto.")
    titles = [package.metadata.title, *package.metadata.alternative_titles]
    if len(titles) != project.config.youtube_growth.title_variants:
        raise ValueError("O pacote não contém a quantidade exata de títulos.")
    if len({title.strip().casefold() for title in titles}) != len(titles):
        raise ValueError("Os títulos precisam ser distintos.")
    if any(not title.strip() or len(title) > 100 for title in titles):
        raise ValueError("Cada título precisa ter de um a cem caracteres.")
    guard = queue["contract_guard"]
    if package.strategy.content_promise != guard["content_promise"]:
        raise ValueError("A estratégia alterou content_promise.")
    if package.strategy.opening_hook != guard["opening_hook"]:
        raise ValueError("A estratégia alterou opening_hook.")
    if project.config.youtube_growth.description_standard == "search_first_unique_v2":
        _validate_search_first_seo(layout, package)
    briefing = read_model(layout.briefing, Briefing)
    suffix = f" | {briefing.main_passage.strip()}"
    if project.config.youtube_growth.title_standard == "creative_pipe_passage_v3":
        if not suffix.strip(" |") or any(
            not title.endswith(suffix) or not title.removesuffix(suffix).strip() for title in titles
        ):
            raise ValueError("O título precisa seguir 'Enfoque concreto | Passagem'.")
        reference = _normalized_biblical_reference_text(briefing.main_passage)
        if reference and any(
            reference in _normalized_biblical_reference_text(title.removesuffix(suffix).strip())
            for title in titles
        ):
            raise ValueError("A passagem deve aparecer uma única vez, após a barra.")
        promise_match = package.strategy.promise_match
        if promise_match is None:
            raise ValueError("O padrão de título exige strategy.promise_match.")
        if (
            promise_match.content_promise != guard["content_promise"]
            or promise_match.opening_line != guard["opening_hook"]
            or promise_match.opening_scene_id != guard["opening_scene_id"]
            or promise_match.title_delivery != package.metadata.title.removesuffix(suffix).strip()
        ):
            raise ValueError("promise_match não corresponde ao contrato e ao título.")
    if not set(package.strategy.official_guidelines).issubset(OFFICIAL_YOUTUBE_GUIDELINES):
        raise ValueError("A estratégia citou orientação não fornecida.")
    sources = read_model(layout.sources, SourceLibrary)
    script = read_model(layout.script, ScriptDocument)
    if not set(package.strategy.source_ids).issubset({item.source_id for item in sources.entries}):
        raise ValueError("A estratégia referencia source_id inexistente.")
    if not set(package.strategy.section_ids).issubset(
        {item.section_id for item in script.sections}
    ):
        raise ValueError("A estratégia referencia section_id inexistente.")


def check_growth_package_staging(layout: ProjectLayout) -> GrowthStagingCheck:
    try:
        queue = read_json(layout.growth_queue)
        if queue.get("generation_mode") == "compact_metadata_draft_v1":
            package = _build_compact_growth_package(
                layout,
                queue,
                _load_compact_growth_draft(layout, queue),
            )
            _validate_package(layout, package, queue)
            return GrowthStagingCheck(
                True,
                (),
                tuple(_compact_growth_advisories(layout, package)),
            )
        package = read_model(
            _safe_staging_file(layout, queue["outputs"]["package"]),
            YouTubeGrowthPackage,
        )
        _validate_package(layout, package, queue)
        return GrowthStagingCheck(True, ())
    except ValidationError as exc:
        return GrowthStagingCheck(
            False,
            tuple(
                f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            ),
        )
    except (FileNotFoundError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return GrowthStagingCheck(False, (str(exc),))


def materialize_compact_growth_fallback(layout: ProjectLayout) -> CompactGrowthDraft:
    queue = read_json(layout.growth_queue)
    if queue.get("generation_mode") != "compact_metadata_draft_v1":
        raise RuntimeError("A fila atual não usa o rascunho compacto.")
    context = read_json(layout.growth_context)
    briefing = read_model(layout.briefing, Briefing)
    theme = briefing.central_theme.strip() or briefing.title.strip() or "Uma palavra de Jesus"
    passage = briefing.main_passage.strip()
    promise = str(context.get("content_promise") or theme)
    draft = CompactGrowthDraft(
        job_id=queue["job_id"],
        input_hash=queue["input_hash"],
        title=theme[:100],
        description=(
            f"{theme} em {passage}: {promise.rstrip('.')} com uma fala direta e verificada de "
            "Jesus. A passagem preserva a citação literal e encerra com a referência exata.\n\n"
            f"{passage} mantém o foco no sentido bíblico e na experiência humana indicada pelo "
            "próprio trecho, com clareza e fidelidade."
        ),
        primary_keyword=theme[:80],
        secondary_keywords=[passage, "fala de Jesus", "mensagem bíblica"],
        topics=[theme, passage, "Jesus", "Bíblia", "palavra de Jesus"],
        pinned_comment=f"Qual frase de {passage} você quer guardar hoje?",
        title_rationale="Enfoque concreto seguido da passagem.",
        description_rationale="Resumo direto do assunto e da fonte bíblica.",
        tag_strategy="Tema, passagem, livro e buscas relacionadas.",
        discovery_hypothesis="Clareza pode facilitar a escolha de assistir.",
    )
    write_json(layout.growth_package_staging, draft)
    return draft


def _chapters_for_project(
    layout: ProjectLayout,
    scenes: SceneManifest,
    audio: AudioManifest | None,
) -> list[str]:
    project = layout.load_manifest()
    if project.video_format == "short":
        return []
    script = read_model(layout.script, ScriptDocument)
    return _build_chapters(scenes, audio, script, scripture_timeline=True)


def _description_document(metadata: YouTubeMetadata) -> str:
    return metadata.description.rstrip() + "\n"


def refresh_growth_metadata(layout: ProjectLayout) -> YouTubeMetadata:
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
        note="Os capítulos dos metadados foram sincronizados.",
    )
    metadata_hash = sha256_value(
        {
            "growth_input_hash": layout.load_manifest().stages["youtube_growth"].input_hash,
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
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="youtube-growth-specialist",
    )
    return metadata


def import_growth_package(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> YouTubeGrowthPackage:
    queue = read_json(layout.growth_queue)
    if queue.get("role") != "youtube_growth_specialist":
        raise ValueError("Fila do especialista inválida.")
    if queue.get("input_hash") != growth_input_hash(layout):
        raise RuntimeError("A fila ficou obsoleta após mudanças no projeto.")
    project = layout.load_manifest()
    if project.approvals["metadata"].status == ApprovalStatus.APPROVED and not force:
        raise RuntimeError("Os metadados estão aprovados; use --force para importar nova versão.")

    compact_mode = queue.get("generation_mode") == "compact_metadata_draft_v1"
    if compact_mode:
        package = _build_compact_growth_package(
            layout,
            queue,
            _load_compact_growth_draft(layout, queue),
        )
    else:
        package = read_model(
            _safe_staging_file(layout, queue["outputs"]["package"]),
            YouTubeGrowthPackage,
        )
    _validate_package(layout, package, queue)
    warnings = _compact_growth_advisories(layout, package) if compact_mode else []
    scenes = read_model(layout.scenes, SceneManifest)
    audio = read_model(layout.audio, AudioManifest) if layout.audio.is_file() else None
    metadata = package.metadata.model_copy(
        update={"chapters": _chapters_for_project(layout, scenes, audio)}
    )
    final_package = package.model_copy(update={"metadata": metadata})

    write_json(layout.metadata, metadata)
    write_json(layout.growth_strategy, package.strategy)
    write_json(layout.growth_package, final_package)
    write_json(
        layout.growth_warnings,
        {
            "generated_at": utc_now(),
            "validation_mode": queue.get("validation_mode"),
            "warnings": warnings,
        },
    )
    write_text(layout.description, _description_document(metadata))
    completed_queue = dict(queue)
    completed_queue["status"] = StageStatus.COMPLETED.value
    completed_queue["imported_at"] = utc_now()
    write_json(layout.growth_queue, completed_queue)
    invalidate(
        layout,
        stages=("validation",),
        approvals=("metadata", "final_render"),
        note="O pacote de metadados foi importado.",
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
            relative_posix(layout.growth_warnings, layout.root),
        ],
        provider="youtube-growth-specialist",
        usage={
            "title_variants": len({metadata.title, *metadata.alternative_titles}),
            "context_bytes": layout.growth_context.stat().st_size,
            "schema_bytes": len(
                json.dumps(queue["schemas"]["package"], ensure_ascii=False).encode("utf-8")
            ),
            "advisory_warnings": len(warnings),
        },
    )
    complete_stage(
        layout,
        "metadata",
        sha256_value(
            {
                "growth_input_hash": growth_hash,
                "package": final_package,
                "audio_hash": sha256_file(layout.audio) if layout.audio.is_file() else None,
            }
        ),
        [
            relative_posix(layout.metadata, layout.root),
            relative_posix(layout.description, layout.root),
            relative_posix(layout.growth_strategy, layout.root),
        ],
        provider="youtube-growth-specialist",
    )
    layout.append_event(
        {
            "event": "youtube_growth_package_imported",
            "job_id": package.job_id,
            "format": project.video_format,
        }
    )
    return final_package
