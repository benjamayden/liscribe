"""Preferences — Transcripts: save path, --here default, open app."""

from __future__ import annotations

from pathlib import Path
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Button, Input, Static, Switch

from liscribe.config import load_config, save_config
from liscribe.screens.base import BackScreen
from liscribe.screens.top_bar import TopBar

_BLOCKED_SAVE_FOLDERS = frozenset({
    Path(p) for p in ("/", "/etc", "/usr", "/bin", "/sbin", "/var", "/dev", "/sys", "/proc")
})


def _is_safe_save_folder(folder: str) -> bool:
    try:
        resolved = Path(folder).expanduser().resolve()
    except Exception:
        return False
    return resolved not in _BLOCKED_SAVE_FOLDERS and len(resolved.parts) > 2


class PrefsTranscriptsScreen(BackScreen):
    """Transcript/output-related settings."""

    def compose(self):
        cfg = load_config()
        folder = cfg.get("save_folder", "~/transcripts") or "~/transcripts"
        use_here_default = bool(cfg.get("record_here_by_default", False))
        open_app = str(cfg.get("open_transcript_app", "cursor") or "cursor")

        with Vertical(classes="screen-frame"):
            yield TopBar(variant="compact", section="Transcripts")
            with ScrollableContainer(classes="scroll-fill"):
                yield Static("")
                save_input = Input(value=folder, id="save-input", placeholder="~/transcripts", classes="text-input")
                save_input.border_title = "Default save path"
                save_input.border_subtitle = "Recordings and transcripts"
                with Horizontal(classes="top-container"):
                    yield save_input
                yield Static("")
                here_switch = Switch(value=use_here_default, id="here-default-switch", classes="switch-input")
                here_switch.border_title = "Use --here by default"
                here_switch.border_subtitle = "Record saves to ./docs/transcripts from current directory"
                with Horizontal(classes="top-container"):
                    yield here_switch
                yield Static("")
                open_app_input = Input(value=open_app, id="open-app-input", placeholder="cursor", classes="text-input")
                open_app_input.border_title = "Open transcript app"
                open_app_input.border_subtitle = "e.g. cursor, code, code -r, default"
                with Horizontal(classes="top-container"):
                    yield open_app_input
                yield Static("")
            with Horizontal(classes="footer-container"):
                yield Button("Save", id="btn-save", classes="btn btn-primary")
                yield Static("", classes="spacer-x")
                yield Button("Back", id="btn-back", classes="btn btn-secondary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.action_back()
            return
        if event.button.id != "btn-save":
            return

        folder = self.query_one("#save-input", Input).value.strip() or "~/transcripts"
        use_here_default = self.query_one("#here-default-switch", Switch).value
        open_app = self.query_one("#open-app-input", Input).value.strip() or "cursor"

        if not _is_safe_save_folder(folder):
            self.notify("Invalid save folder path.", severity="error")
            return

        cfg = load_config()
        cfg["save_folder"] = folder
        cfg["record_here_by_default"] = bool(use_here_default)
        cfg["open_transcript_app"] = open_app
        save_config(cfg)
        self.notify("Transcript settings saved.")
        self.action_back()
