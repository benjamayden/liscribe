"""Tests for shell alias update and target resolution behavior."""

from __future__ import annotations

from pathlib import Path

from liscribe import shell_alias


def test_extract_existing_alias_command() -> None:
    lines = [
        "export PATH=/usr/local/bin:$PATH\n",
        "alias rec='/venv/bin/rec'  # liscribe\n",
    ]
    assert shell_alias._extract_existing_alias_command(lines) == "/venv/bin/rec"


def test_update_shell_alias_uses_existing_marker_target_when_rec_missing(
    monkeypatch, tmp_path: Path
) -> None:
    rc_path = tmp_path / ".zshrc"
    rc_path.write_text("alias rec='/old/bin/rec'  # liscribe\n", encoding="utf-8")

    monkeypatch.setattr(shell_alias, "get_shell_rc_path", lambda: rc_path)
    monkeypatch.setattr(shell_alias.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(shell_alias.shutil, "which", lambda _name: None)

    updated = shell_alias.update_shell_alias("scrib")

    assert updated == rc_path
    content = rc_path.read_text(encoding="utf-8")
    assert "alias scrib='/old/bin/rec'  # liscribe" in content
    assert "alias rec='/old/bin/rec'  # liscribe" not in content


def test_update_shell_alias_falls_back_to_python_module_when_rec_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    rc_path = tmp_path / ".zshrc"
    python_path = tmp_path / "python3.12"

    monkeypatch.setattr(shell_alias, "get_shell_rc_path", lambda: rc_path)
    monkeypatch.setattr(shell_alias.sys, "executable", str(python_path))
    monkeypatch.setattr(shell_alias.shutil, "which", lambda _name: None)

    updated = shell_alias.update_shell_alias("rec")

    assert updated == rc_path
    content = rc_path.read_text(encoding="utf-8")
    assert f"alias rec='{python_path} -m liscribe.cli'  # liscribe" in content
