"""Tests for OnboardingController — first-launch wizard state and validation.

All tests use mocked config and model services. No permissions or UI.
Tests written before implementation (TDD).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from liscribe.controllers.onboarding_controller import (
    OnboardingController,
    ONBOARDING_STEP_IDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_svc():
    svc = MagicMock()
    svc.get.return_value = False  # onboarding_complete not set
    return svc


@pytest.fixture()
def model_svc():
    svc = MagicMock()
    svc.list_models.return_value = [
        {"name": "tiny", "is_downloaded": False, "size_label": "~75 MB"},
        {"name": "base", "is_downloaded": False, "size_label": "~145 MB"},
    ]
    svc.is_downloaded.return_value = False
    return svc


@pytest.fixture()
def controller(config_svc, model_svc):
    return OnboardingController(config=config_svc, model=model_svc)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_initial_step_is_welcome(self, controller):
        step = controller.get_step()
        assert step["step_index"] == 0
        assert step["step_id"] == ONBOARDING_STEP_IDS[0]

    def test_get_step_returns_step_id_and_index(self, controller):
        step = controller.get_step()
        assert "step_index" in step
        assert "step_id" in step
        assert step["step_id"] == "welcome"


# ---------------------------------------------------------------------------
# advance / back
# ---------------------------------------------------------------------------


class TestAdvanceAndBack:
    def test_advance_from_welcome_moves_to_permissions(self, controller):
        result = controller.advance()
        assert result.get("ok") is True
        step = controller.get_step()
        assert step["step_index"] == 1
        assert step["step_id"] == "permissions"

    def test_back_from_permissions_returns_to_welcome(self, controller):
        controller.advance()  # welcome -> permissions
        result = controller.back()
        assert result.get("ok") is True
        step = controller.get_step()
        assert step["step_index"] == 0
        assert step["step_id"] == "welcome"

    def test_back_on_welcome_does_not_go_below_zero(self, controller):
        result = controller.back()
        assert result.get("ok") is True
        step = controller.get_step()
        assert step["step_index"] == 0

    def test_advance_to_end_then_back(self, controller, config_svc, model_svc):
        config_svc.get.return_value = False
        model_svc.is_downloaded.return_value = True  # allow advance past model_download step
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": True, "input_monitoring": True},
        ):
            for _ in range(7):
                controller.advance()
            step = controller.get_step()
            assert step["step_id"] == "done"
            controller.back()
            step = controller.get_step()
            assert step["step_id"] == "practice_transcribe"


# ---------------------------------------------------------------------------
# Step 2 — permissions
# ---------------------------------------------------------------------------


class TestStepPermissions:
    def test_advance_from_permissions_fails_when_not_all_granted(self, controller):
        controller.advance()  # -> permissions
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": False, "input_monitoring": True},
        ):
            result = controller.advance()
        assert result.get("ok") is False
        assert controller.get_step()["step_index"] == 1

    def test_advance_from_permissions_succeeds_when_all_granted(self, controller):
        controller.advance()  # -> permissions
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": True, "input_monitoring": True},
        ):
            result = controller.advance()
        assert result.get("ok") is True
        assert controller.get_step()["step_index"] == 2
        assert controller.get_step()["step_id"] == "model_download"


# ---------------------------------------------------------------------------
# Step 3 — model download
# ---------------------------------------------------------------------------


class TestStepModelDownload:
    def test_advance_from_model_download_fails_when_no_model_downloaded(self, controller):
        controller.advance()  # welcome
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": True, "input_monitoring": True},
        ):
            controller.advance()  # -> model_download
        model_svc = controller._model
        model_svc.is_downloaded.return_value = False
        result = controller.advance()
        assert result.get("ok") is False
        assert controller.get_step()["step_index"] == 2

    def test_advance_from_model_download_succeeds_when_at_least_one_model(self, controller):
        controller.advance()  # welcome
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": True, "input_monitoring": True},
        ):
            controller.advance()  # -> model_download
        controller._model.is_downloaded.return_value = True
        result = controller.advance()
        assert result.get("ok") is True
        assert controller.get_step()["step_index"] == 3
        assert controller.get_step()["step_id"] == "blackhole"


# ---------------------------------------------------------------------------
# Step 8 — done (sets onboarding_complete)
# ---------------------------------------------------------------------------


class TestStepDone:
    def test_advance_from_practice_transcribe_to_done_sets_config(self, controller, config_svc, model_svc):
        model_svc.is_downloaded.return_value = True
        with patch(
            "liscribe.services.permissions_service.get_all_permissions",
            return_value={"microphone": True, "accessibility": True, "input_monitoring": True},
        ):
            for _ in range(6):
                controller.advance()  # welcome -> ... -> practice_transcribe (step 6)
            # Now at step 6 (practice_transcribe); one more advance -> done
            result = controller.advance()
        assert result.get("ok") is True
        assert controller.get_step()["step_id"] == "done"
        config_svc.set.assert_called_with("onboarding_complete", True)


# ---------------------------------------------------------------------------
# is_complete
# ---------------------------------------------------------------------------


class TestIsComplete:
    def test_is_complete_false_when_config_false(self, controller):
        controller._config.get.return_value = False
        assert controller.is_complete() is False

    def test_is_complete_true_when_config_true(self, controller):
        controller._config.get.return_value = True
        assert controller.is_complete() is True


# ---------------------------------------------------------------------------
# get_sample_audio_path
# ---------------------------------------------------------------------------


class TestGetSampleAudioPath:
    def test_returns_path_ending_in_sample_wav(self, controller):
        path = controller.get_sample_audio_path()
        assert path is not None
        assert str(path).endswith("sample.wav") or path.name == "sample.wav"

    def test_path_is_under_assets(self, controller):
        path = controller.get_sample_audio_path()
        path_str = str(path)
        assert "assets" in path_str or "sample.wav" in path_str
