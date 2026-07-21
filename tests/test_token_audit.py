from __future__ import annotations

import json
from datetime import datetime, timedelta

from pipeline_videos.project import create_project
from pipeline_videos.token_audit import write_local_token_audit


def _event(timestamp: datetime, event_type: str, payload: dict) -> str:
    return json.dumps(
        {"timestamp": timestamp.isoformat(), "type": event_type, "payload": payload},
        ensure_ascii=False,
    )


def _token_event(timestamp: datetime, usage: dict[str, int]) -> str:
    return _event(
        timestamp,
        "event_msg",
        {"type": "token_count", "info": {"total_token_usage": usage}},
    )


def test_local_token_audit_discovers_stage_roles_and_persists_effective_cost(tmp_path) -> None:
    layout = create_project(
        "auditoria-short",
        "Auditoria",
        projects_dir=tmp_path / "projects",
        target_duration_seconds=50,
        video_format="short",
    )
    project = layout.load_manifest()
    started = datetime.fromisoformat(project.created_at.replace("Z", "+00:00"))
    coordinator = tmp_path / "coordinator.jsonl"
    coordinator.write_text(
        "\n".join(
            [
                _event(
                    started - timedelta(seconds=2),
                    "session_meta",
                    {"id": "coordinator-id", "source": "cli"},
                ),
                _event(
                    started - timedelta(seconds=1),
                    "response_item",
                    {"type": "message", "text": project.project_id},
                ),
                _token_event(
                    started - timedelta(milliseconds=500),
                    {
                        "input_tokens": 100,
                        "cached_input_tokens": 50,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 2,
                    },
                ),
                _token_event(
                    started + timedelta(seconds=10),
                    {
                        "input_tokens": 1_000,
                        "cached_input_tokens": 750,
                        "output_tokens": 90,
                        "reasoning_output_tokens": 20,
                    },
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script = tmp_path / "script.jsonl"
    script.write_text(
        "\n".join(
            [
                _event(
                    started,
                    "session_meta",
                    {
                        "id": "script-id",
                        "source": {
                            "subagent": {
                                "thread_spawn": {"agent_path": "/root/shorts_script_specialist"}
                            }
                        },
                    },
                ),
                _event(
                    started + timedelta(seconds=1),
                    "response_item",
                    {"type": "message", "text": project.project_id},
                ),
                _token_event(
                    started + timedelta(seconds=20),
                    {
                        "input_tokens": 500,
                        "cached_input_tokens": 400,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 8,
                    },
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report_path = layout.root / "audit/report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Auditoria existente\n\nAnálise humana preservada.\n",
        encoding="utf-8",
    )
    payload = write_local_token_audit(layout, session_logs=[coordinator, script])
    write_local_token_audit(layout, session_logs=[coordinator, script])

    assert payload["codex"]["sessions"] == 2
    assert payload["codex"]["by_stage"]["coordinator"]["fresh_plus_output_tokens"] == 280
    assert payload["codex"]["by_stage"]["script"]["fresh_plus_output_tokens"] == 150
    assert payload["codex"]["usage"]["fresh_plus_output_tokens"] == 430
    assert payload["production"]["elapsed_seconds"] >= 0
    assert payload["production"]["token_budget_behavior"] == "audit_only"
    assert payload["production"]["fallback_counts"] == {}
    assert (layout.root / "audit/production-metrics.json").is_file()
    report_text = report_path.read_text(encoding="utf-8")
    assert "Análise humana preservada." in report_text
    assert "Tokens por etapa" in report_text
    assert report_text.count("<!-- codex-token-audit:start -->") == 1
