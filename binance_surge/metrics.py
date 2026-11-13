"""Candle statistics: trade volume, number of trades and NATR of the last window vs the usual level.

Definitions (all on 1-minute candles):
  * TR (true range) of a candle = max(high - low, |high - previous close|, |low - previous close|).
  * NATR of a candle = TR / close * 100  (percent).
  * NATR of a window = average NATR of its candles.
  * "Usual" level = average per-candle value over the baseline period, i.e. the candles BEFORE the window.
  * volume_ratio = volume of the window / (average candle volume of the baseline * window length).
  * natr_ratio   = NATR of the window / average NATR of the baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .binance_api import Candle


@dataclass
class Stats:
    volume_window: float      # USDT traded during the window
    trades_window: int        # number of trades during the window
    natr_window: float        # %
    volume_ratio: float       # x the usual level (0 if unknown)
    natr_ratio: float         # x the usual level (0 if unknown)
    price_change: float       # % from the open of the first window candle to the close of the last one


def natr_values(candles: Sequence[Candle]) -> list[float]:
    """NATR (%) of every candle. The first candle uses high - low (no previous close)."""
    result: list[float] = []
    prev_close: Optional[float] = None
    for c in candles:
        tr = c.high - c.low
        if prev_close is not None:
            tr = max(tr, abs(c.high - prev_close), abs(c.low - prev_close))
        result.append(tr / c.close * 100.0 if c.close > 0 else 0.0)
        prev_close = c.close
    return result


def compute_stats(candles: Sequence[Candle], window: int, baseline: int) -> Optional[Stats]:
    """Statistics for the last `window` candles. None if there is not enough history
    (a new listing): at least `window` candles of baseline are required."""
    window = max(1, int(window))
    if len(candles) < window * 2:
        return None
    candles = list(candles[-(window + max(window, int(baseline))):])
    win = candles[-window:]
    base = candles[:-window]

    natr = natr_values(candles)
    natr_win = natr[-window:]
    natr_base = natr[:-window]

    volume_window = sum(c.quote_volume for c in win)
    trades_window = sum(c.trades for c in win)
    natr_window = sum(natr_win) / len(natr_win)

    base_volume_avg = sum(c.quote_volume for c in base) / len(base)
    base_natr_avg = sum(natr_base) / len(natr_base)
    volume_ratio = volume_window / (base_volume_avg * window) if base_volume_avg > 0 else 0.0
    natr_ratio = natr_window / base_natr_avg if base_natr_avg > 0 else 0.0

    first_open = win[0].open
    price_change = (win[-1].close - first_open) / first_open * 100.0 if first_open > 0 else 0.0
    return Stats(volume_window, trades_window, natr_window, volume_ratio, natr_ratio, price_change)
