from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from pipeline_videos.editorial import quoted_comment_response
from pipeline_videos.models import (
    AudioManifest,
    CaptionBlock,
    CaptionManifest,
    CaptionStyle,
    SceneManifest,
    ScriptDocument,
    SourceLibrary,
    WordTiming,
)
from pipeline_videos.state import (
    complete_stage,
    fail_stage,
    invalidate,
    stage_is_current,
    start_stage,
)
from pipeline_videos.storage import ProjectLayout, read_model, write_json, write_text
from pipeline_videos.utils import (
    relative_posix,
    sha256_file,
    sha256_value,
    utc_now,
)

CAPTION_ALGORITHM_VERSION = "5"
_TERMINAL_PUNCTUATION = re.compile(r"[.!?…][\"'”’)}\]]*$")
_CLAUSE_PUNCTUATION = re.compile(r"[;:][\"'”’)}\]]*$")


class CaptionValidationError(ValueError):
    """Raised when timed words and rendered caption blocks diverge."""


def _caption_font_file(layout: ProjectLayout, style: CaptionStyle) -> Path | None:
    if not style.font_file:
        return None
    candidate = (layout.root / style.font_file).resolve()
    try:
        candidate.relative_to(layout.root.resolve())
    except ValueError as exc:
        raise ValueError(f"A fonte estÃ¡ fora da pasta portÃ¡til do projeto: {candidate}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Fonte de legenda nÃ£o encontrada: {candidate}")
    return candidate


def validate_word_timings(words: Sequence[WordTiming]) -> None:
    if not words:
        raise CaptionValidationError("O alinhamento não contém palavras.")

    previous_index: int | None = None
    previous_start = -1.0
    seen: set[int] = set()
    for word in words:
        if not word.word.strip():
            raise CaptionValidationError(f"A palavra de índice {word.index} está vazia.")
        if word.index in seen:
            raise CaptionValidationError(f"Índice de palavra duplicado: {word.index}.")
        if previous_index is not None and word.index <= previous_index:
            raise CaptionValidationError("Os índices das palavras não estão em ordem crescente.")
        if word.start < 0 or word.end <= word.start:
            raise CaptionValidationError(
                f"Timing inválido para '{word.word}' ({word.start:.3f}–{word.end:.3f})."
            )
        if word.start < previous_start:
            raise CaptionValidationError("Os timestamps das palavras não estão em ordem temporal.")
        seen.add(word.index)
        previous_index = word.index
        previous_start = word.start


def _wrap_lines(
    tokens: Sequence[str],
    *,
    max_chars: int,
    max_lines: int,
) -> list[list[str]] | None:
    lines: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for token in tokens:
        token = token.strip()
        candidate_length = len(token) if not current else current_length + 1 + len(token)
        if current and candidate_length > max_chars:
            lines.append(current)
            current = [token]
            current_length = len(token)
        else:
            current.append(token)
            current_length = candidate_length
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        return None
    return lines


def _block_from_words(
    number: int,
    words: Sequence[WordTiming],
    style: CaptionStyle,
    *,
    hold_seconds: float = 0.0,
    text_override: str | None = None,
) -> CaptionBlock:
    lines = _wrap_lines(
        [word.word for word in words],
        max_chars=style.max_chars,
        max_lines=style.max_lines,
    )
    if lines is None:
        raise CaptionValidationError("Não foi possível acomodar o bloco na área de legenda.")
    return CaptionBlock(
        block_id=f"block-{number:03d}",
        word_indexes=[word.index for word in words],
        text=text_override or "\n".join(" ".join(line) for line in lines),
        start=words[0].start,
        end=round(words[-1].end + hold_seconds, 6),
        hold_seconds=hold_seconds,
    )


def group_word_timings(
    words: Sequence[WordTiming],
    style: CaptionStyle | None = None,
    *,
    isolated_word_groups: Sequence[tuple[Sequence[int], str]] = (),
    isolated_hold_seconds: float = 0.0,
) -> list[CaptionBlock]:
    """Group timed words into short, naturally delimited caption blocks."""

    style = style or CaptionStyle()
    if style.max_words < 1 or style.max_chars < 1 or style.max_lines < 1:
        raise ValueError("Os limites de palavras, caracteres e linhas devem ser positivos.")
    validate_word_timings(words)

    blocks: list[CaptionBlock] = []
    current: list[WordTiming] = []
    isolated_by_index: dict[int, tuple[int, str]] = {}
    for group_id, (indexes, display_text) in enumerate(isolated_word_groups):
        for index in indexes:
            if index in isolated_by_index:
                raise ValueError("Uma palavra não pode pertencer a dois destaques isolados.")
            isolated_by_index[index] = (group_id, display_text)

    def flush() -> None:
        if current:
            group = isolated_by_index.get(current[0].index)
            blocks.append(
                _block_from_words(
                    len(blocks) + 1,
                    current,
                    style,
                    hold_seconds=isolated_hold_seconds if group else 0.0,
                    text_override=f"“{group[1]}”" if group else None,
                )
            )
            current.clear()

    for position, word in enumerate(words):
        if current:
            current_group = isolated_by_index.get(current[0].index)
            word_group = isolated_by_index.get(word.index)
            if current_group != word_group:
                flush()
        if current:
            pause = word.start - current[-1].end
            candidate = [*current, word]
            fits = _wrap_lines(
                [item.word for item in candidate],
                max_chars=style.max_chars,
                max_lines=style.max_lines,
            )
            if (
                pause >= style.pause_break_seconds
                or len(current) >= style.max_words
                or fits is None
            ):
                flush()

        current.append(word)
        is_last = position == len(words) - 1
        next_group = isolated_by_index.get(words[position + 1].index) if not is_last else None
        current_group = isolated_by_index.get(word.index)
        closes_sentence = bool(_TERMINAL_PUNCTUATION.search(word.word.strip()))
        closes_clause = len(current) >= 3 and bool(_CLAUSE_PUNCTUATION.search(word.word.strip()))
        if (
            is_last
            or closes_sentence
            or closes_clause
            or (current_group is not None and current_group != next_group)
        ):
            flush()

    return blocks


def validate_caption_blocks(
    words: Sequence[WordTiming],
    blocks: Sequence[CaptionBlock],
    *,
    raise_on_error: bool = True,
) -> dict[str, bool | int | str]:
    """Validate exact word coverage, order, text and timing of caption blocks."""

    validate_word_timings(words)
    expected = [word.index for word in words]
    actual = [index for block in blocks for index in block.word_indexes]
    counts = Counter(actual)
    duplicates = sorted(index for index, count in counts.items() if count > 1)
    missing = [index for index in expected if index not in counts]
    extras = [index for index in actual if index not in set(expected)]

    by_index = {word.index: word for word in words}
    text_matches = True
    timings_valid = True
    previous_end = -1.0
    for block in blocks:
        try:
            block_words = [by_index[index] for index in block.word_indexes]
        except KeyError:
            text_matches = False
            timings_valid = False
            continue
        expected_text = " ".join(word.word.strip() for word in block_words)
        rendered_tokens = re.findall(r"\w+", block.text.casefold(), flags=re.UNICODE)
        expected_tokens = re.findall(r"\w+", expected_text.casefold(), flags=re.UNICODE)
        if rendered_tokens != expected_tokens:
            text_matches = False
        if (
            not block_words
            or abs(block.start - block_words[0].start) > 0.001
            or abs(block.end - (block_words[-1].end + block.hold_seconds)) > 0.001
            or block.start < previous_end - 0.001
            or block.end <= block.start
        ):
            timings_valid = False
        previous_end = block.end

    coverage_complete = not missing and not extras and len(actual) == len(expected)
    no_duplicates = not duplicates
    order_valid = actual == expected
    valid = coverage_complete and no_duplicates and order_valid and text_matches and timings_valid
    details = []
    if missing:
        details.append("ausentes=" + ",".join(map(str, missing)))
    if duplicates:
        details.append("duplicados=" + ",".join(map(str, duplicates)))
    if extras:
        details.append("extras=" + ",".join(map(str, extras)))
    if not order_valid:
        details.append("ordem inválida")
    if not text_matches:
        details.append("texto divergente")
    if not timings_valid:
        details.append("timings inválidos")

    report: dict[str, bool | int | str] = {
        "valid": valid,
        "coverage_complete": coverage_complete,
        "no_duplicates": no_duplicates,
        "order_valid": order_valid,
        "text_matches": text_matches,
        "timings_valid": timings_valid,
        "expected_word_count": len(expected),
        "rendered_word_count": len(actual),
        "message": "; ".join(details) if details else "Cobertura integral e ordenada.",
    }
    if raise_on_error and not valid:
        raise CaptionValidationError(f"Legenda inválida: {report['message']}")
    return report


def _ass_color(value: str) -> str:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Cor inválida para ASS: {value}")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    int(value, 16)
    return f"&H00{blue.upper()}{green.upper()}{red.upper()}&"


def _ass_time_from_centiseconds(value: int) -> str:
    hours, remainder = divmod(value, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _subtitle_time(value: float, separator: str) -> str:
    milliseconds = max(0, round(value * 1_000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


def _escape_ass_token(value: str) -> str:
    return value.strip().replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _context_label_ranges(
    words: Sequence[WordTiming],
    context_labels: dict[str, str],
) -> list[tuple[str, float, float]]:
    ranges: list[tuple[str, float, float]] = []
    active_label: str | None = None
    active_start = 0.0
    active_end = 0.0
    for word in words:
        label = context_labels.get(word.scene_id)
        if label == active_label and label is not None:
            active_end = word.end
            continue
        if active_label is not None:
            ranges.append((active_label, active_start, active_end))
        active_label = label
        active_start = word.start
        active_end = word.end
    if active_label is not None:
        ranges.append((active_label, active_start, active_end))
    return ranges


def _context_label_style(style: CaptionStyle) -> str:
    font_family = style.font_family.replace(",", " ").replace("\n", " ").strip()
    font_size = max(26, round(style.font_size * 0.48))
    primary = _ass_color("#F2E9D8")
    outline = _ass_color("#101820")
    return (
        f"Style: ContextLabel,{font_family},{font_size},{primary},{primary},{outline},"
        "&H70000000&,-1,0,0,0,92,100,0,0,3,2,0,8,80,80,"
        f"{style.context_label_margin_top},1"
    )


def _display_block_words(
    block: CaptionBlock,
    words: Sequence[WordTiming],
) -> list[WordTiming]:
    if block.hold_seconds <= 0:
        return list(words)
    display_tokens = block.text.replace("\n", " ").split()
    if len(display_tokens) != len(words):
        return list(words)
    return [
        word.model_copy(update={"word": display})
        for word, display in zip(words, display_tokens, strict=True)
    ]


def _append_context_label_events(
    lines: list[str],
    words: Sequence[WordTiming],
    context_labels: dict[str, str],
) -> None:
    for label, start, end in _context_label_ranges(words, context_labels):
        lines.append(
            "Dialogue: 3,"
            f"{_ass_time_from_centiseconds(round(start * 100))},"
            f"{_ass_time_from_centiseconds(round(end * 100))},"
            f"ContextLabel,,0,0,0,,{_escape_ass_token(label.upper())}"
        )


def _active_ass_text(
    words: Sequence[WordTiming],
    active_position: int,
    style: CaptionStyle,
    italic_word_indexes: set[int] | None = None,
) -> str:
    italic_word_indexes = italic_word_indexes or set()
    display_words = [word.word.upper() if style.uppercase else word.word for word in words]
    lines = _wrap_lines(
        display_words,
        max_chars=style.max_chars,
        max_lines=style.max_lines,
    )
    if lines is None:
        raise CaptionValidationError("Bloco excede os limites configurados para a legenda ASS.")

    line_starts: set[int] = set()
    cursor = 0
    for line in lines[:-1]:
        cursor += len(line)
        line_starts.add(cursor)

    primary = _ass_color(style.primary_color)
    highlight = _ass_color(style.highlight_color)
    parts = [r"{\q2}"]
    for position, _word in enumerate(words):
        if position:
            parts.append(r"\N" if position in line_starts else " ")
        escaped = _escape_ass_token(display_words[position])
        if _word.index in italic_word_indexes:
            escaped = r"{\i1}" + escaped + r"{\i0}"
        if position == active_position:
            parts.append("{\\c" + highlight + "}" + escaped + "{\\c" + primary + "}")
        else:
            parts.append(escaped)
    return "".join(parts)


@dataclass(frozen=True)
class _WordLayout:
    word: WordTiming
    text: str
    center_x: int
    center_y: int
    card_x: int
    card_y: int
    card_width: int
    card_height: int


def _layout_font(
    style: CaptionStyle,
    font_file: Path | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if font_file is not None:
        candidates.append(str(font_file))
    family = style.font_family.casefold()
    if os.name == "nt":
        windows_fonts = Path("C:/Windows/Fonts")
        if "bahnschrift" in family:
            candidates.append(str(windows_fonts / "bahnschrift.ttf"))
        if "arial" in family:
            candidates.append(str(windows_fonts / ("arialbd.ttf" if style.bold else "arial.ttf")))
        if "impact" in family:
            candidates.append(str(windows_fonts / "impact.ttf"))
    candidates.extend(
        [
            style.font_family,
            "DejaVuSans-Bold.ttf" if style.bold else "DejaVuSans.ttf",
        ]
    )
    for candidate in candidates:
        try:
            font = ImageFont.truetype(candidate, style.font_size)
        except OSError:
            continue
        if style.bold and "bahnschrift" in family and hasattr(font, "set_variation_by_name"):
            with suppress(OSError):
                font.set_variation_by_name("SemiBold")
        return font
    return ImageFont.load_default()


def _font_width(font: ImageFont.ImageFont, text: str, scale_x: int) -> float:
    getlength = getattr(font, "getlength", None)
    raw = float(getlength(text)) if callable(getlength) else float(font.getbbox(text)[2])
    return raw * scale_x / 100


def _layout_active_card_block(
    words: Sequence[WordTiming],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    font_file: Path | None = None,
) -> list[_WordLayout]:
    font = _layout_font(style, font_file)
    display = [word.word.upper() if style.uppercase else word.word for word in words]
    token_widths = [
        _font_width(font, token, style.font_scale_x) * style.layout_width_scale for token in display
    ]
    space_width = _font_width(font, " ", style.font_scale_x) * style.layout_width_scale
    safe_width = width - style.margin_left - style.margin_right
    lines: list[list[int]] = []
    current: list[int] = []
    current_width = 0.0
    for index, token_width in enumerate(token_widths):
        candidate = token_width if not current else current_width + space_width + token_width
        line_is_full = (
            style.max_words_per_line is not None and len(current) >= style.max_words_per_line
        )
        if current and (candidate > safe_width or line_is_full):
            lines.append(current)
            current = [index]
            current_width = token_width
        else:
            current.append(index)
            current_width = candidate
    if current:
        lines.append(current)
    if len(lines) > style.max_lines:
        raise CaptionValidationError("Bloco V2 excede a largura segura da legenda.")

    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (style.font_size, 0)
    line_height = max(style.font_size, ascent + descent)
    glyph_box = font.getbbox("Ágj")
    glyph_height = max(style.font_size, glyph_box[3] - glyph_box[1])
    total_height = len(lines) * line_height + max(0, len(lines) - 1) * style.line_spacing
    block_top = (
        round((height - total_height) / 2)
        if style.vertical_position == "center"
        else height - style.margin_bottom - total_height
    )
    if block_top < 0:
        raise CaptionValidationError("Bloco V2 excede a altura segura da legenda.")

    layouts: list[_WordLayout] = []
    for line_number, indexes in enumerate(lines):
        line_width = sum(token_widths[index] for index in indexes)
        line_width += space_width * max(0, len(indexes) - 1)
        cursor_x = (width - line_width) / 2
        center_y = block_top + line_number * (line_height + style.line_spacing) + line_height / 2
        for position, index in enumerate(indexes):
            token_width = token_widths[index]
            center_x = cursor_x + token_width / 2
            card_width = round(token_width + 2 * style.highlight_card_padding_x)
            card_height = round(glyph_height + 2 * style.highlight_card_padding_y)
            layouts.append(
                _WordLayout(
                    word=words[index],
                    text=display[index],
                    center_x=round(center_x),
                    center_y=round(center_y),
                    card_x=round(center_x - card_width / 2),
                    card_y=round(center_y - card_height / 2),
                    card_width=card_width,
                    card_height=card_height,
                )
            )
            cursor_x += token_width
            if position < len(indexes) - 1:
                cursor_x += space_width
    return layouts


def _rounded_rect_ass_path(width: int, height: int, radius: int) -> str:
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        return f"m 0 0 l {width} 0 {width} {height} 0 {height}"
    control = round(radius * 0.55228475)
    return " ".join(
        [
            f"m {radius} 0",
            f"l {width - radius} 0",
            f"b {width - radius + control} 0 {width} {radius - control} {width} {radius}",
            f"l {width} {height - radius}",
            (
                f"b {width} {height - radius + control} {width - radius + control} {height} "
                f"{width - radius} {height}"
            ),
            f"l {radius} {height}",
            f"b {radius - control} {height} 0 {height - radius + control} 0 {height - radius}",
            f"l 0 {radius}",
            f"b 0 {radius - control} {radius - control} 0 {radius} 0",
        ]
    )


def render_ass_active_card_v2(
    words: Sequence[WordTiming],
    blocks: Sequence[CaptionBlock],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    font_file: Path | None = None,
    context_labels: dict[str, str] | None = None,
    italic_word_indexes: set[int] | None = None,
) -> str:
    italic_word_indexes = italic_word_indexes or set()
    validate_caption_blocks(words, blocks)
    primary = _ass_color(style.primary_color)
    active = _ass_color(style.highlight_color)
    outline = _ass_color(style.outline_color)
    card = _ass_color(style.highlight_background_color or "#D92D3A")
    font_family = style.font_family.replace(",", " ").replace("\n", " ").strip()
    bold = -1 if style.bold else 0
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding"
        ),
        (
            f"Style: BaseV2,{font_family},{style.font_size},{primary},{primary},{outline},"
            f"&H80000000&,{bold},0,0,0,{style.font_scale_x},100,0,0,1,"
            f"{style.outline_width},{style.shadow},5,0,0,0,1"
        ),
        (
            f"Style: ActiveV2,{font_family},{style.font_size},{active},{active},{active},"
            f"&H80000000&,{bold},0,0,0,{style.font_scale_x},100,0,0,1,0,0,5,0,0,0,1"
        ),
        (
            f"Style: CardV2,Arial,10,{card},{card},{card},&H00000000&,"
            "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
        ),
    ]
    if context_labels:
        lines.append(_context_label_style(style))
    lines.extend(
        [
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    by_index = {word.index: word for word in words}
    for block in blocks:
        block_words = _display_block_words(
            block,
            [by_index[index] for index in block.word_indexes],
        )
        layouts = _layout_active_card_block(
            block_words,
            style,
            width=width,
            height=height,
            font_file=font_file,
        )
        block_start = _ass_time_from_centiseconds(round(block.start * 100))
        block_end = _ass_time_from_centiseconds(round(block.end * 100))
        for item in layouts:
            escaped = _escape_ass_token(item.text)
            italic = "\\i1" if item.word.index in italic_word_indexes else ""
            active_start = _ass_time_from_centiseconds(round(item.word.start * 100))
            active_end = _ass_time_from_centiseconds(round(item.word.end * 100))
            base_start = active_start if style.word_arrival_animation else block_start
            if style.word_arrival_animation:
                duration = style.word_arrival_duration_ms
                rise = style.word_arrival_rise_px
                start_scale_x = max(1, round(style.font_scale_x * 0.75))
                position = (
                    f"{{\\an5\\move({item.center_x},{item.center_y + rise},"
                    f"{item.center_x},{item.center_y},0,{duration})"
                    f"\\fad({min(65, duration)},0)\\alpha&H68&"
                    f"\\fscx{start_scale_x}\\fscy82"
                    f"\\t(0,{duration},\\alpha&H00&\\fscx{style.font_scale_x}\\fscy100)"
                    f"{italic}}}"
                )
            else:
                position = f"{{\\an5\\pos({item.center_x},{item.center_y}){italic}}}"
            lines.append(f"Dialogue: 1,{base_start},{block_end},BaseV2,,0,0,0,,{position}{escaped}")

            path = _rounded_rect_ass_path(
                item.card_width,
                item.card_height,
                style.highlight_card_radius,
            )
            if style.word_arrival_animation:
                duration = style.word_arrival_duration_ms
                card_rise = round(style.word_arrival_rise_px * 0.7)
                drawing = (
                    f"{{\\an7\\move({item.card_x},{item.card_y + card_rise},"
                    f"{item.card_x},{item.card_y},0,{duration})"
                    f"\\fad({min(55, duration)},{min(45, duration)})"
                    f"\\p1\\1c{card}\\1a&H00&\\bord0\\shad0}}"
                    f"{path}{{\\p0}}"
                )
            else:
                drawing = (
                    f"{{\\an7\\pos({item.card_x},{item.card_y})\\p1\\1c{card}"
                    "\\1a&H00&\\bord0\\shad0}"
                    f"{path}{{\\p0}}"
                )
            lines.append(f"Dialogue: 0,{active_start},{active_end},CardV2,,0,0,0,,{drawing}")
            lines.append(
                f"Dialogue: 2,{active_start},{active_end},ActiveV2,,0,0,0,,{position}{escaped}"
            )
    if context_labels:
        _append_context_label_events(lines, words, context_labels)
    return "\n".join(lines) + "\n"


def render_ass(
    words: Sequence[WordTiming],
    blocks: Sequence[CaptionBlock],
    style: CaptionStyle | None = None,
    *,
    width: int = 1920,
    height: int = 1080,
    font_file: Path | None = None,
    context_labels: dict[str, str] | None = None,
    italic_word_indexes: set[int] | None = None,
) -> str:
    """Create ASS with one stable full-block event for each active word interval."""

    style = style or CaptionStyle()
    if style.variant == "active_card_v2":
        return render_ass_active_card_v2(
            words,
            blocks,
            style,
            width=width,
            height=height,
            font_file=font_file,
            context_labels=context_labels,
            italic_word_indexes=italic_word_indexes,
        )
    validate_caption_blocks(words, blocks)
    primary = _ass_color(style.primary_color)
    highlight = _ass_color(style.highlight_color)
    outline = _ass_color(style.outline_color)
    font_family = style.font_family.replace(",", " ").replace("\n", " ").strip()
    bold = -1 if style.bold else 0
    alignment = 5 if style.vertical_position == "center" else 2
    margin_vertical = 0 if style.vertical_position == "center" else style.margin_bottom

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{font_family},{style.font_size},{primary},{highlight},{outline},"
            f"&H00000000&,{bold},0,0,0,{style.font_scale_x},100,0,0,1,"
            f"{style.outline_width},{style.shadow},{alignment},"
            f"{style.margin_left},{style.margin_right},{margin_vertical},1"
        ),
    ]
    if context_labels:
        lines.append(_context_label_style(style))
    lines.extend(
        [
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    by_index = {word.index: word for word in words}
    for block in blocks:
        block_words = _display_block_words(
            block,
            [by_index[index] for index in block.word_indexes],
        )
        boundaries = [round(word.start * 100) for word in block_words]
        boundaries.append(round(block.end * 100))
        for position in range(1, len(boundaries)):
            boundaries[position] = max(boundaries[position], boundaries[position - 1] + 1)
        for position in range(len(block_words)):
            text = _active_ass_text(
                block_words,
                position,
                style,
                italic_word_indexes,
            )
            lines.append(
                "Dialogue: 0,"
                f"{_ass_time_from_centiseconds(boundaries[position])},"
                f"{_ass_time_from_centiseconds(boundaries[position + 1])},"
                f"Default,,0,0,0,,{text}"
            )
    if context_labels:
        _append_context_label_events(lines, words, context_labels)
    return "\n".join(lines) + "\n"


def _scene_context_labels(layout: ProjectLayout, style: CaptionStyle) -> dict[str, str]:
    if not style.context_labels_enabled:
        return {}
    script = read_model(layout.script, ScriptDocument)
    scenes = read_model(layout.scenes, SceneManifest)
    roles = {section.section_id: section.role for section in script.sections}
    return {
        scene.scene_id: (
            style.scripture_context_label
            if roles.get(scene.section_id) == "opening"
            else style.reflection_context_label
        )
        for scene in scenes.scenes
    }


def _caption_match_token(value: str) -> str:
    return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)


def _literal_bible_word_indexes(
    layout: ProjectLayout,
    words: Sequence[WordTiming],
) -> set[int]:
    """Find exact Bible quotations in narration without italicizing lead-ins or paraphrases."""

    if not layout.script.is_file() or not layout.sources.is_file():
        return set()

    script = read_model(layout.script, ScriptDocument)
    sources = read_model(layout.sources, SourceLibrary)
    candidates: set[tuple[str, ...]] = set()

    def add_candidate(value: str | None) -> None:
        if not value:
            return
        tokens = tuple(
            token for token in (_caption_match_token(item) for item in value.split()) if token
        )
        if len(tokens) >= 2:
            candidates.add(tokens)

    for section in script.sections:
        if "biblical_text" not in section.claim_classification:
            continue
        for quoted in re.findall(r'["“](.+?)["”]', section.narration):
            add_candidate(quoted)

    used_source_ids = set(script.source_ids)
    for source in sources.entries:
        if (
            source.source_id in used_source_ids
            and source.kind == "biblical_text"
            and source.verified
        ):
            add_candidate(source.excerpt)

    normalized_words = [_caption_match_token(word.word) for word in words]
    matches: set[int] = set()
    for candidate in candidates:
        length = len(candidate)
        for start in range(len(normalized_words) - length + 1):
            if tuple(normalized_words[start : start + length]) == candidate:
                matches.update(word.index for word in words[start : start + length])
    return matches


def _comment_response_groups(audio: AudioManifest) -> list[tuple[list[int], str]]:
    groups: list[tuple[list[int], str]] = []
    for segment in audio.segments:
        response = quoted_comment_response(segment.text)
        if not response:
            continue
        target = [_caption_match_token(item) for item in response.split()]
        spoken = [_caption_match_token(item.word) for item in segment.words]
        for start in range(len(spoken) - len(target) + 1):
            if spoken[start : start + len(target)] == target:
                groups.append(
                    ([item.index for item in segment.words[start : start + len(target)]], response)
                )
                break
        else:
            raise CaptionValidationError(
                "Não foi possível isolar na legenda a resposta entre aspas do convite "
                "aos comentários."
            )
    return groups


def _subtitle_block_text(
    block: CaptionBlock,
    italic_word_indexes: set[int],
) -> str:
    indexes = iter(block.word_indexes)
    parts: list[str] = []
    for piece in re.split(r"(\s+)", block.text):
        if not piece or piece.isspace():
            parts.append(piece)
            continue
        index = next(indexes)
        parts.append(f"<i>{piece}</i>" if index in italic_word_indexes else piece)
    return "".join(parts)


def render_srt(
    blocks: Sequence[CaptionBlock],
    *,
    italic_word_indexes: set[int] | None = None,
) -> str:
    italic_word_indexes = italic_word_indexes or set()
    cues = []
    for number, block in enumerate(blocks, start=1):
        cues.append(
            f"{number}\n"
            f"{_subtitle_time(block.start, ',')} --> {_subtitle_time(block.end, ',')}\n"
            f"{_subtitle_block_text(block, italic_word_indexes)}"
        )
    return "\n\n".join(cues) + "\n"


def render_vtt(
    blocks: Sequence[CaptionBlock],
    *,
    italic_word_indexes: set[int] | None = None,
) -> str:
    italic_word_indexes = italic_word_indexes or set()
    cues = ["WEBVTT"]
    for block in blocks:
        cues.append(
            f"{_subtitle_time(block.start, '.')} --> {_subtitle_time(block.end, '.')}\n"
            f"{_subtitle_block_text(block, italic_word_indexes)}"
        )
    return "\n\n".join(cues) + "\n"


def generate_captions(layout: ProjectLayout, *, force: bool = False) -> CaptionManifest:
    audio = read_model(layout.audio, AudioManifest)
    project = layout.load_manifest()
    style = project.config.captions
    render_config = project.config.render
    font_file = _caption_font_file(layout, style)
    context_labels: dict[str, str] = {}
    italic_word_indexes = _literal_bible_word_indexes(layout, audio.words)
    comment_response_groups = (
        _comment_response_groups(audio) if style.isolate_quoted_comment_response else []
    )
    input_hash = sha256_value(
        {
            "algorithm": CAPTION_ALGORITHM_VERSION,
            "words": [word.model_dump(mode="json") for word in audio.words],
            "style": style.model_dump(mode="json"),
            "font_hash": sha256_file(font_file) if font_file else None,
            "width": render_config.width,
            "height": render_config.height,
            "context_labels": context_labels,
            "italic_word_indexes": sorted(italic_word_indexes),
            "comment_response_groups": comment_response_groups,
        }
    )
    if stage_is_current(layout, "captions", input_hash, force=force):
        return read_model(layout.captions, CaptionManifest)

    invalidate(
        layout,
        stages=("render", "validation"),
        approvals=("final_render",),
        note="As legendas foram atualizadas.",
    )
    start_stage(layout, "captions", input_hash, provider="libass")
    try:
        blocks = group_word_timings(
            audio.words,
            style,
            isolated_word_groups=comment_response_groups,
            isolated_hold_seconds=style.comment_response_hold_seconds,
        )
        validation = validate_caption_blocks(audio.words, blocks)
        write_text(
            layout.ass,
            render_ass(
                audio.words,
                blocks,
                style,
                width=render_config.width,
                height=render_config.height,
                font_file=font_file,
                context_labels=context_labels,
                italic_word_indexes=italic_word_indexes,
            ),
        )
        write_text(
            layout.srt,
            render_srt(blocks, italic_word_indexes=italic_word_indexes),
        )
        write_text(
            layout.vtt,
            render_vtt(blocks, italic_word_indexes=italic_word_indexes),
        )
        caption_manifest = CaptionManifest(
            generated_at=utc_now(),
            word_count=len(audio.words),
            blocks=blocks,
            ass_path=relative_posix(layout.ass, layout.root),
            srt_path=relative_posix(layout.srt, layout.root),
            vtt_path=relative_posix(layout.vtt, layout.root),
            validation=validation,
            context_labels=context_labels,
            italic_word_indexes=sorted(italic_word_indexes),
        )
        write_json(layout.captions, caption_manifest)
        artifacts = [
            relative_posix(layout.captions, layout.root),
            caption_manifest.ass_path,
            caption_manifest.srt_path,
            caption_manifest.vtt_path,
        ]
        complete_stage(
            layout,
            "captions",
            input_hash,
            artifacts,
            provider="libass",
            usage={"words": len(audio.words), "blocks": len(blocks)},
        )
        return caption_manifest
    except Exception as exc:
        fail_stage(layout, "captions", exc)
        raise


build_captions = generate_captions
