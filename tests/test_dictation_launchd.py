"""Tests for dictation launchd helpers and rec command resolution."""

from __future__ import annotations

from pathlib import Path

from liscribe import dictation_launchd as dl


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_resolve_rec_command_prefers_valid_stored_binary(monkeypatch, tmp_path: Path) -> None:
    stored = _make_executable(tmp_path / "rec")
    monkeypatch.setattr(dl, "load_config", lambda: {"rec_binary_path": str(stored)})
    assert dl.resolve_rec_command() == [str(stored.resolve())]


def test_resolve_rec_command_uses_path_when_stored_not_executable(
    monkeypatch, tmp_path: Path
) -> None:
    stored = tmp_path / "cli.py"
    stored.write_text("print('x')\n", encoding="utf-8")
    rec_on_path = _make_executable(tmp_path / "path-rec")

    monkeypatch.setattr(dl, "load_config", lambda: {"rec_binary_path": str(stored)})
    monkeypatch.setattr(dl, "_candidate_from_argv0", lambda: stored)
    monkeypatch.setattr(
        dl.shutil,
        "which",
        lambda name: str(rec_on_path) if name == "rec" else None,
    )

    assert dl.resolve_rec_command() == [str(rec_on_path)]


def test_persist_rec_binary_if_missing_uses_executable_candidate(
    monkeypatch, tmp_path: Path
) -> None:
    argv0 = tmp_path / "cli.py"
    argv0.write_text("print('x')\n", encoding="utf-8")
    rec_on_path = _make_executable(tmp_path / "rec")
    saved: dict[str, object] = {}

    monkeypatch.setattr(dl, "load_config", lambda: {"rec_binary_path": None})
    monkeypatch.setattr(dl, "save_config", lambda values: saved.update(values))
    monkeypatch.setattr(dl, "_candidate_from_argv0", lambda: argv0)
    monkeypatch.setattr(
        dl.shutil,
        "which",
        lambda name: str(rec_on_path) if name == "rec" else None,
    )

    dl.persist_rec_binary_if_missing()
    assert saved["rec_binary_path"] == str(rec_on_path.resolve())


def test_get_dictation_agent_status_uses_plist_and_launchctl(
    monkeypatch, tmp_path: Path
) -> None:
    plist = tmp_path / "com.liscribe.dictate.plist"
    plist.write_text("", encoding="utf-8")
    monkeypatch.setattr(dl, "PLIST_PATH", plist)
    monkeypatch.setattr(dl, "run_launchctl", lambda *_args: (0, "pid = 123"))

    status = dl.get_dictation_agent_status()
    assert status.installed is True
    assert status.running is True
    assert "pid" in status.launchctl_output
