"""A private Xvfb display for a headed launch on a Linux machine without one.

The server is started on the first free display number from :99 up, sized to
the screen the launch claims, and stopped when the browser closes or this
process exits. Only the browser's environment is given its DISPLAY.
"""

from __future__ import annotations

import atexit
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping

from .errors import LaunchError

FIRST_DISPLAY = 99
MISSING = "Xvfb is not installed; install it (apt install xvfb) or pass headless=True"

_ATTEMPTS = 100
_START_TIMEOUT = 10.0
_live: set["VirtualDisplay"] = set()
_live_lock = threading.Lock()


def _lock_file(number: int) -> Path:
    return Path(f"/tmp/.X{number}-lock")


def _socket_file(number: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{number}")


def _lock_owner(number: int) -> int | None:
    """The pid an X server wrote into its lock file, if any."""
    try:
        return int(_lock_file(number).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def display_answers(value: str | None) -> bool:
    """True when *value*, a DISPLAY string, names an X server that accepts a connection."""
    host, _, rest = (value or "").rpartition(":")
    number = rest.split(".", 1)[0]
    if not number.isdigit():
        return False
    try:
        if host in ("", "unix"):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                probe.connect(str(_socket_file(int(number))))
        else:
            socket.create_connection((host, 6000 + int(number)), timeout=1).close()
    except OSError:
        return False
    return True


def needs_display(headless: bool, env: Mapping[str, str]) -> bool:
    """Whether a launch has to bring its own display."""
    return (not headless and sys.platform.startswith("linux")
            and not env.get("WAYLAND_DISPLAY") and not display_answers(env.get("DISPLAY")))


class VirtualDisplay:
    """One Xvfb server, owned by one launch."""

    def __init__(self, number: int, process: subprocess.Popen) -> None:
        self.number = number
        self.name = f":{number}"
        self.process = process

    def stop(self) -> None:
        """Stop the server and remove the lock and socket it leaves. Safe to repeat."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        # Xvfb removes both itself when it exits cleanly. A killed one does
        # not, and only a lock that still names this server is ours to remove.
        if _lock_owner(self.number) == self.process.pid:
            for path in (_lock_file(self.number), _socket_file(self.number)):
                path.unlink(missing_ok=True)
        with _live_lock:
            _live.discard(self)


def _ready(display: VirtualDisplay) -> bool:
    """Wait until this server, and not a rival on the same number, answers."""
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if display.process.poll() is not None:
            return False
        if _lock_owner(display.number) == display.process.pid and display_answers(display.name):
            return True
        time.sleep(0.02)
    return False


def start(width: int, height: int) -> VirtualDisplay:
    """Start Xvfb on the first free display from :99 up and wait until it answers."""
    binary = shutil.which("Xvfb")
    if binary is None:
        raise LaunchError(MISSING)
    number = FIRST_DISPLAY
    for _ in range(_ATTEMPTS):
        while _lock_file(number).exists() or _socket_file(number).exists():
            number += 1
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(
                [binary, f":{number}", "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
            display = VirtualDisplay(number, process)
            with _live_lock:
                _live.add(display)
            if _ready(display):
                return display
            display.stop()
            log.seek(0)
            detail = log.read().decode("utf-8", "replace").strip()
        # Another launch took this number between the check and the start.
        if "already active" not in detail:
            raise LaunchError(f"Xvfb failed to start on :{number}"
                              + (f": {detail[-500:]}" if detail else ""))
        number += 1
    raise LaunchError("no free X display number was found for Xvfb")


@atexit.register
def _stop_all() -> None:
    for display in list(_live):
        display.stop()


__all__ = ["FIRST_DISPLAY", "MISSING", "VirtualDisplay", "display_answers", "needs_display",
           "start"]
