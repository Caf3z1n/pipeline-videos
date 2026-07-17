import pytest


@pytest.fixture(autouse=True)
def _isolate_content_registry(monkeypatch) -> None:
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE", "local")
