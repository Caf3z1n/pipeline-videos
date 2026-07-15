from __future__ import annotations

import re

import pytest

from pipeline_videos.captions import (
    CaptionValidationError,
    _layout_active_card_block,
    _literal_bible_word_indexes,
    generate_captions,
    group_word_timings,
    render_ass,
    render_srt,
    render_vtt,
    validate_caption_blocks,
)
from pipeline_videos.models import (
    AudioManifest,
    CaptionBlock,
    CaptionStyle,
    ProjectManifest,
    WordTiming,
)
from pipeline_videos.state import initial_approvals, initial_stage_records
from pipeline_videos.storage import ProjectLayout, write_json
from pipeline_videos.utils import utc_now


def _words(values: list[tuple[str, float, float]]) -> list[WordTiming]:
    return [
        WordTiming(index=index, word=word, start=start, end=end, scene_id="scene-001")
        for index, (word, start, end) in enumerate(values)
    ]


def test_quoted_comment_response_is_isolated_centered_and_held_during_pause() -> None:
    words = _words(
        [
            ("Escreva", 0.0, 0.3),
            ("CORAGEM", 0.3, 0.7),
            ("nos", 1.7, 1.9),
            ("comentários.", 1.9, 2.4),
        ]
    )
    style = CaptionStyle(vertical_position="center")

    blocks = group_word_timings(
        words,
        style,
        isolated_word_groups=[([1], "CORAGEM")],
        isolated_hold_seconds=1.0,
    )

    isolated = next(block for block in blocks if block.word_indexes == [1])
    assert isolated.text == "“CORAGEM”"
    assert isolated.hold_seconds == 1.0
    assert isolated.end == pytest.approx(1.7)
    assert validate_caption_blocks(words, blocks)["valid"] is True
    ass = render_ass(words, blocks, style, width=1920, height=1080)
    assert "“CORAGEM”" in ass


def test_groups_on_sentence_pause_and_configured_word_limit() -> None:
    words = _words(
        [
            ("No", 0.00, 0.20),
            ("princípio,", 0.22, 0.52),
            ("Deus", 0.54, 0.78),
            ("criou.", 0.80, 1.10),
            ("Depois", 1.90, 2.20),
            ("veio", 2.22, 2.45),
            ("a", 2.47, 2.55),
            ("luz", 2.57, 2.80),
            ("eterna", 2.82, 3.10),
        ]
    )
    style = CaptionStyle(max_words=4, max_chars=30, max_lines=2, pause_break_seconds=0.55)

    blocks = group_word_timings(words, style)

    assert [block.word_indexes for block in blocks] == [[0, 1, 2, 3], [4, 5, 6, 7], [8]]
    assert blocks[0].text == "No princípio, Deus criou."
    assert validate_caption_blocks(words, blocks)["valid"] is True


def test_ass_keeps_full_block_stable_and_changes_only_active_word_color() -> None:
    words = _words(
        [
            ("A", 0.00, 0.20),
            ("fé", 0.20, 0.50),
            ("permanece", 0.50, 0.90),
        ]
    )
    style = CaptionStyle(
        primary_color="#FFFFFF",
        highlight_color="#F4C95D",
        margin_left=111,
        margin_right=112,
        margin_bottom=113,
        uppercase=False,
        variant="classic",
    )
    blocks = group_word_timings(words, style)

    content = render_ass(words, blocks, style, width=1280, height=720)
    events = [line for line in content.splitlines() if line.startswith("Dialogue:")]

    assert len(events) == len(words)
    assert "PlayResX: 1280" in content
    assert "PlayResY: 720" in content
    assert ",111,112,113,1" in content
    for event in events:
        visible = event.split(",", 9)[-1]
        visible = re.sub(r"\{[^}]+}", "", visible).replace(r"\N", " ")
        assert visible == "A fé permanece"
        assert event.count(r"{\c&H005DC9F4&}") == 1
        assert event.count(r"{\c&H00FFFFFF&}") == 1
        assert r"\b" not in event
        assert r"\fsc" not in event


def test_ass_can_render_uppercase_active_word_on_background_card() -> None:
    words = _words([("A", 0.0, 0.2), ("fé", 0.2, 0.5), ("permanece", 0.5, 0.9)])
    style = CaptionStyle(
        font_family="Bahnschrift",
        font_size=76,
        primary_color="#FFFFFF",
        highlight_color="#FFFFFF",
        outline_color="#101820",
        uppercase=True,
        bold=True,
        font_scale_x=92,
        variant="active_card_v2",
        highlight_background_color="#E5484D",
        highlight_card_padding_x=14,
        highlight_card_padding_y=8,
        highlight_card_radius=10,
    )
    blocks = group_word_timings(words, style)

    content = render_ass(words, blocks, style, width=1080, height=1920)
    base_events = [line for line in content.splitlines() if line.startswith("Dialogue: 1,")]
    card_events = [line for line in content.splitlines() if line.startswith("Dialogue: 0,")]
    active_events = [line for line in content.splitlines() if line.startswith("Dialogue: 2,")]

    assert "Style: ActiveV2,Bahnschrift,76" in content
    assert len(base_events) == len(words)
    assert len(card_events) == len(words)
    assert len(active_events) == len(words)
    assert any("FÉ" in event for event in base_events)
    assert any("PERMANECE" in event for event in active_events)
    assert all(r"\p1" in event and r"\1a&H00&" in event and " b " in event for event in card_events)
    base_positions = [re.search(r"\\pos\(\d+,\d+\)", event).group() for event in base_events]
    active_positions = [re.search(r"\\pos\(\d+,\d+\)", event).group() for event in active_events]
    assert base_positions == active_positions


def test_active_card_layout_can_compensate_for_font_metric_width() -> None:
    words = _words([("DESAFIAVA", 0.0, 0.3), ("O", 0.3, 0.5), ("EXERCITO", 0.5, 0.9)])
    style = CaptionStyle(
        font_family="DejaVuSans-Bold.ttf",
        font_size=70,
        uppercase=True,
        layout_width_scale=1.0,
        variant="active_card_v2",
    )

    # Use a deliberately wide canvas so both variants remain on the same line
    # regardless of the platform's DejaVu font metrics.
    regular = _layout_active_card_block(words, style, width=1920, height=1920)
    compact = _layout_active_card_block(
        words,
        style.model_copy(update={"layout_width_scale": 0.68}),
        width=1920,
        height=1920,
    )

    regular_span = regular[-1].center_x - regular[0].center_x
    compact_span = compact[-1].center_x - compact[0].center_x
    assert compact_span < regular_span * 0.75
    assert compact[0].card_width < regular[0].card_width

    five_words = _words(
        [
            ("DESAFIAVA", 0.0, 0.2),
            ("O", 0.2, 0.3),
            ("EXERCITO", 0.3, 0.5),
            ("TODOS", 0.5, 0.7),
            ("OS", 0.7, 0.9),
        ]
    )
    limited = _layout_active_card_block(
        five_words,
        style.model_copy(update={"layout_width_scale": 0.68, "max_words_per_line": 3}),
        width=1080,
        height=1920,
    )
    assert len({item.center_y for item in limited}) == 2
    assert len({item.center_y for item in limited[:3]}) == 1
    assert len({item.center_y for item in limited[3:]}) == 1


def test_short_active_card_can_be_anchored_to_vertical_center() -> None:
    words = _words([("A", 0.0, 0.2), ("PALAVRA", 0.2, 0.5), ("PERMANECE", 0.5, 0.9)])
    style = CaptionStyle(
        font_family="DejaVuSans-Bold.ttf",
        font_size=70,
        uppercase=True,
        variant="active_card_v2",
        vertical_position="center",
    )

    layouts = _layout_active_card_block(words, style, width=1080, height=1920)

    assert {item.center_y for item in layouts} == {960}


def test_short_ass_italicizes_only_direct_scripture_words_without_labels() -> None:
    words = [
        WordTiming(index=0, word="Lemos:", start=0.0, end=0.3, scene_id="scene-001"),
        WordTiming(index=1, word="Deus", start=0.3, end=0.6, scene_id="scene-001"),
        WordTiming(index=2, word="cuida.", start=0.6, end=0.9, scene_id="scene-001"),
        WordTiming(index=3, word="Reflita.", start=0.9, end=1.2, scene_id="scene-002"),
    ]
    style = CaptionStyle(variant="active_card_v2", vertical_position="center")
    blocks = group_word_timings(words, style)

    content = render_ass(
        words,
        blocks,
        style,
        width=1080,
        height=1920,
        italic_word_indexes={1, 2},
    )

    assert "ContextLabel" not in content
    assert "TEXTO BÍBLICO" not in content
    assert "REFLEXÃO" not in content
    assert r"\i1}DEUS" in content
    assert r"\i1}CUIDA." in content
    assert r"\i1}LEMOS:" not in content
    assert r"\i1}REFLITA." not in content


def test_literal_bible_detection_excludes_reference_and_reflection(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "video")
    layout.ensure_directories()
    write_json(
        layout.script,
        {
            "title": "Coragem",
            "source_ids": ["verse-1"],
            "sections": [
                {
                    "section_id": "verse",
                    "role": "opening",
                    "title": "Versículo",
                    "narration": (
                        'Em Josué, capítulo 1, versículo 9, lemos: "Seja forte e corajoso."'
                    ),
                    "claim_classification": ["biblical_text"],
                },
                {
                    "section_id": "reflection",
                    "role": "reflection",
                    "title": "Reflexão",
                    "narration": "Coragem não é ausência de medo.",
                    "claim_classification": ["interpretation"],
                },
            ],
        },
    )
    write_json(
        layout.sources,
        {
            "entries": [
                {
                    "source_id": "verse-1",
                    "kind": "biblical_text",
                    "title": "Josué 1:9",
                    "excerpt": "Seja forte e corajoso.",
                    "use_in_video": "Citação literal",
                    "claim_scope": "Texto bíblico",
                    "verified": True,
                }
            ]
        },
    )
    words = _words(
        [
            ("Em", 0.0, 0.1),
            ("Josué,", 0.1, 0.2),
            ("lemos:", 0.2, 0.3),
            ("Seja", 0.3, 0.4),
            ("forte", 0.4, 0.5),
            ("e", 0.5, 0.6),
            ("corajoso.", 0.6, 0.7),
            ("Coragem", 0.7, 0.8),
            ("não", 0.8, 0.9),
            ("é", 0.9, 1.0),
            ("ausência", 1.0, 1.1),
            ("de", 1.1, 1.2),
            ("medo.", 1.2, 1.3),
        ]
    )

    assert _literal_bible_word_indexes(layout, words) == {3, 4, 5, 6}


def test_classic_center_position_uses_ass_center_alignment() -> None:
    words = _words([("Deus", 0.0, 0.3), ("é", 0.3, 0.5), ("fiel", 0.5, 0.8)])
    style = CaptionStyle(variant="classic", vertical_position="center")
    blocks = group_word_timings(words, style)

    content = render_ass(words, blocks, style, width=1080, height=1920)

    assert f",5,{style.margin_left},{style.margin_right},0,1" in content


def test_srt_and_vtt_use_phrase_blocks_and_expected_time_syntax() -> None:
    blocks = [
        CaptionBlock(
            block_id="block-001",
            word_indexes=[0, 1, 2],
            text="Deus é\nfiel.",
            start=1.234,
            end=2.345,
        )
    ]

    srt = render_srt(blocks, italic_word_indexes={1})
    vtt = render_vtt(blocks, italic_word_indexes={1})

    assert "00:00:01,234 --> 00:00:02,345" in srt
    assert "Deus <i>é</i>\nfiel." in srt
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.234 --> 00:00:02.345" in vtt
    assert "Deus <i>é</i>\nfiel." in vtt


def test_validation_reports_omissions_and_duplicates() -> None:
    words = _words([("A", 0.0, 0.2), ("luz", 0.2, 0.5), ("veio", 0.5, 0.8)])
    invalid = [
        CaptionBlock(
            block_id="block-001",
            word_indexes=[0, 1, 1],
            text="A luz luz",
            start=0.0,
            end=0.5,
        )
    ]

    report = validate_caption_blocks(words, invalid, raise_on_error=False)

    assert report["valid"] is False
    assert report["coverage_complete"] is False
    assert report["no_duplicates"] is False
    assert report["order_valid"] is False
    with pytest.raises(CaptionValidationError):
        validate_caption_blocks(words, invalid)


def test_generate_captions_is_idempotent_through_stage_state(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "video")
    layout.ensure_directories()
    now = utc_now()
    manifest = ProjectManifest(
        project_id="video",
        title="Vídeo",
        created_at=now,
        updated_at=now,
        stages=initial_stage_records(),
        approvals=initial_approvals(),
    )
    manifest.config.captions.font_file = None
    words = _words([("A", 0.0, 0.2), ("graça", 0.2, 0.5), ("basta.", 0.5, 0.9)])
    audio = AudioManifest(
        generated_at=now,
        provider="mock",
        sample_rate=48_000,
        narration_path="audio/narration.wav",
        duration=0.9,
        segments=[],
        words=words,
    )
    write_json(layout.manifest, manifest)
    write_json(layout.audio, audio)

    first = generate_captions(layout)
    second = generate_captions(layout)
    saved = layout.load_manifest()

    assert second == first
    assert saved.stages["captions"].attempts == 1
    assert saved.stages["captions"].status.value == "completed"
    assert layout.ass.is_file()
    assert layout.srt.is_file()
    assert layout.vtt.is_file()
