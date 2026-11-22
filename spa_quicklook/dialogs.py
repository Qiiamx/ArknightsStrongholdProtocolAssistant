"""Reusable dark checkbox dialog for covenant selection."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable

BG = "#0c161a"
PANEL = "#112328"
BORDER = "#25464a"
TEXT = "#e7f3f2"
MUTED = "#8aa9a8"
ACCENT = "#39d1c4"
FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)


def dark_scrollbar(parent: tk.Misc, **kwargs) -> ttk.Scrollbar:
    style = ttk.Style(parent)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "ASPA.Vertical.TScrollbar",
        gripcount=0,
        background=BORDER,
        darkcolor=BORDER,
        lightcolor=BORDER,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=ACCENT,
        relief="flat",
    )
    style.map(
        "ASPA.Vertical.TScrollbar",
        background=[("active", ACCENT)],
        arrowcolor=[("active", "#062022")],
    )
    return ttk.Scrollbar(parent, orient="vertical", style="ASPA.Vertical.TScrollbar", **kwargs)


class CovenantCheckDialog:
    """Checkbox list for covenants; checked means "available"/"selected"."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        groups: dict[str, list[str]],
        checked: Iterable[str],
        note: str = "",
    ):
        self.result: set[str] | None = None
        self.vars: dict[str, tk.BooleanVar] = {}
        selected = set(checked)

        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("620x680")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()

        tk.Label(self.win, text=title, bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w", padx=12, pady=(10, 2)
        )
        if note:
            tk.Label(
                self.win, text=note, bg=BG, fg=MUTED, font=FONT_SMALL,
                wraplength=580, justify="left",
            ).pack(anchor="w", padx=12, pady=(0, 6))

        wrap = tk.Frame(self.win, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        scroll = dark_scrollbar(wrap, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=PANEL)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        for group, names in groups.items():
            tk.Label(
                body, text=group, bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 11, "bold")
            ).pack(anchor="w", padx=8, pady=(8, 2))
            grid = tk.Frame(body, bg=PANEL)
            grid.pack(fill="x", padx=6)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)
            for i, name in enumerate(names):
                var = tk.BooleanVar(value=name in selected)
                self.vars[name] = var
                tk.Checkbutton(
                    grid,
                    text=name,
                    variable=var,
                    anchor="w",
                    bg=PANEL,
                    fg=TEXT,
                    activebackground=PANEL,
                    activeforeground=ACCENT,
                    selectcolor="#0a2024",
                    font=FONT,
                    bd=0,
                    highlightthickness=0,
                ).grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=2)

        btns = tk.Frame(self.win, bg=BG)
        btns.pack(fill="x", padx=12, pady=8)
        tk.Button(
            btns, text="应用", command=self._apply, bg=ACCENT, fg="#062022",
            relief="flat", bd=0, font=FONT_SMALL, padx=14, pady=4, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            btns, text="取消", command=self._cancel, bg=PANEL, fg=TEXT,
            relief="flat", bd=0, font=FONT_SMALL, padx=12, pady=4, cursor="hand2",
        ).pack(side="right", padx=6)
        tk.Button(
            btns, text="全选", command=self._all, bg=PANEL, fg=TEXT,
            relief="flat", bd=0, font=FONT_SMALL, padx=10, pady=4, cursor="hand2",
        ).pack(side="left")
        tk.Button(
            btns, text="清空", command=self._clear, bg=PANEL, fg=TEXT,
            relief="flat", bd=0, font=FONT_SMALL, padx=10, pady=4, cursor="hand2",
        ).pack(side="left", padx=6)

        self.win.wait_window(self.win)

    def _all(self) -> None:
        for var in self.vars.values():
            var.set(True)

    def _clear(self) -> None:
        for var in self.vars.values():
            var.set(False)

    def _apply(self) -> None:
        self.result = {name for name, var in self.vars.items() if var.get()}
        self.win.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.win.destroy()

    def show(self) -> set[str] | None:
        return self.result
