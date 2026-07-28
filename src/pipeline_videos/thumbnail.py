from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pipeline_videos.long_form_profile import LongFormCutoutEditPlan
from pipeline_videos.storage import ProjectLayout, read_json, read_model, write_json
from pipeline_videos.utils import relative_posix, sha256_file, sha256_value, utc_now

THUMBNAIL_SIZE = (1280, 720)
TEXT_BOX = (52, 138, 575, 590)
FONT_RELATIVE_PATH = "visual/fonts/files/Knewave-Regular.ttf"
INK = (49, 36, 36, 255)
ACCENT = (161, 61, 48, 255)
SHADOW = (45, 26, 22, 105)


def _cover(image: Image.Image) -> Image.Image:
    source = image.convert("RGB")
    source_ratio = source.width / source.height
    target_ratio = THUMBNAIL_SIZE[0] / THUMBNAIL_SIZE[1]
    if source_ratio > target_ratio:
        crop_width = round(source.height * target_ratio)
        left = (source.width - crop_width) // 2
        source = source.crop((left, 0, left + crop_width, source.height))
    else:
        crop_height = round(source.width / target_ratio)
        top = (source.height - crop_height) // 2
        source = source.crop((0, top, source.width, top + crop_height))
    return source.resize(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)


def _fitted_font(
    font_path: Path,
    text: str,
    *,
    preferred_size: int,
    maximum_width: int,
) -> ImageFont.FreeTypeFont:
    for size in range(preferred_size, 47, -2):
        font = ImageFont.truetype(font_path, size)
        left, _, right, _ = font.getbbox(text)
        if right - left <= maximum_width:
            return font
    return ImageFont.truetype(font_path, 48)


def compose_cutout_thumbnail(layout: ProjectLayout, *, force: bool = False) -> dict[str, Any]:
    """Apply the approved Knewave packaging to the dedicated clay thumbnail base."""

    if layout.thumbnail_png.is_file() and layout.thumbnail_jpg.is_file() and not force:
        return read_json(layout.thumbnail_plan)
    plan = read_model(layout.long_form_edit_plan, LongFormCutoutEditPlan)
    if plan.thumbnail is None:
        raise ValueError("O roteiro não declarou a combinação de thumbnail e título.")
    inventory = read_json(layout.long_form_asset_inventory)
    item = next(
        (
            entry
            for entry in inventory.get("items", [])
            if isinstance(entry, dict) and entry.get("asset_id") == plan.thumbnail.source_asset_id
        ),
        None,
    )
    if not item or item.get("status") != "completed" or not item.get("path"):
        raise FileNotFoundError("A base dedicada da thumbnail ainda não foi gerada.")
    source_path = (layout.root / str(item["path"])).resolve()
    font_path = (layout.root / FONT_RELATIVE_PATH).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Base da thumbnail ausente: {source_path}")
    if not font_path.is_file():
        raise FileNotFoundError(f"Fonte da thumbnail ausente: {font_path}")

    with Image.open(source_path) as source:
        canvas = _cover(source).convert("RGBA")
    overlay = Image.new("RGBA", THUMBNAIL_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = TEXT_BOX
    maximum_width = right - left
    preferred_sizes = [92, 146, 92]
    rendered: list[tuple[str, tuple[int, int, int, int], ImageFont.FreeTypeFont, int, int]] = []
    total_height = 0
    for index, raw_line in enumerate(plan.thumbnail.text_lines):
        line = raw_line.strip().upper()
        font = _fitted_font(
            font_path,
            line,
            preferred_size=preferred_sizes[min(index, len(preferred_sizes) - 1)],
            maximum_width=maximum_width,
        )
        bounds = draw.textbbox((0, 0), line, font=font)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        color = ACCENT if index == plan.thumbnail.emphasis_line else INK
        rendered.append((line, color, font, width, height))
        total_height += height
    gap = 5
    total_height += gap * max(0, len(rendered) - 1)
    y = top + max(0, ((bottom - top) - total_height) // 2)
    for line, color, font, width, height in rendered:
        x = left + (maximum_width - width) // 2
        draw.text((x + 5, y + 7), line, font=font, fill=SHADOW)
        draw.text((x, y), line, font=font, fill=color)
        y += height + gap

    final = Image.alpha_composite(canvas, overlay).convert("RGB")
    layout.thumbnail_png.parent.mkdir(parents=True, exist_ok=True)
    final.save(layout.thumbnail_png, optimize=True)
    final.save(layout.thumbnail_jpg, quality=94, optimize=True, progressive=True)
    artifact = {
        "schema_version": "biblical-clay-thumbnail-v1",
        "generated_at": utc_now(),
        "title": plan.thumbnail.title,
        "text_lines": plan.thumbnail.text_lines,
        "emphasis_line": plan.thumbnail.emphasis_line,
        "source_asset_id": plan.thumbnail.source_asset_id,
        "source_path": relative_posix(source_path, layout.root),
        "source_hash": sha256_file(source_path),
        "composition_hash": sha256_value(plan.thumbnail),
        "png": relative_posix(layout.thumbnail_png, layout.root),
        "jpg": relative_posix(layout.thumbnail_jpg, layout.root),
    }
    write_json(layout.thumbnail_plan, artifact)
    return artifact
