"""Onboarding wizard controller.

Tracks current step, validates advance (permissions, model download),
and persists completion in config. No direct engine imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from liscribe.services import permissions_service as _perms

if TYPE_CHECKING:
    from liscribe.services.config_service import ConfigService
    from liscribe.services.model_service import ModelService

ONBOARDING_STEP_IDS = (
    "welcome",
    "permissions",
    "model_download",
    "blackhole",
    "practice_dictate",
    "practice_scribe",
    "practice_transcribe",
    "done",
)

NUM_STEPS = len(ONBOARDING_STEP_IDS)


class OnboardingController:
    """First-launch wizard state and validation.

    Receives config and model services. Uses permissions_service for
    step 2 (permissions). Advance validates before moving; back does not.
    """

    def __init__(
        self,
        config: ConfigService,
        model: ModelService,
    ) -> None:
        self._config = config
        self._model = model
        self._step_index = 0

    def get_step(self) -> dict[str, Any]:
        """Return current step index, id, and step-specific payload for the UI."""
        step_id = ONBOARDING_STEP_IDS[self._step_index]
        payload: dict[str, Any] = {
            "step_index": self._step_index,
            "step_id": step_id,
        }
        if step_id == "permissions":
            payload["permissions"] = _perms.get_all_permissions()
        elif step_id == "model_download":
            payload["models"] = self._model.list_models()
        return payload

    def advance(self) -> dict[str, Any]:
        """Validate current step and move to next. On step 7 (done), set onboarding_complete."""
        step_id = ONBOARDING_STEP_IDS[self._step_index]
        if step_id == "permissions":
            perms = _perms.get_all_permissions()
            if not all(perms.get(k) for k in ("microphone", "accessibility", "input_monitoring")):
                return {"ok": False, "error": "All permissions must be granted"}
        elif step_id == "model_download":
            if not any(self._model.is_downloaded(m["name"]) for m in self._model.list_models()):
                return {"ok": False, "error": "At least one model must be downloaded"}
        if self._step_index >= NUM_STEPS - 1:
            return {"ok": True}
        self._step_index += 1
        if ONBOARDING_STEP_IDS[self._step_index] == "done":
            self._config.set("onboarding_complete", True)
        return {"ok": True}

    def back(self) -> dict[str, Any]:
        """Move to previous step. No validation."""
        if self._step_index > 0:
            self._step_index -= 1
        return {"ok": True}

    def is_complete(self) -> bool:
        """Return True if onboarding has been completed (persisted in config)."""
        return bool(self._config.get("onboarding_complete", False))

    def get_sample_audio_path(self) -> Path:
        """Return path to the bundled sample WAV for Practice Transcribe step."""
        # Resolve from this file: .../controllers/onboarding_controller.py -> .../ui/assets/sample.wav
        base = Path(__file__).resolve().parent.parent
        return base / "ui" / "assets" / "sample.wav"

    def reset_for_replay(self) -> None:
        """Reset step to 0 so replay from Settings starts at welcome. Does not clear onboarding_complete."""
        self._step_index = 0

    def get_dictation_auto_enter(self) -> bool:
        """Return the dictation auto-enter setting (same as Settings → General)."""
        return self._config.dictation_auto_enter

    def set_dictation_auto_enter(self, value: bool) -> None:
        """Set the dictation auto-enter setting (persisted in config)."""
        self._config.dictation_auto_enter = value

    def get_open_transcript_app(self) -> str:
        """Return the app name used to open transcripts (same as Settings → General)."""
        v = self._config.open_transcript_app
        return v if v and v != "default" else ""

    def set_open_transcript_app(self, name: str) -> None:
        """Set the app name for opening transcripts (persisted in config)."""
        self._config.open_transcript_app = name or "default"
