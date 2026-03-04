"""Dictation daemon — system-wide keyboard-triggered mic recording and transcription.

Run `rec dictate` to start. Double-tap the configured key to begin recording;
tap it once more to stop. The transcript is pasted at the cursor.

Flow: IDLE → (double-tap hotkey) → RECORDING → (single tap hotkey) → TRANSCRIBING → IDLE
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from enum import Enum, auto
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from rich.console import Console

from liscribe.config import load_config
from liscribe.waveform import WaveformMonitor

_console = Console(highlight=False)

DOUBLE_TAP_WINDOW = 0.35  # seconds — two presses within this window trigger start

_HOTKEY_DISPLAY = {
    "right_ctrl": "Right Ctrl",
    "left_ctrl": "Left Ctrl",
    "right_shift": "Right Shift",
    "caps_lock": "Caps Lock",
}

_SOUNDS = {
    "start": "Tink",
    "stop": "Pop",
    "done": "Glass",
    "error": "Basso",
}


class _State(Enum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()


class _DictationRecorder:
    """Lightweight mic-only recorder for a single dictation capture."""

    def __init__(self, device_idx: int | None, sample_rate: int, channels: int):
        self._device_idx = device_idx
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._start_time: float = 0.0
        self.waveform = WaveformMonitor()

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        chunk = indata.copy()
        with self._lock:
            self._chunks.append(chunk)
        self.waveform.push(chunk)

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._stream = sd.InputStream(
            device=self._device_idx,
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            callback=self._callback,
            blocksize=1024,
        )
        self._stream.start()

    def stop_and_save(self, out_dir: Path) -> Path | None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0).astype(np.float32)
            self._chunks.clear()

        wav_path = out_dir / f"dictation_{int(time.time())}.wav"
        audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        wavfile.write(str(wav_path), self._sample_rate, audio_int16)
        wav_path.chmod(0o600)
        return wav_path

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start_time if self._start_time else 0.0


class DictationDaemon:
    """System-wide dictation daemon.

    Double-tap the hotkey to start recording. Tap once more to stop.
    The transcript is pasted at the cursor position in the active app.
    """

    def __init__(self, model_size: str, hotkey: str, sounds: bool) -> None:
        self._model_size = model_size
        self._hotkey_key = hotkey
        self._sounds = sounds
        self._state = _State.IDLE
        self._state_lock = threading.Lock()
        self._last_press: float = 0.0
        self._recorder: _DictationRecorder | None = None
        self._waveform_stop = threading.Event()
        self._model: Any = None  # cached WhisperModel, loaded on first use
        self._tmp_dir: Path | None = None
        self._target_key: Any = None  # set in run()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the daemon — blocks until Ctrl+C."""
        try:
            from pynput import keyboard as _kb
        except ImportError:
            _console.print(
                "[red]pynput is not installed.[/red] Run: [bold]pip install pynput[/bold]"
            )
            sys.exit(1)

        key_map: dict[str, Any] = {
            "right_ctrl": _kb.Key.ctrl_r,
            "left_ctrl": _kb.Key.ctrl_l,
            "right_shift": _kb.Key.shift_r,
            "caps_lock": _kb.Key.caps_lock,
        }
        self._target_key = key_map.get(self._hotkey_key)
        if self._target_key is None:
            _console.print(
                f"[red]Unknown hotkey:[/red] {self._hotkey_key!r}. "
                f"Valid: {', '.join(key_map)}"
            )
            sys.exit(1)

        hotkey_display = _HOTKEY_DISPLAY.get(self._hotkey_key, self._hotkey_key)

        _console.print()
        _console.print("  [bold]Liscribe Dictation[/bold]")
        _console.print(
            f"  Model: [bold]{self._model_size}[/bold]  |  "
            f"Hotkey: [bold]{hotkey_display}[/bold]"
        )
        _console.print()
        _console.print(f"  Double-tap [bold]{hotkey_display}[/bold] to start recording.")
        _console.print(f"  Tap [bold]{hotkey_display}[/bold] once to stop.")
        _console.print("  [dim]Ctrl+C to quit.[/dim]")
        _console.print()
        _console.print(
            "  [yellow]Note:[/yellow] Requires [bold]Input Monitoring[/bold] + "
            "[bold]Accessibility[/bold] in"
        )
        _console.print(
            "  [dim]System Settings → Privacy & Security → Input Monitoring / Accessibility.[/dim]"
        )
        _console.print()

        try:
            listener = _kb.Listener(on_press=self._on_key_press)
            listener.start()
            listener.join()
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            _console.print(f"\n[red]Could not start keyboard listener:[/red] {exc}")
            _console.print(
                "Grant [bold]Input Monitoring[/bold] and [bold]Accessibility[/bold] permissions in"
            )
            _console.print("System Settings → Privacy & Security, then re-run.")
            sys.exit(1)
        finally:
            if self._tmp_dir is not None and self._tmp_dir.exists():
                shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Key listener callback (runs on pynput's listener thread)
    # ------------------------------------------------------------------

    def _on_key_press(self, key: Any) -> None:
        if key != self._target_key:
            return

        now = time.monotonic()
        with self._state_lock:
            state = self._state
            if state == _State.IDLE:
                if now - self._last_press < DOUBLE_TAP_WINDOW:
                    self._last_press = 0.0
                    self._state = _State.RECORDING
                    threading.Thread(
                        target=self._start_recording, daemon=True
                    ).start()
                else:
                    self._last_press = now
            elif state == _State.RECORDING:
                self._state = _State.TRANSCRIBING
                threading.Thread(
                    target=self._stop_and_transcribe, daemon=True
                ).start()
            # TRANSCRIBING: ignore presses until done

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        cfg = load_config()
        mic_arg = cfg.get("default_mic")
        sample_rate: int = int(cfg.get("sample_rate", 16000))
        channels: int = int(cfg.get("channels", 1))

        device_idx: int | None = None
        if mic_arg is not None:
            try:
                from liscribe.recorder import resolve_device
                device_idx = resolve_device(str(mic_arg))
            except Exception:
                pass  # fall back to system default

        self._tmp_dir = Path(tempfile.mkdtemp(prefix="liscribe_dictation_"))
        recorder = _DictationRecorder(device_idx, sample_rate, channels)

        try:
            recorder.start()
        except Exception as exc:
            _console.print(f"\n[red]Could not open microphone:[/red] {exc}")
            with self._state_lock:
                self._state = _State.IDLE
            return

        self._recorder = recorder
        self._play_sound("start")
        self._notify("Recording", "Tap hotkey to stop")

        hotkey_display = _HOTKEY_DISPLAY.get(self._hotkey_key, self._hotkey_key)
        _console.print(
            f"  [red bold]●[/red bold] Recording…  "
            f"[dim](tap {hotkey_display} to stop)[/dim]"
        )

        self._waveform_stop.clear()
        self._waveform_display_loop()

    def _waveform_display_loop(self) -> None:
        """Render a live waveform in the terminal while recording."""
        from rich.live import Live
        from rich.text import Text

        hotkey_display = _HOTKEY_DISPLAY.get(self._hotkey_key, self._hotkey_key)
        recorder = self._recorder

        with Live(console=_console, refresh_per_second=10, transient=True) as live:
            while not self._waveform_stop.is_set():
                if recorder is None:
                    break
                elapsed = recorder.elapsed
                mins, secs = divmod(int(elapsed), 60)
                wave = recorder.waveform.render(width=36)
                line = Text()
                line.append("  ● ", style="red bold")
                line.append(f"{mins:02d}:{secs:02d}  ", style="bold")
                line.append(wave, style="cyan")
                line.append(f"  [tap {hotkey_display} to stop]", style="dim")
                live.update(line)
                time.sleep(0.1)

    # ------------------------------------------------------------------
    # Stop + transcribe + paste
    # ------------------------------------------------------------------

    def _stop_and_transcribe(self) -> None:
        recorder = self._recorder
        self._waveform_stop.set()
        time.sleep(0.15)  # let waveform loop exit cleanly

        if recorder is None:
            with self._state_lock:
                self._state = _State.IDLE
            return

        self._play_sound("stop")
        self._notify("Transcribing\u2026", f"Model: {self._model_size}")
        _console.print(
            f"  [dim]Transcribing with [bold]{self._model_size}[/bold]\u2026[/dim]"
        )

        out_dir = self._tmp_dir or Path(tempfile.gettempdir())
        wav_path = recorder.stop_and_save(out_dir)
        self._recorder = None

        if wav_path is None or not wav_path.exists():
            _console.print("  [yellow]No audio captured.[/yellow]")
            self._play_sound("error")
            with self._state_lock:
                self._state = _State.IDLE
            return

        try:
            text = self._transcribe(wav_path)
        except Exception as exc:
            _console.print(f"  [red]Transcription error:[/red] {exc}")
            self._play_sound("error")
            self._notify("Error", str(exc)[:80])
            with self._state_lock:
                self._state = _State.IDLE
            return
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not text.strip():
            _console.print(
                "  [dim]Nothing transcribed (silence or inaudible).[/dim]"
            )
            self._play_sound("error")
            with self._state_lock:
                self._state = _State.IDLE
            return

        try:
            self._paste_text(text)
        except Exception as exc:
            _console.print(f"  [red]Paste failed:[/red] {exc}")
            _console.print(f"  Text: {text[:120]}")
            self._play_sound("error")
            with self._state_lock:
                self._state = _State.IDLE
            return

        word_count = len(text.split())
        preview = text[:60] + ("\u2026" if len(text) > 60 else "")
        _console.print(
            f"  [green]\u2713[/green] {word_count} "
            f"word{'s' if word_count != 1 else ''}: [dim]{preview}[/dim]"
        )
        self._play_sound("done")
        self._notify("Pasted", f"{word_count} words: {preview}")

        with self._state_lock:
            self._state = _State.IDLE

    def _transcribe(self, wav_path: Path) -> str:
        """Load (or reuse) the whisper model and transcribe wav_path."""
        from liscribe.transcriber import load_model, transcribe

        if self._model is None:
            _console.print(f"  [dim]Loading {self._model_size} model\u2026[/dim]")
            self._model = load_model(self._model_size)

        result = transcribe(wav_path, model=self._model, model_size=self._model_size)
        return result.text.strip()

    def _paste_text(self, text: str) -> None:
        """Copy text to clipboard then simulate Cmd+V to paste at cursor."""
        import pyperclip
        from pynput.keyboard import Controller, Key

        pyperclip.copy(text)
        time.sleep(0.15)  # give clipboard time to settle
        kb = Controller()
        with kb.pressed(Key.cmd):
            kb.press("v")
            kb.release("v")

    # ------------------------------------------------------------------
    # System sounds + notifications
    # ------------------------------------------------------------------

    def _play_sound(self, event: str) -> None:
        """Play a macOS system sound asynchronously. No-op if sounds=False."""
        if not self._sounds:
            return
        afplay = shutil.which("afplay")
        if afplay is None:
            return
        sound_name = _SOUNDS.get(event, "Tink")
        path = f"/System/Library/Sounds/{sound_name}.aiff"
        try:
            subprocess.Popen(
                [afplay, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _notify(self, title: str, body: str) -> None:
        """Show a macOS system notification. No-op if osascript is unavailable."""
        osascript = shutil.which("osascript")
        if osascript is None:
            return
        t = title.replace('"', '\\"')
        b = body.replace('"', '\\"')
        script = f'display notification "{b}" with title "Liscribe \u2014 {t}"'
        try:
            subprocess.Popen(
                [osascript, "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
