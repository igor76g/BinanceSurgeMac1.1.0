"""User-editable settings, stored as JSON.

  macOS:   ~/Library/Application Support/BinanceSurgeMonitor/settings.json
  Windows: %APPDATA%\\BinanceSurgeMonitor\\settings.json
  Other:   ~/.config/BinanceSurgeMonitor/settings.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "Binance Surge Monitor"
DATA_DIR_NAME = "BinanceSurgeMonitor"


@dataclass
class Settings:
    """Defaults match the requested conditions."""

    # --- Window 1: volume + NATR surge ---
    surge_min_volume_million: float = 15.0    # 24h quote volume must be MORE than this (million USDT)
    surge_min_natr: float = 0.7               # NATR over the last window must be at least this (%)
    surge_volume_ratio: float = 4.0           # volume of the last window must be MORE than N x the usual
    surge_natr_ratio: float = 4.0             # NATR of the last window must be MORE than N x the usual
    surge_cooldown_minutes: int = 30          # don't re-notify the same coin within this time

    # --- Window 2: top coins by number of trades ---
    top_min_volume_million: float = 30.0      # 24h quote volume must be MORE than this (million USDT)
    top_min_trades: int = 10000               # trades within the last window, at least
    top_min_natr: float = 0.7                 # NATR over the last window must be at least this (%)
    top_count: int = 7                        # how many coins to show

    # --- General ---
    window_minutes: int = 15                  # "last N minutes" used by both windows
    baseline_minutes: int = 180               # history before the window that defines the "usual" level
    check_interval_minutes: int = 5           # how often all coins are re-checked
    autostart_monitoring: bool = False
    sound_enabled: bool = True
    popup_enabled: bool = True                # always-on-top popup with buttons for list-1 alerts
    toast_enabled: bool = True                # macOS notification banner for list-1 alerts
    prevent_sleep: bool = True                # keep the Mac awake while monitoring
    popup_auto_close_sec: int = 0             # 0 = popups stay until closed
    clear_alerts_log_on_exit: bool = True     # empty alerts.log when the app is closed

    # --- Vataga.terminal (double-click a ticker) ---
    vataga_type: str = "F"                    # F = futures, S = spot
    vataga_port: int = 5005
    vataga_partner_id: str = "BinanceSurgeMonitor"

    @property
    def surge_min_volume_usdt(self) -> float:
        return self.surge_min_volume_million * 1_000_000.0

    @property
    def top_min_volume_usdt(self) -> float:
        return self.top_min_volume_million * 1_000_000.0


def data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / DATA_DIR_NAME


def settings_path() -> Path:
    return data_dir() / "settings.json"


def alerts_log_path() -> Path:
    """Text file where every alert (and notification problem) is recorded."""
    return data_dir() / "alerts.log"


def clear_alerts_log() -> None:
    """Empty alerts.log (never raises)."""
    try:
        path = alerts_log_path()
        if path.exists():
            path.write_text("", encoding="utf-8")
    except OSError:
        pass


def alert_times_path() -> Path:
    """When each coin was last notified (keeps the cooldown across stop/start and restarts)."""
    return data_dir() / "alert_times.json"


def load_settings() -> Settings:
    """Load settings from disk. Missing or broken values fall back to the defaults."""
    defaults = Settings()
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(data, dict):
        return defaults

    values = {}
    for field in fields(Settings):
        if field.name not in data:
            continue
        expected_type = type(getattr(defaults, field.name))
        try:
            values[field.name] = expected_type(data[field.name])
        except (TypeError, ValueError):
            pass
    return Settings(**values)


def save_settings(settings: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
