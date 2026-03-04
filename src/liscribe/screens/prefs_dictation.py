"""Preferences — Dictation: model, hotkey, system sounds.

All settings save immediately on interaction and confirm with a notify() toast.
There is no separate Save button — this matches the prefs_whisper.py pattern
where button-style selections take effect instantly.
"""

from __future__ import annotations

import logging

from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Button, Static, Switch

from liscribe.config import load_config, save_config
from liscribe.dictation import VALID_HOTKEYS
from liscribe.screens.base import BackScreen
from liscribe.screens.top_bar import TopBar
from liscribe.transcriber import WHISPER_MODEL_ORDER, is_model_available

logger = logging.getLogger(__name__)

# Ordered tuple of (config-id, display-label) derived from the single source of truth
_HOTKEY_ITEMS: tuple[tuple[str, str], ...] = tuple(VALID_HOTKEYS.items())


class PrefsDictationScreen(BackScreen):
    """Dictation settings: model, hotkey, sounds.

    All changes take effect immediately (no Save step).
    """

    def compose(self):
        cfg = load_config()
        current_model = cfg.get("dictation_model", "base")
        current_hotkey = cfg.get("dictation_hotkey", "right_ctrl")
        sounds_on = bool(cfg.get("dictation_sounds", True))

        with Vertical(classes="screen-frame"):
            yield TopBar(variant="compact", section="Dictation")
            with ScrollableContainer(classes="scroll-fill"):
                yield Static("")

                # ── Model ──────────────────────────────────────────────
                with Horizontal(classes="strip"):
                    yield Static("", classes="model-col-mark")
                    yield Static("Model", classes="model-col-model")
                    yield Static("Status", classes="model-col-action")
                with Vertical(id="dictation-model-list", classes="hug-container"):
                    pass  # populated in on_mount

                yield Static("")

                # ── Hotkey ─────────────────────────────────────────────
                hotkey_desc = Static(
                    "  Double-tap to start \u00b7 single tap to stop",
                    id="hotkey-label",
                )
                hotkey_desc.border_title = "Hotkey"
                hotkey_desc.border_subtitle = (
                    "right_ctrl / left_ctrl / right_shift / caps_lock"
                )
                with Horizontal(classes="top-container"):
                    yield hotkey_desc

                yield Static("")
                with Horizontal(id="hotkey-row", classes="hug-container"):
                    for key_id, key_label in _HOTKEY_ITEMS:
                        variant = (
                            "btn-primary" if key_id == current_hotkey else "btn-secondary"
                        )
                        yield Button(
                            key_label,
                            id=f"hotkey-{key_id}",
                            classes=f"btn {variant} btn-inline",
                        )

                yield Static("")

                # ── Sounds ─────────────────────────────────────────────
                sounds_switch = Switch(
                    value=sounds_on, id="sounds-switch", classes="switch-input"
                )
                sounds_switch.border_title = "System sounds"
                sounds_switch.border_subtitle = (
                    "Play macOS sounds on start, stop, and paste"
                )
                with Horizontal(classes="top-container"):
                    yield sounds_switch

                yield Static("")

            with Horizontal(classes="footer-container"):
                yield Static("", classes="spacer-x")
                yield Button(
                    "Back", id="btn-back", classes="btn btn-secondary btn-inline fixed-width"
                )

    def on_mount(self) -> None:
        self._refresh_models()

    def _refresh_models(self) -> None:
        cfg = load_config()
        current = cfg.get("dictation_model", "base")
        container = self.query_one("#dictation-model-list", Vertical)
        container.remove_children()

        for name in WHISPER_MODEL_ORDER:
            installed = is_model_available(name)
            is_current = name == current
            mark = "\u2713" if installed else "\u2718"
            heart = " \u2665\ufe0e" if is_current else ""

            if installed:
                name_ctrl: Static | Button = Button(
                    f"{name}{heart}",
                    id=f"dict-set-{name}",
                    classes="btn btn-secondary btn-inline model-col-model",
                )
            else:
                name_ctrl = Static(name, classes="model-col-model model-name-static")

            row = Horizontal(
                Static(mark, classes="model-col-mark"),
                name_ctrl,
                Static(
                    "installed" if installed else "not installed",
                    classes="model-col-action",
                ),
                classes="strip",
            )
            container.mount(row)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Auto-save sounds toggle on change."""
        if event.switch.id != "sounds-switch":
            return
        cfg = load_config()
        cfg["dictation_sounds"] = bool(event.value)
        save_config(cfg)
        label = "on" if event.value else "off"
        self.notify(f"Sounds {label}.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        if bid == "btn-back":
            self.action_back()
            return

        # Model selection — save immediately
        if bid and bid.startswith("dict-set-"):
            model = bid.replace("dict-set-", "")
            if not is_model_available(model):
                self.notify(f"Model {model} is not installed.", severity="error")
                return
            cfg = load_config()
            cfg["dictation_model"] = model
            save_config(cfg)
            self.notify(f"Dictation model set to {model}.")
            self._refresh_models()
            return

        # Hotkey selection — save immediately
        if bid and bid.startswith("hotkey-"):
            hotkey = bid.replace("hotkey-", "")
            if hotkey not in VALID_HOTKEYS:
                logger.warning("Unknown hotkey id pressed: %r", hotkey)
                return
            cfg = load_config()
            cfg["dictation_hotkey"] = hotkey
            save_config(cfg)
            self.notify(f"Hotkey set to {VALID_HOTKEYS[hotkey]}.")
            self._update_hotkey_buttons(hotkey)
            return

    def _update_hotkey_buttons(self, active_hotkey: str) -> None:
        """Update button styles to reflect the newly selected hotkey."""
        for key_id, _ in _HOTKEY_ITEMS:
            try:
                btn = self.query_one(f"#hotkey-{key_id}", Button)
            except Exception as exc:
                logger.debug("Could not find hotkey button %r: %s", key_id, exc)
                continue
            if key_id == active_hotkey:
                btn.remove_class("btn-secondary")
                btn.add_class("btn-primary")
            else:
                btn.remove_class("btn-primary")
                btn.add_class("btn-secondary")
