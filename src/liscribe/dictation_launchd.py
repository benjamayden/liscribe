"""Shared helpers for dictation launchd integration and rec binary resolution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from liscribe.config import load_config, save_config

LAUNCHD_LABEL = "com.liscribe.dictate"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
DICTATE_LOG = Path.home() / ".local" / "share" / "liscribe" / "dictate.log"


@dataclass(frozen=True)
class DictationAgentStatus:
    installed: bool
    running: bool
    launchctl_output: str


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _candidate_from_argv0() -> Path:
    return Path(sys.argv[0]).expanduser().resolve()


def resolve_rec_command() -> list[str]:
    """Return argv for launching liscribe in detached/background contexts."""
    cfg = load_config()
    stored = cfg.get("rec_binary_path")
    if isinstance(stored, str) and stored.strip():
        stored_path = Path(stored).expanduser().resolve()
        if _is_executable_file(stored_path):
            return [str(stored_path)]

    argv0 = _candidate_from_argv0()
    if _is_executable_file(argv0):
        return [str(argv0)]

    rec_on_path = shutil.which("rec")
    if rec_on_path:
        return [rec_on_path]

    # Running via `python -m liscribe.cli` often sets argv0 to cli.py.
    if argv0.is_file():
        return [sys.executable, str(argv0)]

    return [sys.executable, "-m", "liscribe.cli"]


def persist_rec_binary_if_missing() -> None:
    """Save a stable rec binary path for launchd usage when available."""
    cfg = load_config()
    stored = cfg.get("rec_binary_path")
    if isinstance(stored, str) and stored.strip():
        stored_path = Path(stored).expanduser().resolve()
        if _is_executable_file(stored_path):
            return

    candidates: list[Path] = []
    argv0 = _candidate_from_argv0()
    candidates.append(argv0)
    rec_on_path = shutil.which("rec")
    if rec_on_path:
        candidates.append(Path(rec_on_path).expanduser().resolve())

    for candidate in candidates:
        if _is_executable_file(candidate):
            cfg["rec_binary_path"] = str(candidate)
            save_config(cfg)
            return


def _write_plist(rec_args: list[str]) -> None:
    """Write the launchd plist for the dictation daemon."""
    import plistlib

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    DICTATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": rec_args + ["dictate"],
        "RunAtLoad": True,
        "KeepAlive": False,  # Quit from menu bar = stay quit; no auto-restart
        "StandardOutPath": str(DICTATE_LOG),
        "StandardErrorPath": str(DICTATE_LOG),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin"
        },
        # Makes the login item appear as "Liscribe" in System Settings → General → Login Items
        "LSApplicationName": "Liscribe",
    }
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)


def run_launchctl(subcmd: str, *args: str) -> tuple[int, str]:
    """Run launchctl and return (returncode, combined output)."""
    try:
        result = subprocess.run(
            ["launchctl", subcmd, *args],
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def install_dictation_agent() -> tuple[bool, str]:
    rec_args = resolve_rec_command()
    _write_plist(rec_args)
    # Update loaded job when reinstalling.
    run_launchctl("unload", str(PLIST_PATH))
    rc, out = run_launchctl("load", str(PLIST_PATH))
    return rc == 0, out


def uninstall_dictation_agent() -> bool:
    if not PLIST_PATH.exists():
        return False
    run_launchctl("unload", str(PLIST_PATH))
    PLIST_PATH.unlink(missing_ok=True)
    return True


def get_dictation_agent_status() -> DictationAgentStatus:
    installed = PLIST_PATH.exists()
    rc, out = run_launchctl("list", LAUNCHD_LABEL)
    return DictationAgentStatus(
        installed=installed,
        running=(rc == 0 and bool(out)),
        launchctl_output=out,
    )
