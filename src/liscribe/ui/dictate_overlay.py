"""Native NSPanel overlay for the Dictate recording HUD.

Uses AppKit directly (no pywebview) for instant display with zero WKWebView
init overhead. The panel is borderless, non-activating, and always-on-top so
it never steals focus from the app the user is dictating into.

Public API:
    overlay = DictateOverlay()
    overlay.show(ctrl, hotkey_display="^", on_cancel=callback)  # main thread
    overlay.hide()                                                # main thread
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import objc
from Foundation import NSObject

if TYPE_CHECKING:
    from liscribe.controllers.dictate_controller import DictateController

logger = logging.getLogger(__name__)

_PANEL_W = 300
_PANEL_H = 48
_TICK_INTERVAL = 0.10
_BAR_CHARS = "▁▂▃▄▅▆▇█"
_N_BARS = 6

# NSWindowStyleMask constants (AppKit SDK values)
_STYLE_BORDERLESS = 0
_STYLE_NONACTIVATING_PANEL = 1 << 7   # NSWindowStyleMaskNonactivatingPanel = 128

# NSWindowCollectionBehavior constants
_BEHAVIOR_JOIN_ALL_SPACES = 1 << 0    # NSWindowCollectionBehaviorCanJoinAllSpaces
_BEHAVIOR_STATIONARY = 1 << 4         # NSWindowCollectionBehaviorStationary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_waveform(levels: list[float]) -> str:
    if not levels:
        return _BAR_CHARS[0] * _N_BARS
    # Normalize by peak so the display responds to relative dynamics,
    # not absolute amplitude (avoids all-spaces on quiet mics).
    peak = max(levels) if any(v > 0 for v in levels) else 1.0
    step = max(1, len(levels) // _N_BARS)
    chars = []
    for i in range(_N_BARS):
        level = levels[min(i * step, len(levels) - 1)] / peak
        idx = int(level * (len(_BAR_CHARS) - 1))
        chars.append(_BAR_CHARS[max(0, min(idx, len(_BAR_CHARS) - 1))])
    return "".join(chars)


def _format_elapsed(elapsed: float) -> str:
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    return f"{mins}:{secs:02d}"


def _ax_focused_element_frame(dictate_ctrl: object) -> tuple[float, float, float, float] | None:
    """Return (x, y, w, h) of the focused AX element in top-left screen coords, or None.

    Uses the Accessibility API (ApplicationServices) via ctypes. Returns None on
    any error so the caller can fall back to cursor positioning.
    """
    try:
        import ctypes
        import ctypes.util
        import AppKit
        import objc as _objc
        from Foundation import NSString

        bundle_id = getattr(dictate_ctrl, "_target_bundle_id", None)
        if not bundle_id:
            return None

        pid = None
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            if app.bundleIdentifier() == bundle_id:
                pid = app.processIdentifier()
                break
        if pid is None:
            return None

        as_path = ctypes.util.find_library("ApplicationServices")
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not as_path or not cf_path:
            return None

        ax = ctypes.cdll.LoadLibrary(as_path)
        cf = ctypes.cdll.LoadLibrary(cf_path)

        ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int32]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        ax.AXValueGetValue.restype = ctypes.c_bool
        ax.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        attr_focused = NSString.stringWithString_("AXFocusedUIElement")
        attr_frame = NSString.stringWithString_("AXFrame")

        app_elem = ax.AXUIElementCreateApplication(ctypes.c_int32(pid))
        if not app_elem:
            return None

        try:
            focused_out = ctypes.c_void_p()
            err = ax.AXUIElementCopyAttributeValue(
                ctypes.c_void_p(app_elem),
                ctypes.c_void_p(_objc.pyobjc_id(attr_focused)),
                ctypes.byref(focused_out),
            )
            if err != 0 or not focused_out.value:
                return None
            try:
                frame_out = ctypes.c_void_p()
                err = ax.AXUIElementCopyAttributeValue(
                    focused_out,
                    ctypes.c_void_p(_objc.pyobjc_id(attr_frame)),
                    ctypes.byref(frame_out),
                )
                if err != 0 or not frame_out.value:
                    return None
                try:
                    class _CGPoint(ctypes.Structure):
                        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
                    class _CGSize(ctypes.Structure):
                        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]
                    class _CGRect(ctypes.Structure):
                        _fields_ = [("origin", _CGPoint), ("size", _CGSize)]

                    rect = _CGRect()
                    # kAXValueCGRectType = 3
                    if not ax.AXValueGetValue(frame_out, 3, ctypes.byref(rect)):
                        return None
                    return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)
                finally:
                    cf.CFRelease(frame_out)
            finally:
                cf.CFRelease(focused_out)
        finally:
            cf.CFRelease(ctypes.c_void_p(app_elem))
    except Exception:
        logger.debug("_ax_focused_element_frame failed", exc_info=True)
        return None


def _panel_origin(panel_w: int, panel_h: int, dictate_ctrl: object) -> tuple[float, float]:
    """Return NSPanel origin (x, y) in Cocoa screen coords (bottom-left origin)."""
    import AppKit

    # Try AX focused element first.
    try:
        frame = _ax_focused_element_frame(dictate_ctrl)
        if frame is not None:
            fx, fy, fw, fh = frame
            # AX reports top-left origin; Cocoa screen uses bottom-left origin.
            screen = AppKit.NSScreen.mainScreen().frame()
            screen_h = float(screen.size.height)
            screen_w = float(screen.size.width)
            # Centre panel horizontally over the element, just above it.
            px = fx + (fw - panel_w) / 2
            py = screen_h - fy - panel_h - 8
            px = max(8.0, min(float(px), screen_w - panel_w - 8))
            py = max(8.0, min(py, screen_h - panel_h - 8))
            return px, py
    except Exception:
        logger.debug("AX panel positioning failed", exc_info=True)

    # Fall back to cursor position.
    try:
        mouse = AppKit.NSEvent.mouseLocation()
        screen = AppKit.NSScreen.mainScreen().frame()
        screen_h = float(screen.size.height)
        screen_w = float(screen.size.width)
        px = float(mouse.x) + 12
        py = float(mouse.y) + 12
        px = max(8.0, min(px, screen_w - panel_w - 8))
        py = max(8.0, min(py, screen_h - panel_h - 8))
        return px, py
    except Exception:
        return 100.0, 100.0


# ---------------------------------------------------------------------------
# NSObject controller — owns the NSPanel; runs only on main thread
# ---------------------------------------------------------------------------

class _OverlayController(NSObject):
    """Manages the NSPanel and NSTimer. All methods must be called on the main thread."""

    def init(self):
        self = objc.super(_OverlayController, self).init()
        if self is None:
            return None
        self._panel = None
        self._label = None
        self._btn = None
        self._timer = None
        self._tick_count = 0
        self._dictate_ctrl = None
        self._hotkey_display = "^"
        self._on_cancel = None
        return self

    @objc.python_method
    def setup(self, ctrl: object, hotkey: str, on_cancel: Callable[[], None]) -> None:
        self._dictate_ctrl = ctrl
        self._hotkey_display = hotkey or "^"
        self._on_cancel = on_cancel

    @objc.python_method
    def show(self) -> None:
        if self._panel is not None:
            self._panel.orderFront_(None)
            return
        self._build_panel()

    @objc.python_method
    def _build_panel(self) -> None:
        import AppKit

        px, py = _panel_origin(_PANEL_W, _PANEL_H, self._dictate_ctrl)

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(px, py, _PANEL_W, _PANEL_H),
            _STYLE_BORDERLESS | _STYLE_NONACTIVATING_PANEL,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        bg = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.11, 0.11, 0.14, 0.92)
        panel.setBackgroundColor_(bg)
        panel.setOpaque_(False)
        panel.setAlphaValue_(0.92)
        panel.setHasShadow_(True)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setHidesOnDeactivate_(False)
        panel.setIgnoresMouseEvents_(False)
        panel.setCollectionBehavior_(_BEHAVIOR_JOIN_ALL_SPACES | _BEHAVIOR_STATIONARY)

        content = panel.contentView()
        content.setWantsLayer_(True)
        layer = content.layer()
        if layer is not None:
            layer.setCornerRadius_(8.0)
            layer.setMasksToBounds_(True)
            try:
                layer.setBackgroundColor_(bg.CGColor())
                panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            except Exception:
                pass  # CGColor not available; plain dark bg is fine

        # Status label (left side, flexible width).
        label_w = _PANEL_W - 12 - 36
        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(12, 14, label_w, 20)
        )
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setTextColor_(AppKit.NSColor.whiteColor())
        try:
            label.setFont_(
                AppKit.NSFont.monospacedSystemFontOfSize_weight_(13.0, AppKit.NSFontWeightRegular)
            )
        except AttributeError:
            label.setFont_(AppKit.NSFont.userFixedPitchFontOfSize_(13.0))
        label.setStringValue_("● 0:00  ~~~~~~")
        content.addSubview_(label)

        # Cancel button (right side, fixed 26×26px).
        btn = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(_PANEL_W - 34, 10, 26, 26)
        )
        btn.setBordered_(False)
        close_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.9, 0.9, 0.9, 1.0)
        attrs = {
            AppKit.NSForegroundColorAttributeName: close_color,
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(18.0),
        }
        btn.setAttributedTitle_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_("×", attrs)
        )
        btn.setTarget_(self)
        btn.setAction_("cancelAction:")
        content.addSubview_(btn)

        panel.orderFront_(None)
        self._panel = panel
        self._label = label
        self._btn = btn
        self._tick_count = 0

        self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _TICK_INTERVAL, self, "tick:", None, True
        )

    def tick_(self, timer: object) -> None:
        try:
            ctrl = self._dictate_ctrl
            if ctrl is None or self._label is None:
                return
            self._tick_count += 1
            ui_state = ctrl.get_ui_state()
            if ui_state == "recording":
                elapsed = ctrl.get_elapsed()
                levels = ctrl.get_waveform(bars=_N_BARS)
                wave = _render_waveform(levels)
                text = f"● {_format_elapsed(elapsed)}  {wave} — {self._hotkey_display} to stop"
                if self._btn is not None:
                    self._btn.setHidden_(False)
            elif ui_state == "processing":
                dots = "." * (self._tick_count % 4)
                text = f"◌ Transcribing{dots:<3}"
                if self._btn is not None:
                    self._btn.setHidden_(True)
            else:
                return
            self._label.setStringValue_(text)
        except Exception:
            logger.debug("_OverlayController.tick_ error", exc_info=True)

    def cancelAction_(self, sender: object) -> None:
        try:
            if self._on_cancel is not None:
                self._on_cancel()
        except Exception:
            logger.debug("_OverlayController.cancelAction_ error", exc_info=True)

    @objc.python_method
    def hide(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel = None
            self._label = None
            self._btn = None
            self._tick_count = 0


# ---------------------------------------------------------------------------
# Public thread-safe wrapper
# ---------------------------------------------------------------------------

class DictateOverlay:
    """Thread-safe wrapper around _OverlayController.

    show() and hide() must be called on the main thread (or via AppHelper.callAfter).
    """

    def __init__(self) -> None:
        self._controller: _OverlayController | None = None

    def show(
        self,
        ctrl: "DictateController",
        hotkey_display: str,
        on_cancel: Callable[[], None],
    ) -> None:
        if self._controller is None:
            self._controller = _OverlayController.alloc().init()
        self._controller.setup(ctrl, hotkey_display, on_cancel)
        self._controller.show()

    def hide(self) -> None:
        if self._controller is not None:
            self._controller.hide()
