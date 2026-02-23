"""Preferences — General: clipboard, alias, update/uninstall commands."""

from __future__ import annotations

from pathlib import Path

from textual.containers import Vertical, Horizontal
from textual.widgets import Button, Input, Static, Switch


from liscribe.config import load_config, save_config
from liscribe.screens.base import BackScreen
from liscribe.screens.top_bar import TopBar
from liscribe.shell_alias import get_shell_rc_path, update_shell_alias


class PrefsGeneralScreen(BackScreen):
    """General settings and maintenance commands."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._repo_root = Path(__file__).resolve().parents[3]

    def compose(self):
        cfg = load_config()
        alias = cfg.get("command_alias", "rec") or "rec"
        auto_clipboard = bool(cfg.get("auto_clipboard", True))

        with Vertical(classes="screen-frame"):
            yield TopBar(variant="compact", section="General")
            yield Static("")
            clipboard_switch = Switch(value=auto_clipboard, id="clipboard-switch", classes="switch-input")
            clipboard_switch.border_title = "Auto copy"
            clipboard_switch.border_subtitle = "Copy transcript to clipboard when transcription finishes"
            with Horizontal(classes="top-container"):
                yield clipboard_switch
            yield Static("")
            alias_input = Input(value=alias, id="alias-input", placeholder="rec")
            alias_input.border_title = "Command alias"
            alias_input.border_subtitle = f"Updates {get_shell_rc_path()}. Changes shell commands."
            with Horizontal(classes="top-container"):
                yield alias_input
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

        alias = self.query_one("#alias-input", Input).value.strip() or "rec"
        auto_clipboard = self.query_one("#clipboard-switch", Switch).value

        cfg = load_config()
        current_alias = cfg.get("command_alias", "rec") or "rec"
        current_clipboard = bool(cfg.get("auto_clipboard", True))
        if alias == current_alias and auto_clipboard == current_clipboard:
            self.notify("Nothing changed.")
            self.action_back()
            return

        cfg["command_alias"] = alias
        cfg["auto_clipboard"] = bool(auto_clipboard)
        save_config(cfg)
        if alias != current_alias:
            rc = update_shell_alias(alias)
            if rc is None:
                self.notify("General settings saved, but could not update shell rc.", severity="warning")
                self.action_back()
                return
        self.notify("General settings saved.")
        self.action_back()
