"""Preferences — Dictation: model, hotkey, system sounds."""

from __future__ import annotations

from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Button, Static, Switch

from liscribe.config import load_config, save_config
from liscribe.screens.base import BackScreen
from liscribe.screens.top_bar import TopBar
from liscribe import transcriber as _transcriber

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

HOTKEYS = [
    ("right_ctrl", "Right Ctrl"),
    ("left_ctrl", "Left Ctrl"),
    ("right_shift", "Right Shift"),
    ("caps_lock", "Caps Lock"),
]


def _is_model_available(name: str) -> bool:
    return _transcriber.is_model_available(name)


class PrefsDictationScreen(BackScreen):
    """Dictation settings: model, hotkey, sounds."""

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
                hotkey_input = Static(
                    "  Double-tap to start · single tap to stop",
                    id="hotkey-label",
                )
                hotkey_input.border_title = "Hotkey"
                hotkey_input.border_subtitle = "right_ctrl / left_ctrl / right_shift / caps_lock"
                with Horizontal(classes="top-container"):
                    yield hotkey_input

                yield Static("")
                with Horizontal(id="hotkey-row", classes="hug-container"):
                    for key_id, key_label in HOTKEYS:
                        variant = "btn-primary" if key_id == current_hotkey else "btn-secondary"
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
                yield Button("Save", id="btn-save", classes="btn btn-primary")
                yield Static("", classes="spacer-x")
                yield Button("Back", id="btn-back", classes="btn btn-secondary")

    def on_mount(self) -> None:
        self._refresh_models()

    def _refresh_models(self) -> None:
        cfg = load_config()
        current = cfg.get("dictation_model", "base")
        container = self.query_one("#dictation-model-list", Vertical)
        container.remove_children()

        for name in WHISPER_MODELS:
            installed = _is_model_available(name)
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        if bid == "btn-back":
            self.action_back()
            return

        if bid == "btn-save":
            cfg = load_config()
            cfg["dictation_sounds"] = bool(
                self.query_one("#sounds-switch", Switch).value
            )
            save_config(cfg)
            self.notify("Dictation settings saved.")
            self.action_back()
            return

        # Model selection
        if bid and bid.startswith("dict-set-"):
            model = bid.replace("dict-set-", "")
            if not _is_model_available(model):
                self.notify(
                    f"Model {model} is not installed.", severity="error"
                )
                return
            cfg = load_config()
            cfg["dictation_model"] = model
            save_config(cfg)
            self.notify(f"Dictation model set to {model}.")
            self._refresh_models()
            return

        # Hotkey selection
        if bid and bid.startswith("hotkey-"):
            hotkey = bid.replace("hotkey-", "")
            cfg = load_config()
            cfg["dictation_hotkey"] = hotkey
            save_config(cfg)
            label = dict(HOTKEYS).get(hotkey, hotkey)
            self.notify(f"Hotkey set to {label}.")
            for key_id, _ in HOTKEYS:
                try:
                    btn = self.query_one(f"#hotkey-{key_id}", Button)
                    if key_id == hotkey:
                        btn.remove_class("btn-secondary")
                        btn.add_class("btn-primary")
                    else:
                        btn.remove_class("btn-primary")
                        btn.add_class("btn-secondary")
                except Exception:
                    pass
