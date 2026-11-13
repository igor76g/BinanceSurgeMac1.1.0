"""Open a coin's order book in Vataga.terminal (local HTTP API, see Vataga integration doc v1.2).

Request: POST http://localhost:{port}/api/open/{partner_id}/{exchange}/{type}/{symbol}
  exchange = binance, type = F (futures) or S (spot), symbol = BTCUSDT.
Vataga.terminal must be running with Settings > General > "Application integration" turned on,
and the window to control must be linked to "Screener" (right-click window title > Link to window).

Vataga.terminal for macOS runs on the same Mac, so the app talks to it directly on localhost.
"""

from __future__ import annotations

from urllib.parse import quote

import requests

from .settings import APP_NAME, Settings, load_settings

EXCHANGE = "binance"


def build_api_url(settings: Settings, symbol: str) -> str:
    return (
        f"http://localhost:{settings.vataga_port}/api/open/"
        f"{quote(settings.vataga_partner_id, safe='')}/{EXCHANGE}/{settings.vataga_type.upper()}/{quote(symbol, safe='-_')}"
    )


def open_symbol(symbol: str, settings: Settings | None = None) -> tuple[bool, str]:
    """Ask Vataga.terminal to open `symbol`. Returns (ok, message)."""
    settings = settings or load_settings()
    url = build_api_url(settings, symbol)
    try:
        response = requests.post(url, timeout=3)
    except requests.RequestException as exc:
        return False, (
            f"Vataga.terminal is not reachable at localhost:{settings.vataga_port}. "
            f"Start the terminal and turn on Settings > General > Application integration. ({exc.__class__.__name__})"
        )
    if response.status_code != 200:
        return False, f"Vataga.terminal answered HTTP {response.status_code}: {response.text[:200]}"
    return True, response.text
