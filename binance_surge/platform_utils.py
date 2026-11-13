"""Small OS-specific helpers (macOS first, with Windows/Linux fallbacks)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_file(path: Path) -> None:
    """Open a file with the default application."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class SleepGuard:
    """Keeps a Mac from going to idle sleep while monitoring runs (via `caffeinate`).

    `caffeinate -i -w <pid>` prevents idle system sleep and exits by itself when this app exits.
    The display may still turn off. Closing the lid or choosing Sleep still sleeps the Mac.
    Does nothing on other systems.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if sys.platform != "darwin":
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            self._proc = None

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
