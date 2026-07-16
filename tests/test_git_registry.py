from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pipeline_videos.git_registry import read_shared_registry, update_shared_registry


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        check=True,
        shell=False,
    )


def _repository(tmp_path: Path, name: str, remote: Path) -> Path:
    repository = tmp_path / name
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "remote", "add", "registry", str(remote))
    return repository


def test_git_registry_writes_versioned_json_without_checking_out_branch(
    tmp_path, monkeypatch
) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(remote)],
        capture_output=True,
        check=True,
        shell=False,
    )
    repository = _repository(tmp_path, "client", remote)
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_REMOTE", "registry")

    def mutate(document):
        document["records"].append({"project_id": "short-1"})
        return document, "reserved"

    result = update_shared_registry(mutate, message="reserve short-1", repo_root=repository)
    document = read_shared_registry(repo_root=repository)

    assert result == "reserved"
    assert document["records"] == [{"project_id": "short-1"}]
    assert not (repository / "content-registry.json").exists()
    history = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--format=%s", "content-registry"],
        capture_output=True,
        check=True,
        text=True,
        shell=False,
    ).stdout
    assert "reserve short-1" in history


def test_git_registry_reloads_and_retries_after_concurrent_push(tmp_path, monkeypatch) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(remote)],
        capture_output=True,
        check=True,
        shell=False,
    )
    first = _repository(tmp_path, "first", remote)
    second = _repository(tmp_path, "second", remote)
    monkeypatch.setenv("PIPELINE_VIDEOS_CONTENT_REGISTRY_REMOTE", "registry")

    update_shared_registry(
        lambda document: (document, None),
        message="bootstrap",
        repo_root=first,
    )
    calls = 0

    def competing_mutation(document):
        nonlocal calls
        calls += 1
        if calls == 1:

            def reserve_other(other):
                other["records"].append({"project_id": "remote"})
                return other, None

            update_shared_registry(
                reserve_other,
                message="reserve remote",
                repo_root=second,
            )
        document["records"].append({"project_id": "local"})
        return document, None

    update_shared_registry(
        competing_mutation,
        message="reserve local",
        repo_root=first,
    )
    document = read_shared_registry(repo_root=first)

    assert calls == 2
    assert {item["project_id"] for item in document["records"]} == {"local", "remote"}
    json.dumps(document)
