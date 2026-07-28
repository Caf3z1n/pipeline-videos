from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageStat

from pipeline_videos.imagegen_retry import (
    imagegen_retry_instructions,
    imagegen_retry_policy,
)
from pipeline_videos.models import (
    ApprovalStatus,
    ImageItem,
    ImageManifest,
    ImageVersion,
    Palette,
    SceneManifest,
    StageRecord,
    StageStatus,
)
from pipeline_videos.scenes import _resolved_style
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    set_approval,
    stage_is_current,
    start_stage,
    wait_stage,
)
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value, utc_now

CODEX_QUEUE_SCHEMA_VERSION = "1.8"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _scene_prompt_payload(layout: ProjectLayout, scenes: SceneManifest) -> dict[str, Any]:
    """Return only inputs that affect an image, excluding mutable workflow state."""
    return {
        "script_version": scenes.script_version,
        "style_profile_id": scenes.style_profile_id,
        "palette_id": scenes.palette_id,
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "image_id": scene.image_id,
                "order": scene.order,
                "prompt": scene.prompt.assembled_prompt,
                "image_source_mode": scene.image_source_mode,
                "image_source_path": scene.image_source_path,
                "image_source_hash": (
                    sha256_file(layout.root / scene.image_source_path)
                    if scene.image_source_mode == "fixed_profile_asset_v1"
                    and scene.image_source_path
                    and (layout.root / scene.image_source_path).is_file()
                    else None
                ),
            }
            for scene in scenes.scenes
        ],
    }


def _selected_ids(scenes: SceneManifest, scene_id: str | None) -> set[str]:
    available = {scene.scene_id for scene in scenes.scenes}
    if scene_id is None:
        return available
    if scene_id not in available:
        raise ValueError(f"Cena desconhecida: {scene_id}")
    return {scene_id}


def _safe_component(value: str, label: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{label} inválido para uso em caminho: {value!r}")
    if "/" in value or "\\" in value:
        raise ValueError(f"{label} inválido para uso em caminho: {value!r}")
    return value


def _image_path(layout: ProjectLayout, scene_id: str, version: int, extension: str) -> Path:
    scene_id = _safe_component(scene_id, "scene_id")
    if not extension.startswith("."):
        extension = f".{extension}"
    return layout.image_assets / scene_id / f"v{version:03d}{extension.lower()}"


def _staging_path(layout: ProjectLayout, scene_id: str, version: int, extension: str) -> Path:
    scene_id = _safe_component(scene_id, "scene_id")
    if not extension.startswith("."):
        extension = f".{extension}"
    return layout.image_staging / f"{scene_id}-v{version:03d}{extension.lower()}"


def _is_valid_png(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            if handle.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                return False
        with Image.open(path) as image:
            image.load()
        return True
    except (OSError, ValueError):
        return False


def stage_generated_image(
    layout: ProjectLayout,
    *,
    job_id: str,
    source_path: Path,
    codex_home: Path | None = None,
) -> Path:
    """Materialize one Imagegen ``savedPath`` into its declared staging destination."""
    if not layout.image_queue.is_file():
        raise FileNotFoundError(f"Fila do Codex não encontrada: {layout.image_queue}")
    queue = read_json(layout.image_queue)
    items = queue.get("items") if isinstance(queue, dict) else None
    if not isinstance(items, list):
        raise ValueError("Fila do Codex inválida: items ausente ou não é uma lista.")
    matches = [
        entry for entry in items if isinstance(entry, dict) and entry.get("job_id") == job_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Job de imagem desconhecido ou duplicado: {job_id}")
    entry = matches[0]
    staging_path = entry.get("staging_path")
    if not isinstance(staging_path, str):
        raise ValueError(f"Job {job_id} sem staging_path válido.")

    if not source_path.is_absolute():
        raise ValueError("O savedPath do Imagegen precisa ser um caminho absoluto.")
    try:
        source = source_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            "Não foi possível abrir o savedPath retornado pelo Imagegen. "
            "Não use result/base64, revisedPrompt ou outro campo como caminho."
        ) from exc
    configured_home = codex_home or Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    generated_root = (configured_home.expanduser().resolve() / "generated_images").resolve()
    try:
        relative_source = source.relative_to(generated_root)
    except ValueError as exc:
        raise ValueError(
            f"O arquivo precisa estar dentro do cache do Codex: {generated_root}"
        ) from exc
    if len(relative_source.parts) < 2 or source.suffix.casefold() != ".png":
        raise ValueError("O savedPath não corresponde a um PNG gerado pelo Imagegen.")
    if not _is_valid_png(source):
        raise ValueError(f"O savedPath não contém um PNG íntegro: {source}")

    destination = (layout.root / staging_path).resolve()
    try:
        destination.relative_to(layout.image_staging.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho de staging inseguro: {staging_path}") from exc
    if _is_valid_png(destination):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temp)
        if not _is_valid_png(temp):
            raise ValueError(f"A cópia temporária não contém um PNG íntegro: {temp}")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return destination


def _version(item: ImageItem, version: int | None) -> ImageVersion | None:
    if version is None:
        return None
    return next((candidate for candidate in item.versions if candidate.version == version), None)


def _next_version(layout: ProjectLayout, item: ImageItem, extension: str) -> tuple[int, Path]:
    version = max((candidate.version for candidate in item.versions), default=0) + 1
    destination = _image_path(layout, item.scene_id, version, extension)
    while destination.exists():
        version += 1
        destination = _image_path(layout, item.scene_id, version, extension)
    return version, destination


def _active_version_is_current(layout: ProjectLayout, item: ImageItem) -> bool:
    active = _version(item, item.active_version)
    return bool(
        active and active.prompt_hash == item.prompt_hash and (layout.root / active.path).is_file()
    )


def _manifest_matches(manifest: ImageManifest, scenes: SceneManifest) -> bool:
    if manifest.style_profile_id != scenes.style_profile_id:
        return False
    if manifest.palette_id != scenes.palette_id:
        return False
    expected = [
        (
            scene.image_id,
            scene.scene_id,
            scene.prompt.assembled_prompt,
            scene.image_source_mode,
            scene.image_source_path,
        )
        for scene in scenes.scenes
    ]
    current = [
        (item.image_id, item.scene_id, item.prompt, item.source_mode, item.source_path)
        for item in manifest.items
    ]
    return current == expected


def _fixed_profile_asset_path(layout: ProjectLayout, item: ImageItem) -> Path:
    if item.source_mode != "fixed_profile_asset_v1" or not item.source_path:
        raise ValueError(f"A cena {item.scene_id} não declara um ativo fixo válido.")
    source = (layout.root / item.source_path).resolve()
    try:
        source.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError(f"Caminho inseguro para ativo fixo: {item.source_path}") from exc
    if not source.is_file():
        raise FileNotFoundError(f"Ativo visual fixo não encontrado: {source}")
    return source


def _materialize_fixed_profile_assets(
    layout: ProjectLayout,
    scenes: SceneManifest,
    manifest: ImageManifest,
) -> tuple[bool, bool]:
    manifest_changed = False
    scenes_changed = False
    scene_by_id = {scene.scene_id: scene for scene in scenes.scenes}
    for item in manifest.items:
        if item.source_mode != "fixed_profile_asset_v1":
            continue
        source = _fixed_profile_asset_path(layout, item)
        source_hash = sha256_file(source)
        current = next(
            (
                version
                for version in item.versions
                if version.provider == "profile-fixed-asset"
                and version.prompt_hash == item.prompt_hash
                and version.metadata.get("source_hash") == source_hash
                and (layout.root / version.path).is_file()
            ),
            None,
        )
        if current is None:
            version, destination = _next_version(layout, item, ".png")
            width, height = _save_imported_png(
                source,
                destination,
                reject_panel_like=False,
            )
            current = ImageVersion(
                version=version,
                path=relative_posix(destination, layout.root),
                provider="profile-fixed-asset",
                prompt_hash=item.prompt_hash,
                created_at=utc_now(),
                metadata={
                    "asset_role": "spoken_reference",
                    "source_path": item.source_path,
                    "source_hash": source_hash,
                    "width": width,
                    "height": height,
                    "sha256": sha256_file(destination),
                },
            )
            item.versions.append(current)
            manifest_changed = True
        if item.active_version != current.version or item.status != StageStatus.COMPLETED:
            item.active_version = current.version
            item.status = StageStatus.COMPLETED
            item.last_error = None
            manifest_changed = True
        scene = scene_by_id[item.scene_id]
        if scene.generation_status != StageStatus.COMPLETED:
            scene.generation_status = StageStatus.COMPLETED
            scenes_changed = True
    return manifest_changed, scenes_changed


def _write_scenes_if_changed(
    layout: ProjectLayout,
    scenes: SceneManifest,
    before_hash: str,
) -> None:
    if sha256_value(scenes) != before_hash:
        write_json(layout.scenes, scenes)


def _active_artifacts(layout: ProjectLayout, manifest: ImageManifest) -> list[str] | None:
    artifacts = [relative_posix(layout.images, layout.root)]
    for item in manifest.items:
        active = _version(item, item.active_version)
        if active is None or not (layout.root / active.path).is_file():
            return None
        artifacts.append(active.path)
    return artifacts


def _mark_images_ready(
    layout: ProjectLayout,
    input_hash: str,
    artifacts: list[str],
    *,
    provider: str,
    note: str,
) -> None:
    project = layout.load_manifest()
    previous = project.stages["images"]
    project.stages["images"] = StageRecord(
        status=StageStatus.READY,
        input_hash=input_hash,
        provider=provider,
        attempts=previous.attempts,
        artifacts=artifacts,
        updated_at=utc_now(),
        note=note,
    )
    layout.save_manifest(project)
    layout.append_event({"event": "stage_ready", "stage": "images", "note": note})


def _finish_images_stage(
    layout: ProjectLayout,
    manifest: ImageManifest,
    input_hash: str,
    *,
    provider: str,
    waiting_external: bool,
    waiting_note: str | None = None,
    extra_artifacts: list[str] | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    artifacts = _active_artifacts(layout, manifest)
    if artifacts is not None:
        complete_stage(
            layout,
            "images",
            input_hash,
            artifacts,
            provider=provider,
            usage={"images": len(manifest.items), **(usage or {})},
        )
        return

    existing = [relative_posix(layout.images, layout.root)]
    if layout.image_queue.is_file():
        existing.append(relative_posix(layout.image_queue, layout.root))
    existing.extend(extra_artifacts or [])
    if waiting_external:
        wait_stage(
            layout,
            "images",
            input_hash,
            waiting_note or "Aguardando o Codex gerar e importar as imagens pendentes.",
            existing,
        )
    else:
        _mark_images_ready(
            layout,
            input_hash,
            existing,
            provider=provider,
            note="Há imagens de fixture ainda não geradas.",
        )


def build_image_manifest(
    layout: ProjectLayout,
    *,
    scene_id: str | None = None,
    force: bool = False,
) -> ImageManifest:
    """Build the portable image manifest while preserving every existing version."""
    scenes = read_model(layout.scenes, SceneManifest)
    selected = _selected_ids(scenes, scene_id)
    input_hash = sha256_value(_scene_prompt_payload(layout, scenes))

    existing: ImageManifest | None = None
    if layout.images.is_file():
        existing = read_model(layout.images, ImageManifest)
        if not force and _manifest_matches(existing, scenes):
            manifest_changed, scenes_changed = _materialize_fixed_profile_assets(
                layout, scenes, existing
            )
            if manifest_changed:
                existing.generated_at = utc_now()
                write_json(layout.images, existing)
            if scenes_changed:
                write_json(layout.scenes, scenes)
            if not stage_is_current(layout, "image_manifest", input_hash):
                start_stage(layout, "image_manifest", input_hash, provider="scene-manifest")
                complete_stage(
                    layout,
                    "image_manifest",
                    input_hash,
                    [relative_posix(layout.images, layout.root)],
                    provider="scene-manifest",
                )
            return existing

    start_stage(layout, "image_manifest", input_hash, provider="scene-manifest")
    try:
        existing_by_id = {item.image_id: item for item in existing.items} if existing else {}
        items: list[ImageItem] = []
        for scene in sorted(scenes.scenes, key=lambda item: item.order):
            prompt = scene.prompt.assembled_prompt.strip()
            if not prompt:
                raise ValueError(f"A cena {scene.scene_id} não possui prompt montado.")
            prompt_hash = sha256_value(prompt)
            previous = existing_by_id.get(scene.image_id)
            if previous is None:
                item = ImageItem(
                    image_id=scene.image_id,
                    scene_id=scene.scene_id,
                    prompt=prompt,
                    prompt_hash=prompt_hash,
                    source_mode=scene.image_source_mode,
                    source_path=scene.image_source_path,
                )
            else:
                item = previous.model_copy(deep=True)
                item.scene_id = scene.scene_id
                source_changed = (
                    item.source_mode != scene.image_source_mode
                    or item.source_path != scene.image_source_path
                )
                prompt_changed = (
                    item.prompt_hash != prompt_hash or item.prompt != prompt or source_changed
                )
                item.prompt = prompt
                item.prompt_hash = prompt_hash
                item.source_mode = scene.image_source_mode
                item.source_path = scene.image_source_path
                if prompt_changed:
                    item.status = StageStatus.STALE if item.versions else StageStatus.PENDING
                    item.last_error = None
                elif force and scene.scene_id in selected:
                    item.status = StageStatus.PENDING
                    item.last_error = None
            items.append(item)

        manifest = ImageManifest(
            generated_at=utc_now(),
            style_profile_id=scenes.style_profile_id,
            palette_id=scenes.palette_id,
            items=items,
        )
        _, scenes_changed = _materialize_fixed_profile_assets(layout, scenes, manifest)
        write_json(layout.images, manifest)
        if scenes_changed:
            write_json(layout.scenes, scenes)
        complete_stage(
            layout,
            "image_manifest",
            input_hash,
            [relative_posix(layout.images, layout.root)],
            provider="scene-manifest",
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "image_manifest", exc)
        raise


def _queue_core(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "generated_at"}


def _worker_job_paths(layout: ProjectLayout, items: list[dict[str, Any]]) -> dict[str, str]:
    worker_ids = sorted(
        {
            str(item["worker_id"])
            for item in items
            if isinstance(item.get("worker_id"), str) and item["worker_id"]
        }
    )
    return {
        worker_id: relative_posix(layout.image_jobs / f"{worker_id}.json", layout.root)
        for worker_id in worker_ids
    }


def _compact_worker_prompts(
    jobs: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Deduplicate an exact common prefix while preserving byte-identical final prompts."""

    if len(jobs) < 2:
        return None, jobs
    prompts = [str(job.get("prompt") or "") for job in jobs]
    prefix = os.path.commonprefix(prompts)
    newline = prefix.rfind("\n")
    if newline >= 0:
        prefix = prefix[: newline + 1]
    if len(prefix.encode("utf-8")) < 256:
        return None, jobs
    compacted: list[dict[str, Any]] = []
    for job, prompt in zip(jobs, prompts, strict=True):
        item = dict(job)
        item.pop("prompt", None)
        item["prompt_delta"] = prompt[len(prefix) :]
        compacted.append(item)
    return prefix, compacted


def _write_worker_job_files(layout: ProjectLayout, queue: dict[str, Any]) -> list[str]:
    declared = queue.get("orchestration", {}).get("worker_job_files", {})
    if not isinstance(declared, dict):
        return []
    for stale in layout.image_jobs.glob("image-worker-*.json"):
        stale.unlink()
    artifacts: list[str] = []
    for worker_id, relative_path in declared.items():
        if not isinstance(worker_id, str) or not isinstance(relative_path, str):
            raise ValueError("Arquivo compacto de trabalhador inválido na fila de imagens.")
        jobs = [item for item in queue.get("items", []) if item.get("worker_id") == worker_id]
        common_prompt_prefix, compacted_jobs = _compact_worker_prompts(jobs)
        path = (layout.root / relative_path).resolve()
        try:
            path.relative_to(layout.image_jobs.resolve())
        except ValueError as exc:
            raise ValueError(f"Caminho inseguro para job de trabalhador: {relative_path}") from exc
        write_json(
            path,
            {
                "schema_version": "1.1",
                "project_id": queue["project_id"],
                "batch_id": queue["batch_id"],
                "worker_id": worker_id,
                "role": "image_worker",
                "context_policy": "compact_only",
                "fork_turns": "none",
                "recommended_model": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "generation_retry_policy": imagegen_retry_policy(),
                "prompt_hash_algorithm": "sha256_canonical_json_utf8_v1",
                "instructions": [
                    "Leia somente este arquivo; não leia o histórico da conversa ou manifestos.",
                    (
                        "Quando common_prompt_prefix existir, reconstrua cada prompt exatamente "
                        "como "
                        "common_prompt_prefix + jobs[].prompt_delta."
                        if common_prompt_prefix is not None
                        else "Use jobs[].prompt integralmente, sem resumir ou reescrever."
                    ),
                    (
                        "Confira prompt_hash com SHA-256 dos bytes UTF-8 da serializacao JSON "
                        "canonica do prompt completo: ensure_ascii=false, sem espacos e incluindo "
                        "as aspas externas da string JSON. Nao calcule o hash diretamente sobre "
                        "os bytes do texto."
                    ),
                    *imagegen_retry_instructions(asset_label="job de imagem"),
                    (
                        "Após uma geração bem-sucedida, use exclusivamente o campo savedPath "
                        "retornado pelo Imagegen. Nunca trate result/base64, revisedPrompt ou "
                        "output_hint como caminho de arquivo."
                    ),
                    (
                        "Execute jobs[].materialize_command substituindo "
                        "<IMAGEGEN_SAVED_PATH> pelo savedPath absoluto entre aspas. O comando "
                        "valida e copia o PNG atomicamente ao staging_path declarado."
                    ),
                    (
                        "Se a materialização falhar, reporte job_id e savedPath integral ao "
                        "coordenador e não regenere a imagem."
                    ),
                    "Não importe, aprove ou modifique o estado compartilhado do projeto.",
                    "Processe o lote em uma única execução retomável sempre que possível.",
                ],
                "imagegen_result_contract": {
                    "source_field": "savedPath",
                    "forbidden_path_fields": ["result", "revisedPrompt", "output_hint"],
                    "materialization_mode": "stage_generated_cli_v1",
                },
                "common_prompt_prefix": common_prompt_prefix,
                "jobs": compacted_jobs,
            },
        )
        artifacts.append(relative_path)
    return artifacts


def build_codex_queue(
    layout: ProjectLayout,
    *,
    scene_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Create the resumable image hand-off for the built-in Codex image generator."""
    project = layout.load_manifest()
    style = _resolved_style(layout)
    scenes = read_model(layout.scenes, SceneManifest)
    standard_marker = f"Visual standard: {project.config.images.visual_standard_id}"
    if project.config.images.enforce_visual_standard and any(
        standard_marker not in scene.prompt.assembled_prompt for scene in scenes.scenes
    ):
        raise RuntimeError(
            "As cenas foram construídas antes do padrão visual atual. Execute build-scenes "
            "novamente antes de preparar a fila de imagens."
        )
    manifest = build_image_manifest(layout, scene_id=scene_id, force=force)
    selected = _selected_ids(scenes, scene_id)
    extension = project.config.images.extension
    production = project.config.production
    queue_items: list[dict[str, Any]] = []
    pending_items = [
        item
        for item in manifest.items
        if item.scene_id in selected
        and item.source_mode == "generated"
        and (force or not _active_version_is_current(layout, item))
    ]
    growth_worker_reserved = (
        project.config.youtube_growth.enabled
        and project.config.youtube_growth.use_specialist_subagent
        and production.use_subagents
    )
    image_worker_limit = (
        min(production.image_workers, 2) if growth_worker_reserved else production.image_workers
    )
    worker_count = (
        min(image_worker_limit, len(pending_items))
        if pending_items and production.use_subagents
        else 1
        if pending_items
        else 0
    )
    for index, item in enumerate(pending_items):
        version, destination = _next_version(layout, item, extension)
        staging = _staging_path(layout, item.scene_id, version, extension)
        queue_entry: dict[str, Any] = {
            "job_id": f"{item.scene_id}-v{version:03d}",
            "image_id": item.image_id,
            "scene_id": item.scene_id,
            "version": version,
            "prompt": item.prompt,
            "prompt_hash": item.prompt_hash,
            "expected_path": relative_posix(destination, layout.root),
            "staging_path": relative_posix(staging, layout.root),
            "worker_id": (
                f"image-worker-{(index % worker_count) + 1}"
                if production.use_subagents and worker_count
                else "main"
                if worker_count
                else None
            ),
            "status": StageStatus.WAITING_EXTERNAL.value,
            "materialize_command": (
                f'uv run pipeline-videos images stage-generated "{project.project_id}" '
                f'--job-id "{item.scene_id}-v{version:03d}" '
                '--source "<IMAGEGEN_SAVED_PATH>"'
            ),
        }
        queue_items.append(queue_entry)

    generated_at = utc_now()
    source_manifest_hash = sha256_value(_scene_prompt_payload(layout, scenes))
    batch_id = sha256_value(
        {
            "project_id": project.project_id,
            "source_manifest_hash": source_manifest_hash,
            "jobs": [
                (entry["job_id"], entry["prompt_hash"], entry["expected_path"])
                for entry in queue_items
            ],
        }
    )[:16]
    payload: dict[str, Any] = {
        "schema_version": CODEX_QUEUE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "project_id": project.project_id,
        "batch_id": batch_id,
        "source_manifest_hash": source_manifest_hash,
        "provider": "codex-built-in-imagegen",
        "fixed_profile_assets": [
            {
                "scene_id": item.scene_id,
                "source_path": item.source_path,
                "active_path": (
                    _version(item, item.active_version).path
                    if _version(item, item.active_version) is not None
                    else None
                ),
                "role": "spoken_reference",
            }
            for item in manifest.items
            if item.source_mode == "fixed_profile_asset_v1"
        ],
        "visual_standard": {
            "id": style.visual_standard_id,
            "style_profile": relative_posix(layout.style_profile, layout.root),
            "reference_note": style.reference_note,
            "enforced": project.config.images.enforce_visual_standard,
        },
        "generation_retry_policy": imagegen_retry_policy(),
        "orchestration": {
            "mode": "subagents" if production.use_subagents else "single_agent",
            "worker_count": worker_count,
            "single_writer": True,
            "reasoning_effort": "low" if production.use_subagents else "medium",
            "recommended_model": "gpt-5.6-sol",
            "context_policy": production.worker_context_policy,
            "fork_turns": "none" if production.worker_context_policy == "compact_only" else "all",
            "growth_worker_reserved": growth_worker_reserved,
            "worker_job_files": (
                _worker_job_paths(layout, queue_items) if production.use_subagents else []
            ),
        },
        "status": (
            StageStatus.WAITING_EXTERNAL.value if queue_items else StageStatus.COMPLETED.value
        ),
        "instructions": [
            (
                "Dividir os itens por worker_id e gerar os grupos em paralelo."
                if production.use_subagents
                else "Gerar os itens sequencialmente no agente principal persistente."
            ),
            "Quando growth_worker_reserved for true, manter uma vaga para "
            "youtube_growth_specialist.",
            "Gerar uma imagem por item com a ferramenta imagegen embutida do Codex.",
            *imagegen_retry_instructions(asset_label="item de imagem"),
            (
                f"Preservar o visual_standard {style.visual_standard_id} em todas as cenas: "
                f"{style.reference_note or style.medium}."
            ),
            (
                "Cada arquivo deve mostrar uma única composição contínua, um único momento, "
                "um único ambiente e uma ação principal. Não gerar colagem, storyboard, "
                "díptico, tríptico, tela dividida, cenas empilhadas, faixa branca ou divisor."
            ),
            (
                "Aplicar literalmente o estilo, os princípios e as restrições negativas já "
                "incluídos em cada prompt; não substituir o padrão por outro meio visual."
            ),
            "Cada trabalhador salva somente no staging_path relativo à raiz do projeto.",
            (
                "Para materializar, use exclusivamente imagegen.savedPath no "
                "materialize_command do item; nunca passe result/base64, revisedPrompt ou "
                "output_hint como caminho."
            ),
            (
                "Se a cópia falhar, preserve e reporte o savedPath ao agente principal; não "
                "gere outra imagem para corrigir um erro de materialização."
            ),
            "Trabalhadores não importam, não aprovam e não modificam JSONs compartilhados.",
            "O agente principal executa images import-batch após todos terminarem.",
        ],
        "items": queue_items,
    }
    input_hash = sha256_value(_queue_core(payload))

    previous_queue: dict[str, Any] | None = None
    if layout.image_queue.is_file():
        raw = read_json(layout.image_queue)
        if isinstance(raw, dict):
            previous_queue = raw
            if not force and _queue_core(previous_queue) == _queue_core(payload):
                record = layout.load_manifest().stages["images"]
                expected_status = (
                    StageStatus.WAITING_EXTERNAL if queue_items else StageStatus.COMPLETED
                )
                if record.status == expected_status and record.input_hash == input_hash:
                    _write_worker_job_files(layout, previous_queue)
                    return previous_queue

    provider = "codex"
    start_stage(layout, "images", input_hash, provider=provider)
    try:
        before_manifest = sha256_value(manifest)
        before_scenes = sha256_value(scenes)
        queued_ids = {entry["scene_id"] for entry in queue_items}
        for item in manifest.items:
            if item.scene_id in queued_ids:
                item.status = StageStatus.WAITING_EXTERNAL
                item.last_error = None
        for scene in scenes.scenes:
            if scene.scene_id in queued_ids:
                scene.generation_status = StageStatus.WAITING_EXTERNAL

        if sha256_value(manifest) != before_manifest:
            manifest.generated_at = utc_now()
            write_json(layout.images, manifest)
        _write_scenes_if_changed(layout, scenes, before_scenes)
        write_json(layout.image_queue, payload)
        extra_artifacts = _write_worker_job_files(layout, payload)
        _finish_images_stage(
            layout,
            manifest,
            input_hash,
            provider=provider,
            waiting_external=bool(queue_items),
            waiting_note=None,
            extra_artifacts=extra_artifacts,
        )
        return payload
    except Exception as exc:
        fail_stage(layout, "images", exc)
        raise


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    if os.name == "nt":
        names.append(str(Path("C:/Windows/Fonts") / ("arialbd.ttf" if bold else "arial.ttf")))
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fixture_colors(palette: Palette) -> list[str]:
    colors = [entry.hex for entry in palette.colors]
    return colors or ["#14213D", "#FCA311", "#E5E5E5", "#2A9D8F"]


def _save_fixture(
    destination: Path,
    *,
    width: int,
    height: int,
    scene_id: str,
    order: int,
    colors: list[str],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    background = colors[order % len(colors)]
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    sky = colors[(order + 1) % len(colors)]
    ground = colors[(order + 2) % len(colors)]
    accent = colors[(order + 3) % len(colors)]
    draw.ellipse(
        (int(width * 0.67), int(height * 0.10), int(width * 0.86), int(height * 0.36)),
        fill=accent,
    )
    draw.polygon(
        [
            (0, int(height * 0.56)),
            (int(width * 0.28), int(height * 0.36)),
            (int(width * 0.55), int(height * 0.59)),
            (int(width * 0.78), int(height * 0.42)),
            (width, int(height * 0.58)),
            (width, height),
            (0, height),
        ],
        fill=sky,
    )
    draw.polygon(
        [
            (0, int(height * 0.73)),
            (int(width * 0.32), int(height * 0.61)),
            (int(width * 0.64), int(height * 0.77)),
            (width, int(height * 0.65)),
            (width, height),
            (0, height),
        ],
        fill=ground,
    )

    person_x = int(width * (0.31 if order % 2 else 0.43))
    head = max(8, int(min(width, height) * 0.035))
    silhouette = "#17202A"
    draw.ellipse(
        (person_x - head, int(height * 0.48) - head * 2, person_x + head, int(height * 0.48)),
        fill=silhouette,
    )
    draw.polygon(
        [
            (person_x, int(height * 0.47)),
            (person_x - head * 2, int(height * 0.72)),
            (person_x + head * 2, int(height * 0.72)),
        ],
        fill=silhouette,
    )

    banner_height = max(32, int(height * 0.13))
    draw.rectangle((0, 0, width, banner_height), fill="#111111")
    title_font = _font(max(16, int(banner_height * 0.44)), bold=True)
    detail_font = _font(max(12, int(banner_height * 0.25)))
    draw.text(
        (int(width * 0.035), int(banner_height * 0.12)), "FIXTURE", fill="#FFFFFF", font=title_font
    )
    draw.text(
        (int(width * 0.035), int(banner_height * 0.63)),
        f"{scene_id} | asset local de teste",
        fill="#D8D8D8",
        font=detail_font,
    )

    temp = destination.with_suffix(destination.suffix + ".tmp")
    image.save(temp, format="PNG", optimize=True)
    os.replace(temp, destination)


def generate_fixture_images(
    layout: ProjectLayout,
    *,
    scene_id: str | None = None,
    force: bool = False,
) -> ImageManifest:
    manifest = build_image_manifest(layout, scene_id=scene_id, force=force)
    scenes = read_model(layout.scenes, SceneManifest)
    selected = _selected_ids(scenes, scene_id)
    project = layout.load_manifest()
    image_config = project.config.images
    palette = read_model(layout.palette, Palette)
    input_hash = sha256_value(
        {
            "provider": "fixture",
            "scene_id": scene_id,
            "width": image_config.width,
            "height": image_config.height,
            "items": [(item.image_id, item.prompt_hash) for item in manifest.items],
        }
    )
    if stage_is_current(layout, "images", input_hash, force=force):
        return manifest

    start_stage(layout, "images", input_hash, provider="fixture")
    try:
        colors = _fixture_colors(palette)
        scene_order = {scene.scene_id: scene.order for scene in scenes.scenes}
        before_scenes = sha256_value(scenes)
        changed = False
        for item in manifest.items:
            if item.scene_id not in selected:
                continue
            if not force and _active_version_is_current(layout, item):
                continue
            version, destination = _next_version(layout, item, image_config.extension)
            _save_fixture(
                destination,
                width=image_config.width,
                height=image_config.height,
                scene_id=item.scene_id,
                order=scene_order[item.scene_id],
                colors=colors,
            )
            item.versions.append(
                ImageVersion(
                    version=version,
                    path=relative_posix(destination, layout.root),
                    provider="fixture",
                    prompt_hash=item.prompt_hash,
                    created_at=utc_now(),
                    metadata={
                        "fixture": True,
                        "label": "FIXTURE",
                        "width": image_config.width,
                        "height": image_config.height,
                        "sha256": sha256_file(destination),
                    },
                )
            )
            item.active_version = version
            item.status = StageStatus.COMPLETED
            item.last_error = None
            scene = next(
                candidate for candidate in scenes.scenes if candidate.scene_id == item.scene_id
            )
            scene.generation_status = StageStatus.COMPLETED
            changed = True

        if changed:
            manifest.generated_at = utc_now()
            write_json(layout.images, manifest)
        _write_scenes_if_changed(layout, scenes, before_scenes)
        _finish_images_stage(
            layout,
            manifest,
            input_hash,
            provider="fixture",
            waiting_external=False,
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "images", exc)
        raise


def _horizontal_separator_band(image: Image.Image) -> tuple[float, float] | None:
    """Detecta uma faixa clara que separa painéis sem fazer análise semântica da arte."""
    rgb = image.convert("RGB")
    sample_height = 192
    sample_width = max(64, round(rgb.width / rgb.height * sample_height))
    sample = rgb.resize((sample_width, sample_height), Image.Resampling.BILINEAR)
    gray = sample.convert("L")
    saturation = sample.convert("HSV").getchannel("S")
    qualifying: list[bool] = []
    means: list[float] = []
    for row in range(sample_height):
        box = (0, row, sample_width, row + 1)
        gray_row = gray.crop(box)
        saturation_row = saturation.crop(box)
        gray_stats = ImageStat.Stat(gray_row)
        mean = gray_stats.mean[0]
        means.append(mean)
        light_neutral_fraction = (
            sum(
                1
                for lightness, colorfulness in zip(
                    gray_row.getdata(), saturation_row.getdata(), strict=True
                )
                if lightness >= 205 and colorfulness <= 55
            )
            / sample_width
        )
        qualifying.append(light_neutral_fraction >= 0.75)

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, matches in enumerate([*qualifying, False]):
        if matches and start is None:
            start = index
        elif not matches and start is not None:
            runs.append((start, index))
            start = None

    minimum_height = round(sample_height * 0.055)
    maximum_height = round(sample_height * 0.35)
    margin = max(3, round(sample_height * 0.025))
    for start, end in runs:
        if not minimum_height <= end - start <= maximum_height:
            continue
        if start < sample_height * 0.12 or end > sample_height * 0.88:
            continue
        above = means[max(0, start - margin) : start]
        below = means[end : min(sample_height, end + margin)]
        if not above or not below:
            continue
        band_mean = sum(means[start:end]) / (end - start)
        if band_mean - (sum(above) / len(above)) < 28:
            continue
        if band_mean - (sum(below) / len(below)) < 28:
            continue
        return start / sample_height, end / sample_height
    return None


def _save_imported_png(
    source: Path,
    destination: Path,
    *,
    reject_panel_like: bool,
) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            opened.load()
            has_alpha = "A" in opened.getbands()
            converted = opened.convert("RGBA" if has_alpha else "RGB")
    except Exception as exc:
        raise ValueError(f"Arquivo de imagem inválido: {source}") from exc
    separator = _horizontal_separator_band(converted) if reject_panel_like else None
    if separator is not None:
        start, end = separator
        raise ValueError(
            "A imagem parece conter painéis separados por uma faixa horizontal clara "
            f"({start:.0%}–{end:.0%} da altura). Gere uma única composição contínua."
        )
    temp = destination.with_suffix(destination.suffix + ".tmp")
    converted.save(temp, format="PNG", optimize=True)
    os.replace(temp, destination)
    return converted.size


def _update_queue_after_import(
    layout: ProjectLayout,
    scene_id: str,
    version: int,
    actual_path: str,
) -> None:
    if not layout.image_queue.is_file():
        return
    queue = read_json(layout.image_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        return
    changed = False
    for entry in queue["items"]:
        if not isinstance(entry, dict) or entry.get("scene_id") != scene_id:
            continue
        if entry.get("version") != version and entry.get("expected_path") != actual_path:
            continue
        entry["status"] = StageStatus.COMPLETED.value
        entry["actual_path"] = actual_path
        entry["imported_at"] = utc_now()
        changed = True
    if changed:
        pending = any(
            isinstance(entry, dict) and entry.get("status") == StageStatus.WAITING_EXTERNAL.value
            for entry in queue["items"]
        )
        queue["status"] = (
            StageStatus.WAITING_EXTERNAL.value if pending else StageStatus.COMPLETED.value
        )
        write_json(layout.image_queue, queue)


def _queued_destination(
    layout: ProjectLayout,
    item: ImageItem,
) -> tuple[int, Path] | None:
    """Return the unclaimed version reserved by the Codex queue, if present."""
    if not layout.image_queue.is_file():
        return None
    queue = read_json(layout.image_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        return None
    claimed = {candidate.version for candidate in item.versions}
    for entry in queue["items"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("scene_id") != item.scene_id or entry.get("prompt_hash") != item.prompt_hash:
            continue
        version = entry.get("version")
        expected_path = entry.get("expected_path")
        if not isinstance(version, int) or version in claimed or not isinstance(expected_path, str):
            continue
        destination = (layout.root / expected_path).resolve()
        try:
            destination.relative_to(layout.root.resolve())
        except ValueError as exc:
            raise ValueError(f"Destino inseguro na fila do Codex: {expected_path}") from exc
        return version, destination
    return None


def import_external_image(
    layout: ProjectLayout,
    scene_id: str,
    source: Path,
    *,
    provider: str = "codex",
    force: bool = False,
) -> ImageManifest:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Imagem não encontrada: {source}")

    manifest = build_image_manifest(layout, scene_id=scene_id)
    item = next((candidate for candidate in manifest.items if candidate.scene_id == scene_id), None)
    if item is None:
        raise ValueError(f"Cena desconhecida: {scene_id}")
    source_hash = sha256_file(source)

    for existing in item.versions:
        existing_path = layout.root / existing.path
        if (
            source_hash in {existing.metadata.get("source_hash"), existing.metadata.get("sha256")}
            and existing.prompt_hash == item.prompt_hash
            and existing_path.is_file()
        ):
            before_manifest = sha256_value(manifest)
            item.active_version = existing.version
            item.status = StageStatus.COMPLETED
            if sha256_value(manifest) != before_manifest:
                write_json(layout.images, manifest)
            return manifest

    if item.approved_version is not None and not force:
        raise RuntimeError(
            f"A cena {scene_id} já possui a versão v{item.approved_version:03d} aprovada; "
            "use force para importar uma nova candidata sem sobrescrevê-la."
        )

    project = layout.load_manifest()
    input_hash = sha256_value(
        {
            "provider": provider,
            "scene_id": scene_id,
            "source_hash": source_hash,
            "prompt_hash": item.prompt_hash,
        }
    )
    start_stage(layout, "images", input_hash, provider=provider)
    try:
        reservation = _queued_destination(layout, item)
        if reservation is None:
            version, destination = _next_version(layout, item, project.config.images.extension)
        else:
            version, destination = reservation
            if (
                destination.is_file()
                and destination != source
                and sha256_file(destination) != source_hash
            ):
                version, destination = _next_version(
                    layout,
                    item,
                    project.config.images.extension,
                )
        width, height = _save_imported_png(
            source,
            destination,
            reject_panel_like=project.config.images.reject_panel_like_images,
        )
        stored_path = relative_posix(destination, layout.root)
        item.versions.append(
            ImageVersion(
                version=version,
                path=stored_path,
                provider=provider,
                prompt_hash=item.prompt_hash,
                created_at=utc_now(),
                metadata={
                    "source_name": source.name,
                    "source_hash": source_hash,
                    "width": width,
                    "height": height,
                    "sha256": sha256_file(destination),
                },
            )
        )
        item.active_version = version
        item.status = StageStatus.COMPLETED
        item.last_error = None
        manifest.generated_at = utc_now()
        write_json(layout.images, manifest)

        scenes = read_model(layout.scenes, SceneManifest)
        before_scenes = sha256_value(scenes)
        scene = next(candidate for candidate in scenes.scenes if candidate.scene_id == scene_id)
        scene.generation_status = StageStatus.COMPLETED
        _write_scenes_if_changed(layout, scenes, before_scenes)
        _update_queue_after_import(layout, scene_id, version, stored_path)
        layout.append_event(
            {
                "event": "image_imported",
                "scene_id": scene_id,
                "version": version,
                "path": stored_path,
                "provider": provider,
            }
        )
        pending_external = any(
            not _active_version_is_current(layout, candidate) for candidate in manifest.items
        )
        _finish_images_stage(
            layout,
            manifest,
            input_hash,
            provider=provider,
            waiting_external=pending_external,
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "images", exc)
        raise


def import_staged_images(
    layout: ProjectLayout,
    *,
    force: bool = False,
) -> ImageManifest:
    """Import staged worker results serially so only the root agent mutates shared state."""
    if not layout.image_queue.is_file():
        raise FileNotFoundError(f"Fila do Codex não encontrada: {layout.image_queue}")
    queue = read_json(layout.image_queue)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise ValueError("Fila do Codex inválida: items ausente ou não é uma lista.")

    project = layout.load_manifest()
    manifest = build_image_manifest(layout)
    items_by_scene = {item.scene_id: item for item in manifest.items}
    staged: list[tuple[str, Path, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for entry in queue["items"]:
        if not isinstance(entry, dict) or entry.get("status") == StageStatus.COMPLETED.value:
            continue
        scene_id = entry.get("scene_id")
        staging_path = entry.get("staging_path")
        prompt_hash = entry.get("prompt_hash")
        if not isinstance(scene_id, str) or not isinstance(staging_path, str):
            raise ValueError("Item da fila sem scene_id ou staging_path válido.")
        if scene_id in seen:
            raise ValueError(f"A fila contém mais de um job pendente para {scene_id}.")
        seen.add(scene_id)
        item = items_by_scene.get(scene_id)
        if item is None or item.prompt_hash != prompt_hash:
            raise RuntimeError(f"O job de {scene_id} pertence a um prompt antigo.")
        if item.approved_version is not None and not force:
            raise RuntimeError(
                f"A cena {scene_id} já possui uma versão aprovada; use force para nova candidata."
            )
        source = (layout.root / staging_path).resolve()
        try:
            source.relative_to(layout.image_staging.resolve())
        except ValueError as exc:
            raise ValueError(f"Caminho de staging inseguro: {staging_path}") from exc
        if not source.is_file():
            continue
        try:
            with Image.open(source) as opened:
                opened.load()
                separator = (
                    _horizontal_separator_band(opened)
                    if project.config.images.reject_panel_like_images
                    else None
                )
        except Exception as exc:
            raise ValueError(f"Arquivo de imagem inválido: {source}") from exc
        if separator is not None:
            start, end = separator
            raise ValueError(
                "A imagem parece conter painéis separados por uma faixa horizontal clara "
                f"({start:.0%}–{end:.0%} da altura). Gere uma única composição contínua."
            )
        staged.append((scene_id, source, sha256_file(source), entry))

    if not staged:
        if all(_active_version_is_current(layout, item) for item in manifest.items):
            stage = layout.load_manifest().stages["images"]
            if stage.status != StageStatus.COMPLETED:
                _finish_images_stage(
                    layout,
                    manifest,
                    stage.input_hash or sha256_value(_queue_core(queue)),
                    provider="codex",
                    waiting_external=False,
                    usage={"batch_size": 0, "batch_import_ms": 0},
                )
            return manifest
        raise FileNotFoundError("Nenhuma imagem pronta foi encontrada na pasta de staging.")

    queue_input_hash = sha256_value(_queue_core(queue))
    stage = layout.load_manifest().stages["images"]
    if stage.status == StageStatus.WAITING_EXTERNAL and stage.input_hash != queue_input_hash:
        raise RuntimeError("A fila de imagens não corresponde à execução ativa do projeto.")

    scenes = read_model(layout.scenes, SceneManifest)
    scenes_by_id = {scene.scene_id: scene for scene in scenes.scenes}
    imported_items: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    start_stage(layout, "images", queue_input_hash, provider="codex")
    try:
        for scene_id, source, source_hash, queue_entry in staged:
            item = items_by_scene[scene_id]
            existing = next(
                (
                    version
                    for version in item.versions
                    if source_hash
                    in {version.metadata.get("source_hash"), version.metadata.get("sha256")}
                    and version.prompt_hash == item.prompt_hash
                    and (layout.root / version.path).is_file()
                ),
                None,
            )
            if existing is None:
                version = queue_entry.get("version")
                expected_path = queue_entry.get("expected_path")
                if not isinstance(version, int) or not isinstance(expected_path, str):
                    raise ValueError(f"Reserva inválida na fila para {scene_id}.")
                destination = (layout.root / expected_path).resolve()
                try:
                    destination.relative_to(layout.root.resolve())
                except ValueError as exc:
                    raise ValueError(f"Destino inseguro na fila do Codex: {expected_path}") from exc
                claimed_versions = {candidate.version for candidate in item.versions}
                if version in claimed_versions or (
                    destination.is_file()
                    and destination != source
                    and sha256_file(destination) != source_hash
                ):
                    version, destination = _next_version(
                        layout,
                        item,
                        project.config.images.extension,
                    )
                width, height = _save_imported_png(
                    source,
                    destination,
                    reject_panel_like=False,
                )
                stored_path = relative_posix(destination, layout.root)
                existing = ImageVersion(
                    version=version,
                    path=stored_path,
                    provider="codex",
                    prompt_hash=item.prompt_hash,
                    created_at=utc_now(),
                    metadata={
                        "source_name": source.name,
                        "source_hash": source_hash,
                        "width": width,
                        "height": height,
                        "sha256": sha256_file(destination),
                    },
                )
                item.versions.append(existing)

            item.active_version = existing.version
            item.status = StageStatus.COMPLETED
            item.last_error = None
            scenes_by_id[scene_id].generation_status = StageStatus.COMPLETED
            queue_entry["status"] = StageStatus.COMPLETED.value
            queue_entry["actual_path"] = existing.path
            queue_entry["imported_at"] = utc_now()
            imported_items.append(
                {
                    "scene_id": scene_id,
                    "version": existing.version,
                    "path": existing.path,
                }
            )

        manifest.generated_at = utc_now()
        queue["status"] = (
            StageStatus.WAITING_EXTERNAL.value
            if any(
                isinstance(entry, dict)
                and entry.get("status") == StageStatus.WAITING_EXTERNAL.value
                for entry in queue["items"]
            )
            else StageStatus.COMPLETED.value
        )
        write_json(layout.images, manifest)
        write_json(layout.scenes, scenes)
        write_json(layout.image_queue, queue)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        pending = queue["status"] == StageStatus.WAITING_EXTERNAL.value
        _finish_images_stage(
            layout,
            manifest,
            queue_input_hash,
            provider="codex",
            waiting_external=pending,
            usage={"batch_size": len(imported_items), "batch_import_ms": elapsed_ms},
        )
        layout.append_event(
            {
                "event": "images_batch_imported",
                "batch_id": queue.get("batch_id"),
                "count": len(imported_items),
                "elapsed_ms": elapsed_ms,
                "items": imported_items,
            }
        )
        return manifest
    except Exception as exc:
        fail_stage(layout, "images", exc)
        raise


def approve_image(
    layout: ProjectLayout,
    scene_id: str,
    *,
    version: int | None = None,
    decided_by: str = "user",
    note: str | None = None,
) -> ImageManifest:
    manifest = read_model(layout.images, ImageManifest)
    item = next((candidate for candidate in manifest.items if candidate.scene_id == scene_id), None)
    if item is None:
        raise ValueError(f"Cena desconhecida: {scene_id}")
    previous_approved_version = item.approved_version
    chosen_version = version if version is not None else item.active_version
    chosen = _version(item, chosen_version)
    if chosen is None:
        raise ValueError(f"Versão inexistente para {scene_id}: {chosen_version}")
    if not (layout.root / chosen.path).is_file():
        raise FileNotFoundError(f"Artefato da versão não encontrado: {chosen.path}")

    if item.approved_version is not None and item.approved_version != chosen.version:
        previous = _version(item, item.approved_version)
        if previous is not None:
            previous.metadata = {**previous.metadata, "superseded_by": chosen.version}
    chosen.status = "approved"
    chosen.metadata = {
        **chosen.metadata,
        "approved_at": utc_now(),
        "approved_by": decided_by,
        "approval_note": note,
    }
    item.approved_version = chosen.version
    item.active_version = chosen.version
    item.status = StageStatus.COMPLETED
    manifest.generated_at = utc_now()
    write_json(layout.images, manifest)

    scenes = read_model(layout.scenes, SceneManifest)
    before_scenes = sha256_value(scenes)
    scene = next(candidate for candidate in scenes.scenes if candidate.scene_id == scene_id)
    scene.approval = ApprovalStatus.APPROVED
    scene.generation_status = StageStatus.COMPLETED
    _write_scenes_if_changed(layout, scenes, before_scenes)
    layout.append_event(
        {
            "event": "image_approved",
            "scene_id": scene_id,
            "version": chosen.version,
            "by": decided_by,
            "note": note,
        }
    )

    if previous_approved_version != chosen.version:
        invalidate(
            layout,
            stages=("render", "validation"),
            approvals=("final_render",),
            note=f"A versão aprovada de {scene_id} foi atualizada.",
        )

    if all(candidate.approved_version is not None for candidate in manifest.items):
        project = layout.load_manifest()
        if project.approvals["images"].status != ApprovalStatus.APPROVED:
            set_approval(
                layout,
                "images",
                ApprovalStatus.APPROVED,
                decided_by=decided_by,
                note=note,
            )
    return manifest


def reuse_approved_images(
    layout: ProjectLayout,
    *,
    decided_by: str = "user",
    note: str | None = None,
) -> ImageManifest:
    """Attach previously approved image versions to a revised scene manifest explicitly."""

    scenes = read_model(layout.scenes, SceneManifest)
    manifest = build_image_manifest(layout)
    items_by_id = {item.image_id: item for item in manifest.items}
    artifacts = [relative_posix(layout.images, layout.root)]
    selected: list[dict[str, Any]] = []
    for scene in sorted(scenes.scenes, key=lambda entry: entry.order):
        item = items_by_id.get(scene.image_id)
        if item is None:
            raise ValueError(
                f"A cena {scene.scene_id} referencia uma imagem sem histórico: {scene.image_id}."
            )
        approved = _version(item, item.approved_version)
        if approved is None or approved.status != "approved":
            raise RuntimeError(f"A imagem {item.image_id} não possui versão aprovada para reuso.")
        approved_path = layout.root / approved.path
        if not approved_path.is_file():
            raise FileNotFoundError(f"Versão aprovada ausente: {approved.path}")
        approved.metadata = {
            **approved.metadata,
            "reused_at": utc_now(),
            "reused_by": decided_by,
            "reused_for_script_version": scenes.script_version,
            "reuse_note": note,
        }
        item.active_version = approved.version
        item.status = StageStatus.COMPLETED
        item.last_error = None
        scene.approval = ApprovalStatus.APPROVED
        scene.generation_status = StageStatus.COMPLETED
        artifacts.append(approved.path)
        selected.append(
            {
                "scene_id": scene.scene_id,
                "image_id": item.image_id,
                "version": approved.version,
                "sha256": sha256_file(approved_path),
            }
        )

    manifest.generated_at = utc_now()
    write_json(layout.images, manifest)
    write_json(layout.scenes, scenes)
    input_hash = sha256_value(
        {
            "mode": "reuse-approved",
            "script_version": scenes.script_version,
            "selected": selected,
        }
    )
    complete_stage(
        layout,
        "images",
        input_hash,
        artifacts,
        provider="reused-approved-assets",
        usage={"images": len(selected)},
        note=note or "Versões previamente aprovadas foram reutilizadas deliberadamente.",
    )
    set_approval(
        layout,
        "images",
        ApprovalStatus.APPROVED,
        decided_by=decided_by,
        note=note or "Reuso explícito de imagens já aprovadas pelo usuário.",
    )
    invalidate(
        layout,
        stages=("render", "validation"),
        approvals=("final_render",),
        note="As imagens aprovadas foram vinculadas ao novo roteiro.",
    )
    layout.append_event(
        {
            "event": "approved_images_reused",
            "by": decided_by,
            "script_version": scenes.script_version,
            "selected": selected,
            "note": note,
        }
    )
    return manifest


def prepare_images(
    layout: ProjectLayout,
    *,
    scene_id: str | None = None,
    force: bool = False,
) -> ImageManifest | dict[str, Any]:
    project = layout.load_manifest()
    provider = project.config.images.provider
    if provider == "fixture":
        return generate_fixture_images(layout, scene_id=scene_id, force=force)
    if provider == "codex":
        return build_codex_queue(layout, scene_id=scene_id, force=force)
    raise ValueError(f"Provedor de imagens não suportado: {provider}")
