"""Incremental cache of 1-minute candles.

The first load of a coin downloads window + baseline candles; later checks download only the
candles that were closed since the previous check (a few per coin), which keeps traffic low.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .binance_api import Candle, RateLimitError

MINUTE_MS = 60_000


class CandleStore:
    def __init__(self, api, workers: int = 6) -> None:
        self.api = api
        self.workers = workers
        self._data: dict[str, deque] = {}
        self._keep = 0

    def get(self, symbol: str) -> list[Candle]:
        return list(self._data.get(symbol, ()))

    def _load(self, symbol: str, keep: int, now_ms: int) -> None:
        cached = self._data.get(symbol)
        if cached and cached[-1].open_time >= now_ms - (keep - 1) * MINUTE_MS:
            missing = int((now_ms - cached[-1].open_time) // MINUTE_MS) + 2
            raw = self.api.klines(symbol, missing, start_time=cached[-1].open_time + MINUTE_MS)
            last_known = cached[-1].open_time
        else:
            cached = deque(maxlen=keep)
            self._data[symbol] = cached
            raw = self.api.klines(symbol, keep + 1)
            last_known = -1
        for candle in raw:
            if candle.close_time >= now_ms:   # the candle is still forming
                continue
            if candle.open_time <= last_known:
                continue
            cached.append(candle)
            last_known = candle.open_time

    def update(self, symbols: list[str], keep: int, now_ms: int) -> tuple[int, int, RateLimitError | None]:
        """Bring the cache up to date for `symbols`. Returns (updated, failed, rate_limit_error_or_None)."""
        if keep != self._keep:
            self._data.clear()
            self._keep = keep
        for symbol in list(self._data):
            if symbol not in symbols:
                del self._data[symbol]

        failed = 0
        limit_error: RateLimitError | None = None

        def work(symbol: str):
            try:
                self._load(symbol, keep, now_ms)
                return None
            except Exception as exc:  # one bad coin must not stop the others
                return exc

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for result in pool.map(work, symbols):
                if result is None:
                    continue
                failed += 1
                if isinstance(result, RateLimitError):
                    limit_error = result
        return len(symbols) - failed, failed, limit_error
