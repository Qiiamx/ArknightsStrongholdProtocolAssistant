"""Stacker-list editor for ASPA.

A preset is now just the list of stacking operators to keep an eye on.  The
old lineup/weight/enemy-fit editor was removed as part of the v0.2 refactor.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

from .dialogs import CovenantCheckDialog, dark_scrollbar
from .game import GameData
from .paths import load_settings, save_settings, settings_path
from .stackers import StackerPreset, all_stackers, effective_operator, group_by_tier, tier_roman

BG = "#0c161a"
PANEL = "#112328"
BORDER = "#25464a"
TEXT = "#e7f3f2"
MUTED = "#8aa9a8"
ACCENT = "#39d1c4"
BAD = "#e87575"
OK = "#65d49a"
FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)


class PresetEditor:
    def __init__(
        self,
        parent: tk.Misc,
        game: GameData,
        preset_path: Path,
        on_saved: Callable[[Path], None] | None = None,
    ):
        self.game = game
        self.path = Path(preset_path)
        self.on_saved = on_saved
        self.data = StackerPreset.load(self.path, game)
        self.settings = load_settings()
        self.proxy_var = tk.StringVar(value=str(self.settings.get("proxy") or ""))
        self.stackers = all_stackers(game)
        self.selected = set(self.data.names)
        self.overrides = dict(self.data.overrides)
        self.vars: dict[str, tk.BooleanVar] = {}
        self._query = ""

        self.win = tk.Toplevel(parent)
        self.win.title("ASPA 设置")
        sw, sh = parent.winfo_screenwidth(), parent.winfo_screenheight()
        w, h = min(980, sw - 40), min(780, sh - 60)
        self.win.geometry(f"{w}x{h}+{max(10, (sw - w) // 2)}+{max(10, (sh - h) // 2)}")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._refresh()

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="设置", bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(top, text=str(self.path), bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left", padx=12)

        tk.Button(
            top, text="保存", command=self.save, bg=ACCENT, fg="#062022", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=4, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            top, text="另存为", command=self.save_as, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4, cursor="hand2",
        ).pack(side="right", padx=6)
        tk.Button(
            top, text="关闭", command=self.close, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4, cursor="hand2",
        ).pack(side="right", padx=6)

        tk.Label(
            self.win,
            text="勾选本局要关注的叠层手。识别后侧栏只展示名单里没被 ban 的；名单外的干员不会出现。",
            bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(
            self.win,
            text="点击「盟约…」可改该干员的盟约。判定规则：所有大盟约被 ban，或没有大盟约且所有小盟约被 ban，才视为被 ban。",
            bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w", wraplength=900, justify="left",
        ).pack(fill="x", padx=12, pady=(0, 4))

        proxy_box = tk.Frame(self.win, bg=PANEL)
        proxy_box.pack(fill="x", padx=12, pady=(4, 6))
        proxy_row = tk.Frame(proxy_box, bg=PANEL)
        proxy_row.pack(fill="x")
        tk.Label(proxy_row, text="更新代理", bg=PANEL, fg=TEXT, font=FONT).pack(side="left", padx=(10, 8), pady=6)
        tk.Entry(
            proxy_row, textvariable=self.proxy_var, width=44, bg="#0a2024", fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=4, font=FONT,
        ).pack(side="left", padx=(0, 8), pady=6)
        tk.Label(
            proxy_row, text="留空 = 直连 / 环境变量", bg=PANEL, fg=MUTED, font=FONT_SMALL,
        ).pack(side="left", pady=6)
        tk.Label(
            proxy_box,
            text=f"保存到 {settings_path()}；这是用户配置，版本更新不会覆盖。",
            bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=900,
        ).pack(fill="x", padx=10, pady=(0, 5))

        bar = tk.Frame(self.win, bg=BG)
        bar.pack(fill="x", padx=12, pady=(4, 6))
        tk.Label(bar, text="搜索", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left")
        self.search_var = tk.StringVar()
        search = tk.Entry(
            bar, textvariable=self.search_var, width=22, bg="#0a2024", fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=4, font=FONT,
        )
        search.pack(side="left", padx=6)
        self.search_var.trace_add("write", lambda *_: self._refresh())
        for text, cmd in (("全选", self.select_all), ("清空", self.clear)):
            tk.Button(
                bar, text=text, command=cmd, bg=PANEL, fg=TEXT, relief="flat", bd=0,
                font=FONT_SMALL, padx=10, pady=3, cursor="hand2",
            ).pack(side="left", padx=(4, 0))
        self.count_label = tk.Label(bar, text="", bg=BG, fg=MUTED, font=FONT_SMALL)
        self.count_label.pack(side="right")

        wrap = tk.Frame(self.win, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        scroll = dark_scrollbar(wrap, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = tk.Frame(canvas, bg=PANEL)
        window = canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        self.canvas = canvas

        self.status = tk.Label(self.win, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 8))

    def _visible_stackers(self):
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        if not query:
            return self.stackers
        return [
            op
            for op in self.stackers
            if query in op.name.lower()
            or any(query in c.lower() for c in op.covenants)
            or query == tier_roman(op.tier).lower()
        ]

    def _refresh(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.vars = {}
        visible = self._visible_stackers()
        groups = group_by_tier(visible)
        for tier, ops in groups:
            header = tk.Frame(self.list_frame, bg=BG)
            header.pack(fill="x", pady=(8, 2), padx=6)
            tk.Label(
                header, text=f"等级 {tier_roman(tier)}", bg=BG, fg=ACCENT,
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(side="left")
            tk.Label(header, text=f"{len(ops)} 人", bg=BG, fg=MUTED, font=FONT_SMALL).pack(
                side="left", padx=8
            )
            grid = tk.Frame(self.list_frame, bg=PANEL)
            grid.pack(fill="x", padx=6, pady=2)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)
            for index, op in enumerate(ops):
                var = tk.BooleanVar(value=op.name in self.selected)
                self.vars[op.name] = var
                cell = tk.Frame(grid, bg=PANEL)
                cell.grid(row=index // 2, column=index % 2, sticky="w", padx=6, pady=2)
                cb = tk.Checkbutton(
                    cell,
                    text=op.name,
                    variable=var,
                    command=lambda name=op.name: self._toggle(name),
                    anchor="w",
                    justify="left",
                    bg=PANEL,
                    fg=TEXT,
                    activebackground=PANEL,
                    activeforeground=ACCENT,
                    selectcolor="#0a2024",
                    font=FONT,
                    bd=0,
                    highlightthickness=0,
                )
                cb.pack(side="left")
                tk.Button(
                    cell,
                    text="盟约…",
                    command=lambda name=op.name: self.edit_covenants(name),
                    bg=BG,
                    fg=ACCENT,
                    activebackground=BORDER,
                    activeforeground=TEXT,
                    relief="flat",
                    bd=0,
                    font=FONT_SMALL,
                    padx=5,
                    pady=0,
                    cursor="hand2",
                ).pack(side="left", padx=(2, 0))
        if not groups:
            tk.Label(
                self.list_frame, text="没有匹配的叠层手", bg=PANEL, fg=MUTED, font=FONT
            ).pack(anchor="w", padx=12, pady=20)
        self._update_count()

    def edit_covenants(self, name: str) -> None:
        base = self.game.operators.get(name)
        if base is None:
            return
        temp = StackerPreset(self.path, [], self.overrides)
        op = effective_operator(self.game, name, temp) or base
        groups = {
            "核心盟约": [c.name for c in self.game.majors],
            "附加盟约": [c.name for c in self.game.minors],
        }
        dialog = CovenantCheckDialog(
            self.win,
            f"修改「{name}」的盟约",
            groups,
            set(op.majors) | set(op.minors),
            "勾选该干员实际拥有的盟约；这会覆盖内置数据。",
        )
        result = dialog.show()
        if result is None:
            return
        majors = [n for n in result if n in self.game.covenants and self.game.covenants[n].cls == "major"]
        minors = [n for n in result if n in self.game.covenants and self.game.covenants[n].cls == "minor"]
        self.overrides[name] = {"major": majors, "minor": minors}
        self.status.configure(text=f"{name} 的盟约已修改，点保存写入", fg=MUTED)

    def _toggle(self, name: str) -> None:
        if self.vars[name].get():
            self.selected.add(name)
        else:
            self.selected.discard(name)
        self._update_count()

    def _update_count(self) -> None:
        total = len(self.stackers)
        self.count_label.configure(text=f"已选 {len(self.selected)} / {total}")
        self.status.configure(text=f"名单 {len(self.selected)} 人；勾选后点保存", fg=MUTED)

    def select_all(self) -> None:
        for op in self.stackers:
            self.selected.add(op.name)
        self._refresh()

    def clear(self) -> None:
        self.selected.clear()
        self._refresh()

    # ------------------------------------------------------------ save
    def _names_in_order(self) -> list[str]:
        return [op.name for op in self.stackers if op.name in self.selected]

    def save(self) -> None:
        try:
            preset = StackerPreset(self.path, self._names_in_order(), self.overrides)
            target = preset.save()
            self.data = preset
            self.path = target
            self.settings["proxy"] = self.proxy_var.get().strip()
            settings = save_settings(self.settings)
            self.status.configure(text=f"已保存名单与设置：{settings}", fg=MUTED)
            if self.on_saved:
                self.on_saved(target)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.win)
            self.status.configure(text=f"保存失败：{exc}", fg=BAD)

    def save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="另存叠层手名单",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
            initialfile=self.path.name,
        )
        if not path:
            return
        self.path = Path(path)
        self.save()

    def close(self) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
