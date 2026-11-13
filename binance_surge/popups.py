"""Always-on-top alert popups with clickable buttons (macOS banners cannot have buttons).

The popups are drawn by the app itself, so they always appear, even if banners are muted (Focus / Do Not Disturb).
Must be created on the Tk (UI) thread.
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import vataga
from .settings import load_settings

POPUP_W, POPUP_H, GAP = 360, 150, 8
IS_MAC = sys.platform == "darwin"
_used_slots: set[int] = set()
_open: list = []


def _free_slot() -> int:
    slot = 0
    while slot in _used_slots:
        slot += 1
    return slot


class AlertPopup(tk.Toplevel):
    def __init__(self, master: tk.Misc, symbol: str, title: str, message: str, direction: int = 0) -> None:
        super().__init__(master)
        self.symbol = symbol
        self.slot = _free_slot()
        _used_slots.add(self.slot)

        if IS_MAC:
            # borderless windows are unreliable in Tk on macOS: use a normal small window with a title bar
            self.title(title)
            self.resizable(False, False)
            self.protocol("WM_DELETE_WINDOW", self.close)
        else:
            self.overrideredirect(True)
        self.attributes("-topmost", True)
        # Stack upwards from the bottom-right corner; when a column is full, start a new one to the left.
        rows = max(1, (self.winfo_screenheight() - 110) // (POPUP_H + GAP))
        col, row = divmod(self.slot, rows)
        x = max(self.winfo_screenwidth() - (col + 1) * (POPUP_W + GAP) - 8, 0)
        y = self.winfo_screenheight() - 110 - (row + 1) * (POPUP_H + GAP)
        self.geometry(f"{POPUP_W}x{POPUP_H}+{x}+{max(y, 0)}")
        _open.append(self)

        warning = not symbol
        if warning:
            accent = "#0ecb81" if "restored" in title.lower() else "#f6465d"
        else:
            accent = "#f6465d" if direction < 0 else "#f0b90b"
        frame = tk.Frame(self, bg="#1e2329", highlightbackground=accent, highlightthickness=2)
        frame.pack(fill="both", expand=True)

        head = tk.Frame(frame, bg="#1e2329")
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text=title, bg="#1e2329", fg=accent, font=("Helvetica", 13, "bold")).pack(side="left")
        close = tk.Label(head, text="✕", bg="#1e2329", fg="#9aa4af", cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())

        tk.Label(
            frame, text=message, bg="#1e2329", fg="#eaecef", justify="left", wraplength=POPUP_W - 24
        ).pack(anchor="w", padx=10, pady=(2, 4))

        self.status = tk.Label(frame, text="", bg="#1e2329", fg="#f6465d", wraplength=POPUP_W - 24, justify="left")
        row = tk.Frame(frame, bg="#1e2329")
        row.pack(fill="x", padx=10, pady=(0, 8), side="bottom")
        if not warning:
            ttk.Button(row, text="Open in Vataga", command=self.open_vataga).pack(side="left")
            ttk.Button(row, text="Open on Binance", command=self.open_binance).pack(side="left", padx=6)
        self.status.pack(anchor="w", padx=10, side="bottom")

        seconds = load_settings().popup_auto_close_sec
        if seconds > 0:
            self.after(seconds * 1000, self.close)

    def open_vataga(self) -> None:
        def work() -> None:
            ok, message = vataga.open_symbol(self.symbol, load_settings())
            self.after(0, lambda: self._done(ok, message))

        self.status.config(text="Sending to Vataga.terminal...", fg="#9aa4af")
        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok: bool, message: str) -> None:
        if not self.winfo_exists():
            return
        if ok:
            self.status.config(text="Opened in Vataga.terminal", fg="#0ecb81")
        else:
            self.status.config(text=message, fg="#f6465d")

    def open_binance(self) -> None:
        webbrowser.open(f"https://www.binance.com/en/futures/{self.symbol}")

    def close(self) -> None:
        if self.winfo_exists():
            _used_slots.discard(self.slot)
            if self in _open:
                _open.remove(self)
            self.destroy()


def show_popup(master: tk.Misc, symbol: str, title: str, message: str, direction: int = 0) -> None:
    AlertPopup(master, symbol, title, message, direction)


def close_all() -> None:
    for popup in list(_open):
        popup.close()
