"""Path display helpers for UI: show paths with ~ for home (anonymous)."""

from pathlib import Path


def to_display(path: str | None) -> str:
    """Return path with home directory replaced by ~ for display in the UI.
    If path is None or empty, returns empty string.
    Only replaces the home prefix; does not resolve symlinks (e.g. /tmp stays /tmp).
    """
    if not path or not path.strip():
        return ""
    try:
        expanded = Path(path).expanduser()
        home = Path.home()
        home_str = str(home)
        exp_str = str(expanded)
        if exp_str == home_str or exp_str.startswith(home_str + "/"):
            return "~" + exp_str[len(home_str) :]
        return path.strip()
    except (OSError, RuntimeError):
        return path.strip()


def from_display(path: str | None) -> str:
    """Expand ~ in path to actual home directory (e.g. before opening a file)."""
    if not path or not path.strip():
        return ""
    return str(Path(path).expanduser())
