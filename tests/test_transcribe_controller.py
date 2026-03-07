"""Tests for TranscribeController — Transcribe workflow orchestration.

All tests use mocked services; no audio files or models are required.
Tests are written before implementation (TDD).
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from liscribe.controllers.transcribe_controller import (
    TranscribeController,
    TranscribeState,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_svc():
    svc = MagicMock()
    svc.save_folder = "~/transcripts"
    svc.scribe_models = ["base"]
    svc.open_transcript_app = "Cursor"
    return svc


@pytest.fixture()
def model_svc():
    svc = MagicMock()
    svc.list_models.return_value = [
        {"name": "base", "is_downloaded": True, "size_label": "~145 MB"},
        {"name": "small", "is_downloaded": True, "size_label": "~465 MB"},
    ]
    svc.is_downloaded.side_effect = lambda m: m in ("base", "small")
    result = MagicMock()
    result.text = "Hello world"
    svc.transcribe.return_value = result
    svc.save_transcript.return_value = Path("/tmp/out_base.md")
    return svc


@pytest.fixture()
def controller(config_svc, model_svc):
    return TranscribeController(config=config_svc, model=model_svc)


# ---------------------------------------------------------------------------
# Constants / validation
# ---------------------------------------------------------------------------


class TestAllowedExtensions:
    def test_wav_accepted(self, controller):
        controller.set_audio_path("/tmp/recording.wav")
        assert controller.audio_path == "/tmp/recording.wav"

    def test_mp3_accepted(self, controller):
        controller.set_audio_path("/tmp/recording.mp3")
        assert controller.audio_path == "/tmp/recording.mp3"

    def test_m4a_accepted(self, controller):
        controller.set_audio_path("/tmp/recording.m4a")
        assert controller.audio_path == "/tmp/recording.m4a"

    def test_rejects_txt(self, controller):
        with pytest.raises(ValueError) as exc_info:
            controller.set_audio_path("/tmp/notes.txt")
        assert "extension" in str(exc_info.value).lower() or "allowed" in str(exc_info.value).lower()

    def test_rejects_mp4(self, controller):
        with pytest.raises(ValueError):
            controller.set_audio_path("/tmp/video.mp4")

    def test_rejects_extension_only(self, controller):
        with pytest.raises(ValueError):
            controller.set_audio_path(".wav")

    def test_case_insensitive_extension(self, controller):
        controller.set_audio_path("/tmp/rec.WAV")
        assert controller.audio_path == "/tmp/rec.WAV"


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_state_is_idle(self, controller):
        assert controller.state == TranscribeState.IDLE

    def test_audio_path_none(self, controller):
        assert controller.audio_path is None

    def test_output_folder_defaults_to_config(self, controller, config_svc):
        assert controller.output_folder == config_svc.save_folder

    def test_selected_models_from_config(self, controller, config_svc):
        assert controller.selected_models == list(config_svc.scribe_models)


# ---------------------------------------------------------------------------
# set_output_folder / set_models / prefill
# ---------------------------------------------------------------------------


class TestSessionConfig:
    def test_set_output_folder(self, controller):
        controller.set_output_folder("/custom/output")
        assert controller.output_folder == "/custom/output"

    def test_set_models(self, controller):
        controller.set_models(["base", "small"])
        assert controller.selected_models == ["base", "small"]

    def test_set_prefill_stores_values(self, controller):
        controller.set_prefill(audio_path="/tmp/x.wav", output_folder="/out")
        prefill = controller.get_prefill()
        assert prefill.get("audio_path") == "/tmp/x.wav"
        assert prefill.get("output_folder") == "/out"

    def test_get_prefill_consumes_prefill(self, controller):
        controller.set_prefill(audio_path="/tmp/x.wav", output_folder="/out")
        first = controller.get_prefill()
        second = controller.get_prefill()
        assert first.get("audio_path") == "/tmp/x.wav"
        assert second.get("audio_path") == "" or second.get("audio_path") is None

    def test_get_prefill_without_set_returns_empty_or_defaults(self, controller, config_svc):
        prefill = controller.get_prefill()
        assert "audio_path" in prefill or prefill.get("audio_path") is None or prefill.get("audio_path") == ""


# ---------------------------------------------------------------------------
# start_transcribe()
# ---------------------------------------------------------------------------


class TestStartTranscribe:
    def test_raises_when_no_audio_path(self, controller):
        controller.set_models(["base"])
        with pytest.raises(RuntimeError) as exc_info:
            controller.start_transcribe()
        assert "audio" in str(exc_info.value).lower() or "file" in str(exc_info.value).lower()

    def test_raises_when_no_downloaded_models_selected(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["large"])
        model_svc.is_downloaded.return_value = False
        with pytest.raises(RuntimeError) as exc_info:
            controller.start_transcribe()
        assert "model" in str(exc_info.value).lower() or "download" in str(exc_info.value).lower()

    def test_transitions_to_transcribing(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base"])
        with patch.object(controller, "_run_transcription"):
            controller.start_transcribe()
        assert controller.state == TranscribeState.TRANSCRIBING

    def test_returns_initial_progress_immediately(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base"])
        with patch.object(controller, "_run_transcription"):
            controller.start_transcribe()
        progress = controller.get_progress()
        assert len(progress) == 1
        assert progress[0]["model_name"] == "base"
        assert progress[0]["is_done"] is False

    def test_multiple_models_produce_multiple_progress_entries(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base", "small"])
        with patch.object(controller, "_run_transcription"):
            controller.start_transcribe()
        progress = controller.get_progress()
        assert len(progress) == 2
        names = [p["model_name"] for p in progress]
        assert "base" in names and "small" in names


# ---------------------------------------------------------------------------
# get_progress()
# ---------------------------------------------------------------------------


class TestGetProgress:
    def test_returns_empty_list_when_idle(self, controller):
        assert controller.get_progress() == []

    def test_returns_list_of_dicts_with_expected_keys(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base"])
        with patch.object(controller, "_run_transcription"):
            controller.start_transcribe()
        progress = controller.get_progress()
        assert len(progress) >= 1
        for p in progress:
            assert "model_name" in p
            assert "progress" in p
            assert "md_path" in p
            assert "error" in p
            assert "is_done" in p


# ---------------------------------------------------------------------------
# open_transcript()
# ---------------------------------------------------------------------------


class TestOpenTranscript:
    def test_delegates_to_config_open_transcript(self, controller, config_svc):
        controller.open_transcript("/tmp/out.md")
        config_svc.open_transcript.assert_called_once_with("/tmp/out.md")


# ---------------------------------------------------------------------------
# Full transcription run (integration-style with mocked model)
# ---------------------------------------------------------------------------


class TestRunTranscription:
    def test_calls_model_transcribe_and_save_per_model(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base"])
        controller.start_transcribe()
        # Wait for background thread to run
        for _ in range(50):
            if controller.state == TranscribeState.DONE:
                break
            threading.Event().wait(0.05)
        assert model_svc.transcribe.called
        assert model_svc.save_transcript.called

    def test_state_becomes_done_after_run(self, controller, model_svc):
        controller.set_audio_path("/tmp/x.wav")
        controller.set_models(["base"])
        controller.start_transcribe()
        for _ in range(100):
            if controller.state == TranscribeState.DONE:
                break
            threading.Event().wait(0.05)
        assert controller.state == TranscribeState.DONE
