from datetime import date

from pipeline_videos.project import create_project
from pipeline_videos.storage import ProjectLayout, write_json
from pipeline_videos.youtube_analytics import fetch_and_join, query_analytics


class _Request:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.response


class _Reports:
    def __init__(self, responses, errors=None):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.errors = errors or {}
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        response = self.responses[min(index, len(self.responses) - 1)]
        return _Request(response, self.errors.get(index))


class _Service:
    def __init__(self, response, errors=None):
        self.reports_api = _Reports(response, errors)

    def reports(self):
        return self.reports_api


def _response():
    return {
        "columnHeaders": [
            {"name": "video"},
            {"name": "day"},
            {"name": "views"},
            {"name": "likes"},
        ],
        "rows": [["abc123", "2026-07-20", 123, 9]],
    }


def _daily_response():
    return {
        "columnHeaders": [
            {"name": "day"},
            {"name": "views"},
            {"name": "likes"},
        ],
        "rows": [["2026-07-20", 123, 9]],
    }


def test_query_converts_rows_without_network():
    service = _Service(_response())
    rows, unavailable = query_analytics(
        service,
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 24),
        metrics=["views"],
    )
    assert rows == [{"video": "abc123", "day": "2026-07-20", "views": 123, "likes": 9}]
    assert unavailable == []
    assert service.reports_api.calls[0]["dimensions"] == "video"
    assert service.reports_api.calls[0]["sort"] == "-views"
    assert service.reports_api.calls[0]["maxResults"] == 200


def test_query_falls_back_when_unique_viewers_is_rejected():
    service = _Service(_response(), errors={0: RuntimeError("uniques unsupported")})
    rows, unavailable = query_analytics(
        service,
        start_date="2026-07-01",
        end_date="2026-07-24",
        dimensions=["day"],
        metrics=["views", "uniques"],
    )
    assert rows[0]["views"] == 123
    assert unavailable == ["uniques"]
    assert len(service.reports_api.calls) == 2
    assert "uniques" not in service.reports_api.calls[1]["metrics"]


def test_fetch_joins_local_publication_by_video_id(tmp_path):
    projects = tmp_path / "projects"
    layout = create_project(
        "short-local",
        "Short local",
        projects_dir=projects,
        project_date=date(2026, 7, 20),
        video_format="short",
        target_duration_seconds=18,
    )
    assert isinstance(layout, ProjectLayout)
    write_json(
        layout.publication,
        {
            "project_id": layout.load_manifest().project_id,
            "video_id": "abc123",
            "title": "Short local",
            "privacy_status": "public",
        },
    )
    layout.narration.parent.mkdir(parents=True, exist_ok=True)
    layout.narration.write_text("Eu te digo uma coisa importante.", encoding="utf-8")
    result = fetch_and_join(
        _Service([_response(), _daily_response()]),
        start_date="2026-07-01",
        end_date="2026-07-24",
        projects_dir=projects,
        metrics=["views"],
    )
    assert result.rows[0]["local"]["video_id"] == "abc123"
    assert result.rows[0]["local"]["narration_preview"].startswith("Eu te digo")
    assert result.daily_rows == [{"day": "2026-07-20", "views": 123, "likes": 9, "local": None}]


def test_fetch_for_one_video_uses_filter_and_injects_local_id(tmp_path):
    projects = tmp_path / "projects"
    layout = create_project(
        "short-filtered",
        "Short filtrado",
        projects_dir=projects,
        project_date=date(2026, 7, 20),
        video_format="short",
        target_duration_seconds=18,
    )
    write_json(
        layout.publication,
        {
            "project_id": layout.load_manifest().project_id,
            "video_id": "abc123",
            "title": "Short filtrado",
            "privacy_status": "public",
        },
    )
    aggregate_response = {
        "columnHeaders": [{"name": "views"}],
        "rows": [[123]],
    }
    service = _Service([aggregate_response, _daily_response()])
    result = fetch_and_join(
        service,
        start_date="2026-07-01",
        end_date="2026-07-24",
        projects_dir=projects,
        video_id="abc123",
        metrics=["views"],
    )
    assert "dimensions" not in service.reports_api.calls[0]
    assert service.reports_api.calls[0]["filters"] == "video==abc123"
    assert service.reports_api.calls[1]["dimensions"] == "day"
    assert result.rows[0]["video"] == "abc123"
    assert result.rows[0]["local"]["project_id"] == layout.load_manifest().project_id
    assert result.daily_rows[0]["video"] == "abc123"
