# Binance Surge Monitor 1.1.0 for macOS

Same as the Windows version: one window, two lists (Binance USDT-M perpetuals, public endpoints only, no API keys).

1. **Volume + NATR surge** - volume and NATR of the last 15 min > 4x the previous 180 min. Filters: 24h volume > 15M USDT, NATR >= 0.7 %. New coins trigger sound + popup + macOS banner (cooldown 30 min per coin, saved on disk).
2. **Top 7 by number of trades** in the last 15 min (24h volume > 30M, trades >= 10000, NATR >= 0.7). No notifications.

Checked every 5 minutes ("Check now" forces a check). Volume/NATR settings are separate for each list (Settings button). Double-click a ticker -> opens it in Vataga.terminal.

## Build (GitHub Actions)
Upload the folder (with hidden `.github`) to a GitHub repo -> Actions -> "Build macOS app" -> download the artifact:
`BinanceSurgeMonitor-mac-apple-silicon` (M1/M2/M3/M4) or `BinanceSurgeMonitor-mac-intel`. Open the DMG, drag the app to Applications.
Or locally: `./build.sh`.

## First launch
The app is not notarized. Right-click -> Open, or:
`xattr -dr com.apple.quarantine /Applications/BinanceSurgeMonitor.app`

## Notifications
Banners are sent through `osascript`, so macOS may show them as "Script Editor": allow them in System Settings -> Notifications -> Script Editor. Popups with buttons ("Open in Vataga", "Open on Binance") always work, even if banners are muted.

## Vataga.terminal on Mac
Enable "Application integration" in Vataga settings and link the window to the "Screener" group (partner id `BinanceSurgeMonitor`, port 5005).

## Other
- "Keep the Mac awake while monitoring" uses `caffeinate`.
- Data: `~/Library/Application Support/BinanceSurgeMonitor/` (settings.json, alert_times.json, alerts.log).
- Traffic: REST only, candles loaded incrementally, symbol list refreshed every 10 min.
- Run from source: `pip install -r requirements.txt && python3 run.py`.
- Not built/tested on a real Mac by the author - report any issue.

## 1.2.0
Every row of both lists has a "Copy" cell: one click copies the ticker (e.g. INITUSDT) to the clipboard. Double-click on the ticker still opens it in Vataga.

## 1.3.0 (rebuilt from the Windows 1.3.0 version)
- **Chart** cell in every row (next to Copy): mini chart of the coin. **Mouse wheel / trackpad two-finger scroll = scroll through history**, Ctrl + wheel = zoom, Home / End = oldest / newest, arrows also scroll. 1000 candles are loaded once, the newest are refreshed every 10 seconds; intervals 1m ... 1d.
- `alerts.log` is emptied when the app is closed (Settings > "Empty the alerts log when the app is closed", on by default).
- Compact window (660x540).
