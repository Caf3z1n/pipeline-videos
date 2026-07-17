from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from pipeline_videos.storage import REPO_ROOT

REGISTRY_MODE_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_MODE"
REGISTRY_REMOTE_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_REMOTE"
REGISTRY_REF_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_REF"
REGISTRY_PATH_ENV = "PIPELINE_VIDEOS_CONTENT_REGISTRY_PATH"
DEFAULT_REGISTRY_REF = "refs/heads/content-registry"
DEFAULT_REGISTRY_PATH = "content-registry.json"
LOCAL_TRACKING_REF = "refs/pipeline-videos/content-registry"


class RegistryUnavailableError(RuntimeError):
    """Raised when the shared registry cannot be read or atomically updated."""


class RegistryConflictError(RuntimeError):
    """Raised when all compare-and-swap retries are exhausted."""


def shared_registry_enabled() -> bool:
    return os.getenv(REGISTRY_MODE_ENV, "git").strip().casefold() == "git"


def _git(
    args: list[str],
    *,
    repo_root: Path,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "pipeline-videos")
    env.setdefault("GIT_AUTHOR_EMAIL", "pipeline-videos@localhost")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        shell=False,
        env=env,
    )


def _registry_settings() -> tuple[str, str, str]:
    remote = os.getenv(REGISTRY_REMOTE_ENV, "").strip()
    if not remote:
        raise RegistryUnavailableError(
            f"{REGISTRY_REMOTE_ENV} é obrigatório quando o registro Git está ativo."
        )
    reference = os.getenv(REGISTRY_REF_ENV, DEFAULT_REGISTRY_REF).strip()
    path = os.getenv(REGISTRY_PATH_ENV, DEFAULT_REGISTRY_PATH).strip()
    if not reference.startswith("refs/heads/"):
        raise ValueError(f"{REGISTRY_REF_ENV} deve começar com refs/heads/.")
    if not path or Path(path).name != path:
        raise ValueError(f"{REGISTRY_PATH_ENV} deve ser somente um nome de arquivo.")
    return remote, reference, path.replace("\\", "/")


def _fetch_head(*, repo_root: Path, remote: str, reference: str) -> str | None:
    result = _git(
        ["fetch", "--quiet", "--no-tags", remote, f"+{reference}:{LOCAL_TRACKING_REF}"],
        repo_root=repo_root,
        check=False,
    )
    if result.returncode == 0:
        return _git(["rev-parse", LOCAL_TRACKING_REF], repo_root=repo_root).stdout.strip()
    missing_markers = ("couldn't find remote ref", "could not find remote ref")
    details = f"{result.stdout}\n{result.stderr}".casefold()
    if any(marker in details for marker in missing_markers):
        return None
    raise RegistryUnavailableError(
        "Não foi possível atualizar o registro compartilhado antes da seleção. "
        "A operação foi interrompida para não arriscar repetição."
    )


def _read_document(*, repo_root: Path, commit: str | None, path: str) -> dict[str, Any]:
    if commit is None:
        return {"schema_version": "1.0", "generated_at": "", "records": []}
    result = _git(["show", f"{commit}:{path}"], repo_root=repo_root, check=False)
    if result.returncode != 0:
        return {"schema_version": "1.0", "generated_at": "", "records": []}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RegistryUnavailableError("O JSON do registro compartilhado está inválido.") from exc
    if not isinstance(value, dict):
        raise RegistryUnavailableError("O registro compartilhado não contém um objeto JSON.")
    return value


def _create_commit(
    document: dict[str, Any],
    *,
    repo_root: Path,
    parent: str | None,
    path: str,
    message: str,
) -> str:
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    blob = _git(["hash-object", "-w", "--stdin"], repo_root=repo_root, input_text=payload)
    tree_input = f"100644 blob {blob.stdout.strip()}\t{path}\0"
    tree = _git(["mktree", "-z"], repo_root=repo_root, input_text=tree_input).stdout.strip()
    args = ["commit-tree", tree, "-m", message]
    if parent:
        args.extend(["-p", parent])
    return _git(args, repo_root=repo_root).stdout.strip()


def read_shared_registry(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    remote, reference, path = _registry_settings()
    head = _fetch_head(repo_root=repo_root, remote=remote, reference=reference)
    return _read_document(repo_root=repo_root, commit=head, path=path)


def update_shared_registry[T](
    mutate: Callable[[dict[str, Any]], tuple[dict[str, Any], T]],
    *,
    message: str,
    repo_root: Path = REPO_ROOT,
    max_attempts: int = 5,
) -> T:
    """Atomically mutate the Git registry; rejected pushes are reloaded and retried."""
    remote, reference, path = _registry_settings()
    for attempt in range(max_attempts):
        parent = _fetch_head(repo_root=repo_root, remote=remote, reference=reference)
        current = _read_document(repo_root=repo_root, commit=parent, path=path)
        previous = deepcopy(current)
        updated, result = mutate(current)
        if updated == previous:
            return result
        commit = _create_commit(
            updated,
            repo_root=repo_root,
            parent=parent,
            path=path,
            message=message,
        )
        push = _git(
            ["push", "--quiet", remote, f"{commit}:{reference}"],
            repo_root=repo_root,
            check=False,
        )
        if push.returncode == 0:
            return result
        details = f"{push.stdout}\n{push.stderr}".casefold()
        conflict = any(
            marker in details
            for marker in ("non-fast-forward", "fetch first", "failed to push some refs")
        )
        if not conflict:
            raise RegistryUnavailableError(
                "Não foi possível registrar a reserva compartilhada. "
                "A criação foi interrompida para não arriscar repetição."
            )
        if attempt + 1 < max_attempts:
            time.sleep(0.05 * (attempt + 1))
    raise RegistryConflictError(
        "O registro compartilhado mudou repetidamente; tente novamente em alguns segundos."
    )
