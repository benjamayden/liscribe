"""Global hotkey listener using macOS NSEvent monitors.

Replaces the previous pynput CGEventTap-based approach. pynput creates a
kCGSessionEventTap with kCGHeadInsertEventTap that, even in ListenOnly mode,
runs synchronously in the keyboard event delivery chain — every modifier key
change or keypress system-wide must wait for the Python callback to return.
Two such taps (one for Scribe, one for Dictate) made the whole system feel
sluggish and caused focus "stickiness".

NSEvent.addGlobalMonitorForEventsMatchingMask_handler_ is documented as
asynchronous: events are delivered to the target app first, then the
callback is invoked. It does not add latency to event delivery.

Fires registered callbacks for:
  - on_scribe              : ⌃⌥L (or configured combo) — opens Scribe panel
  - on_dictate_toggle      : double-tap — start toggle recording
  - on_dictate_hold_start  : key held past threshold — start hold recording
  - on_dictate_hold_end    : held key released — stop hold recording
  - on_dictate_single_release : press + release while in toggle recording — stop toggle

Both a global monitor (events going to other apps) and a local monitor
(events going to Liscribe's own windows) are installed so hotkeys work
regardless of which app has focus.

Monitors must be added and removed on the main thread. The service schedules
setup/teardown via AppHelper.callAfter.

Dictate key state machine
─────────────────────────
Same three-phase design as before. NSEvent callbacks run on the main thread;
threading.Timer callbacks run on a background thread. A lock guards all shared
state machine variables.

Phase A — IDLE / AFTER FIRST TAP
  Press  → start hold timer.
  Release before hold timer fires (quick tap):
    If this was the first tap → set _after_first_tap, wait for second press.
    If _after_first_tap was set → second quick tap → fire on_dictate_toggle.
  Hold timer fires (no release):
    If _after_first_tap → tap-then-hold → fire on_dictate_hold_start (Phase C).
    Otherwise (long first press from idle) → ignore.

Phase B — TOGGLE RECORDING
  Press  → set _expect_release_to_stop.
  Release → fire on_dictate_single_release; clear flag.

Phase C — HOLD RECORDING
  Press  → ignore (key repeat while held).
  Release → fire on_dictate_hold_end; back to IDLE.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from liscribe.services.config_service import ConfigService

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# NSEvent constants (stable macOS SDK values; no import required)
# ──────────────────────────────────────────────────────────────

# NSEventModifierFlag bitmasks
_FLAG_CAPS_LOCK = 1 << 16   # NSEventModifierFlagCapsLock  0x10000
_FLAG_SHIFT     = 1 << 17   # NSEventModifierFlagShift     0x20000
_FLAG_CONTROL   = 1 << 18   # NSEventModifierFlagControl   0x40000
_FLAG_OPTION    = 1 << 19   # NSEventModifierFlagOption    0x80000
_FLAG_COMMAND   = 1 << 20   # NSEventModifierFlagCommand   0x100000
# Mask to strip device-dependent bits (side-specific raw flags etc.)
_FLAG_INDEPENDENT_MASK = 0xFFFF0000

# NSEventMask bitmasks for addGlobalMonitorForEventsMatchingMask_handler_
_MASK_KEY_DOWN      = 1 << 10   # NSEventMaskKeyDown
_MASK_FLAGS_CHANGED = 1 << 12   # NSEventMaskFlagsChanged

# Dictate key config name → NSEventModifierFlag bitmask.
# macOS standard modifier flags don't distinguish left vs right on most keys;
# this mirrors the previous pynput behaviour.
_DICTATE_KEY_TO_FLAG: dict[str, int] = {
    "left_ctrl":    _FLAG_CONTROL,
    "right_ctrl":   _FLAG_CONTROL,
    "right_option": _FLAG_OPTION,
    "right_shift":  _FLAG_SHIFT,
    "caps_lock":    _FLAG_CAPS_LOCK,
}

# Seconds the second press must be held to enter hold-recording mode.
_HOLD_THRESHOLD = 0.40


def _parse_hotkey_spec(spec: str) -> tuple[int, str] | None:
    """Parse a pynput-style hotkey spec like '<ctrl>+<alt>+l'.

    Returns (modifier_flags, key_char) or None on parse failure.
    modifier_flags is an OR of _FLAG_* constants; key_char is the bare letter.
    """
    _mod_map: dict[str, int] = {
        "ctrl":    _FLAG_CONTROL,
        "control": _FLAG_CONTROL,
        "alt":     _FLAG_OPTION,
        "option":  _FLAG_OPTION,
        "shift":   _FLAG_SHIFT,
        "cmd":     _FLAG_COMMAND,
        "command": _FLAG_COMMAND,
    }
    modifier_flags = 0
    key_char = ""
    for part in (p.strip() for p in spec.split("+")):
        if part.startswith("<") and part.endswith(">"):
            flag = _mod_map.get(part[1:-1].lower())
            if flag is not None:
                modifier_flags |= flag
        elif part:
            key_char = part.lower()
    return (modifier_flags, key_char) if key_char else None


class HotkeyService:
    """NSEvent-based global keyboard monitor.

    One instance, created in app.py and started once. Monitors are
    registered/removed on the main AppKit run loop thread.
    """

    def __init__(self, config: ConfigService) -> None:
        self._config = config

        # Callbacks wired from app.py
        self._on_scribe: Callable[[], None] = lambda: None
        self._on_dictate_toggle: Callable[[], None] = lambda: None
        self._on_dictate_hold_start: Callable[[], None] = lambda: None
        self._on_dictate_hold_end: Callable[[], None] = lambda: None
        self._on_dictate_single_release: Callable[[], None] = lambda: None
        # Query injected from app: True when controller is in toggle-mode recording.
        self._get_is_toggle_recording: Callable[[], bool] = lambda: False

        # Live NSEvent monitor objects (list of opaque handles returned by AppKit).
        # Must only be mutated on the main thread.
        self._scribe_monitors: list = []
        self._dictate_monitors: list = []
        self._dictate_monitors_active: bool = False

        # Dictate key state machine — protected by _lock because NSEvent callbacks
        # run on the main thread while threading.Timer callbacks run on a
        # background thread.
        self._lock = threading.Lock()
        self._prev_flags: int = 0              # last observed NSEventModifierFlags
        self._after_first_tap: bool = False    # True after one quick tap; waiting for second
        self._hold_timer: threading.Timer | None = None
        self._in_hold_recording: bool = False
        self._expect_release_to_stop: bool = False

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    def start(
        self,
        on_scribe: Callable[[], None] | None = None,
        on_dictate_toggle: Callable[[], None] | None = None,
        on_dictate_hold_start: Callable[[], None] | None = None,
        on_dictate_hold_end: Callable[[], None] | None = None,
        on_dictate_single_release: Callable[[], None] | None = None,
        get_is_toggle_recording: Callable[[], bool] | None = None,
    ) -> None:
        """Register callbacks and schedule Scribe monitor setup on the main thread."""
        if on_scribe:
            self._on_scribe = on_scribe
        if on_dictate_toggle:
            self._on_dictate_toggle = on_dictate_toggle
        if on_dictate_hold_start:
            self._on_dictate_hold_start = on_dictate_hold_start
        if on_dictate_hold_end:
            self._on_dictate_hold_end = on_dictate_hold_end
        if on_dictate_single_release:
            self._on_dictate_single_release = on_dictate_single_release
        if get_is_toggle_recording:
            self._get_is_toggle_recording = get_is_toggle_recording

        # Schedule monitor creation for after the AppKit run loop starts.
        self._call_on_main(self._setup_scribe_monitors)

    def start_dictate_listener(self) -> None:
        """Install the Dictate key monitor if not already active.

        Safe to call multiple times. Called when the user first opens Dictate
        (or at startup if Input Monitoring is already granted) so the monitor
        is only added once permissions are confirmed.
        """
        if self._dictate_monitors_active:
            return
        self._dictate_monitors_active = True
        self._call_on_main(self._setup_dictate_monitors)

    def restart_scribe_listener(self) -> None:
        """Remove and reinstall the Scribe monitor with current config.

        Safe to call from a background thread; actual work runs on main thread.
        """
        self._call_on_main(self._restart_scribe_on_main)

    def restart_dictate_listener(self) -> None:
        """Remove and reinstall the Dictate monitor with current config.

        Safe to call from a background thread; actual work runs on main thread.
        """
        self._call_on_main(self._restart_dictate_on_main)

    def stop(self) -> None:
        """Remove all monitors. Safe to call from any thread."""
        self._call_on_main(self._stop_on_main)

    # ──────────────────────────────────────────────────────────────
    # Main-thread helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _call_on_main(fn: Callable[[], None]) -> None:
        """Schedule fn on the main AppKit run loop (safe from any thread)."""
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(fn)
        except Exception:
            logger.warning("Could not schedule hotkey setup on main thread", exc_info=True)

    def _restart_scribe_on_main(self) -> None:
        self._remove_monitors(self._scribe_monitors)
        self._scribe_monitors = []
        self._setup_scribe_monitors()

    def _restart_dictate_on_main(self) -> None:
        self._remove_monitors(self._dictate_monitors)
        self._dictate_monitors = []
        self._dictate_monitors_active = False
        self.start_dictate_listener()

    def _stop_on_main(self) -> None:
        self._remove_monitors(self._scribe_monitors)
        self._remove_monitors(self._dictate_monitors)
        self._scribe_monitors = []
        self._dictate_monitors = []
        self._dictate_monitors_active = False

    @staticmethod
    def _remove_monitors(monitors: list) -> None:
        """Remove a list of NSEvent monitor handles. Must be called on main thread."""
        try:
            import AppKit
        except ImportError:
            return
        for mon in monitors:
            if mon is not None:
                try:
                    AppKit.NSEvent.removeMonitor_(mon)
                except Exception:
                    logger.debug("Error removing NSEvent monitor", exc_info=True)

    # ──────────────────────────────────────────────────────────────
    # Scribe monitor (⌃⌥L or configured combo)
    # ──────────────────────────────────────────────────────────────

    def _setup_scribe_monitors(self) -> None:
        """Install NSEvent monitors for the Scribe hotkey. Must run on main thread."""
        try:
            import AppKit
        except ImportError:
            logger.warning("AppKit not available; Scribe hotkey disabled")
            return

        spec = self._config.get("launch_hotkey") or "<ctrl>+<alt>+l"
        parsed = _parse_hotkey_spec(spec)
        if parsed is None:
            logger.warning("Cannot parse Scribe hotkey %r; hotkey disabled", spec)
            return
        req_flags, key_char = parsed

        def _check(event: object) -> None:
            try:
                flags = int(event.modifierFlags()) & _FLAG_INDEPENDENT_MASK  # type: ignore[attr-defined]
                if (flags & req_flags) != req_flags:
                    return
                chars = event.charactersIgnoringModifiers()  # type: ignore[attr-defined]
                if chars and chars.lower() == key_char:
                    self._on_scribe()
            except Exception:
                logger.debug("Scribe hotkey handler error", exc_info=True)

        global_mon = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _MASK_KEY_DOWN, _check
        )
        # Local monitor: must return the event (optionally modified; we return unchanged).
        def _check_local(event: object) -> object:
            _check(event)
            return event

        local_mon = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            _MASK_KEY_DOWN, _check_local
        )
        self._scribe_monitors = [m for m in (global_mon, local_mon) if m is not None]

    # ──────────────────────────────────────────────────────────────
    # Dictate monitor (single modifier key)
    # ──────────────────────────────────────────────────────────────

    def _setup_dictate_monitors(self) -> None:
        """Install NSEvent monitors for the Dictate key. Must run on main thread."""
        try:
            import AppKit
        except ImportError:
            logger.warning("AppKit not available; Dictate key listener disabled")
            return

        flag = self._resolve_dictate_flag()
        if flag is None:
            return

        def _on_flags(event: object) -> None:
            try:
                current_flags = int(event.modifierFlags())  # type: ignore[attr-defined]
                current_bit = current_flags & flag
                with self._lock:
                    was_down = bool(self._prev_flags & flag)
                    self._prev_flags = (self._prev_flags & ~flag) | current_bit
                if not was_down and current_bit:
                    self._on_dictate_key_press()
                elif was_down and not current_bit:
                    self._on_dictate_key_release()
            except Exception:
                logger.debug("Dictate flags handler error", exc_info=True)

        global_mon = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            _MASK_FLAGS_CHANGED, _on_flags
        )

        def _on_flags_local(event: object) -> object:
            _on_flags(event)
            return event

        local_mon = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            _MASK_FLAGS_CHANGED, _on_flags_local
        )
        self._dictate_monitors = [m for m in (global_mon, local_mon) if m is not None]

    def _resolve_dictate_flag(self) -> int | None:
        hotkey_name = self._config.dictation_hotkey or "left_ctrl"
        flag = _DICTATE_KEY_TO_FLAG.get(hotkey_name)
        if flag is None:
            logger.warning("Unknown dictation_hotkey %r — listener not started", hotkey_name)
        return flag

    # ──────────────────────────────────────────────────────────────
    # Dictate key state machine (unchanged logic; added locking)
    # ──────────────────────────────────────────────────────────────

    def _on_dictate_key_press(self) -> None:
        """Handle a dictate key press event (called from NSEvent callback or Timer thread)."""
        with self._lock:
            if self._get_is_toggle_recording():
                self._expect_release_to_stop = True
                return
            if self._in_hold_recording:
                return
            if self._hold_timer is not None:
                return
            timer = threading.Timer(_HOLD_THRESHOLD, self._trigger_hold_mode)
            timer.daemon = True
            self._hold_timer = timer
        # Start outside lock — timer callback also acquires lock
        timer.start()

    def _trigger_hold_mode(self) -> None:
        """Hold timer fired: key has been held past threshold. Runs on Timer thread."""
        do_start = False
        with self._lock:
            self._hold_timer = None
            if self._after_first_tap:
                self._after_first_tap = False
                self._in_hold_recording = True
                do_start = True
            # else: long first press from idle → ignore
        if do_start:
            self._on_dictate_hold_start()

    def _on_dictate_key_release(self) -> None:
        """Handle a dictate key release event (called from NSEvent callback or Timer thread)."""
        do_single_release = False
        do_hold_end = False
        do_toggle = False
        do_first_tap = False

        with self._lock:
            if self._expect_release_to_stop:
                self._expect_release_to_stop = False
                do_single_release = True
            elif self._in_hold_recording:
                self._in_hold_recording = False
                do_hold_end = True
            elif self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None
                if self._after_first_tap:
                    self._after_first_tap = False
                    do_toggle = True
                else:
                    self._after_first_tap = True
                    do_first_tap = True

        if do_single_release:
            self._on_dictate_single_release()
        elif do_hold_end:
            self._on_dictate_hold_end()
        elif do_toggle:
            self._on_dictate_toggle()
        # do_first_tap: just waiting — no callback
