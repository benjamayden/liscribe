"""Tests for TranscribeBridge — JS-to-Python call translation for Transcribe panel.

Bridge methods must delegate to controller/model; no business logic.
Return JSON-serialisable dicts; surface errors to the caller.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from liscribe.bridge.transcribe_bridge import TranscribeBridge
from liscribe.controllers.transcribe_controller import TranscribeState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def controller():
    ctrl = MagicMock()
    ctrl.state = TranscribeState.IDLE
    ctrl.audio_path = None
    ctrl.output_folder = "~/transcripts"
    ctrl.selected_models = ["base"]
    ctrl.get_prefill.return_value = {"audio_path": "", "output_folder": "~/transcripts"}
    ctrl.get_progress.return_value = []
    return ctrl


@pytest.fixture()
def model_svc():
    svc = MagicMock()
    svc.list_models.return_value = [
        {"name": "base", "is_downloaded": True, "size_label": "~145 MB"},
        {"name": "small", "is_downloaded": False, "size_label": "~465 MB"},
    ]
    return svc


@pytest.fixture()
def config_svc():
    svc = MagicMock()
    svc.save_folder = "~/transcripts"
    return svc


@pytest.fixture()
def bridge(controller, model_svc, config_svc):
    return TranscribeBridge(
        controller=controller,
        model=model_svc,
        config=config_svc,
    )


# ---------------------------------------------------------------------------
# get_initial_state()
# ---------------------------------------------------------------------------


class TestGetInitialState:
    def test_returns_controller_prefill(self, bridge, controller):
        controller.get_prefill.return_value = {
            "audio_path": "/tmp/from_scribe.wav",
            "output_folder": "/out",
        }
        result = bridge.get_initial_state()
        assert result["audio_path"] == "/tmp/from_scribe.wav"
        assert result["output_folder"] == "/out"

    def test_delegates_to_controller_get_prefill(self, bridge, controller):
        bridge.get_initial_state()
        controller.get_prefill.assert_called_once()


# ---------------------------------------------------------------------------
# get_models()
# ---------------------------------------------------------------------------


class TestGetModels:
    def test_delegates_to_model_list_models(self, bridge, model_svc):
        bridge.get_models()
        model_svc.list_models.assert_called_once()

    def test_adds_is_selected_from_controller(self, bridge, controller):
        controller.selected_models = ["base"]
        result = bridge.get_models()
        for entry in result:
            if entry["name"] == "base":
                assert entry["is_selected"] is True
            else:
                assert entry["is_selected"] is False

    def test_returns_list(self, bridge):
        result = bridge.get_models()
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# set_audio_path()
# ---------------------------------------------------------------------------


class TestSetAudioPath:
    def test_delegates_to_controller(self, bridge, controller):
        result = bridge.set_audio_path("/tmp/x.wav")
        controller.set_audio_path.assert_called_once_with("/tmp/x.wav")
        assert result.get("ok") is True

    def test_returns_ok_false_on_invalid_extension(self, bridge, controller):
        controller.set_audio_path.side_effect = ValueError("File type not allowed")
        result = bridge.set_audio_path("/tmp/x.txt")
        assert result.get("ok") is False
        assert "error" in result


# ---------------------------------------------------------------------------
# set_output_folder() / set_models()
# ---------------------------------------------------------------------------


class TestSetOutputFolderAndModels:
    def test_set_output_folder_calls_controller(self, bridge, controller):
        bridge.set_output_folder("/custom/out")
        controller.set_output_folder.assert_called_once_with("/custom/out")

    def test_set_models_calls_controller(self, bridge, controller):
        bridge.set_models(["base", "small"])
        controller.set_models.assert_called_once_with(["base", "small"])


# ---------------------------------------------------------------------------
# pick_file() / pick_folder()
# ---------------------------------------------------------------------------


class TestPickFileAndFolder:
    def test_pick_file_returns_none_when_no_window(self, bridge):
        result = bridge.pick_file()
        assert result is None

    def test_pick_file_returns_path_when_window_returns_path(self, bridge):
        mock_window = MagicMock()
        mock_window.create_file_dialog.return_value = ["/tmp/selected.wav"]
        bridge.set_window(mock_window)
        result = bridge.pick_file()
        assert result == "/tmp/selected.wav"

    def test_pick_file_returns_none_when_user_cancels(self, bridge):
        mock_window = MagicMock()
        mock_window.create_file_dialog.return_value = None
        bridge.set_window(mock_window)
        result = bridge.pick_file()
        assert result is None

    def test_pick_folder_returns_none_when_no_window(self, bridge):
        result = bridge.pick_folder()
        assert result is None

    def test_pick_folder_returns_path_when_window_returns_path(self, bridge):
        mock_window = MagicMock()
        mock_window.create_file_dialog.return_value = ["/tmp/selected_folder"]
        bridge.set_window(mock_window)
        result = bridge.pick_folder()
        assert result == "/tmp/selected_folder"


# ---------------------------------------------------------------------------
# transcribe()
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_calls_controller_start_transcribe(self, bridge, controller):
        result = bridge.transcribe()
        controller.start_transcribe.assert_called_once()
        assert result.get("ok") is True

    def test_returns_ok_false_on_runtime_error(self, bridge, controller):
        controller.start_transcribe.side_effect = RuntimeError("No audio file selected")
        result = bridge.transcribe()
        assert result.get("ok") is False
        assert "error" in result


# ---------------------------------------------------------------------------
# get_progress()
# ---------------------------------------------------------------------------


class TestGetProgress:
    def test_delegates_to_controller(self, bridge, controller):
        controller.get_progress.return_value = [
            {"model_name": "base", "progress": 0.5, "md_path": None, "error": None, "is_done": False},
        ]
        result = bridge.get_progress()
        controller.get_progress.assert_called_once()
        assert len(result) == 1
        assert result[0]["model_name"] == "base"


# ---------------------------------------------------------------------------
# open_transcript()
# ---------------------------------------------------------------------------


class TestOpenTranscript:
    def test_calls_controller_open_transcript(self, bridge, controller):
        bridge.open_transcript("/tmp/out_base.md")
        controller.open_transcript.assert_called_once_with("/tmp/out_base.md")
