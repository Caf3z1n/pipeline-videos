"""API-first client for read-only YouTube Analytics data.

This module deliberately keeps Analytics OAuth separate from the upload token.
The Analytics API is queried through an injectable service so tests never need a
network call.  Results can be joined with local publication manifests by
YouTube video id.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pipeline_videos.storage import PROJECT_CATEGORY_DIRS, ProjectLayout, read_json

ANALYTICS_SCOPES = (
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
)
DEFAULT_ANALYTICS_TOKEN_FILE = (
    Path.home() / ".config" / "pipeline-videos" / "youtube" / "analytics-token.json"
)
DEFAULT_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "likes,comments,shares,subscribersGained,subscribersLost,engagedViews"
)


@dataclass(frozen=True)
class LocalVideo:
    video_id: str
    project_id: str
    project_path: str
    title: str
    privacy_status: str
    format: str
    duration_seconds: float | None
    narration_preview: str


@dataclass(frozen=True)
class AnalyticsQueryResult:
    start_date: str
    end_date: str
    dimensions: list[str]
    daily_dimensions: list[str]
    metrics: list[str]
    rows: list[dict[str, Any]]
    daily_rows: list[dict[str, Any]]
    unavailable_metrics: list[str]
    local_videos: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def analytics_token_file(path: Path | None = None) -> Path:
    configured = os.getenv("YOUTUBE_ANALYTICS_TOKEN_FILE")
    target = path or (Path(configured) if configured else DEFAULT_ANALYTICS_TOKEN_FILE)
    return target.expanduser().resolve()


def analytics_client_secrets_file(path: Path | None = None) -> Path:
    configured = os.getenv("YOUTUBE_ANALYTICS_CLIENT_SECRETS_FILE") or os.getenv(
        "YOUTUBE_CLIENT_SECRETS_FILE"
    )
    if path:
        return path.expanduser().resolve()
    if not configured:
        raise FileNotFoundError(
            "Defina YOUTUBE_ANALYTICS_CLIENT_SECRETS_FILE (ou YOUTUBE_CLIENT_SECRETS_FILE)."
        )
    return Path(configured).expanduser().resolve()


def authorize_analytics(client_secrets: Path, *, destination: Path | None = None) -> Path:
    """Run the one-time OAuth consent flow for read-only Analytics scope."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    source = client_secrets.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Credencial OAuth não encontrada: {source}")
    flow = InstalledAppFlow.from_client_secrets_file(str(source), scopes=list(ANALYTICS_SCOPES))
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        access_type="offline",
        prompt="consent",
        success_message="Autorização da Analytics concluída. Você pode fechar esta janela.",
    )
    target = analytics_token_file(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.replace(temporary, target)
    with suppress(OSError):
        target.chmod(0o600)
    return target


def authenticated_analytics_service(*, source: Path | None = None) -> Any:
    """Build a read-only YouTube Analytics API service from the separate token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = analytics_token_file(source)
    if not token.is_file():
        raise FileNotFoundError(
            f"Token da Analytics não encontrado em {token}. "
            "Execute `pipeline-videos analytics auth`."
        )
    credentials = Credentials.from_authorized_user_file(str(token), scopes=list(ANALYTICS_SCOPES))
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        temporary = token.with_suffix(token.suffix + ".tmp")
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        os.replace(temporary, token)
    if not credentials.valid:
        raise RuntimeError("Token da Analytics inválido ou sem refresh token; autorize novamente.")
    return build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)


def _response_rows(response: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [str(item.get("name")) for item in response.get("columnHeaders", [])]
    return headers, response.get("rows", [])


def query_analytics(
    service: Any,
    *,
    start_date: date | str,
    end_date: date | str,
    dimensions: Iterable[str] = ("video",),
    metrics: Iterable[str] = DEFAULT_METRICS.split(","),
    video_id: str | None = None,
    sort: str | None = None,
    max_results: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Query one supported report shape and retry when ``uniques`` is unavailable."""
    start = start_date.isoformat() if isinstance(start_date, date) else str(start_date)
    end = end_date.isoformat() if isinstance(end_date, date) else str(end_date)
    dims = list(dimensions)
    requested = list(metrics)
    if sort is None:
        sort = "-views" if dims == ["video"] else "day" if dims == ["day"] else None
    if max_results is None and dims == ["video"]:
        max_results = 200

    def execute(selected_metrics: list[str]) -> dict[str, Any]:
        query_args: dict[str, Any] = {
            "ids": "channel==MINE",
            "startDate": start,
            "endDate": end,
            "metrics": ",".join(selected_metrics),
        }
        if dims:
            query_args["dimensions"] = ",".join(dims)
        if video_id:
            query_args["filters"] = f"video=={video_id}"
        if sort:
            query_args["sort"] = sort
        if max_results is not None:
            query_args["maxResults"] = max_results
        return service.reports().query(**query_args).execute()

    try:
        response = execute(requested)
    except Exception as exc:
        normalized_error = str(exc).lower().replace(" ", "")
        optional_unique_metrics = {"uniques", "uniqueViewers"}
        rejected_optional = [
            metric
            for metric in requested
            if metric in optional_unique_metrics
            and ("unique" in normalized_error or metric.casefold() in normalized_error)
        ]
        if not rejected_optional:
            raise
        fallback = [metric for metric in requested if metric not in optional_unique_metrics]
        response = execute(fallback)
        unavailable = rejected_optional
        headers, raw_rows = _response_rows(response)
        return [dict(zip(headers, row, strict=False)) for row in raw_rows], unavailable
    headers, raw_rows = _response_rows(response)
    return [dict(zip(headers, row, strict=False)) for row in raw_rows], []


def _join_local_rows(
    rows: Iterable[dict[str, Any]],
    *,
    locals_by_id: dict[str, LocalVideo],
    forced_video_id: str | None = None,
) -> list[dict[str, Any]]:
    joined: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if forced_video_id and not item.get("video"):
            item["video"] = forced_video_id
        local = locals_by_id.get(str(item.get("video")))
        item["local"] = asdict(local) if local else None
        joined.append(item)
    return joined


def _narration_preview(layout: ProjectLayout) -> str:
    if not layout.narration.is_file():
        return ""
    text = " ".join(layout.narration.read_text(encoding="utf-8").split())
    return text[:180]


def local_publications(projects_dir: Path) -> list[LocalVideo]:
    """Index local publication manifests without touching project state."""
    result: list[LocalVideo] = []
    for category in PROJECT_CATEGORY_DIRS:
        root = projects_dir / category
        if not root.is_dir():
            continue
        for manifest_path in sorted(root.glob("*/project.json")):
            layout = ProjectLayout(manifest_path.parent)
            publication_path = layout.publication
            if not publication_path.is_file():
                continue
            publication = read_json(publication_path)
            video_id = publication.get("video_id")
            if not isinstance(video_id, str) or not video_id:
                continue
            manifest = read_json(layout.manifest)
            context = publication.get("short_analytics_context") or {}
            duration = (
                context.get("video_duration_seconds")
                or manifest.get("effective_duration_seconds")
                or manifest.get("target_duration_seconds")
            )
            result.append(
                LocalVideo(
                    video_id=video_id,
                    project_id=str(publication.get("project_id") or manifest.get("project_id", "")),
                    project_path=str(layout.root),
                    title=str(publication.get("title") or manifest.get("title", "")),
                    privacy_status=str(publication.get("privacy_status", "")),
                    format=str(manifest.get("video_format", "")),
                    duration_seconds=float(duration) if duration is not None else None,
                    narration_preview=_narration_preview(layout),
                )
            )
    return result


def fetch_and_join(
    service: Any,
    *,
    start_date: date | str,
    end_date: date | str,
    projects_dir: Path,
    video_id: str | None = None,
    metrics: Iterable[str] | None = None,
) -> AnalyticsQueryResult:
    requested_metrics = list(metrics or DEFAULT_METRICS.split(","))
    aggregate_metrics = [
        metric for metric in requested_metrics if metric not in {"uniques", "uniqueViewers"}
    ]
    aggregate_dimensions: tuple[str, ...] = () if video_id else ("video",)
    aggregate_rows, aggregate_unavailable = query_analytics(
        service,
        start_date=start_date,
        end_date=end_date,
        dimensions=aggregate_dimensions,
        metrics=aggregate_metrics,
        video_id=video_id,
    )
    daily_rows, daily_unavailable = query_analytics(
        service,
        start_date=start_date,
        end_date=end_date,
        dimensions=("day",),
        metrics=requested_metrics,
        video_id=video_id,
    )
    locals_by_id = {item.video_id: item for item in local_publications(projects_dir)}
    return AnalyticsQueryResult(
        start_date=str(start_date),
        end_date=str(end_date),
        dimensions=list(aggregate_dimensions),
        daily_dimensions=["day"],
        metrics=requested_metrics,
        rows=_join_local_rows(
            aggregate_rows,
            locals_by_id=locals_by_id,
            forced_video_id=video_id,
        ),
        daily_rows=_join_local_rows(
            daily_rows,
            locals_by_id=locals_by_id,
            forced_video_id=video_id,
        ),
        unavailable_metrics=sorted(set(aggregate_unavailable).union(daily_unavailable)),
        local_videos=[asdict(item) for item in locals_by_id.values()],
    )


def write_result(result: AnalyticsQueryResult, destination: Path) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    payload = json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, destination)
