"""Alerts on macOS: notification-center banner (osascript) + sound (afplay) + popup window + alerts.log.

* The banner is shown with `osascript` (`display notification`). macOS attributes it to "Script Editor", so allow
  notifications for Script Editor in System Settings > Notifications (it appears there after the first notification).
  Banners cannot have buttons, so the popup window (shown by the GUI through `alert_listener`) offers
  "Open in Vataga" / "Open on Binance".
* The sound is played by the app itself with `afplay`, so it works even if the banner is muted.
* Banners run in a background thread, so a slow call never delays market checks.

On non-macOS systems (e.g. when testing) everything falls back to console output.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from .settings import alerts_log_path, load_settings

IS_MAC = sys.platform == "darwin"
SOUND_FILE = "/System/Library/Sounds/Glass.aiff"

# Set by the GUI: called as listener(symbol, title, message, direction) from any thread; shows the popup window.
alert_listener = None


def _append_log(line: str) -> None:
    """Record an alert / problem in alerts.log (never raises)."""
    try:
        path = alerts_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
    except OSError:
        pass


def play_sound() -> None:
    """Play the alert sound without blocking the caller."""
    if not IS_MAC:
        return
    try:
        subprocess.Popen(["afplay", SOUND_FILE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _show_banner(title: str, message: str) -> None:
    try:
        if not IS_MAC:
            print(f"[notification] {title}: {message}")
            return
        # Text is passed as arguments (not pasted into the script), so quotes etc. are safe.
        cmd = [
            "osascript",
            "-e", "on run argv",
            "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
            "-e", "end run",
            message.replace("\n", " | "),
            title,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            _append_log(f"BANNER FAILED for '{title}': {result.stderr.strip()}")
    except Exception as exc:
        _append_log(f"BANNER FAILED for '{title}': {exc}")


def _notify(symbol: str, title: str, message: str, sound: bool, direction: int = 0) -> None:
    settings = load_settings()
    if sound:
        play_sound()
    if settings.toast_enabled:
        threading.Thread(target=_show_banner, args=(title, message), daemon=True).start()
    if settings.popup_enabled and alert_listener is not None:
        try:
            alert_listener(symbol, title, message, direction)
        except Exception as exc:
            _append_log(f"POPUP FAILED for '{title}': {exc}")


def show_surge(
    symbol: str,
    volume_ratio: float,
    natr_ratio: float,
    natr: float,
    volume_window: float,
    window_minutes: int,
    sound: bool = True,
) -> None:
    """Alert for a coin that entered the surge list (volume and NATR jumped)."""
    message = (
        f"Volume x{volume_ratio:.1f} and NATR x{natr_ratio:.1f} vs the usual level (last {window_minutes} min).\n"
        f"NATR {natr:.2f}%, volume in the window {volume_window / 1e6:.2f}M USDT"
    )
    _append_log(
        f"ALERT {symbol} volume x{volume_ratio:.1f}, NATR x{natr_ratio:.1f} ({natr:.2f}%), "
        f"window volume {volume_window / 1e6:.2f}M USDT"
    )
    _notify(symbol=symbol, title=f"{symbol}  vol x{volume_ratio:.1f} / NATR x{natr_ratio:.1f}", message=message, sound=sound)


def show_warning(title: str, message: str, sound: bool = True) -> None:
    """Problem notice (e.g. connection lost). Shown without coin buttons."""
    _append_log(f"WARNING {title}: {message}")
    _notify(symbol="", title=title, message=message, sound=sound)


def show_test(sound: bool = True) -> None:
    _notify("BTCUSDT", "Test notification", "Notifications are working.", sound)
