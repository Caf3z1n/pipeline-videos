from __future__ import annotations

import subprocess

from pipeline_videos import cli


def test_codex_image_generation_status_detects_enabled_feature(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="image_generation stable true\n",
            stderr="",
        ),
    )

    assert cli._codex_image_generation_status() == (
        "DISPONÍVEL",
        "Codex CLI com image_generation habilitado",
    )


def test_codex_image_generation_status_reports_missing_cli(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    assert cli._codex_image_generation_status() == (
        "AUSENTE",
        "instale e autentique o Codex CLI",
    )
