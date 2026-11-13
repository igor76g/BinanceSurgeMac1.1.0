"""Binance USDT-M Futures PUBLIC REST API client (no API key needed).

    GET /fapi/v1/exchangeInfo   - list of symbols
    GET /fapi/v1/ticker/24hr    - 24h statistics (quoteVolume, lastPrice)
    GET /fapi/v1/klines         - 1-minute candles (high, low, close, quote volume, number of trades)
"""

from __future__ import annotations

import threading
from typing import NamedTuple

import requests

BASE_URL = "https://fapi.binance.com"
REQUEST_TIMEOUT = (10, 20)  # (connect, read) seconds


class RateLimitError(Exception):
    """HTTP 429 / 418 from Binance: we must slow down."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Binance rate limit, retry after {retry_after:g} s")
        self.retry_after = retry_after


class Candle(NamedTuple):
    open_time: int      # ms
    close_time: int     # ms
    open: float
    high: float
    low: float
    close: float
    quote_volume: float  # USDT traded in this candle
    trades: int          # number of trades in this candle


def parse_candle(raw: list) -> Candle:
    """Binance kline array: [openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]."""
    return Candle(
        open_time=int(raw[0]),
        close_time=int(raw[6]),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        quote_volume=float(raw[7]),
        trades=int(raw[8]),
    )


CHART_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]


class BinanceApi:
    def __init__(self) -> None:
        self._local = threading.local()  # one HTTP session per thread (candles are loaded in parallel)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    def _get(self, path: str, params: dict | None = None):
        response = self._session().get(BASE_URL + path, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code in (418, 429):
            try:
                retry_after = float(response.headers.get("Retry-After", 60))
            except ValueError:
                retry_after = 60.0
            raise RateLimitError(retry_after)
        response.raise_for_status()
        return response.json()

    def usdt_perpetual_symbols(self) -> set[str]:
        """Currently trading USDT-margined perpetual futures symbols (e.g. BTCUSDT)."""
        data = self._get("/fapi/v1/exchangeInfo")
        return {
            s["symbol"]
            for s in data.get("symbols", [])
            if s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        }

    def quote_volumes(self) -> dict[str, float]:
        """Map of symbol -> 24h quote volume in USDT."""
        result: dict[str, float] = {}
        for item in self._get("/fapi/v1/ticker/24hr"):
            try:
                result[item["symbol"]] = float(item["quoteVolume"])
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def klines(self, symbol: str, limit: int, start_time: int | None = None, interval: str = "1m") -> list[Candle]:
        """Candles (default 1-minute), oldest first. The last one may still be open (the caller drops it)."""
        params: dict = {"symbol": symbol, "interval": interval, "limit": max(1, min(1500, limit))}
        if start_time is not None:
            params["startTime"] = int(start_time)
        return [parse_candle(raw) for raw in self._get("/fapi/v1/klines", params)]
