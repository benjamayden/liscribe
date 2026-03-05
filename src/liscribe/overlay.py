"""Floating always-on-top recording overlay for macOS dictation mode.

Displays a small panel near the cursor while recording is active.
Requires PyObjC (AppKit, Foundation, Quartz) — already installed on macOS with liscribe.

Architecture
------------
DictationOverlay        Plain Python wrapper. Called from background threads.
_OverlayController      NSObject subclass. Owns the NSPanel + NSTimer.
                        All AppKit calls happen on the main thread via mainQueue.

Usage (called by DictationDaemon)
------
    overlay = DictationOverlay()
    overlay.show(recorder, "Right Option")   # called from background thread
    ...
    overlay.hide()                            # called from background thread
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liscribe.dictation import _DictationRecorder

logger = logging.getLogger(__name__)

_PANEL_WIDTH = 290
_PANEL_HEIGHT = 48
_TIMER_INTERVAL = 0.10  # seconds between display refreshes


def _dispatch_to_main(fn: Any) -> None:
    """Schedule fn() on the AppKit main queue (thread-safe)."""
    from Foundation import NSBlockOperation, NSOperationQueue
    op = NSBlockOperation.blockOperationWithBlock_(fn)
    NSOperationQueue.mainQueue().addOperation_(op)


class _OverlayController:
    """Thin wrapper around an NSPanel + NSTimer.

    Instantiated and used exclusively on the main thread via _dispatch_to_main.
    """

    def __init__(self) -> None:
        self._panel: Any = None
        self._label: Any = None
        self._timer: Any = None
        self._recorder: Any = None
        self._hotkey_display: str = ""

    def create(self, recorder: "_DictationRecorder", hotkey_display: str) -> None:
        """Create and show the NSPanel. Must be called on the main thread."""
        import AppKit
        import Quartz
        from Foundation import NSTimer

        self._recorder = recorder
        self._hotkey_display = hotkey_display

        # Get cursor position (Quartz coordinates: origin at bottom-left)
        try:
            event_ref = Quartz.CGEventCreate(None)
            loc = Quartz.CGEventGetLocation(event_ref)
            cursor_x, cursor_y = loc.x, loc.y
        except Exception:
            cursor_x, cursor_y = 400.0, 400.0

        # Convert from Quartz (bottom-left origin) to AppKit (also bottom-left on macOS)
        screen = AppKit.NSScreen.mainScreen().frame()
        screen_h = screen.size.height

        x = max(10.0, min(cursor_x - _PANEL_WIDTH / 2, screen.size.width - _PANEL_WIDTH - 10))
        # Place panel ~30px above cursor; Quartz and AppKit share the same Y origin on macOS
        y = max(10.0, min(cursor_y + 30, screen_h - _PANEL_HEIGHT - 10))

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(x, y, _PANEL_WIDTH, _PANEL_HEIGHT),
            (
                AppKit.NSWindowStyleMaskBorderless
                | AppKit.NSWindowStyleMaskNonactivatingPanel
            ),
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setOpaque_(False)
        panel.setAlphaValue_(0.90)
        panel.setBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.08, 0.08, 0.12, 0.92)
        )
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(False)

        # Content label — white text, no background
        content = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, _PANEL_WIDTH, _PANEL_HEIGHT)
        )
        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(10, 8, _PANEL_WIDTH - 20, _PANEL_HEIGHT - 14)
        )
        label.setStringValue_("\u25cf 00:00")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        # #FF9E64 — TUI accent orange
        label.setTextColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.619, 0.392, 1.0)
        )

        # Use system monospaced font for uniform waveform width
        font = AppKit.NSFont.monospacedSystemFontOfSize_weight_(13.0, AppKit.NSFontWeightRegular)
        label.setFont_(font)

        content.addSubview_(label)
        panel.setContentView_(content)

        self._panel = panel
        self._label = label

        panel.orderFront_(None)

        # NSTimer for live updates — fires on main run loop
        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            _TIMER_INTERVAL, True, self._tick
        )

    def _tick(self, timer: Any) -> None:
        """NSTimer callback — refresh elapsed time and waveform. Runs on main thread."""
        if self._recorder is None or self._label is None:
            return
        elapsed = self._recorder.elapsed
        mins, secs = divmod(int(elapsed), 60)
        wave = self._recorder.waveform.render(width=18)
        text = f"\u25cf {mins:02d}:{secs:02d}  {wave}  \u2014 tap {self._hotkey_display}"
        self._label.setStringValue_(text)

    def close(self) -> None:
        """Close the panel and invalidate the timer. Must be called on the main thread."""
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:
                pass
            self._timer = None

        if self._panel is not None:
            try:
                self._panel.close()
            except Exception:
                pass
            self._panel = None

        self._label = None
        self._recorder = None


class DictationOverlay:
    """Public API — called from background threads, dispatches to main thread."""

    def __init__(self) -> None:
        self._controller = _OverlayController()

    def show(self, recorder: "_DictationRecorder", hotkey_display: str) -> None:
        """Show the floating panel near the cursor. Safe to call from any thread."""
        ctrl = self._controller

        def _create() -> None:
            try:
                ctrl.create(recorder, hotkey_display)
            except Exception as exc:
                logger.warning("Overlay create failed: %s", exc)

        _dispatch_to_main(_create)

    def hide(self) -> None:
        """Close the floating panel. Safe to call from any thread."""
        ctrl = self._controller

        def _close() -> None:
            try:
                ctrl.close()
            except Exception as exc:
                logger.warning("Overlay close failed: %s", exc)

        _dispatch_to_main(_close)
