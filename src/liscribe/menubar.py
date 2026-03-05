"""macOS menu bar status item for the Liscribe dictation daemon.

Displays a persistent "Liscribe" item in the menu bar with state-aware title
and a dropdown menu: Start/Stop Dictation, Transcribe, Preferences, Quit.

Architecture
------------
DictationMenuBar    Public API. Called from background threads via update_state().
_MenuBarController  Owns NSStatusItem + NSMenu.
                    All AppKit calls happen on the main thread via _dispatch_to_main.
_MenuDelegate       NSObject subclass (module-level, required by PyObjC) that handles
                    menu item actions.

Usage (called by DictationDaemon)
------
    menubar = DictationMenuBar(daemon)
    menubar.setup()                         # dispatches NSStatusItem creation to main thread
    menubar.update_state(_State.RECORDING)  # call whenever state changes
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

import AppKit
import objc

if TYPE_CHECKING:
    from liscribe.dictation import DictationDaemon, _State

logger = logging.getLogger(__name__)

# Menu bar titles per state
_TITLE_IDLE = "Liscribe"
_TITLE_RECORDING = "\u25cf Liscribe"      # ● Liscribe
_TITLE_TRANSCRIBING = "\u2026 Liscribe"  # … Liscribe


def _dispatch_to_main(fn: Any) -> None:
    """Schedule fn() on the AppKit main queue (thread-safe)."""
    from Foundation import NSBlockOperation, NSOperationQueue
    op = NSBlockOperation.blockOperationWithBlock_(fn)
    NSOperationQueue.mainQueue().addOperation_(op)


# ---------------------------------------------------------------------------
# ObjC delegate — must be defined at module level so PyObjC registers
# its methods as proper ObjC selectors (respondsToSelector: works correctly).
# ---------------------------------------------------------------------------

class _MenuDelegate(AppKit.NSObject):  # type: ignore[misc]
    """Handles NSMenuItem actions for the Liscribe menu bar."""

    # Set by _make_menu_delegate() after alloc/init
    _daemon: Any = None

    def startDictation_(self, sender: Any) -> None:
        if self._daemon is not None:
            try:
                self._daemon._trigger_recording()
            except Exception as exc:
                logger.warning("startDictation failed: %s", exc)

    def stopDictation_(self, sender: Any) -> None:
        if self._daemon is not None:
            try:
                self._daemon._trigger_stop()
            except Exception as exc:
                logger.warning("stopDictation failed: %s", exc)

    def _run_in_terminal_and_focus(self, cmd: str) -> None:
        """Open Terminal, run cmd in a new window, and bring Terminal to front."""
        # Escape for AppleScript: \ and " in cmd
        escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
        # Do script, then activate, then force front via System Events (reliable from menu bar).
        # Use a temp script file so multi-line AppleScript runs correctly.
        script = f'''tell application "Terminal"
  do script "{escaped}"
  activate
end tell
delay 0.2
tell application "System Events" to set frontmost of process "Terminal" to true
'''
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".scpt.txt", delete=False
        ) as f:
            f.write(script)
            path = f.name
        subprocess.Popen(["osascript", path])
        # Leave temp file; osascript reads it at start. OS cleans temp dir.

    def openTranscribe_(self, sender: Any) -> None:
        from liscribe.dictation_launchd import resolve_rec_command
        try:
            rec_args = resolve_rec_command()
            cmd = " ".join(rec_args) + " -s"
            self._run_in_terminal_and_focus(cmd)
        except Exception as exc:
            logger.warning("openTranscribe failed: %s", exc)

    def openPreferences_(self, sender: Any) -> None:
        from liscribe.dictation_launchd import resolve_rec_command
        try:
            rec_args = resolve_rec_command()
            cmd = " ".join(rec_args) + " preferences"
            self._run_in_terminal_and_focus(cmd)
        except Exception as exc:
            logger.warning("openPreferences failed: %s", exc)

    def quitLiscribe_(self, sender: Any) -> None:
        if self._daemon is not None:
            try:
                self._daemon.shutdown()
            except Exception as exc:
                logger.warning("quitLiscribe failed: %s", exc)

    def validateMenuItem_(self, item: Any) -> bool:
        """Tell macOS all items are always valid — enabled state is controlled manually."""
        return True


def _make_menu_delegate(daemon: "DictationDaemon") -> "_MenuDelegate":
    """Instantiate the delegate and bind it to the daemon."""
    d = _MenuDelegate.alloc().init()
    d._daemon = daemon
    return d


class _MenuBarController:
    """Owns the NSStatusItem and NSMenu. All methods must be called on the main thread."""

    def __init__(self, daemon: "DictationDaemon") -> None:
        self._daemon = daemon
        self._status_item: Any = None
        self._item_start: Any = None
        self._item_stop: Any = None
        self._delegate: Any = None  # keep a strong reference

    def create(self) -> None:
        """Create the NSStatusItem and populate the menu."""
        self._delegate = _make_menu_delegate(self._daemon)
        delegate = self._delegate

        status_bar = AppKit.NSStatusBar.systemStatusBar()
        self._status_item = status_bar.statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self._status_item.button().setTitle_(_TITLE_IDLE)

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        self._item_start = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Start Dictation", "startDictation:", ""
        )
        self._item_start.setTarget_(delegate)
        self._item_start.setEnabled_(True)
        menu.addItem_(self._item_start)

        self._item_stop = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Stop Dictation", "stopDictation:", ""
        )
        self._item_stop.setTarget_(delegate)
        self._item_stop.setEnabled_(False)
        menu.addItem_(self._item_stop)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item_transcribe = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Transcribe\u2026", "openTranscribe:", ""
        )
        item_transcribe.setTarget_(delegate)
        item_transcribe.setEnabled_(True)
        menu.addItem_(item_transcribe)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item_prefs = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Preferences\u2026", "openPreferences:", ""
        )
        item_prefs.setTarget_(delegate)
        item_prefs.setEnabled_(True)
        menu.addItem_(item_prefs)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        item_quit = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Liscribe", "quitLiscribe:", ""
        )
        item_quit.setTarget_(delegate)
        item_quit.setEnabled_(True)
        menu.addItem_(item_quit)

        self._status_item.setMenu_(menu)

    def set_title(self, title: str) -> None:
        """Update the status bar button title."""
        if self._status_item is not None:
            try:
                self._status_item.button().setTitle_(title)
            except Exception as exc:
                logger.debug("set_title failed: %s", exc)

    def set_state_enabled(self, recording_active: bool) -> None:
        """Toggle Start/Stop enabled state."""
        if self._item_start is not None:
            self._item_start.setEnabled_(not recording_active)
        if self._item_stop is not None:
            self._item_stop.setEnabled_(recording_active)

    def remove(self) -> None:
        """Remove the status item from the menu bar."""
        if self._status_item is not None:
            try:
                AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            except Exception as exc:
                logger.debug("remove status item failed: %s", exc)
            self._status_item = None


class DictationMenuBar:
    """Public API — called from background threads, dispatches to main thread."""

    def __init__(self, daemon: "DictationDaemon") -> None:
        self._controller = _MenuBarController(daemon)

    def setup(self) -> None:
        """Create the menu bar item. Safe to call from any thread."""
        ctrl = self._controller

        def _create() -> None:
            try:
                ctrl.create()
            except Exception as exc:
                logger.warning("MenuBar create failed: %s", exc)

        _dispatch_to_main(_create)

    def update_state(self, state: "_State") -> None:
        """Update menu bar title and menu item states. Safe to call from any thread."""
        from liscribe.dictation import _State

        if state == _State.IDLE:
            title = _TITLE_IDLE
            recording_active = False
        elif state == _State.RECORDING:
            title = _TITLE_RECORDING
            recording_active = True
        else:  # TRANSCRIBING
            title = _TITLE_TRANSCRIBING
            recording_active = True

        ctrl = self._controller

        def _update() -> None:
            ctrl.set_title(title)
            ctrl.set_state_enabled(recording_active)

        _dispatch_to_main(_update)

    def remove_now(self) -> None:
        """Remove the status item synchronously. Must be called from the main thread."""
        try:
            self._controller.remove()
        except Exception as exc:
            logger.debug("remove_now failed: %s", exc)

    def teardown(self) -> None:
        """Remove the status item. Safe to call from any thread."""
        ctrl = self._controller

        def _remove() -> None:
            ctrl.remove()

        _dispatch_to_main(_remove)
