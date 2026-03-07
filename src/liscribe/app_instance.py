"""Single-instance lock and activate socket for the menu bar app.

Ensures only one Liscribe process runs per user. A second launch tries to
tell the existing instance to activate (bring to front), then exits.
"""

from __future__ import annotations

import atexit
import fcntl
import logging
import socket
import threading
from pathlib import Path
from typing import Callable

from liscribe.config import CACHE_DIR

logger = logging.getLogger(__name__)

_LOCK_FILE = CACHE_DIR / "app.lock"
_SOCKET_PATH = CACHE_DIR / "liscribe.sock"


class InstanceGuard:
    """Holds the single-instance lock and listener socket. Call release() on exit."""

    def __init__(self, lock_file: object, listener_socket: socket.socket) -> None:
        self._lock_file = lock_file
        self._listener_socket = listener_socket

    def release(self) -> None:
        """Release the lock and socket so a new instance can start after we exit."""
        if self._listener_socket is not None:
            try:
                self._listener_socket.close()
            except OSError:
                pass
            self._listener_socket = None
        if _SOCKET_PATH.exists():
            try:
                _SOCKET_PATH.unlink()
            except OSError:
                pass
        if self._lock_file is not None:
            try:
                self._lock_file.close()
            except OSError:
                pass
            self._lock_file = None


def acquire(*, on_activate: Callable[[], None]) -> InstanceGuard | None:
    """Try to acquire the single-instance lock.

    If another instance holds it, returns None. The caller should then call
    try_activate_existing() and exit.

    If we got the lock, starts a daemon thread that listens for 'activate'
    from a second launch. When a message is received, on_activate() is called
    (typically from a background thread; the caller must ensure it schedules
    main-thread work, e.g. via AppHelper.callAfter). Returns an InstanceGuard
    that must be released on exit (e.g. via atexit).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        f = open(_LOCK_FILE, "w")
    except OSError as e:
        logger.warning("Could not create instance lock file: %s", e)
        return None
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        f.close()
        return None

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if _SOCKET_PATH.exists():
        _SOCKET_PATH.unlink()
    sock.bind(str(_SOCKET_PATH))
    sock.listen(1)

    def accept_loop() -> None:
        while True:
            try:
                conn, _ = sock.accept()
                try:
                    conn.recv(64)
                finally:
                    conn.close()
                on_activate()
            except OSError:
                # Closing the server socket makes accept() raise; we exit the loop.
                break

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()

    return InstanceGuard(lock_file=f, listener_socket=sock)


def try_activate_existing() -> bool:
    """Tell the already-running instance to activate (bring to front).

    Returns True if we connected and sent the message, False otherwise.
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(_SOCKET_PATH))
        s.sendall(b"activate")
        s.close()
        return True
    except OSError:
        return False
