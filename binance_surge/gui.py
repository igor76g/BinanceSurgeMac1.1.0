"""Tkinter UI: one window with two lists (1 = volume + NATR surge, 2 = top coins by number of trades) and a settings dialog.

Double-click a ticker in either list to open the coin in Vataga.terminal; the "Chart" cell opens a mini chart
(mouse wheel = scroll through history) and the "Copy" cell copies the ticker. Vataga runs on the same Mac.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from . import __version__, notifier, popups, vataga
from .binance_api import CHART_INTERVALS, BinanceApi, Candle
from .chart import MAX_VIEW, MIN_VIEW, build_scene, clamp_offset, clamp_view, visible
from .monitor import Monitor
from .platform_utils import SleepGuard, open_file
from .settings import (
    APP_NAME, Settings, alert_times_path, alerts_log_path, clear_alerts_log, load_settings, save_settings,
)

MAX_LOG_LINES = 300
BOLD = ("Helvetica", 12, "bold")

# (settings key, label, python type, minimum allowed value); label None = section header
SETTINGS_FIELDS = [
    ("Window 1: volume + NATR surge", None, None, None),
    ("surge_min_volume_million", "Minimum 24h volume (million USDT)", float, 0.0),
    ("surge_min_natr", "Minimum NATR of the last window (%)", float, 0.0),
    ("surge_volume_ratio", "Volume must grow more than (x usual)", float, 0.0),
    ("surge_natr_ratio", "NATR must grow more than (x usual)", float, 0.0),
    ("surge_cooldown_minutes", "Notification cooldown per coin (minutes)", int, 0),
    ("Window 2: top coins by number of trades", None, None, None),
    ("top_min_volume_million", "Minimum 24h volume (million USDT)", float, 0.0),
    ("top_min_trades", "Minimum trades in the last window", int, 0),
    ("top_min_natr", "Minimum NATR of the last window (%)", float, 0.0),
    ("top_count", "Number of coins in the list", int, 1),
    ("General", None, None, None),
    ("window_minutes", "Window length: 'last N minutes'", int, 1),
    ("baseline_minutes", "History that defines the 'usual' level (minutes)", int, 15),
    ("check_interval_minutes", "Check interval (minutes)", int, 1),
    ("popup_auto_close_sec", "Close alert popups after (seconds, 0 = never)", int, 0),
]


def fmt_million(value: float) -> str:
    return f"{value / 1e6:,.1f}"


class SymbolTree(ttk.Frame):
    """Treeview with a scrollbar. Right after the ticker column there are two clickable cells:
    "Chart" (opens the mini chart) and "Copy" (copies the ticker). Double-click on a ticker opens Vataga."""

    CHART_TEXT = "Chart"
    COPY_TEXT = "Copy"

    def __init__(self, master, columns, on_open, on_copy=None, on_chart=None, height=6) -> None:
        super().__init__(master)
        self.on_open = on_open
        self.on_copy = on_copy
        self.on_chart = on_chart
        columns = list(columns)
        self.ticker_index = [c[0] for c in columns].index("ticker")
        columns[self.ticker_index + 1:self.ticker_index + 1] = [("chart", "", 52), ("copy", "", 48)]
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", height=height)
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            anchor = "w" if key == "ticker" else ("center" if key in ("chart", "copy") else "e")
            self.tree.column(key, width=width, anchor=anchor, stretch=key not in ("chart", "copy"))
        bar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Button-1>", self._click)

    def _column(self, event) -> str:
        """Name of the column under the mouse ('' if none)."""
        spec = self.tree.identify_column(event.x)  # "#3" = the third displayed column
        try:
            return str(self.tree["columns"][int(spec.lstrip("#")) - 1])
        except (ValueError, IndexError):
            return ""

    def _click(self, event) -> str | None:
        """A single click on the 'Chart' / 'Copy' cell acts on the ticker of that row."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        item = self.tree.identify_row(event.y)
        column = self._column(event)
        if item and column in ("chart", "copy"):
            callback = self.on_chart if column == "chart" else self.on_copy
            if callback is not None:
                callback(str(self.tree.set(item, "ticker")))
            return "break"
        return None

    def _double_click(self, event) -> None:
        if self._column(event) in ("chart", "copy"):
            return  # must not open Vataga
        item = self.tree.identify_row(event.y)
        if item:
            self.on_open(str(self.tree.set(item, "ticker")))

    def set_rows(self, rows: list[tuple]) -> None:
        self.tree.delete(*self.tree.get_children())
        i = self.ticker_index + 1
        for row in rows:
            row = tuple(row)
            self.tree.insert("", "end", values=row[:i] + (self.CHART_TEXT, self.COPY_TEXT) + row[i:])


class TopPanel(ttk.LabelFrame):
    """Panel 2 (below the surge list): coins with the most trades in the last window."""

    COLUMNS = [
        ("rank", "#", 28),
        ("ticker", "Ticker", 100),
        ("trades", "Trades", 70),
        ("natr", "NATR %", 60),
        ("vol24", "24h vol, M", 80),
    ]

    def __init__(self, master, on_open, on_copy=None, on_chart=None) -> None:
        super().__init__(master, text=" 2. Top coins by number of trades ", padding=4)
        self.heading_var = tk.StringVar(value="Waiting for the first check...")
        ttk.Label(self, textvariable=self.heading_var, wraplength=600).pack(anchor="w")
        self.list = SymbolTree(self, self.COLUMNS, on_open, on_copy, on_chart, height=5)
        self.list.pack(fill="both", expand=True, pady=(2, 0))

    def update_rows(self, rows, settings: Settings, now: float) -> None:
        self.heading_var.set(
            f"Top {settings.top_count} by trades in {settings.window_minutes} min (>{settings.top_min_volume_million:g}M, "
            f"trades >= {settings.top_min_trades}, NATR >= {settings.top_min_natr:g}%). "
            f"Updated {time.strftime('%H:%M:%S', time.localtime(now))}; {len(rows)} match"
        )
        self.list.set_rows(
            [
                (i + 1, r.symbol, f"{r.trades:,}", f"{r.natr:.2f}", fmt_million(r.volume_24h))
                for i, r in enumerate(rows)
            ]
        )


class ChartWindow(tk.Toplevel):
    """Mini chart of a coin. Mouse wheel = scroll through history, Ctrl + wheel = zoom, Home / End = oldest / newest.

    1000 candles are loaded once (one small request); the newest candles are refreshed every few seconds."""

    REDRAW_MS = 1000
    REFRESH_SEC = 10

    def __init__(self, app: "App", symbol: str) -> None:
        super().__init__(app)
        self.app = app
        self.symbol = symbol
        self.title(f"{symbol} - chart")
        self.geometry("820x480+140+90")
        self.candles: list[Candle] = []
        self.view = 120          # candles on the screen
        self.offset = 0          # newest candles scrolled out of view
        self.error = ""
        self._fetching = False
        self._fetched_at = 0.0
        self._full_loaded_interval = ""
        self._job = None

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text=symbol, font=BOLD).pack(side="left")
        ttk.Label(top, text="   Candles:").pack(side="left")
        self.interval_var = tk.StringVar(value="1m")
        box = ttk.Combobox(top, textvariable=self.interval_var, values=CHART_INTERVALS, width=5, state="readonly")
        box.pack(side="left", padx=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self._interval_changed())
        ttk.Button(top, text="Newest", command=self.go_newest).pack(side="left", padx=(12, 0))
        self.canvas = tk.Canvas(self, bg="#1e2329", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.info = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info).pack(anchor="w", padx=8, pady=4)

        # the wheel goes to the focused widget on Windows, so bind it on the window itself
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.bind("<Button-4>", lambda e: self._scroll(+1))        # Linux
        self.bind("<Button-5>", lambda e: self._scroll(-1))
        self.bind("<Left>", lambda e: self._scroll(+1))
        self.bind("<Right>", lambda e: self._scroll(-1))
        self.bind("<Home>", lambda e: self._set_offset(10 ** 9))
        self.bind("<End>", lambda e: self.go_newest())
        self.canvas.bind("<Configure>", lambda _e: self._draw_safe())
        self.canvas.bind("<Enter>", lambda _e: self.canvas.focus_set())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._tick()

    # ---------------------------------------------------------------- data

    def _close(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self.app.charts.pop(self.symbol, None)
        self.destroy()

    def _interval_changed(self) -> None:
        self.candles = []
        self.offset = 0
        self._fetched_at = 0.0
        self._tick(reschedule=False)

    def _tick(self, reschedule: bool = True) -> None:
        if time.time() - self._fetched_at >= self.REFRESH_SEC and not self._fetching:
            self._start_fetch()
        self._draw_safe()
        if reschedule:
            self._job = self.after(self.REDRAW_MS, self._tick)

    def _start_fetch(self) -> None:
        self._fetching = True
        interval = self.interval_var.get()
        full = not self.candles or self._full_loaded_interval != interval

        def work() -> None:
            try:
                fresh = self.app.api.klines(self.symbol, 1000 if full else 5, interval=interval)
                if interval == self.interval_var.get():
                    if full:
                        self.candles = fresh
                        self._full_loaded_interval = interval
                    else:   # replace / append the newest candles
                        by_time = {c.open_time: c for c in self.candles}
                        by_time.update({c.open_time: c for c in fresh})
                        self.candles = [by_time[t] for t in sorted(by_time)][-2000:]
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
            finally:
                self._fetched_at = time.time()
                self._fetching = False

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------------------- scrolling

    def _step(self) -> int:
        return max(1, self.view // 10)

    def _set_offset(self, offset: int) -> None:
        total = len(self.candles)
        self.view = clamp_view(self.view, total)
        self.offset = clamp_offset(offset, total, self.view)
        self._draw_safe()

    def go_newest(self) -> None:
        self._set_offset(0)

    def _scroll(self, direction: int) -> None:
        """direction +1 = back in time (older candles), -1 = forward."""
        self._set_offset(self.offset + direction * self._step())

    @staticmethod
    def _wheel_direction(event) -> int:
        delta = getattr(event, "delta", 0)
        return 1 if delta > 0 else (-1 if delta < 0 else 0)

    def _wheel(self, event) -> None:
        direction = self._wheel_direction(event)
        if direction:
            self._scroll(direction)

    def _ctrl_wheel(self, event) -> None:
        """Ctrl + wheel up = zoom in (fewer candles), down = zoom out."""
        direction = self._wheel_direction(event)
        if not direction:
            return
        factor = 0.8 if direction > 0 else 1.25
        self.view = max(MIN_VIEW, min(MAX_VIEW, int(round(self.view * factor))))
        self._set_offset(self.offset)

    # ------------------------------------------------------------------ draw

    def _draw_safe(self) -> None:
        try:
            self._draw()
        except Exception as exc:  # a drawing problem must never break the app
            self.info.set(f"Chart error: {exc}")

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        width, height = max(c.winfo_width(), 300), max(c.winfo_height(), 160)
        if not self.candles:
            c.create_text(width / 2, height / 2, fill="#9aa4af", text=self.error or "Loading candles...")
            return
        total = len(self.candles)
        self.view = clamp_view(self.view, total)
        self.offset = clamp_offset(self.offset, total, self.view)
        shown = visible(self.candles, self.view, self.offset)
        last_price = self.candles[-1].close
        scene = build_scene(shown, last_price, width, height)
        right = width - 76
        for y, value in scene.ticks:
            c.create_line(8, y, right, y, fill="#2b3139")
            c.create_text(right + 6, y, anchor="w", fill="#9aa4af", font=("Helvetica", 8), text=f"{value:.6g}")
        for x, y_high, y_low, y_open, y_close, is_up in scene.candles:
            color = "#0ecb81" if is_up else "#f6465d"
            c.create_line(x, y_high, x, y_low, fill=color)
            half = scene.body_width / 2
            c.create_rectangle(x - half, min(y_open, y_close), x + half, max(y_open, y_close) + 1, fill=color, outline=color)
        if scene.price_y is not None:
            c.create_line(8, scene.price_y, right, scene.price_y, fill="#f0b90b", dash=(4, 3))
            c.create_text(right + 6, scene.price_y - 10, anchor="w", fill="#f0b90b", font=("Helvetica", 9, "bold"),
                          text=f"{last_price:.6g}")
        first = time.strftime("%d.%m %H:%M", time.localtime(shown[0].open_time / 1000))
        last = time.strftime("%d.%m %H:%M", time.localtime(shown[-1].open_time / 1000))
        self.info.set(
            f"{self.interval_var.get()} candles: showing {len(shown)} of {total}  ({first} - {last}).  "
            "Mouse wheel = scroll through history, Ctrl + wheel = zoom, Home / End = oldest / newest."
        )


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: "App") -> None:
        super().__init__(app)
        self.app = app
        self.title("Settings")
        self.resizable(False, False)
        self.transient(app)

        settings = load_settings()
        self.vars: dict[str, tk.StringVar] = {}
        self.types: dict[str, tuple] = {}
        self.labels: dict[str, str] = {}

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        form = ttk.Frame(root)
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        row = 0
        for key, label, py_type, minimum in SETTINGS_FIELDS:
            if label is None:
                ttk.Label(form, text=key, font=BOLD).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
            else:
                var = tk.StringVar()
                value = getattr(settings, key)
                var.set(f"{value:g}" if isinstance(value, float) else str(value))
                self.vars[key] = var
                self.types[key] = (py_type, minimum)
                self.labels[key] = label
                ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 16))
                ttk.Entry(form, textvariable=var, width=12).grid(row=row, column=1, sticky="e", pady=2)
            row += 1

        self.sound_var = tk.BooleanVar(value=settings.sound_enabled)
        self.popup_var = tk.BooleanVar(value=settings.popup_enabled)
        self.toast_var = tk.BooleanVar(value=settings.toast_enabled)
        self.autostart_var = tk.BooleanVar(value=settings.autostart_monitoring)
        self.sleep_var = tk.BooleanVar(value=settings.prevent_sleep)
        self.clear_log_var = tk.BooleanVar(value=settings.clear_alerts_log_on_exit)
        ttk.Label(root, text="Notifications (window 1 only)", font=BOLD).pack(anchor="w", pady=(12, 2))
        ttk.Checkbutton(root, text="Play a sound", variable=self.sound_var).pack(anchor="w")
        ttk.Checkbutton(root, text="Popup window with buttons (always on top)", variable=self.popup_var).pack(anchor="w")
        ttk.Checkbutton(root, text="macOS notification banner", variable=self.toast_var).pack(anchor="w")
        ttk.Checkbutton(root, text="Keep the Mac awake while monitoring", variable=self.sleep_var).pack(anchor="w")
        ttk.Checkbutton(root, text="Start monitoring automatically when the app opens", variable=self.autostart_var).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(root, text="Empty the alerts log when the app is closed", variable=self.clear_log_var).pack(anchor="w")

        ttk.Label(root, text="Vataga.terminal", font=BOLD).pack(anchor="w", pady=(12, 2))
        vrow = ttk.Frame(root)
        vrow.pack(fill="x")
        self.vtype_var = tk.StringVar(value=settings.vataga_type)
        self.vport_var = tk.StringVar(value=str(settings.vataga_port))
        ttk.Label(vrow, text="Market").pack(side="left")
        ttk.Combobox(vrow, textvariable=self.vtype_var, values=["F", "S"], width=3, state="readonly").pack(side="left", padx=4)
        ttk.Label(vrow, text="(F = futures, S = spot)   Port").pack(side="left")
        ttk.Entry(vrow, textvariable=self.vport_var, width=7).pack(side="left", padx=4)
        ttk.Button(vrow, text="Test Vataga", command=self.test_vataga).pack(side="right")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=6)

    def read(self) -> Settings | None:
        values: dict = {}
        for key, var in self.vars.items():
            py_type, minimum = self.types[key]
            raw = var.get().strip().replace(",", ".")
            try:
                value = py_type(raw)
            except ValueError:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be a valid number.", parent=self)
                return None
            if value < minimum:
                messagebox.showerror(APP_NAME, f"'{self.labels[key]}' must be at least {minimum:g}.", parent=self)
                return None
            values[key] = value
        try:
            port = int(self.vport_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "Port must be a number from 1 to 65535.", parent=self)
            return None
        return Settings(
            sound_enabled=self.sound_var.get(),
            popup_enabled=self.popup_var.get(),
            toast_enabled=self.toast_var.get(),
            autostart_monitoring=self.autostart_var.get(),
            prevent_sleep=self.sleep_var.get(),
            clear_alerts_log_on_exit=self.clear_log_var.get(),
            vataga_type=self.vtype_var.get() or "F",
            vataga_port=port,
            vataga_partner_id=load_settings().vataga_partner_id,
            **values,
        )

    def save(self) -> None:
        settings = self.read()
        if settings is None:
            return
        try:
            save_settings(settings)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings: {exc}", parent=self)
            return
        self.app.add_log("Settings saved")
        self.app.settings_saved()
        self.destroy()

    def test_vataga(self) -> None:
        settings = self.read()
        if settings is None:
            return
        ok, message = vataga.open_symbol("BTCUSDT", settings)
        if ok:
            messagebox.showinfo(APP_NAME, "Request sent. BTCUSDT should open in the window linked to 'Screener'.", parent=self)
        else:
            messagebox.showerror(APP_NAME, message, parent=self)


class App(tk.Tk):
    SURGE_COLUMNS = [
        ("ticker", "Ticker", 100),
        ("vol_x", "Volume x", 62),
        ("natr_x", "NATR x", 52),
        ("natr", "NATR %", 56),
        ("vol24", "24h vol, M", 76),
        ("change", "Change %", 66),
        ("since", "Since", 50),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("660x540+60+30")
        self.minsize(600, 460)
        self.charts: dict[str, ChartWindow] = {}

        self.api = BinanceApi()
        self.sleep_guard = SleepGuard()
        self.monitor: Monitor | None = None
        self.messages: queue.Queue = queue.Queue()
        self.last_settings = load_settings()

        self._build_widgets()
        notifier.alert_listener = lambda symbol, title, message, direction=0: self.messages.put(
            ("popup", (symbol, title, message, direction))
        )

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            self.createcommand("::tk::mac::Quit", self._on_close)  # Cmd+Q
        self.after(200, self._drain_messages)
        if self.last_settings.autostart_monitoring:
            self.after(500, self.start_monitoring)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        buttons = ttk.Frame(root)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start", command=self.start_monitoring)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.check_button = ttk.Button(buttons, text="Check now", command=self.check_now, state="disabled")
        self.check_button.pack(side="left")
        ttk.Button(buttons, text="Settings", command=self.open_settings).pack(side="right")
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(buttons, textvariable=self.status_var, wraplength=360).pack(side="left", padx=10)

        paned = ttk.PanedWindow(root, orient="vertical")
        paned.pack(fill="both", expand=True, pady=(6, 2))

        surge_frame = ttk.LabelFrame(paned, text=" 1. Volume + NATR surge ", padding=4)
        self.heading_var = tk.StringVar(value="")
        ttk.Label(surge_frame, textvariable=self.heading_var, wraplength=600).pack(anchor="w")
        self.updated_var = tk.StringVar(value="")  # kept for compatibility; shown inside the heading
        self.surge_list = SymbolTree(
            surge_frame, self.SURGE_COLUMNS, self.open_symbol, self.copy_symbol, self.open_chart, height=6
        )
        self.surge_list.pack(fill="both", expand=True, pady=(2, 0))
        paned.add(surge_frame, weight=1)

        self.top_panel = TopPanel(paned, self.open_symbol, self.copy_symbol, self.open_chart)
        paned.add(self.top_panel, weight=1)

        self.hint_var = tk.StringVar(value="Double-click a ticker = Vataga.terminal; click \"Chart\" or \"Copy\" in a row")
        ttk.Label(root, textvariable=self.hint_var).pack(anchor="w")

        tools = ttk.Frame(root)
        tools.pack(fill="x", pady=(4, 2))
        ttk.Label(tools, text="Log", font=BOLD).pack(side="left")
        ttk.Button(tools, text="Test", command=self.test_notification).pack(side="right")
        ttk.Button(tools, text="Alerts log", command=self.open_alerts_log).pack(side="right", padx=4)
        ttk.Button(tools, text="Close popups", command=popups.close_all).pack(side="right")
        self.log_widget = scrolledtext.ScrolledText(root, height=4, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=False)

    # ------------------------------------------------------------- actions

    def open_settings(self) -> None:
        SettingsDialog(self)

    def settings_saved(self) -> None:
        self.last_settings = load_settings()
        if self.monitor is not None and self.monitor.is_alive():
            self.monitor.check_now()  # apply the new conditions right away

    def start_monitoring(self) -> None:
        if self.monitor is not None and self.monitor.is_alive():
            return
        self.monitor = Monitor(
            api=self.api,
            load_settings=load_settings,
            on_results=lambda surge, top, since, now: self.messages.put(("results", (surge, top, since, now))),
            on_alert=lambda row, settings: notifier.show_surge(
                row.symbol, row.volume_ratio, row.natr_ratio, row.natr, row.volume_window,
                settings.window_minutes, sound=settings.sound_enabled,
            ),
            log=lambda text: self.messages.put(("log", text)),
            set_status=lambda text: self.messages.put(("status", text)),
            warn=lambda title, text: notifier.show_warning(title, text, sound=load_settings().sound_enabled),
            state_path=alert_times_path(),
        )
        if load_settings().prevent_sleep:
            self.sleep_guard.start()
        self.monitor.start()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.check_button.config(state="normal")
        self.status_var.set("Starting: the first check downloads candles of all coins and takes a little longer...")

    def stop_monitoring(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        self.sleep_guard.stop()
        self.stop_button.config(state="disabled")
        self.check_button.config(state="disabled")

    def check_now(self) -> None:
        if self.monitor is not None and self.monitor.is_alive():
            self.monitor.check_now()
            self.status_var.set("Checking now...")

    def copy_symbol(self, symbol: str) -> None:
        """Put the ticker (e.g. INITUSDT) on the clipboard."""
        try:
            self.clipboard_clear()
            self.clipboard_append(symbol)
            self.update_idletasks()
            self.hint_var.set(f"{symbol} copied to the clipboard")
        except tk.TclError as exc:
            self.hint_var.set(f"Could not copy {symbol}: {exc}")

    def open_chart(self, symbol: str) -> None:
        """Open (or raise) the mini chart of a coin."""
        window = self.charts.get(symbol)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
            return
        self.charts[symbol] = ChartWindow(self, symbol)

    def open_symbol(self, symbol: str) -> None:
        """Open a coin in Vataga.terminal (called on double-click; the request runs in a thread)."""
        settings = load_settings()
        self.hint_var.set(f"Sending {symbol} to Vataga.terminal...")

        def work() -> None:
            ok, message = vataga.open_symbol(symbol, settings)
            self.messages.put(("hint", f"{symbol} opened in Vataga.terminal" if ok else message))

        threading.Thread(target=work, daemon=True).start()

    def test_notification(self) -> None:
        try:
            notifier.show_test(sound=load_settings().sound_enabled)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not show notification: {exc}")

    def open_alerts_log(self) -> None:
        path = alerts_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            open_file(path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {path}: {exc}")

    # ---------------------------------------------------------------- misc

    def add_log(self, text: str) -> None:
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        lines = int(self.log_widget.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_widget.delete("1.0", f"{lines - MAX_LOG_LINES + 1}.0")
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def _show_results(self, surge, top, since, now) -> None:
        s = load_settings()
        self.heading_var.set(
            f"Last {s.window_minutes} min vs previous {s.baseline_minutes} min (>{s.surge_min_volume_million:g}M, "
            f"NATR >= {s.surge_min_natr:g}%, volume > x{s.surge_volume_ratio:g}, NATR > x{s.surge_natr_ratio:g}). "
            f"Updated {time.strftime('%H:%M:%S', time.localtime(now))}; {len(surge)} match"
        )
        self.surge_list.set_rows(
            [
                (r.symbol, f"{r.volume_ratio:.1f}", f"{r.natr_ratio:.1f}", f"{r.natr:.2f}",
                 fmt_million(r.volume_24h), f"{r.price_change:+.2f}",
                 time.strftime("%H:%M", time.localtime(since.get(r.symbol, now))))
                for r in surge
            ]
        )
        self.top_panel.update_rows(top, s, now)

    def _drain_messages(self) -> None:
        """Move messages from the monitor thread into the UI. One bad message never stops the loop."""
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "log":
                    self.add_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "hint":
                    self.hint_var.set(payload)
                elif kind == "results":
                    self._show_results(*payload)
                elif kind == "popup":
                    popups.show_popup(self, *payload)
            except Exception as exc:
                try:
                    self.add_log(f"UI error ({kind}): {exc}")
                except Exception:
                    pass

        if self.monitor is not None and not self.monitor.is_alive():
            self.monitor = None
            self.sleep_guard.stop()
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.check_button.config(state="disabled")
        self.after(200, self._drain_messages)

    def _on_close(self) -> None:
        monitor = self.monitor
        if monitor is not None:
            monitor.stop()
            monitor.join(timeout=2.0)   # let it finish writing alerts.log before the file is emptied
        self.sleep_guard.stop()
        try:
            if load_settings().clear_alerts_log_on_exit:
                clear_alerts_log()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    App().mainloop()
