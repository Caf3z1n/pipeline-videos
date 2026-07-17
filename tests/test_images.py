import json
from pathlib import Path

import pytest
from PIL import Image

from pipeline_videos.images import (
    approve_image,
    build_codex_queue,
    build_image_manifest,
    generate_fixture_images,
    import_external_image,
    import_staged_images,
)
from pipeline_videos.models import (
    ApprovalStatus,
    ImageManifest,
    PromptBlocks,
    Scene,
    SceneManifest,
    StageStatus,
)
from pipeline_videos.project import create_project
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json


def _create_layout(tmp_path: Path, *, scene_count: int = 2, demo: bool = False) -> ProjectLayout:
    layout = create_project(
        "video-teste",
        "Vídeo de teste",
        projects_dir=tmp_path,
        target_duration_seconds=60,
        demo=demo,
    )
    scenes = []
    for index in range(1, scene_count + 1):
        scene_id = f"scene-{index:03d}"
        assembled = (
            "Visual standard: cinematic-family-3d-v1\n"
            f"PROMPT MONTADO {index}: viajante diante de uma paisagem ampla"
        )
        scenes.append(
            Scene(
                scene_id=scene_id,
                order=index,
                section_id="section-001",
                narration=f"Narração da cena {index}.",
                estimated_duration_seconds=20,
                emotional_intent="esperança",
                environment="deserto",
                shot="plano geral",
                narrative_function="estabelecer o ambiente",
                prompt=PromptBlocks(
                    style_profile_id="cinematic-family-3d-animation",
                    palette_id="noite-azul-dourada",
                    continuity=["roupa azul consistente"],
                    scene="viajante no deserto",
                    composition="plano geral cinematográfico",
                    emotion="esperança serena",
                    negative=["sem texto", "sem fotorrealismo"],
                    assembled_prompt=assembled,
                ),
                image_id=f"image-{index:03d}",
            )
        )
    write_json(
        layout.scenes,
        SceneManifest(
            script_version=1,
            style_profile_id="cinematic-family-3d-animation",
            palette_id="noite-azul-dourada",
            total_estimated_duration_seconds=20 * scene_count,
            scenes=scenes,
        ),
    )
    return layout


def test_manifest_and_codex_queue_are_idempotent_and_can_target_one_scene(
    tmp_path: Path,
) -> None:
    layout = _create_layout(tmp_path)

    first = build_image_manifest(layout)
    manifest_bytes = layout.images.read_bytes()
    second = build_image_manifest(layout)

    assert second == first
    assert layout.images.read_bytes() == manifest_bytes
    assert [item.prompt for item in first.items] == [
        (
            "Visual standard: cinematic-family-3d-v1\n"
            "PROMPT MONTADO 1: viajante diante de uma paisagem ampla"
        ),
        (
            "Visual standard: cinematic-family-3d-v1\n"
            "PROMPT MONTADO 2: viajante diante de uma paisagem ampla"
        ),
    ]

    queue = build_codex_queue(layout, scene_id="scene-002")
    assert queue["status"] == "waiting_external"
    assert len(queue["items"]) == 1
    assert queue["items"][0]["scene_id"] == "scene-002"
    assert queue["items"][0]["prompt"] == first.items[1].prompt
    assert queue["items"][0]["expected_path"] == "images/assets/scene-002/v001.png"
    assert queue["items"][0]["staging_path"] == "images/staging/scene-002-v001.png"
    assert queue["items"][0]["worker_id"] == "image-worker-1"
    assert queue["schema_version"] == "1.6"
    retry_policy = queue["generation_retry_policy"]
    assert retry_policy["max_attempts_per_item"] == 3
    assert retry_policy["same_prompt_attempts"] == 2
    assert retry_policy["alternative_scene_attempts"] == 1
    assert "apparent_age" in retry_policy["immutable_on_alternative"]
    assert queue["visual_standard"]["id"] == "cinematic-family-3d-v1"
    assert queue["visual_standard"]["enforced"] is True
    assert any(
        "animação 3D cinematográfica" in instruction for instruction in queue["instructions"]
    )
    assert any("boneco de plástico" in instruction for instruction in queue["instructions"])
    assert any(
        "uma única composição contínua" in instruction for instruction in queue["instructions"]
    )
    assert queue["orchestration"]["mode"] == "subagents"
    assert queue["orchestration"]["worker_count"] == 1
    assert queue["orchestration"]["single_writer"] is True
    assert queue["orchestration"]["reasoning_effort"] == "low"
    assert queue["orchestration"]["recommended_model"] == "gpt-5.6-sol"
    assert queue["orchestration"]["context_policy"] == "compact_only"
    assert queue["orchestration"]["fork_turns"] == "none"
    assert queue["orchestration"]["growth_worker_reserved"] is True
    worker_file = layout.root / queue["orchestration"]["worker_job_files"]["image-worker-1"]
    assert worker_file.is_file()
    worker_job = read_json(worker_file)
    assert worker_job["context_policy"] == "compact_only"
    assert worker_job["fork_turns"] == "none"
    assert worker_job["generation_retry_policy"] == retry_policy
    assert any("exatamente o mesmo prompt" in item for item in worker_job["instructions"])
    assert [job["scene_id"] for job in worker_job["jobs"]] == ["scene-002"]
    assert layout.load_manifest().stages["images"].status == StageStatus.WAITING_EXTERNAL

    queue_bytes = layout.image_queue.read_bytes()
    attempts = layout.load_manifest().stages["images"].attempts
    repeated = build_codex_queue(layout, scene_id="scene-002")
    assert repeated == queue
    assert layout.image_queue.read_bytes() == queue_bytes
    assert layout.load_manifest().stages["images"].attempts == attempts

    saved_scenes = read_model(layout.scenes, SceneManifest)
    assert saved_scenes.scenes[0].generation_status == StageStatus.PENDING
    assert saved_scenes.scenes[1].generation_status == StageStatus.WAITING_EXTERNAL


def test_fixture_png_is_marked_idempotent_and_force_preserves_approved_version(
    tmp_path: Path,
) -> None:
    layout = _create_layout(tmp_path, demo=True)
    project = layout.load_manifest()
    project.config.images.width = 320
    project.config.images.height = 180
    layout.save_manifest(project)

    generated = generate_fixture_images(layout)
    assert all(len(item.versions) == 1 for item in generated.items)
    assert layout.load_manifest().stages["images"].status == StageStatus.COMPLETED

    first_item = generated.items[0]
    first_path = layout.root / first_item.versions[0].path
    assert first_item.versions[0].metadata["fixture"] is True
    assert first_item.versions[0].metadata["label"] == "FIXTURE"
    with Image.open(first_path) as image:
        assert image.size == (320, 180)
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (17, 17, 17)

    repeated = generate_fixture_images(layout)
    assert [len(item.versions) for item in repeated.items] == [1, 1]

    approve_image(layout, "scene-001", version=1, decided_by="teste")
    original_bytes = first_path.read_bytes()
    regenerated = generate_fixture_images(layout, scene_id="scene-001", force=True)
    changed_item = regenerated.items[0]
    untouched_item = regenerated.items[1]

    assert changed_item.active_version == 2
    assert changed_item.approved_version == 1
    assert [version.version for version in changed_item.versions] == [1, 2]
    assert changed_item.versions[0].status == "approved"
    assert first_path.read_bytes() == original_bytes
    assert (layout.root / changed_item.versions[1].path).is_file()
    assert len(untouched_item.versions) == 1


def test_import_uses_reserved_version_is_idempotent_and_never_overwrites_approved(
    tmp_path: Path,
) -> None:
    layout = _create_layout(tmp_path, scene_count=1)
    queue = build_codex_queue(layout)
    reserved = layout.root / queue["items"][0]["expected_path"]
    reserved.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (96, 54), "#AA2244").save(reserved)

    imported = import_external_image(layout, "scene-001", reserved)
    item = imported.items[0]
    assert item.active_version == 1
    assert len(item.versions) == 1
    assert item.versions[0].path == "images/assets/scene-001/v001.png"
    assert layout.load_manifest().stages["images"].status == StageStatus.COMPLETED

    repeated = import_external_image(layout, "scene-001", reserved)
    assert len(repeated.items[0].versions) == 1

    approved = approve_image(layout, "scene-001", version=1, decided_by="teste")
    assert approved.items[0].approved_version == 1
    assert layout.load_manifest().approvals["images"].status == ApprovalStatus.APPROVED
    approved_bytes = reserved.read_bytes()

    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (96, 54), "#2266AA").save(replacement)
    with pytest.raises(RuntimeError, match="possui a versão"):
        import_external_image(layout, "scene-001", replacement)

    version_two = import_external_image(layout, "scene-001", replacement, force=True)
    item = version_two.items[0]
    assert item.active_version == 2
    assert item.approved_version == 1
    assert len(item.versions) == 2
    assert reserved.read_bytes() == approved_bytes
    second_path = layout.root / item.versions[1].path
    assert second_path.is_file()
    assert second_path != reserved

    latest = approve_image(layout, "scene-001", version=2, decided_by="teste")
    assert latest.items[0].approved_version == 2
    assert latest.items[0].versions[0].status == "approved"
    assert latest.items[0].versions[0].metadata["superseded_by"] == 2
    assert latest.items[0].versions[1].status == "approved"


def test_import_rejects_bright_horizontal_separator_between_panels(tmp_path: Path) -> None:
    layout = _create_layout(tmp_path, scene_count=1)
    source = tmp_path / "panel-like.png"
    image = Image.new("RGB", (120, 200), "#17324D")
    for y in range(72, 112):
        for x in range(image.width):
            image.putpixel((x, y), (247, 244, 234))
    image.save(source)

    with pytest.raises(ValueError, match="painéis separados"):
        import_external_image(layout, "scene-001", source)


def test_batch_prevalidates_every_image_before_writing_any_asset(tmp_path: Path) -> None:
    layout = _create_layout(tmp_path, scene_count=2)
    queue = build_codex_queue(layout)
    first = layout.root / queue["items"][0]["staging_path"]
    second = layout.root / queue["items"][1]["staging_path"]
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 200), "#17324D").save(first)
    panel = Image.new("RGB", (120, 200), "#17324D")
    for y in range(72, 112):
        for x in range(panel.width):
            panel.putpixel((x, y), (247, 244, 234))
    panel.save(second)

    with pytest.raises(ValueError, match="painéis separados"):
        import_staged_images(layout)

    assert not list(layout.image_assets.rglob("*.png"))
    assert layout.load_manifest().stages["images"].status == StageStatus.WAITING_EXTERNAL


def test_unknown_scene_is_rejected(tmp_path: Path) -> None:
    layout = _create_layout(tmp_path, scene_count=1)
    with pytest.raises(ValueError, match="Cena desconhecida"):
        build_codex_queue(layout, scene_id="scene-999")

    build_image_manifest(layout)
    manifest = read_model(layout.images, ImageManifest)
    assert len(manifest.items) == 1


def test_codex_queue_rejects_scenes_from_the_previous_visual_standard(tmp_path: Path) -> None:
    layout = _create_layout(tmp_path, scene_count=1)
    scenes = read_model(layout.scenes, SceneManifest)
    scenes.scenes[0].prompt.assembled_prompt = "prompt antigo sem padrão visual"
    write_json(layout.scenes, scenes)

    with pytest.raises(RuntimeError, match="construídas antes do padrão visual atual"):
        build_codex_queue(layout)


def test_subagents_write_only_staging_and_root_imports_the_batch(tmp_path: Path) -> None:
    layout = _create_layout(tmp_path, scene_count=3)
    queue = build_codex_queue(layout)
    assert [item["worker_id"] for item in queue["items"]] == [
        "image-worker-1",
        "image-worker-2",
        "image-worker-1",
    ]
    assert queue["orchestration"]["growth_worker_reserved"] is True

    shared_before = {
        path: path.read_bytes()
        for path in (layout.manifest, layout.scenes, layout.images, layout.image_queue)
    }
    for index, entry in enumerate(queue["items"], start=1):
        staging = layout.root / entry["staging_path"]
        staging.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 160), f"#{index:02x}4466").save(staging)
    assert all(path.read_bytes() == content for path, content in shared_before.items())

    event_count = len(layout.events.read_text(encoding="utf-8").splitlines())
    imported = import_staged_images(layout)
    assert all(item.active_version == 1 for item in imported.items)
    assert all(item.approved_version is None for item in imported.items)
    assert layout.load_manifest().stages["images"].status == StageStatus.COMPLETED
    completed_queue = read_json(layout.image_queue)
    assert completed_queue["status"] == StageStatus.COMPLETED.value
    assert all(item["status"] == StageStatus.COMPLETED.value for item in completed_queue["items"])
    new_events = [
        json.loads(line)
        for line in layout.events.read_text(encoding="utf-8").splitlines()[event_count:]
    ]
    assert [event["event"] for event in new_events] == [
        "stage_started",
        "stage_completed",
        "images_batch_imported",
    ]
    stage = layout.load_manifest().stages["images"]
    assert stage.usage["batch_size"] == 3
    assert stage.usage["batch_import_ms"] >= 0

    state_before_retry = {
        path: path.read_bytes()
        for path in (layout.manifest, layout.scenes, layout.images, layout.image_queue)
    }
    assert import_staged_images(layout) == imported
    assert all(path.read_bytes() == content for path, content in state_before_retry.items())

    interrupted = layout.load_manifest()
    interrupted.stages["images"].status = StageStatus.RUNNING
    layout.save_manifest(interrupted)
    assert import_staged_images(layout) == imported
    assert layout.load_manifest().stages["images"].status == StageStatus.COMPLETED


def test_local_subagents_reserve_one_slot_for_growth_specialist(
    tmp_path: Path,
) -> None:
    layout = _create_layout(tmp_path, scene_count=4)
    project = layout.load_manifest()
    project.config.production.use_subagents = True
    project.config.production.image_workers = 3
    project.config.youtube_growth.enabled = True
    project.config.youtube_growth.use_specialist_subagent = True
    layout.save_manifest(project)

    queue = build_codex_queue(layout)

    assert queue["orchestration"]["worker_count"] == 2
    assert queue["orchestration"]["growth_worker_reserved"] is True
    assert [item["worker_id"] for item in queue["items"]] == [
        "image-worker-1",
        "image-worker-2",
        "image-worker-1",
        "image-worker-2",
    ]
