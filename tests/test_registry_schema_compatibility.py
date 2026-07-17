from pipeline_videos.models import VerseUsageIndex


def test_shared_registry_preserves_additive_fields_from_newer_writer() -> None:
    payload = {
        "schema_version": "1.0",
        "generated_at": "2026-07-17T01:04:33Z",
        "writer_metadata": {"release": "newer-desktop"},
        "records": [
            {
                "registry_id": "desktop:example",
                "environment": "desktop",
                "project_id": "example",
                "project_path": "shorts/example",
                "video_format": "short",
                "status": "reserved",
                "references": ["João 14:27"],
                "retention_archetype": "counterintuitive_reveal",
                "hook_family": "counterintuitive_reveal",
                "engagement_mode": None,
                "future_editorial_signal": {"score": 0.9},
                "created_at": "2026-07-17T00:58:49Z",
            }
        ],
    }

    registry = VerseUsageIndex.model_validate(payload)
    dumped = registry.model_dump(mode="json")

    assert dumped["writer_metadata"] == {"release": "newer-desktop"}
    assert dumped["records"][0]["retention_archetype"] == "counterintuitive_reveal"
    assert dumped["records"][0]["hook_family"] == "counterintuitive_reveal"
    assert dumped["records"][0]["engagement_mode"] is None
    assert dumped["records"][0]["future_editorial_signal"] == {"score": 0.9}
