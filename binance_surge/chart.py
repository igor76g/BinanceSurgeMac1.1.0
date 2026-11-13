"""Geometry of the mini chart (pure functions, no tkinter).

The chart shows a window of `view` candles out of all loaded candles. `offset` = how many of the newest candles are
scrolled out of view (0 = the newest candles are shown). The mouse wheel changes the offset (scrolls through history),
Ctrl + wheel changes `view` (zoom).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .binance_api import Candle

MIN_VIEW = 20
MAX_VIEW = 600


def clamp_view(view: int, total: int) -> int:
    """Number of candles to show: between MIN_VIEW and MAX_VIEW, and never more than there are candles."""
    if total <= MIN_VIEW:
        return max(1, total)
    return max(MIN_VIEW, min(int(view), MAX_VIEW, total))


def clamp_offset(offset: int, total: int, view: int) -> int:
    """0 <= offset <= total - view."""
    return max(0, min(int(offset), max(0, total - view)))


def visible(candles: Sequence[Candle], view: int, offset: int) -> list[Candle]:
    """The slice of candles to draw."""
    total = len(candles)
    view = clamp_view(view, total)
    offset = clamp_offset(offset, total, view)
    end = total - offset
    return list(candles[max(0, end - view):end])


@dataclass
class Scene:
    low: float = 0.0
    high: float = 0.0
    candles: list = field(default_factory=list)    # (x, y_high, y_low, y_open, y_close, is_up)
    ticks: list = field(default_factory=list)      # (y, price) grid lines
    price_y: float | None = None                   # the last price (None if it is scrolled out of the vertical range)
    body_width: float = 4.0


def build_scene(
    shown: Sequence[Candle],
    price: float | None,
    width: float,
    height: float,
    margin_left: float = 8.0,
    margin_right: float = 80.0,
    margin_y: float = 10.0,
    tick_count: int = 5,
) -> Scene:
    """Map the shown candles to canvas coordinates (y grows downwards). The vertical range fits the SHOWN candles."""
    scene = Scene()
    shown = list(shown)
    if not shown or width <= margin_left + margin_right or height <= 2 * margin_y:
        return scene
    low = min(c.low for c in shown)
    high = max(c.high for c in shown)
    pad = (high - low) * 0.05 or high * 0.001
    low, high = low - pad, high + pad
    scene.low, scene.high = low, high

    def y_of(value: float) -> float:
        return margin_y + (high - value) / (high - low) * (height - 2 * margin_y)

    slot = (width - margin_left - margin_right) / len(shown)
    scene.body_width = max(1.0, min(slot * 0.7, 12.0))
    for i, c in enumerate(shown):
        x = margin_left + slot * (i + 0.5)
        scene.candles.append((x, y_of(c.high), y_of(c.low), y_of(c.open), y_of(c.close), c.close >= c.open))
    for k in range(tick_count):
        value = low + (high - low) * k / max(1, tick_count - 1)
        scene.ticks.append((y_of(value), value))
    if price is not None and low <= price <= high:
        scene.price_y = y_of(price)
    return scene
