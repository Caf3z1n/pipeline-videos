from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pipeline_videos.storage import ProjectLayout, read_json, write_json
from pipeline_videos.utils import utc_now

USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


@dataclass(frozen=True)
class SessionUsage:
    session_id: str
    role: str
    path: Path
    usage: dict[str, int]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalized_usage(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        key: max(0, int(source.get(key, 0))) if isinstance(source.get(key, 0), int | float) else 0
        for key in USAGE_KEYS
    }


def _usage_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {key: max(0, after[key] - before[key]) for key in USAGE_KEYS}


def _usage_summary(usage: dict[str, int]) -> dict[str, int | bool]:
    fresh = max(0, usage["input_tokens"] - usage["cached_input_tokens"])
    return {
        **usage,
        "fresh_input_tokens": fresh,
        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
        "fresh_plus_output_tokens": fresh + usage["output_tokens"],
        "reasoning_is_subset_of_output": True,
    }


def _role_from_meta(meta: dict[str, Any]) -> str:
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    agent_path = str(spawn.get("agent_path") or "") if isinstance(spawn, dict) else ""
    normalized = agent_path.casefold()
    if "shorts_script" in normalized or "script" in normalized:
        return "script"
    if "biblical" in normalized or "review" in normalized:
        return "biblical_review"
    if "image" in normalized:
        return "images"
    if "growth" in normalized or "youtube" in normalized:
        return "youtube_growth"
    return "coordinator"


def read_codex_session_usage(
    path: Path,
    *,
    project_id: str,
    started_at: datetime,
    ended_at: datetime,
) -> SessionUsage | None:
    meta: dict[str, Any] = {}
    contains_project = False
    desktop_samples: list[tuple[datetime, dict[str, int]]] = []
    completed_samples: list[tuple[datetime, dict[str, int]]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            contains_project = contains_project or project_id in raw
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            payload = event.get("payload")
            if event_type == "session_meta" and isinstance(payload, dict):
                meta = payload
            timestamp = _parse_time(event.get("timestamp"))
            if timestamp is None:
                continue
            if event_type == "event_msg" and isinstance(payload, dict):
                info = payload.get("info")
                total = info.get("total_token_usage") if isinstance(info, dict) else None
                if payload.get("type") == "token_count" and isinstance(total, dict):
                    desktop_samples.append((timestamp, _normalized_usage(total)))
            elif event_type == "turn.completed" and isinstance(event.get("usage"), dict):
                completed_samples.append((timestamp, _normalized_usage(event["usage"])))
    if not contains_project:
        return None
    usage: dict[str, int]
    if desktop_samples:
        before = {key: 0 for key in USAGE_KEYS}
        after = {key: 0 for key in USAGE_KEYS}
        for timestamp, sample in desktop_samples:
            if timestamp < started_at:
                before = sample
            if timestamp <= ended_at:
                after = sample
        usage = _usage_delta(after, before)
    else:
        matching = [
            sample for timestamp, sample in completed_samples if started_at <= timestamp <= ended_at
        ]
        if not matching:
            return None
        usage = matching[-1]
    if not any(usage.values()):
        return None
    session_id = str(meta.get("id") or meta.get("session_id") or path.stem)
    return SessionUsage(session_id, _role_from_meta(meta), path.resolve(), usage)


def discover_codex_sessions(
    sessions_root: Path,
    *,
    project_id: str,
    started_at: datetime,
    ended_at: datetime,
) -> list[SessionUsage]:
    root = sessions_root.expanduser().resolve()
    if not root.is_dir():
        return []
    lower_bound = started_at - timedelta(days=1)
    upper_bound = ended_at + timedelta(days=1)
    sessions: list[SessionUsage] = []
    for path in root.rglob("*.jsonl"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < lower_bound or modified > upper_bound:
            continue
        parsed = read_codex_session_usage(
            path,
            project_id=project_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        if parsed is not None:
            sessions.append(parsed)
    return sorted(sessions, key=lambda item: (item.role, item.session_id))


def _sum_usage(items: list[dict[str, int]]) -> dict[str, int]:
    return {key: sum(item[key] for item in items) for key in USAGE_KEYS}


def _context_sizes(layout: ProjectLayout) -> dict[str, int]:
    paths = {
        "script_context_bytes": layout.shorts_script_context,
        "script_queue_bytes": layout.shorts_script_queue,
        "review_context_bytes": layout.shorts_biblical_review_context,
        "review_queue_bytes": layout.shorts_biblical_review_queue,
        "growth_context_bytes": layout.growth_context,
        "growth_queue_bytes": layout.growth_queue,
    }
    result = {name: path.stat().st_size if path.is_file() else 0 for name, path in paths.items()}
    result["image_jobs_total_bytes"] = sum(
        path.stat().st_size for path in layout.image_jobs.glob("image-worker-*.json")
    )
    return result


def _fallback_counts(layout: ProjectLayout) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not layout.events.is_file():
        return counts
    with layout.events.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                event = json.loads(raw).get("event")
            except json.JSONDecodeError:
                continue
            if isinstance(event, str) and "fallback" in event:
                counts[event] = counts.get(event, 0) + 1
    return counts


def write_local_token_audit(
    layout: ProjectLayout,
    *,
    sessions_root: Path | None = None,
    session_logs: list[Path] | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> dict[str, Any]:
    project = layout.load_manifest()
    start = started_at or _parse_time(project.created_at)
    end = ended_at or _parse_time(project.updated_at)
    if start is None or end is None:
        raise ValueError("O projeto não possui janela temporal válida para auditoria.")
    production_end = end
    end += timedelta(minutes=2)
    if session_logs:
        sessions = [
            parsed
            for path in session_logs
            if (
                parsed := read_codex_session_usage(
                    path.expanduser().resolve(),
                    project_id=project.project_id,
                    started_at=start,
                    ended_at=end,
                )
            )
            is not None
        ]
    else:
        root = sessions_root or (Path.home() / ".codex" / "sessions")
        sessions = discover_codex_sessions(
            root,
            project_id=project.project_id,
            started_at=start,
            ended_at=end,
        )
    by_role: dict[str, list[dict[str, int]]] = {}
    for session in sessions:
        by_role.setdefault(session.role, []).append(session.usage)
    stage_usage = {
        role: _usage_summary(_sum_usage(usages)) for role, usages in sorted(by_role.items())
    }
    total = _usage_summary(_sum_usage([session.usage for session in sessions]))
    coordinator_budget = project.config.production.coordinator_effective_token_budget
    total_budget = project.config.production.total_effective_token_budget
    budget = {
        "coordinator_effective_token_budget": coordinator_budget,
        "total_effective_token_budget": total_budget,
        "coordinator_exceeded": (
            int(stage_usage.get("coordinator", {}).get("fresh_plus_output_tokens", 0))
            > coordinator_budget
        ),
        "total_exceeded": int(total["fresh_plus_output_tokens"]) > total_budget,
    }
    audit_dir = layout.root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = audit_dir / "production-metrics.json"
    payload = read_json(metrics_path) if metrics_path.is_file() else {"schema_version": "1.1"}
    payload.update(
        {
            "captured_at": utc_now(),
            "project_id": project.project_id,
            "codex": {
                "sessions": len(sessions),
                "window": {"started_at": start.isoformat(), "ended_at": end.isoformat()},
                "usage": total,
                "by_stage": stage_usage,
                "session_ids": [session.session_id for session in sessions],
            },
            "contexts": _context_sizes(layout),
            "token_budget": budget,
            "production": {
                "elapsed_seconds": max(
                    0.0,
                    round((production_end - start).total_seconds(), 3),
                ),
                "optimization_profile": project.config.production.optimization_profile,
                "validation_policy": project.config.production.validation_policy,
                "repair_policy": project.config.production.repair_policy,
                "token_budget_behavior": project.config.production.token_budget_behavior,
                "duration_policy": project.duration_policy,
                "requested_duration_min_seconds": project.requested_duration_min_seconds,
                "requested_duration_max_seconds": project.requested_duration_max_seconds,
                "effective_duration_seconds": project.effective_duration_seconds,
                "fallback_counts": _fallback_counts(layout),
            },
        }
    )
    write_json(metrics_path, payload)
    report = audit_dir / "report.md"
    start_marker = "<!-- codex-token-audit:start -->"
    end_marker = "<!-- codex-token-audit:end -->"
    lines = [
        start_marker,
        "## Auditoria automática de tokens",
        "",
        f"- Projeto: `{project.project_id}`",
        f"- Sessões Codex: {len(sessions)}",
        f"- Tokens efetivos: {total['fresh_plus_output_tokens']}",
        f"- Orçamento efetivo: {total_budget}",
        "",
        "## Tokens por etapa",
        "",
        "| Etapa | Entrada | Cache | Saída | Efetivos |",
        "|---|---:|---:|---:|---:|",
    ]
    for role, usage in stage_usage.items():
        lines.append(
            f"| {role} | {usage['input_tokens']} | {usage['cached_input_tokens']} | "
            f"{usage['output_tokens']} | {usage['fresh_plus_output_tokens']} |"
        )
    lines.extend(["", end_marker])
    token_section = "\n".join(lines)
    if report.is_file():
        report_text = report.read_text(encoding="utf-8")
        marker_start = report_text.find(start_marker)
        marker_end = report_text.find(end_marker)
        if marker_start >= 0 and marker_end >= marker_start:
            marker_end += len(end_marker)
            report_text = (
                report_text[:marker_start].rstrip()
                + "\n\n"
                + token_section
                + report_text[marker_end:]
            )
        else:
            report_text = report_text.rstrip() + "\n\n" + token_section + "\n"
    else:
        report_text = "# Auditoria de produção\n\n" + token_section + "\n"
    report.write_text(report_text, encoding="utf-8")
    return payload
