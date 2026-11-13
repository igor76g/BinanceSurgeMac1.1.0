"""Applies the user's conditions to the statistics and builds the rows of both windows."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import Stats
from .settings import Settings


@dataclass
class SurgeRow:
    symbol: str
    volume_ratio: float
    natr_ratio: float
    natr: float
    volume_window: float
    volume_24h: float
    price_change: float


@dataclass
class TopRow:
    symbol: str
    trades: int
    natr: float
    volume_24h: float
    volume_window: float
    price_change: float


def candidate_symbols(settings: Settings, volumes_24h: dict[str, float], symbols: set[str]) -> list[str]:
    """Coins whose 24h volume is high enough for at least one of the two windows."""
    limit = min(settings.surge_min_volume_usdt, settings.top_min_volume_usdt)
    return sorted(s for s in symbols if volumes_24h.get(s, 0.0) > limit)


def build_rows(
    settings: Settings, volumes_24h: dict[str, float], stats: dict[str, Stats]
) -> tuple[list[SurgeRow], list[TopRow]]:
    surge: list[SurgeRow] = []
    top: list[TopRow] = []
    for symbol, st in stats.items():
        volume_24h = volumes_24h.get(symbol, 0.0)

        if (
            volume_24h > settings.surge_min_volume_usdt
            and st.natr_window >= settings.surge_min_natr
            and st.volume_ratio > settings.surge_volume_ratio
            and st.natr_ratio > settings.surge_natr_ratio
        ):
            surge.append(
                SurgeRow(symbol, st.volume_ratio, st.natr_ratio, st.natr_window, st.volume_window, volume_24h, st.price_change)
            )

        if (
            volume_24h > settings.top_min_volume_usdt
            and st.trades_window >= settings.top_min_trades
            and st.natr_window >= settings.top_min_natr
        ):
            top.append(TopRow(symbol, st.trades_window, st.natr_window, volume_24h, st.volume_window, st.price_change))

    surge.sort(key=lambda r: r.volume_ratio, reverse=True)
    top.sort(key=lambda r: r.trades, reverse=True)
    return surge, top[: max(1, settings.top_count)]
