"""Shell alias: get rc path and write alias line (shared by CLI and Preferences TUI)."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

# Must match install.sh and cli.py
ALIAS_MARKER = "# liscribe"


def get_shell_rc_path() -> Path:
    """Path to the current shell's rc file (e.g. ~/.zshrc)."""
    shell = os.path.basename(os.environ.get("SHELL", "/bin/zsh"))
    if shell == "zsh":
        return Path.home() / ".zshrc"
    if shell == "bash":
        return Path.home() / ".bashrc"
    return Path.home() / f".{shell}rc"


def _extract_existing_alias_command(lines: list[str]) -> str | None:
    """Extract the command path from an existing liscribe alias line, if present."""
    pattern = re.compile(r"^\s*alias\s+\S+=(['\"])(?P<cmd>.+?)\1\s*(?:#.*)?$")
    for line in lines:
        if ALIAS_MARKER not in line:
            continue
        match = pattern.match(line.strip())
        if match:
            return match.group("cmd")
    return None


def _resolve_alias_target(existing_command: str | None = None) -> str:
    """Resolve the command target used in shell alias definitions."""
    candidates = (
        Path(sys.executable).parent / "rec",
        Path(sys.executable).parent / "rec.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    rec_on_path = shutil.which("rec")
    if rec_on_path:
        return rec_on_path

    if existing_command:
        return existing_command

    return f"{sys.executable} -m liscribe.cli"


def update_shell_alias(alias_name: str) -> Path | None:
    """Update shell rc so the given alias runs liscribe. Remove old liscribe alias, add new one.
    Returns the rc path if the file was updated, None otherwise.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", alias_name):
        return None
    rc = get_shell_rc_path()
    try:
        if rc.exists():
            lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
        else:
            lines = []
        alias_target = _resolve_alias_target(_extract_existing_alias_command(lines))
        alias_line = f"alias {alias_name}='{alias_target}'  {ALIAS_MARKER}\n"
        new_lines = [line for line in lines if ALIAS_MARKER not in line]
        prefix = "\n" if new_lines else ""
        new_lines.append(prefix + alias_line)
        rc.parent.mkdir(parents=True, exist_ok=True)
        rc.write_text("".join(new_lines).rstrip() + "\n", encoding="utf-8")
        return rc
    except (OSError, IOError):
        return None
