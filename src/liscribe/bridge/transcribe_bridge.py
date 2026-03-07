"""Transcribe panel bridge.

Translates JS calls from transcribe.html into controller/service calls.
No business logic — translation layer only.

pywebview exposes every public method as a JS-callable function on
window.pywebview.api.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import webview

if TYPE_CHECKING:
    from liscribe.controllers.transcribe_controller import TranscribeController
    from liscribe.services.config_service import ConfigService
    from liscribe.services.model_service import ModelService

logger = logging.getLogger(__name__)

# File types for the native file picker (single description string)
TRANSCRIBE_FILE_TYPES = ("Audio files (*.wav;*.mp3;*.m4a)",)


class TranscribeBridge:
    """JS-callable API for the Transcribe panel.

    Receives controller and services as constructor arguments.
    Instantiated once in app.py. set_window() is called after the
    panel window is created so pick_file/pick_folder can use native dialogs.
    """

    def __init__(
        self,
        controller: TranscribeController,
        model: ModelService,
        config: ConfigService,
    ) -> None:
        self._controller = controller
        self._model = model
        self._config = config
        self._window = None

    def set_window(self, window) -> None:
        """Set the pywebview window for file/folder dialogs. Called by app after create_window."""
        self._window = window

    # ------------------------------------------------------------------
    # Initial state (prefill from Scribe "Open in Transcribe")
    # ------------------------------------------------------------------

    def get_initial_state(self) -> dict:
        """Return prefill for the panel (audio_path, output_folder). Consumed on first call."""
        return self._controller.get_prefill()

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------

    def get_models(self) -> list[dict]:
        """Return all models with download status and selection flag.

        Uses list_models() so the Transcribe panel shows download status and
        disables checkboxes for unavailable models, matching the Scribe flow.
        """
        selected = set(self._controller.selected_models)
        return [
            {**m, "is_selected": m["name"] in selected}
            for m in self._model.list_models()
        ]

    # ------------------------------------------------------------------
    # Session config
    # ------------------------------------------------------------------

    def set_audio_path(self, path: str) -> dict:
        """Set the audio file path. Validates extension. Returns {ok: True} or {ok: False, error: str}."""
        if not path or not path.strip():
            self._controller.set_audio_path(None)
            return {"ok": True}
        try:
            self._controller.set_audio_path(path.strip())
            return {"ok": True}
        except ValueError as exc:
            logger.warning("set_audio_path rejected: %s", exc)
            return {"ok": False, "error": str(exc)}

    def set_output_folder(self, path: str) -> None:
        self._controller.set_output_folder(path or "")

    def set_models(self, model_names: list[str]) -> None:
        self._controller.set_models(model_names or [])

    # ------------------------------------------------------------------
    # File / folder pickers
    # ------------------------------------------------------------------

    def pick_file(self) -> str | None:
        """Open native file picker for audio files. Returns path or None if cancelled."""
        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=TRANSCRIBE_FILE_TYPES,
            )
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception as exc:
            logger.warning("pick_file failed: %s", exc)
            return None

    def pick_folder(self) -> str | None:
        """Open native folder picker. Returns path or None if cancelled."""
        if self._window is None:
            return None
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER,
                allow_multiple=False,
            )
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception as exc:
            logger.warning("pick_folder failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Transcribe
    # ------------------------------------------------------------------

    def transcribe(self) -> dict:
        """Start transcription. Returns {ok: True} or {ok: False, error: str}."""
        try:
            self._controller.start_transcribe()
            return {"ok": True}
        except RuntimeError as exc:
            logger.warning("transcribe failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_progress(self) -> list[dict]:
        """Return per-model progress for the current run."""
        return self._controller.get_progress()

    # ------------------------------------------------------------------
    # Open transcript in external app
    # ------------------------------------------------------------------

    def open_transcript(self, file_path: str) -> None:
        """Open the transcript file with the app set in Settings."""
        self._controller.open_transcript(file_path)
