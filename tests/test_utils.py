from pipeline_videos.models import Briefing
from pipeline_videos.utils import (
    aspect_ratio_label,
    canonical_json,
    matches_aspect_ratio,
    sha256_value,
)


def test_canonical_json_serializes_nested_models_deterministically() -> None:
    briefing = Briefing(title="Teste")
    first = {"briefing": briefing, "tags": {"b", "a"}}
    second = {"tags": {"a", "b"}, "briefing": briefing.model_copy()}

    assert canonical_json(first) == canonical_json(second)
    assert sha256_value(first) == sha256_value(second)


def test_aspect_ratio_helpers_support_vertical_video() -> None:
    assert aspect_ratio_label(1080, 1920) == "9:16"
    assert matches_aspect_ratio(540, 960, 1080, 1920)
    assert not matches_aspect_ratio(1280, 720, 1080, 1920)
