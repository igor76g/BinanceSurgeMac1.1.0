"""Background monitoring loop.

Every `check_interval_minutes`:
  1. read 24h volumes of all USDT perpetual futures (one request),
  2. keep 1-minute candles of the coins that pass the volume filter up to date (incremental),
  3. compute volume / trades / NATR statistics of the last window vs the usual level,
  4. build the two lists (surge list and top-by-trades list) and notify about NEW surge coins.

Settings are re-read on every cycle, so changes saved in the settings window apply at the next check.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from .analyzer import SurgeRow, TopRow, build_rows, candidate_symbols
from .binance_api import RateLimitError
from .candles import CandleStore
from .metrics import compute_stats
from .settings import Settings

SYMBOLS_REFRESH_SEC = 600   # exchangeInfo is large, so the symbol list is refreshed rarely
RETRY_AFTER_ERROR_SEC = 60  # wait this long after a failed cycle
WARN_AFTER_FAILED_CYCLES = 3


class Monitor(threading.Thread):
    def __init__(
        self,
        api,
        load_settings: Callable[[], Settings],
        on_results: Callable[[list[SurgeRow], list[TopRow], dict[str, float], float], None],
        on_alert: Callable[[SurgeRow, Settings], None],
        log: Callable[[str], None],
        set_status: Callable[[str], None],
        warn: Callable[[str, str], None] | None = None,
        state_path: Path | None = None,
    ) -> None:
        super().__init__(name="monitor", daemon=True)
        self.api = api
        self.load_settings = load_settings
        self.on_results = on_results
        self.on_alert = on_alert
        self.log = log
        self.set_status = set_status
        self.warn = warn

        self.store = CandleStore(api)
        self._symbols: set[str] = set()
        self._symbols_at: float | None = None
        self.state_path = state_path
        self._last_alert: dict[str, float] = self._load_alert_times()
        self._since: dict[str, float] = {}      # symbol -> when it entered the surge list
        self._failed_cycles = 0
        self._connection_lost = False
        self._stopping = threading.Event()
        self._check_now = threading.Event()

    # ------------------------------------------------------------------ cooldown memory

    def _load_alert_times(self) -> dict[str, float]:
        """Last notification time of every coin. Stored on disk, so the cooldown survives Stop/Start and restarts."""
        if self.state_path is None:
            return {}
        try:
            data = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_alert_times(self, now: float) -> None:
        if self.state_path is None:
            return
        self._last_alert = {k: v for k, v in self._last_alert.items() if now - v < 7 * 86400}
        try:
            path = Path(self.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._last_alert), encoding="utf-8")
        except OSError:
            pass

    def stop(self) -> None:
        self._stopping.set()
        self._check_now.set()

    def check_now(self) -> None:
        self._check_now.set()

    def run(self) -> None:
        self.log("Monitoring started")
        while not self._stopping.is_set():
            settings = self.load_settings()
            wait_sec = max(60, settings.check_interval_minutes * 60)
            try:
                self.cycle(settings, time.time())
                self._cycle_ok()
            except RateLimitError as exc:
                wait_sec = max(RETRY_AFTER_ERROR_SEC, exc.retry_after)
                self._cycle_failed(f"{exc}; waiting {wait_sec:g} s")
            except Exception as exc:  # network errors, bad responses, ...
                wait_sec = min(wait_sec, RETRY_AFTER_ERROR_SEC)
                self._cycle_failed(f"{exc} (retry in {wait_sec:g} s)")
            self._check_now.wait(wait_sec)
            self._check_now.clear()
        self.set_status("Stopped")
        self.log("Monitoring stopped")

    # ------------------------------------------------------------------ connection watchdog

    def _cycle_ok(self) -> None:
        self._failed_cycles = 0
        if self._connection_lost:
            self._connection_lost = False
            self.log("Connection restored")
            self._warn("Connection restored", "Market data from Binance is flowing again.")

    def _cycle_failed(self, text: str) -> None:
        self._failed_cycles += 1
        self.log(f"Error: {text}")
        self.set_status(f"Error: {text}")
        if self._failed_cycles >= WARN_AFTER_FAILED_CYCLES and not self._connection_lost:
            self._connection_lost = True
            self._warn(
                "Connection lost",
                "Binance data could not be loaded several times in a row. "
                "The lists are NOT being updated. Check the internet / VPN.",
            )

    def _warn(self, title: str, text: str) -> None:
        if self.warn is None:
            return
        try:
            self.warn(title, text)
        except Exception as exc:
            self.log(f"Could not show warning: {exc}")

    # ------------------------------------------------------------------ one check

    def cycle(self, settings: Settings, now: float) -> None:
        started = time.time()
        if self._symbols_at is None or now - self._symbols_at >= SYMBOLS_REFRESH_SEC:
            self._symbols = self.api.usdt_perpetual_symbols()
            self._symbols_at = now
        volumes = self.api.quote_volumes()
        if not volumes:
            raise RuntimeError("empty ticker data")

        candidates = candidate_symbols(settings, volumes, self._symbols)
        window = max(1, settings.window_minutes)
        keep = window + max(window, settings.baseline_minutes)
        updated, failed, limit_error = self.store.update(candidates, keep, int(now * 1000))
        if limit_error is not None:
            raise limit_error
        if candidates and updated == 0:
            raise RuntimeError("could not load candles for any coin")
        if failed:
            self.log(f"Candles could not be loaded for {failed} of {len(candidates)} coins (will retry next check)")

        stats = {}
        for symbol in candidates:
            st = compute_stats(self.store.get(symbol), window, settings.baseline_minutes)
            if st is not None:
                stats[symbol] = st
        surge, top = build_rows(settings, volumes, stats)

        # remember when each coin entered the surge list
        current = {row.symbol for row in surge}
        for symbol in list(self._since):
            if symbol not in current:
                del self._since[symbol]
        for symbol in current:
            self._since.setdefault(symbol, now)

        self.on_results(surge, top, dict(self._since), now)

        # notify about coins that are NEW in the surge list (and not in cooldown)
        cooldown = max(0, settings.surge_cooldown_minutes) * 60
        for row in surge:
            last = self._last_alert.get(row.symbol)
            if last is not None and now - last < cooldown:
                continue
            self._last_alert[row.symbol] = now
            self._save_alert_times(now)
            self.log(
                f"ALERT {row.symbol}: volume x{row.volume_ratio:.1f}, NATR x{row.natr_ratio:.1f} ({row.natr:.2f}%)"
            )
            try:
                self.on_alert(row, settings)
            except Exception as exc:  # a notification problem must not stop monitoring
                self.log(f"Could not show notification: {exc}")

        self.set_status(
            f"Checked {len(candidates)} coins in {time.time() - started:.0f} s at {time.strftime('%H:%M:%S')}. "
            f"Surge: {len(surge)}, top list: {len(top)}. Next check in {settings.check_interval_minutes} min."
        )
