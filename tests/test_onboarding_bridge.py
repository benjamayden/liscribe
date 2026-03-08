"""Tests for OnboardingBridge — JS-to-Python call translation for onboarding panel.

Bridge delegates to controller and permissions; app callbacks for open_scribe,
open_transcribe_with_sample, on_onboarding_complete.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from liscribe.bridge.onboarding_bridge import OnboardingBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_svc():
    svc = MagicMock()
    svc.get.return_value = False
    return svc


@pytest.fixture()
def model_svc():
    svc = MagicMock()
    svc.list_models.return_value = [
        {"name": "base", "is_downloaded": True, "size_label": "~145 MB"},
    ]
    svc.is_downloaded.return_value = True
    return svc


@pytest.fixture()
def controller(config_svc, model_svc):
    from liscribe.controllers.onboarding_controller import OnboardingController
    return OnboardingController(config=config_svc, model=model_svc)


@pytest.fixture()
def on_open_scribe():
    return MagicMock()


@pytest.fixture()
def on_open_transcribe_with_sample():
    return MagicMock()


@pytest.fixture()
def on_onboarding_complete():
    return MagicMock()


@pytest.fixture()
def bridge(controller, on_open_scribe, on_open_transcribe_with_sample, on_onboarding_complete):
    return OnboardingBridge(
        controller=controller,
        on_open_scribe=on_open_scribe,
        on_open_transcribe_with_sample=on_open_transcribe_with_sample,
        on_onboarding_complete=on_onboarding_complete,
    )


# ---------------------------------------------------------------------------
# get_step / advance / back / is_complete
# ---------------------------------------------------------------------------


class TestDelegationToController:
    def test_get_step_returns_controller_step(self, bridge):
        step = bridge.get_step()
        assert step["step_index"] == 0
        assert step["step_id"] == "welcome"

    def test_advance_returns_controller_result(self, bridge):
        result = bridge.advance()
        assert "ok" in result
        assert result["ok"] is True

    def test_back_returns_controller_result(self, bridge):
        bridge.advance()  # move to step 1
        result = bridge.back()
        assert result["ok"] is True
        assert bridge.get_step()["step_index"] == 0

    def test_is_complete_returns_controller_value(self, bridge, config_svc):
        config_svc.get.return_value = True
        assert bridge.is_complete() is True
        config_svc.get.return_value = False
        assert bridge.is_complete() is False


# ---------------------------------------------------------------------------
# request_permission / check_permission
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_request_permission_calls_open_system_settings(self, bridge):
        with patch("liscribe.bridge.onboarding_bridge._perms") as mock_perms:
            bridge.request_permission("microphone")
            mock_perms.open_system_settings.assert_called_once_with("microphone")

    def test_check_permission_returns_bool_from_get_all_permissions(self, bridge):
        with patch(
            "liscribe.bridge.onboarding_bridge._perms.get_all_permissions",
            return_value={"microphone": True, "accessibility": False, "input_monitoring": True},
        ):
            assert bridge.check_permission("microphone") is True
            assert bridge.check_permission("accessibility") is False


# ---------------------------------------------------------------------------
# open_scribe / open_transcribe_with_sample callbacks
# ---------------------------------------------------------------------------


class TestAppCallbacks:
    def test_open_scribe_invokes_callback(self, bridge, on_open_scribe):
        bridge.open_scribe()
        on_open_scribe.assert_called_once()

    def test_open_transcribe_with_sample_invokes_callback(self, bridge, on_open_transcribe_with_sample):
        bridge.open_transcribe_with_sample()
        on_open_transcribe_with_sample.assert_called_once()


# ---------------------------------------------------------------------------
# get_sample_audio_path
# ---------------------------------------------------------------------------


class TestGetSampleAudioPath:
    def test_returns_string_path_ending_in_sample_wav(self, bridge):
        path = bridge.get_sample_audio_path()
        assert isinstance(path, str)
        assert path.endswith("sample.wav")
