"""Sticky side panel: follows the Arknights window and shows the last scan."""

from __future__ import annotations

import tempfile
import threading
import tkinter as tk
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .capture import capture_client_area
from .cli import _to_recognition_dict
from .dialogs import dark_scrollbar
from .game import GameData, Recognition
from .ocr import OcrEngine
from .paths import counter_data_path, game_data_path, screenshots_dir
from .recognize import read_image, recognize
from .stackers import StackerPreset, effective_operator, remaining, tier_roman
from .updater import UpdateInfo, check_latest, download_and_apply
from .version import __version__
from .winutil import find_game_window, is_minimized, is_window, set_dpi_aware, window_rect


BG = "#0c161a"
PANEL = "#112328"
BORDER = "#25464a"
TEXT = "#e7f3f2"
MUTED = "#8aa9a8"
ACCENT = "#39d1c4"
OK = "#6fe3a8"
BAD = "#e87575"
WARN = "#e8b768"
FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")

WIDTH = 360
MARGIN = 8


class SidePanel:
    def __init__(self, preset_path: Path, pid: int | None = None):
        set_dpi_aware()
        self.preset_path = Path(preset_path)
        self.pid = int(pid) if pid else 0
        self.hwnd = 0
        self.follow = True
        self.closed = False
        self.busy = False
        self.ocr: OcrEngine | None = None
        self.game = None
        self.last_rec: dict[str, Any] | None = None
        self.last_rec_dict: dict[str, Any] | None = None
        self.cov_available: dict[str, bool] = {}
        self.cov_collapsed: set[str] = set()
        self.filter_covs: set[str] = set()
        self.selected_ops: set[str] = set()
        self.updating = False
        self.update_info: UpdateInfo | None = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        try:
            self.root.attributes("-alpha", 0.98)
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build()
        self._load_game_data()
        self._place_initial()
        self._tick()
        self.root.after(150, self._initial_scan_hint)
        self.root.after(3000, self._auto_check_update)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        outer.pack(fill="both", expand=True)

        self.header = tk.Frame(outer, bg=BG, height=52)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        title = tk.Label(
            self.header,
            text="ASPA 卫戍速查",
            bg=BG,
            fg=ACCENT,
            font=FONT_TITLE,
            anchor="w",
        )
        title.pack(side="left", padx=(12, 6), pady=(9, 0))

        self.close_btn = tk.Button(
            self.header,
            text="×",
            command=self.close,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 13),
            cursor="hand2",
        )
        self.close_btn.pack(side="right", padx=(0, 8), pady=(6, 0))

        self.follow_btn = tk.Button(
            self.header,
            text="贴边",
            command=self._toggle_follow,
            bg=PANEL,
            fg=ACCENT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            padx=8,
            pady=3,
            cursor="hand2",
        )
        self.follow_btn.pack(side="right", padx=4, pady=(7, 0))

        self.edit_btn = tk.Button(
            self.header,
            text="设置",
            command=self._open_editor,
            bg=PANEL,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            padx=8,
            pady=3,
            cursor="hand2",
        )
        self.edit_btn.pack(side="right", padx=4, pady=(7, 0))

        self._bind_drag(self.header)
        self._bind_drag(title)

        sub = tk.Frame(outer, bg=BG)
        sub.pack(fill="x", padx=12, pady=(0, 6))
        self.preset_label = tk.Label(
            sub,
            text=self.preset_path.name,
            bg=BG,
            fg=MUTED,
            font=FONT_SMALL,
            anchor="e",
        )
        self.preset_label.pack(side="right")

        body = tk.Frame(outer, bg=PANEL)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.canvas = tk.Canvas(body, bg=PANEL, highlightthickness=0)
        self.scroll = dark_scrollbar(body, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=PANEL)
        self._content_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._content_window, width=e.width)
        )
        self.root.bind("<MouseWheel>", self._on_wheel, add="+")
        self.info = tk.Label(
            self.content, text="等待截图", bg=PANEL, fg=ACCENT,
            font=("Microsoft YaHei UI", 11, "bold"), anchor="w", justify="left", wraplength=300,
        )
        self.info.pack(fill="x", padx=10, pady=(10, 2))
        self.detail = tk.Label(
            self.content, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL,
            anchor="w", justify="left", wraplength=300,
        )
        self.detail.pack(fill="x", padx=10, pady=(0, 4))
        self.cov_frame = tk.Frame(self.content, bg=PANEL)
        self.cov_frame.pack(fill="x", padx=6, pady=(2, 0))
        tools = tk.Frame(self.content, bg=PANEL)
        tools.pack(fill="x", padx=10, pady=(4, 2))
        tk.Button(
            tools, text="全可", command=lambda: self._set_all_cov(True), bg=BG, fg=ACCENT,
            activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=7, pady=3, cursor="hand2",
        ).pack(side="left")
        tk.Button(
            tools, text="全禁", command=lambda: self._set_all_cov(False), bg=BG, fg=ACCENT,
            activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=7, pady=3, cursor="hand2",
        ).pack(side="left", padx=4)
        self.cov_label = tk.Label(
            self.content, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL,
            anchor="w", justify="left", wraplength=300,
        )
        self.cov_label.pack(fill="x", padx=10, pady=(2, 0))
        self.filter_label = tk.Label(
            self.content, text="筛选（从未禁用的盟约中选择）", bg=PANEL, fg=MUTED,
            font=FONT_SMALL, anchor="w", justify="left", wraplength=300,
        )
        self.filter_label.pack(fill="x", padx=10, pady=(4, 0))
        self.filter_frame = tk.Frame(self.content, bg=PANEL)
        self.filter_frame.pack(fill="x", padx=4, pady=(1, 2))
        self.selected_label = tk.Label(
            self.content, text="已选组合：未勾选", bg=PANEL, fg=OK,
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w", justify="left", wraplength=300,
        )
        self.selected_label.pack(fill="x", padx=10, pady=(2, 4))
        self.ops_frame = tk.Frame(self.content, bg=PANEL)
        self.ops_frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        self.scan_btn = tk.Button(
            footer,
            text="截图识别",
            command=self.scan,
            bg=ACCENT,
            fg="#062022",
            activebackground="#6ee7dc",
            activeforeground="#062022",
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 11, "bold"),
            padx=10,
            pady=8,
            cursor="hand2",
        )
        self.scan_btn.pack(side="left", fill="x", expand=True)

        self.reload_btn = tk.Button(
            footer,
            text="重载名单",
            command=self._reload_preset,
            bg=PANEL,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            padx=8,
            pady=8,
            cursor="hand2",
        )
        self.reload_btn.pack(side="left", padx=(6, 0))

        self.update_btn = tk.Button(
            footer,
            text="更新",
            command=self._on_update_click,
            bg=PANEL,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            padx=8,
            pady=8,
            cursor="hand2",
        )
        self.update_btn.pack(side="left", padx=(6, 0))

        self.status = tk.Label(
            outer, text=f"v{__version__} · 准备就绪", bg=BG, fg=MUTED,
            font=FONT_SMALL, anchor="w", justify="left", wraplength=330,
        )
        self.status.pack(fill="x", padx=12, pady=(0, 6))

        self._render_initial()

    def _bind_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, event) -> None:
        self._drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if not hasattr(self, "_drag"):
            return
        x0, y0, wx, wy = self._drag
        self.follow = False
        self.follow_btn.configure(text="自由", fg=MUTED)
        self.root.geometry(f"+{wx + event.x_root - x0}+{wy + event.y_root - y0}")

    # -------------------------------------------------------------- geometry
    def _find_game(self) -> None:
        if not is_window(self.hwnd):
            hwnd, pid = find_game_window()
            self.hwnd, self.pid = hwnd, pid

    def _place_initial(self) -> None:
        self._find_game()
        rect = window_rect(self.hwnd)
        if rect and not is_minimized(self.hwnd) and rect[0] > -10000:
            self._place_at(rect)
        else:
            self._default_geometry()

    def _default_geometry(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{WIDTH}x{min(900, sh - 80)}+{sw - WIDTH - 20}+60")

    def _place_at(self, rect: tuple[int, int, int, int]) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        left, top, right, bottom = rect
        height = max(520, min(bottom - top, sh - top - 20))
        x = right + MARGIN
        if x + WIDTH > sw - 4:
            x = left - WIDTH - MARGIN
        if x < 4:
            x = max(4, sw - WIDTH - MARGIN)
        y = max(4, top)
        if y + height > sh - 4:
            height = sh - y - 8
        self.root.geometry(f"{WIDTH}x{height}+{x}+{y}")

    def _tick(self) -> None:
        if self.closed:
            return
        try:
            self._find_game()
            if not self.hwnd:
                if not (self.updating or self.busy or self.update_info is not None):
                    self.status.configure(text="游戏未运行；编辑/更新仍可用", fg=WARN)
                self.root.deiconify()
            elif is_minimized(self.hwnd):
                if not (self.updating or self.busy or self.update_info is not None):
                    self.status.configure(text="游戏窗口已最小化，恢复后可截图", fg=WARN)
                if self.root.state() == "withdrawn":
                    self.root.deiconify()
            else:
                if self.root.state() == "withdrawn":
                    self.root.deiconify()
                if self.follow:
                    rect = window_rect(self.hwnd)
                    if rect:
                        self._place_at(rect)
        except Exception:
            pass
        self.root.after(300, self._tick)

    def _toggle_follow(self) -> None:
        self.follow = not self.follow
        self.follow_btn.configure(text="贴边" if self.follow else "自由", fg=ACCENT if self.follow else MUTED)
        if self.follow:
            self._find_game()
            rect = window_rect(self.hwnd)
            if rect and not is_minimized(self.hwnd) and rect[0] > -10000:
                self._place_at(rect)
            else:
                self._default_geometry()

    # -------------------------------------------------------------- content
    def _on_wheel(self, event) -> str:
        if event.widget.winfo_toplevel() is not self.root:
            return ""
        delta = int(-event.delta / 120) if event.delta else 0
        if delta:
            self.canvas.yview_scroll(delta, "units")
        return "break"

    def _major_names(self) -> list[str]:
        return [c.name for c in self.game.majors] if self.game is not None else []

    def _minor_names(self) -> list[str]:
        return [c.name for c in self.game.minors] if self.game is not None else []

    def _build_cov(self) -> None:
        for child in self.cov_frame.winfo_children():
            child.destroy()
        self.cov_vars: dict[str, tk.BooleanVar] = {}
        self.cov_groups: dict[str, tuple[tk.Frame, tk.Button]] = {}
        self.cov_group_headers: dict[str, tk.Frame] = {}
        if self.game is None:
            return
        for label, names in (("核心盟约", self._major_names()), ("附加盟约", self._minor_names())):
            header = tk.Frame(self.cov_frame, bg=PANEL)
            header.pack(fill="x", padx=2, pady=(4, 0))
            button = tk.Button(
                header,
                text=("▾ " if label not in self.cov_collapsed else "▸ ") + label,
                command=lambda name=label: self._toggle_cov_group(name),
                bg=PANEL, fg=ACCENT, activebackground=BORDER, activeforeground=TEXT,
                relief="flat", bd=0, font=("Microsoft YaHei UI", 10, "bold"),
                anchor="w", padx=2, pady=1, cursor="hand2",
            )
            button.pack(side="left")
            grid = tk.Frame(self.cov_frame, bg=PANEL)
            grid.pack(fill="x", padx=2)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)
            for i, name in enumerate(names):
                var = tk.BooleanVar(value=self.cov_available.get(name, False))
                self.cov_vars[name] = var
                tk.Checkbutton(
                    grid,
                    text=name,
                    variable=var,
                    command=lambda n=name: self._toggle_cov(n),
                    anchor="w",
                    bg=PANEL,
                    fg=TEXT,
                    activebackground=PANEL,
                    activeforeground=ACCENT,
                    selectcolor="#0a2024",
                    font=FONT_SMALL,
                    bd=0,
                    highlightthickness=0,
                ).grid(row=i // 2, column=i % 2, sticky="w", padx=3, pady=0)
            self.cov_groups[label] = (grid, button)
            self.cov_group_headers[label] = header
            if label in self.cov_collapsed:
                grid.pack_forget()
        self._build_filter_options()

    def _toggle_cov_group(self, label: str) -> None:
        grid, button = self.cov_groups[label]
        if label in self.cov_collapsed:
            self.cov_collapsed.discard(label)
            # Reinsert core before the additional header instead of appending
            # after it; this avoids rebuilding (and any white flash).
            before = self.cov_group_headers.get("附加盟约") if label == "核心盟约" else None
            if before is not None:
                grid.pack(fill="x", padx=2, before=before)
            else:
                grid.pack(fill="x", padx=2)
            button.configure(text="▾ " + label)
        else:
            self.cov_collapsed.add(label)
            grid.pack_forget()
            button.configure(text="▸ " + label)

    def _toggle_cov(self, name: str) -> None:
        self.cov_available[name] = self.cov_vars[name].get()
        self._update_cov_label()
        self._build_filter_options()
        self._render_ops()

    def _set_all_cov(self, value: bool) -> None:
        for name, var in self.cov_vars.items():
            var.set(value)
            self.cov_available[name] = value
        self._update_cov_label()
        self._build_filter_options()
        self._render_ops()

    def _sync_cov_vars(self) -> None:
        for name, var in self.cov_vars.items():
            var.set(self.cov_available.get(name, False))

    def _build_filter_options(self) -> None:
        for child in self.filter_frame.winfo_children():
            child.destroy()
        self.filter_vars: dict[str, tk.BooleanVar] = {}
        available = [n for n in self._major_names() + self._minor_names() if self.cov_available.get(n)]
        self.filter_covs &= set(available)
        if not available:
            self.filter_label.configure(text="筛选：先勾选上方可用盟约")
            return
        self.filter_label.configure(text="筛选（默认不选，点击平铺项）：")
        grid = tk.Frame(self.filter_frame, bg=PANEL)
        grid.pack(fill="x", padx=2)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for i, name in enumerate(available):
            var = tk.BooleanVar(value=name in self.filter_covs)
            self.filter_vars[name] = var
            tk.Checkbutton(
                grid,
                text=name,
                variable=var,
                command=lambda n=name: self._toggle_filter(n),
                anchor="w",
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                activeforeground=ACCENT,
                selectcolor="#0a2024",
                font=FONT_SMALL,
                bd=0,
                highlightthickness=0,
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=3, pady=0)

    def _toggle_filter(self, name: str) -> None:
        if self.filter_vars[name].get():
            self.filter_covs.add(name)
        else:
            self.filter_covs.discard(name)
        self._render_ops()

    def _render_initial(self) -> None:
        self.info.configure(text="等待截图")
        self.detail.configure(
            text="把游戏停在「确认本局信息」页，点「截图识别」。\n"
            "没有截图也可以直接勾选上方盟约，再点筛选区的盟约查看干员。"
        )
        self._update_cov_label()
        self._build_filter_options()
        self._update_selected_label()
        self._clear_ops("先选择盟约筛选，或勾选已选干员。")

    def _clear_ops(self, hint: str = "") -> None:
        for child in self.ops_frame.winfo_children():
            child.destroy()
        if hint:
            tk.Label(
                self.ops_frame, text=hint, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                anchor="w", justify="left", wraplength=300,
            ).pack(fill="x", padx=8, pady=10)

    def _update_cov_label(self) -> None:
        majors = self._major_names()
        minors = self._minor_names()
        if not majors and not minors:
            self.cov_label.configure(text="盟约：数据未加载")
            return
        ma = sum(1 for n in majors if self.cov_available.get(n))
        mi = sum(1 for n in minors if self.cov_available.get(n))
        self.cov_label.configure(text=f"盟约：大 {ma}/{len(majors)} 可用 · 小 {mi}/{len(minors)} 可用")

    def _update_selected_label(self) -> None:
        if not self.selected_ops or self.game is None:
            self.selected_label.configure(text="已选组合：未勾选")
            return
        try:
            preset = StackerPreset.load(self.preset_path, self.game)
        except Exception:
            preset = StackerPreset(self.preset_path, [], {})
        counts: Counter[str] = Counter()
        for name in self.selected_ops:
            op = effective_operator(self.game, name, preset)
            if op is None:
                continue
            counts.update(op.covenants)
        if not counts:
            self.selected_label.configure(text="已选组合：—")
            return
        text = "  ".join(f"{count}{name}" for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        self.selected_label.configure(text=f"已选组合：{text}")

    def _recognition(self) -> Recognition:
        return Recognition(
            major_banned={n for n in self._major_names() if not self.cov_available.get(n)},
            minor_banned={n for n in self._minor_names() if not self.cov_available.get(n)},
        )

    def _apply_recognition(self, rec: dict) -> None:
        self.cov_available = {name: False for name in self._major_names() + self._minor_names()}
        cov = rec.get("covenants") or {}
        for cls in ("major", "minor"):
            part = cov.get(cls) or {}
            for name in part.get("available") or []:
                if name in self.cov_available:
                    self.cov_available[name] = True
            for name in part.get("disabled") or []:
                if name in self.cov_available:
                    self.cov_available[name] = False
        self._sync_cov_vars()
        self._update_cov_label()
        self._build_filter_options()
        self._render_ops()

    def _toggle_operator(self, name: str, var: tk.BooleanVar) -> None:
        if var.get():
            self.selected_ops.add(name)
        else:
            self.selected_ops.discard(name)
        self._update_selected_label()
        self._render_ops()

    def _render_ops(self) -> None:
        self._update_selected_label()
        self._clear_ops()
        if self.game is None:
            self._clear_ops("干员数据未加载。")
            return
        try:
            preset = StackerPreset.load(self.preset_path, self.game)
        except Exception as exc:
            self._clear_ops(f"读取叠层手名单失败：{exc}")
            return
        rows, _banned, _unknown = remaining(self.game, self._recognition(), preset)
        available = {op.name for op in rows}
        self.selected_ops &= available
        shown = [
            op
            for op in rows
            if op.name in self.selected_ops
            or (self.filter_covs and set(op.covenants) & self.filter_covs)
        ]
        shown.sort(key=lambda op: (op.name not in self.selected_ops, -op.tier, op.name))
        if not shown:
            if self.filter_covs:
                self._clear_ops("当前筛选下没有剩余叠层手。")
            else:
                self._clear_ops("先选择盟约筛选，或勾选已选干员。")
            return
        for op in shown:
            var = tk.BooleanVar(value=op.name in self.selected_ops)
            cb = tk.Checkbutton(
                self.ops_frame,
                text=f"{tier_roman(op.tier)}   {op.name}",
                variable=var,
                command=lambda n=op.name, v=var: self._toggle_operator(n, v),
                anchor="w",
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                activeforeground=ACCENT,
                selectcolor="#0a2024",
                font=FONT,
                bd=0,
                highlightthickness=0,
            )
            cb.pack(fill="x", padx=8, pady=1)
        self._update_selected_label()

    def _text_if_empty(self) -> None:
        """Kept for the window tick; content is now manual/state driven."""
        return

    def _load_game_data(self) -> None:
        try:
            self.game = GameData(game_data_path(), counter_data_path())
            self._build_cov()
            if self.last_rec is None:
                self._render_initial()
        except Exception as exc:
            self.status.configure(text=f"数据加载失败：{exc}", fg=BAD)
            self.game = None

    def _reload_preset(self) -> None:
        self.status.configure(text="叠层手名单已重载", fg=MUTED)
        self._render_ops()

    def _auto_check_update(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        threading.Thread(target=self._check_update_worker, args=(False,), daemon=True).start()

    def _on_update_click(self) -> None:
        if self.update_info is not None:
            self._download_update()
            return
        self.update_btn.configure(state="disabled", text="检查中…")
        threading.Thread(target=self._check_update_worker, args=(True,), daemon=True).start()

    def _check_update_worker(self, manual: bool) -> None:
        try:
            info = check_latest()
            self.root.after(0, lambda: self._update_checked(info, manual))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self._update_failed(message, manual))

    def _update_checked(self, info: UpdateInfo | None, manual: bool) -> None:
        self.update_btn.configure(state="normal")
        if info is not None:
            self.update_info = info
            self.status.configure(text=f"发现新版本 v{info.version}", fg=ACCENT)
            self.update_btn.configure(text=f"更新 v{info.version}", fg=ACCENT)
            if manual:
                from tkinter import messagebox

                messagebox.showinfo(
                    "发现新版本",
                    f"v{info.version}\n\n{info.notes[:800]}",
                    parent=self.root,
                )
        else:
            self.update_btn.configure(text="更新")
            self.status.configure(text="已是最新版本" if manual else "更新检查完成", fg=MUTED)

    def _update_failed(self, message: str, manual: bool) -> None:
        self.updating = False
        self.update_btn.configure(state="normal", text="更新")
        if manual:
            self.status.configure(text=f"更新失败：{message[:80]}", fg=BAD)

    def _download_update(self) -> None:
        if self.update_info is None:
            return
        from tkinter import messagebox

        if not messagebox.askyesno(
            "更新",
            f"更新到 v{self.update_info.version}？\n应用会自动退出、替换文件并重启。",
            parent=self.root,
        ):
            return
        self.status.configure(text="下载更新中…", fg=WARN)
        self.update_btn.configure(state="disabled")
        self.updating = True
        info = self.update_info

        def progress(received: int, total: int | None) -> None:
            mb = received / (1024 * 1024)
            if total:
                percent = min(100, int(received * 100 / total))
                text = f"下载更新 {percent}%（{mb:.1f}/{total / 1024 / 1024:.1f} MB）"
            else:
                text = f"下载更新中… {mb:.1f} MB"
            self.root.after(0, lambda: self.status.configure(text=text, fg=WARN))

        def worker() -> None:
            try:
                download_and_apply(
                    info,
                    on_status=lambda text: self.root.after(
                        0, lambda: self.status.configure(text=text, fg=WARN)
                    ),
                    on_progress=progress,
                )
                self.root.after(800, self.close)
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda: self._update_failed(message, True))

        threading.Thread(target=worker, daemon=True).start()

    def _initial_scan_hint(self) -> None:
        if not self.hwnd:
            self._text_if_empty()

    # ---------------------------------------------------------------- scan
    def scan(self) -> None:
        if self.busy:
            return
        self._find_game()
        if not self.hwnd:
            self.status.configure(text="找不到游戏窗口", fg=BAD)
            return
        if is_minimized(self.hwnd):
            self.status.configure(text="请先恢复游戏窗口", fg=BAD)
            return
        if self.game is None:
            self.status.configure(text="数据未加载", fg=BAD)
            return
        self.busy = True
        self.scan_btn.configure(state="disabled", text="识别中…")
        self.status.configure(text="截图中…", fg=WARN)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        tmpdir: Path | None = None
        try:
            tmpdir = Path(tempfile.mkdtemp())
            path = tmpdir / "capture.png"
            shots = screenshots_dir()
            debug = shots / "last_scan_debug.png"
            capture_client_area(path, self.pid or None)
            try:
                (shots / "last_capture.png").write_bytes(path.read_bytes())
            except Exception:
                pass
            image = read_image(path)
            if image is None:
                raise RuntimeError("无法读取截图")
            if self.ocr is None:
                self.ocr = OcrEngine()
            rec = recognize(image, self.ocr, debug_path=debug)
            if not rec.get("ok"):
                raise RuntimeError("；".join(rec.get("warnings") or ["未识别到确认本局信息"]))

            rec_dict, _ = _to_recognition_dict(rec)
            self.root.after(0, lambda: self._show_result(rec, rec_dict))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self._show_error(message))
        finally:
            if tmpdir is not None:
                try:
                    for p in tmpdir.rglob("*"):
                        if p.is_file():
                            p.unlink()
                    tmpdir.rmdir()
                except Exception:
                    pass

    def _show_error(self, message: str) -> None:
        self.busy = False
        self.scan_btn.configure(state="normal", text="截图识别")
        self.status.configure(text="识别失败；可手动调整盟约", fg=BAD)
        self.info.configure(text="识别失败")
        self.detail.configure(
            text=f"{message}\n可点「调整盟约」手动设置本局状态，不依赖截图。"
        )

    def _show_result(self, rec: dict, rec_dict: dict) -> None:
        self.busy = False
        self.last_rec, self.last_rec_dict = rec, rec_dict
        self.scan_btn.configure(state="normal", text="截图识别")
        self.status.configure(text=f"识别完成 · 置信度 {rec.get('confidence')}", fg=MUTED)
        enemy = rec.get("enemy") or {}
        self.info.configure(text="本次识别")
        self.detail.configure(
            text=f"敌人：{enemy.get('leader') or '—'} · {'/'.join(enemy.get('tags') or []) or '未读到'}"
        )
        self._apply_recognition(rec)

    # -------------------------------------------------------------- editor
    def _open_editor(self) -> None:
        if self.game is None:
            self.status.configure(text="数据未加载，无法编辑", fg=BAD)
            return
        try:
            from .editor import PresetEditor

            PresetEditor(self.root, self.game, self.preset_path, on_saved=self._on_preset_saved)
        except Exception as exc:
            self.status.configure(text=f"编辑器打开失败：{exc}", fg=BAD)

    def _on_preset_saved(self, path: Path | None = None) -> None:
        if path is not None:
            self.preset_path = Path(path)
        self.preset_label.configure(text=self.preset_path.name)
        self.status.configure(text="叠层手名单已保存", fg=MUTED)
        if self.last_rec is not None and self.last_rec_dict is not None:
            self._show_result(self.last_rec, self.last_rec_dict)
        else:
            self._render_ops()

    def close(self) -> None:
        self.closed = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def launch_ui(preset_path: Path, pid: int | None = None) -> int:
    panel = SidePanel(preset_path, pid=pid)
    panel.run()
    return 0
