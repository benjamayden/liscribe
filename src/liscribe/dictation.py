"""Dictation daemon — system-wide keyboard-triggered mic recording and transcription.

Run `rec dictate` to start. Double-tap the configured hotkey to begin recording;
tap it once more to stop. The transcript is pasted at the cursor.

Flow: IDLE → (double-tap) → RECORDING → (single tap) → TRANSCRIBING → IDLE

Internal structure
------------------
Constants           Timing, sound names, hotkey map — single source of truth.
_DictationRecorder  Lightweight mic recorder — audio chunks + WaveformMonitor.
Free functions      _play_sound / _notify / _paste_text — independently callable.
DictationDaemon     State machine + listener lifecycle; calls the free functions.
"""

from __future__ import annotations

import fcntl
import logging
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
from rich.console import Console

from liscribe.config import load_config, CONFIG_DIR
from liscribe.dictation_launchd import resolve_rec_command
from liscribe.recorder import _save_private_wav, get_preferred_mic
from liscribe.waveform import WaveformMonitor

logger = logging.getLogger(__name__)
_console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Timing constants (seconds) — all named, all in one place
# ---------------------------------------------------------------------------

#: Window within which two key presses count as a double-tap.
DOUBLE_TAP_WINDOW: float = 0.35
#: Waveform display refresh interval.
_WAVEFORM_REFRESH: float = 0.10
#: How long to wait for the waveform thread to exit before giving up.
_WAVEFORM_JOIN_TIMEOUT: float = 0.5
#: Delay between writing to clipboard and simulating Cmd+V.
_CLIPBOARD_SETTLE: float = 0.15
#: Delay after Cmd+V before restoring the original clipboard.
_PASTE_LAND: float = 0.10

# ---------------------------------------------------------------------------
# Hotkey definitions — single source of truth for daemon AND prefs screen.
# prefs_dictation.py imports VALID_HOTKEYS from here; do not redefine it.
# ---------------------------------------------------------------------------

VALID_HOTKEYS: dict[str, str] = {
    "right_option": "Right Option",
    "right_ctrl":   "Right Ctrl",
    "left_ctrl":    "Left Ctrl",
    "right_shift":  "Right Shift",
    "caps_lock":    "Caps Lock",
}

# ---------------------------------------------------------------------------
# macOS tool paths — resolved once at import, not on every call
# ---------------------------------------------------------------------------

_AFPLAY: str | None = shutil.which("afplay")
_OSASCRIPT: str | None = shutil.which("osascript")

_SOUNDS: dict[str, str] = {
    "start": "Tink",
    "stop":  "Pop",
    "done":  "Glass",
    "error": "Basso",
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class _State(Enum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()


# ---------------------------------------------------------------------------
# Free functions — feedback side effects, testable independently of daemon
# ---------------------------------------------------------------------------


def _sanitize_applescript(text: str) -> str:
    """Remove control characters and escape for embedding in an AppleScript string."""
    clean = "".join(c for c in text if c.isprintable())
    # Escape backslash first, then double-quote
    return clean.replace("\\", "\\\\").replace('"', '\\"')


def _play_sound(event: str, *, enabled: bool = True) -> None:
    """Play a macOS system sound asynchronously. No-op when disabled or unavailable."""
    if not enabled or _AFPLAY is None:
        return
    sound_name = _SOUNDS.get(event, "Tink")
    try:
        subprocess.Popen(
            [_AFPLAY, f"/System/Library/Sounds/{sound_name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.debug("_play_sound failed (%s): %s", event, exc)


def _notify(title: str, body: str) -> None:
    """Show a macOS system notification. No-op if osascript is unavailable."""
    if _OSASCRIPT is None:
        return
    script = (
        f'display notification "{_sanitize_applescript(body)}" '
        f'with title "Liscribe \u2014 {_sanitize_applescript(title)}"'
    )
    try:
        subprocess.Popen(
            [_OSASCRIPT, "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.debug("_notify failed: %s", exc)


def _check_permissions() -> tuple[bool, bool]:
    """Return (has_input_monitoring, has_accessibility) by querying macOS APIs."""
    import ctypes

    has_input_monitoring = False
    has_accessibility = False
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGPreflightListenEventAccess.restype = ctypes.c_bool
        has_input_monitoring = bool(cg.CGPreflightListenEventAccess())
    except Exception:
        pass
    try:
        ax = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        has_accessibility = bool(ax.AXIsProcessTrusted())
    except Exception:
        pass
    return has_input_monitoring, has_accessibility


def _paste_text(text: str) -> None:
    """Copy *text* to clipboard and paste it into the frontmost application.

    Uses osascript (System Events) to send Cmd+V to whatever app is focused.
    Falls back to pynput Controller if osascript is unavailable.
    """
    import pyperclip

    pyperclip.copy(text)
    time.sleep(_CLIPBOARD_SETTLE)

    pasted = False
    if _OSASCRIPT is not None:
        try:
            result = subprocess.run(
                [
                    _OSASCRIPT, "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ],
                capture_output=True,
                timeout=3,
            )
            pasted = result.returncode == 0
        except Exception as exc:
            logger.debug("osascript paste failed: %s", exc)

    if not pasted:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.cmd):
            kb.press("v")
            kb.release("v")

    time.sleep(_PASTE_LAND)


# ---------------------------------------------------------------------------
# Lightweight mic recorder — no TUI, no SIGINT handler, no agent logging
# ---------------------------------------------------------------------------


class _DictationRecorder:
    """Capture mic audio for a single dictation utterance."""

    def __init__(self, device_idx: int | None, sample_rate: int, channels: int) -> None:
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
        """Open and start the sounddevice input stream."""
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

    def stop(self) -> None:
        """Stop and close the stream without saving. Safe to call multiple times."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.debug("Error stopping mic stream: %s", exc)
            finally:
                self._stream = None

    def stop_and_save(self, out_dir: Path) -> Path | None:
        """Stop stream and write captured audio to a WAV file. Returns path or None."""
        self.stop()
        with self._lock:
            if not self._chunks:
                return None
            audio = np.concatenate(self._chunks, axis=0).astype(np.float32)
            self._chunks.clear()

        wav_path = out_dir / f"dictation_{int(time.time())}.wav"
        _save_private_wav(wav_path, self._sample_rate, audio)
        return wav_path

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start_time if self._start_time else 0.0


# ---------------------------------------------------------------------------
# Daemon — state machine + listener lifecycle only
# ---------------------------------------------------------------------------


class DictationDaemon:
    """System-wide dictation daemon.

    Double-tap the hotkey to start recording. Tap once more to stop.
    The transcript is pasted at the cursor in whatever app is focused.

    Raises ``ValueError`` at construction time if *model_size* or *hotkey*
    are invalid — fail fast before any listener is started.
    """

    def __init__(
        self,
        model_size: str,
        hotkey: str,
        sounds: bool,
        overlay_enabled: bool = False,
    ) -> None:
        from liscribe.transcriber import WHISPER_MODEL_ORDER

        if model_size not in WHISPER_MODEL_ORDER:
            raise ValueError(
                f"Invalid dictation model {model_size!r}. "
                f"Valid: {', '.join(WHISPER_MODEL_ORDER)}"
            )
        if hotkey not in VALID_HOTKEYS:
            raise ValueError(
                f"Invalid hotkey {hotkey!r}. Valid: {', '.join(VALID_HOTKEYS)}"
            )

        self._model_size = model_size
        self._hotkey_key = hotkey
        self._sounds = sounds
        self._overlay_enabled = overlay_enabled

        # State machine — all mutations guarded by _state_lock
        self._state = _State.IDLE
        self._state_lock = threading.Lock()
        self._last_press: float = 0.0

        # Active session resources
        self._recorder: _DictationRecorder | None = None
        self._tmp_dir: Path | None = None
        self._waveform_stop = threading.Event()
        self._waveform_thread: threading.Thread | None = None

        # WhisperModel cached after first load — reused across dictations
        self._model: Any = None

        # Read stable config once at construction; re-read mic per-recording
        # (user may hot-plug a headset between dictations)
        cfg = load_config()
        self._sample_rate: int = int(cfg.get("sample_rate", 16000))
        self._channels: int = int(cfg.get("channels", 1))

        # pynput objects set in run() after deferred import
        self._target_key: Any = None
        self._listener: Any = None

        # Overlay (created lazily when overlay_enabled)
        self._overlay: Any = None

        # Menu bar (created in run())
        self._menubar: Any = None

        # Single-instance lock file (held while running with menu bar)
        self._dictate_lock_file: Any = None

        # State change listeners — callables that receive the new _State value
        self._state_listeners: list[Any] = []

    # ------------------------------------------------------------------
    # State listener helpers
    # ------------------------------------------------------------------

    def _notify_state(self, state: "_State") -> None:
        """Call all registered state listeners with the new state."""
        for fn in self._state_listeners:
            try:
                fn(state)
            except Exception as exc:
                logger.debug("State listener error: %s", exc)

    # ------------------------------------------------------------------
    # Public control methods (called from menu bar or external code)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Request daemon shutdown — safe to call from any thread."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        # Stop the AppKit run loop so _shutdown() runs and removes the status item
        if self._overlay_enabled:
            try:
                import AppKit
                app = AppKit.NSApplication.sharedApplication()
                app.stop_(None)
                event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    AppKit.NSEventTypeApplicationDefined,
                    AppKit.NSMakePoint(0, 0),
                    0, 0, 0, None, 0, 0, 0,
                )
                app.postEvent_atStart_(event, True)
            except Exception as exc:
                logger.debug("shutdown: NSApp stop failed: %s", exc)

    def _trigger_recording(self) -> None:
        """Start recording directly (bypasses double-tap) — called from menu bar."""
        with self._state_lock:
            if self._state != _State.IDLE:
                return
            self._state = _State.RECORDING
        self._notify_state(_State.RECORDING)
        threading.Thread(
            target=self._start_recording,
            daemon=True,
            name="dictation-record",
        ).start()

    def _trigger_stop(self) -> None:
        """Stop recording — called from menu bar."""
        with self._state_lock:
            if self._state != _State.RECORDING:
                return
            self._state = _State.TRANSCRIBING
        self._notify_state(_State.TRANSCRIBING)
        threading.Thread(
            target=self._stop_and_transcribe,
            daemon=True,
            name="dictation-transcribe",
        ).start()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @staticmethod
    def _set_process_name(name: str) -> None:
        """Rename this process so macOS Privacy dialogs show 'Liscribe' not 'python3.13'."""
        # setproctitle via ctypes — no new dependency, works on macOS
        try:
            import ctypes, ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            if hasattr(libc, "setprogname"):
                libc.setprogname(name.encode())
        except Exception:
            pass
        # PyObjC — updates the NSRunningApplication display name seen by System Settings
        try:
            from Foundation import NSProcessInfo
            NSProcessInfo.processInfo().setValue_forKey_(name, "processName")
        except Exception:
            pass

    def run(self) -> None:
        """Start the daemon — blocks until Ctrl+C."""
        self._set_process_name("Liscribe")
        try:
            from pynput import keyboard as _kb
        except ImportError:
            _console.print(
                "[red]pynput is not installed.[/red] "
                "Run: [bold]pip install pynput[/bold]"
            )
            sys.exit(1)

        pynput_key_map: dict[str, Any] = {
            "right_option": _kb.Key.alt_r,
            "right_ctrl":   _kb.Key.ctrl_r,
            "left_ctrl":    _kb.Key.ctrl_l,
            "right_shift":  _kb.Key.shift_r,
            "caps_lock":    _kb.Key.caps_lock,
        }
        self._target_key = pynput_key_map[self._hotkey_key]
        hotkey_display = VALID_HOTKEYS[self._hotkey_key]

        _console.print()
        _console.print("  [bold]Liscribe Dictation[/bold]")
        _console.print(
            f"  Model: [bold]{self._model_size}[/bold]  |  "
            f"Hotkey: [bold]{hotkey_display}[/bold]"
        )
        _console.print()
        _console.print(f"  Double-tap [bold]{hotkey_display}[/bold] to start recording.")
        _console.print(f"  Tap [bold]{hotkey_display}[/bold] once more to stop.")
        _console.print("  [dim]Ctrl+C to quit.[/dim]")
        has_input_mon, has_accessibility = _check_permissions()
        _console.print()
        if not has_input_mon or not has_accessibility:
            _console.print("  [red bold]Permission required:[/red bold]")
            if not has_input_mon:
                _console.print(
                    "  [red]\u2717[/red] [bold]Input Monitoring[/bold] — hotkey won't work"
                )
            if not has_accessibility:
                _console.print(
                    "  [red]\u2717[/red] [bold]Accessibility[/bold] — paste won't work"
                )
            _console.print(
                "  Open [bold]System Settings \u2192 Privacy & Security[/bold]"
            )
            _console.print(
                "  and add [bold]Terminal[/bold] (or [bold]Liscribe[/bold]) to each list."
            )
            _console.print()
        else:
            _console.print(
                "  [green]\u2713[/green] Input Monitoring + Accessibility granted."
            )
            _console.print()

        try:
            self._listener = _kb.Listener(on_press=self._on_key_press)
            self._listener.start()

            if self._overlay_enabled:
                # Single-instance: only one menu bar icon per user
                lock_path = CONFIG_DIR / "dictate.lock"
                try:
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    self._dictate_lock_file = open(lock_path, "w")
                    fcntl.flock(self._dictate_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    _console.print(
                        "[yellow]Another instance of Liscribe is already running.[/yellow]"
                    )
                    _console.print(
                        "  Quit it from its menu bar icon (Liscribe → Quit Liscribe)."
                    )
                    _console.print(
                        "  If you see an icon that doesn't respond, it may be from a crashed run—"
                    )
                    _console.print("  log out and back in to clear it.")
                    if self._dictate_lock_file is not None:
                        self._dictate_lock_file.close()
                        self._dictate_lock_file = None
                    sys.exit(0)
                # Overlay and menu bar both require the AppKit run loop on the main thread
                try:
                    from liscribe.overlay import DictationOverlay
                    from liscribe.menubar import DictationMenuBar
                    import AppKit
                    self._overlay = DictationOverlay()
                    self._menubar = DictationMenuBar(self)
                    self._state_listeners.append(self._menubar.update_state)
                    app = AppKit.NSApplication.sharedApplication()
                    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
                    self._menubar.setup()
                    app.run()
                except ImportError as exc:
                    logger.warning("Overlay unavailable (%s) — falling back to terminal mode", exc)
                    self._overlay_enabled = False
                    self._listener.join()
            else:
                self._listener.join()

        except KeyboardInterrupt:
            pass
        except Exception as exc:
            _console.print(f"\n[red]Could not start keyboard listener:[/red] {exc}")
            _console.print(
                "Grant [bold]Input Monitoring[/bold] and [bold]Accessibility[/bold] "
                "in System Settings \u2192 Privacy & Security, then re-run."
            )
            sys.exit(1)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        """Stop listener, release mic stream, join threads, remove temp files."""
        if self._dictate_lock_file is not None:
            try:
                fcntl.flock(self._dictate_lock_file.fileno(), fcntl.LOCK_UN)
                self._dictate_lock_file.close()
            except Exception:
                pass
            self._dictate_lock_file = None
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass

        if self._menubar is not None:
            try:
                self._menubar.remove_now()  # already on main thread in finally block
            except Exception as exc:
                logger.debug("Menubar remove failed: %s", exc)

        if self._overlay_enabled:
            try:
                import AppKit
                app = AppKit.NSApplication.sharedApplication()
                app.stop_(None)
                # Post a dummy event so the run loop wakes and exits
                event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    AppKit.NSEventTypeApplicationDefined,
                    AppKit.NSMakePoint(0, 0),
                    0, 0, 0, None, 0, 0, 0,
                )
                app.postEvent_atStart_(event, True)
            except Exception as exc:
                logger.debug("NSApp stop failed: %s", exc)

        # Pull recorder under lock so we don't race with _start_recording
        with self._state_lock:
            recorder = self._recorder
            self._recorder = None

        if recorder is not None:
            recorder.stop()  # releases the PortAudio stream

        self._waveform_stop.set()
        if self._waveform_thread is not None:
            self._waveform_thread.join(timeout=_WAVEFORM_JOIN_TIMEOUT)
            self._waveform_thread = None

        if self._tmp_dir is not None and self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None

    # ------------------------------------------------------------------
    # Key listener callback (runs on pynput's listener thread)
    # ------------------------------------------------------------------

    def _on_key_press(self, key: Any) -> None:
        if key != self._target_key:
            return

        now = time.monotonic()
        new_state: _State | None = None
        with self._state_lock:
            state = self._state
            if state == _State.IDLE:
                if now - self._last_press < DOUBLE_TAP_WINDOW:
                    self._last_press = 0.0
                    self._state = _State.RECORDING
                    new_state = _State.RECORDING
                    threading.Thread(
                        target=self._start_recording,
                        daemon=True,
                        name="dictation-record",
                    ).start()
                else:
                    self._last_press = now
            elif state == _State.RECORDING:
                self._state = _State.TRANSCRIBING
                new_state = _State.TRANSCRIBING
                threading.Thread(
                    target=self._stop_and_transcribe,
                    daemon=True,
                    name="dictation-transcribe",
                ).start()
            # TRANSCRIBING: ignore presses until the cycle completes
        if new_state is not None:
            self._notify_state(new_state)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_recording(self) -> None:
        # Re-read config each session so mic preference changes take effect immediately
        cfg = load_config()
        try:
            device_idx, used_fallback = get_preferred_mic(None, cfg)
        except ValueError as exc:
            logger.debug("Could not resolve mic: %s", exc)
            device_idx = None
            used_fallback = False
        if used_fallback:
            logger.debug("Saved default mic unavailable, falling back to system default")

        tmp_dir = Path(tempfile.mkdtemp(prefix="liscribe_dictation_"))
        recorder = _DictationRecorder(device_idx, self._sample_rate, self._channels)

        try:
            recorder.start()
        except Exception as exc:
            _console.print(f"\n[red]Could not open microphone:[/red] {exc}")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return

        # Commit session state under lock now that start() succeeded
        with self._state_lock:
            self._recorder = recorder
            self._tmp_dir = tmp_dir

        hotkey_display = VALID_HOTKEYS[self._hotkey_key]
        _play_sound("start", enabled=self._sounds)
        _notify("Recording", f"Tap {hotkey_display} to stop")
        _console.print(
            f"  [red bold]\u25cf[/red bold] Recording\u2026  "
            f"[dim](tap {hotkey_display} to stop)[/dim]"
        )

        if self._overlay_enabled and self._overlay is not None:
            self._overlay.show(recorder, hotkey_display)

        self._waveform_stop.clear()
        self._waveform_thread = threading.Thread(
            target=self._waveform_display_loop,
            args=(recorder,),
            daemon=True,
            name="dictation-waveform",
        )
        self._waveform_thread.start()

    def _waveform_display_loop(self, recorder: _DictationRecorder) -> None:
        """Render a live waveform in the terminal. Exits when _waveform_stop is set."""
        from rich.live import Live
        from rich.text import Text

        hotkey_display = VALID_HOTKEYS[self._hotkey_key]

        with Live(console=_console, refresh_per_second=10, transient=True) as live:
            # .wait() blocks for up to _WAVEFORM_REFRESH seconds, returns True when set
            while not self._waveform_stop.wait(timeout=_WAVEFORM_REFRESH):
                elapsed = recorder.elapsed
                mins, secs = divmod(int(elapsed), 60)
                wave = recorder.waveform.render(width=36)
                line = Text()
                line.append("  \u25cf ", style="red bold")
                line.append(f"{mins:02d}:{secs:02d}  ", style="bold")
                line.append(wave, style="cyan")
                line.append(f"  [tap {hotkey_display} to stop]", style="dim")
                live.update(line)

    # ------------------------------------------------------------------
    # Stop → transcribe → paste
    # ------------------------------------------------------------------

    def _stop_and_transcribe(self) -> None:
        # Retrieve and clear the recorder under lock
        with self._state_lock:
            recorder = self._recorder
            self._recorder = None
            tmp_dir = self._tmp_dir

        # Hide overlay before waveform thread exits
        if self._overlay_enabled and self._overlay is not None:
            self._overlay.hide()

        # Signal waveform thread and wait for a clean exit
        self._waveform_stop.set()
        if self._waveform_thread is not None:
            self._waveform_thread.join(timeout=_WAVEFORM_JOIN_TIMEOUT)
            self._waveform_thread = None

        if recorder is None:
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return

        _play_sound("stop", enabled=self._sounds)
        _notify("Transcribing\u2026", f"Model: {self._model_size}")
        _console.print(
            f"  [dim]Transcribing with [bold]{self._model_size}[/bold]\u2026[/dim]"
        )

        out_dir = tmp_dir or Path(tempfile.gettempdir())
        wav_path = recorder.stop_and_save(out_dir)

        if wav_path is None or not wav_path.exists():
            _console.print("  [yellow]No audio captured.[/yellow]")
            _play_sound("error", enabled=self._sounds)
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return

        try:
            text = self._transcribe(wav_path)
        except Exception as exc:
            logger.exception("Transcription failed")
            _console.print(f"  [red]Transcription error:[/red] {exc}")
            _play_sound("error", enabled=self._sounds)
            _notify("Error", str(exc)[:80])
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return
        finally:
            wav_path.unlink(missing_ok=True)

        if not text.strip():
            _console.print("  [dim]Nothing transcribed (silence or inaudible).[/dim]")
            _play_sound("error", enabled=self._sounds)
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return

        try:
            _paste_text(text)
        except Exception as exc:
            logger.exception("Paste failed")
            _console.print(f"  [red]Paste failed:[/red] {exc}")
            _console.print(f"  Text: {text[:120]}")
            _play_sound("error", enabled=self._sounds)
            with self._state_lock:
                self._state = _State.IDLE
            self._notify_state(_State.IDLE)
            return

        # Press Return after paste if auto-enter is enabled.
        # Re-read config here so the toggle takes effect without restarting the daemon.
        # Guard: skip if the text already ends with a newline to avoid a double blank line.
        if load_config().get("dictation_auto_enter", True) and not text.endswith("\n"):
            try:
                time.sleep(_PASTE_LAND)
                entered = False
                if _OSASCRIPT is not None:
                    try:
                        result = subprocess.run(
                            [
                                _OSASCRIPT, "-e",
                                'tell application "System Events" to key code 36',
                            ],
                            capture_output=True,
                            timeout=3,
                        )
                        entered = result.returncode == 0
                    except Exception:
                        pass
                if not entered:
                    from pynput.keyboard import Controller, Key
                    kb = Controller()
                    kb.press(Key.enter)
                    kb.release(Key.enter)
            except Exception as exc:
                logger.debug("Auto-enter failed: %s", exc)

        word_count = len(text.split())
        preview = text[:60] + ("\u2026" if len(text) > 60 else "")
        _console.print(
            f"  [green]\u2713[/green] {word_count} "
            f"word{'s' if word_count != 1 else ''}: [dim]{preview}[/dim]"
        )
        _play_sound("done", enabled=self._sounds)
        _notify("Pasted", f"{word_count} words: {preview}")

        with self._state_lock:
            self._state = _State.IDLE
        self._notify_state(_State.IDLE)

    def _transcribe(self, wav_path: Path) -> str:
        """Load (or reuse) the whisper model and return transcribed text."""
        from liscribe.transcriber import load_model, transcribe

        if self._model is None:
            _console.print(f"  [dim]Loading {self._model_size} model\u2026[/dim]")
            self._model = load_model(self._model_size)

        result = transcribe(wav_path, model=self._model, model_size=self._model_size)
        return result.text.strip()
