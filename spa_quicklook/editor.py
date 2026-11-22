"""Preset editor for SPA QuickLook.

A small Tk editor: pick lineups, covenant targets, operators, enemy-tag scores
and global weights; save back to the preset JSON with a backup.
"""

from __future__ import annotations

import json
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from .recognize import ENEMY_TAGS, MAJORS, MINORS

BG = "#0c161a"
PANEL = "#112328"
BORDER = "#25464a"
TEXT = "#e7f3f2"
MUTED = "#8aa9a8"
ACCENT = "#39d1c4"
BAD = "#e87575"
FONT = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)


class Tooltip:
    """Small hover tooltip used by the preset editor's ? marks."""

    def __init__(self, widget: tk.Misc, text: str, delay: int = 350):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._destroy, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self) -> None:
        self._after = None
        if self._tip is not None or not self.widget.winfo_exists():
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_attributes("-topmost", True)
        label = tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            anchor="w",
            bg="#173438",
            fg=TEXT,
            relief="solid",
            bd=1,
            font=FONT_SMALL,
            wraplength=380,
            padx=9,
            pady=7,
        )
        label.pack()
        self._tip.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        width = self._tip.winfo_width()
        height = self._tip.winfo_height()
        if x + width > self.widget.winfo_screenwidth() - 8:
            x = self.widget.winfo_rootx() - width - 8
        if y + height > self.widget.winfo_screenheight() - 8:
            y = self.widget.winfo_rooty() - height - 4
        self._tip.wm_geometry(f"+{max(4, x)}+{max(4, y)}")

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _destroy(self, _event=None) -> None:
        self._cancel()
        self._hide()


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]


def _parse_targets(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for part in _split(text):
        if ":" in part or "：" in part:
            key, value = part.replace("：", ":").split(":", 1)
            key, value = key.strip(), value.strip()
            if key and value.isdigit():
                result[key] = int(value)
    return result


def _targets_text(targets: dict[str, int]) -> str:
    return "，".join(f"{k}:{v}" for k, v in targets.items())


class PresetEditor:
    def __init__(
        self,
        parent: tk.Misc,
        game: Any,
        preset_path: Path,
        on_saved: Callable[[Path], None] | None = None,
    ):
        self.game = game
        self.path = Path(preset_path)
        self.on_saved = on_saved
        self.data = self._load()
        self.lineups: list[dict[str, Any]] = self.data.setdefault("lineups", [])
        self.current = 0
        self.vars: dict[str, tk.Variable] = {}
        self.enemy_vars: dict[str, tk.StringVar] = {}
        self.core_targets: dict[str, int] = {}
        self.minor_targets: dict[str, int] = {}

        self.win = tk.Toplevel(parent)
        self.win.title("SPA 预设编辑器")
        sw, sh = parent.winfo_screenwidth(), parent.winfo_screenheight()
        w, h = min(1020, sw - 40), min(800, sh - 60)
        self.win.geometry(f"{w}x{h}+{max(10, (sw - w) // 2)}+{max(10, (sh - h) // 2)}")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self._refresh_list()
        if self.lineups:
            self.listbox.selection_set(0)
            self._select(0)
        self._refresh_raw()

    # ------------------------------------------------------------ data
    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                messagebox.showerror("预设读取失败", f"{self.path}\n{exc}", parent=getattr(self, "win", None))
        return {
            "schemaVersion": "0.1",
            "scope": {
                "season": "规程",
                "sequence": "序列Ⅰ",
                "availabilityRule": "anyCoreBanned",
            },
            "weights": {"priority": 1.0, "enemy": 2.0, "activation": 1.5, "banResilience": 0.5},
            "synergyPairs": [],
            "lineups": [],
        }

    def _style(self) -> None:
        style = ttk.Style(self.win)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=TEXT, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", BORDER)], foreground=[("selected", ACCENT)])
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("TButton", background=PANEL, foreground=TEXT, font=FONT_SMALL)
        style.configure("TEntry", fieldbackground="#0a2024", foreground=TEXT)
        style.configure("TCombobox", fieldbackground="#0a2024", foreground=TEXT)

    # ------------------------------------------------------------ UI
    def _build(self) -> None:
        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="预设编辑器", bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
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

        self.nb = ttk.Notebook(self.win)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        self.tab_lineup = ttk.Frame(self.nb)
        self.tab_weights = ttk.Frame(self.nb)
        self.tab_raw = ttk.Frame(self.nb)
        self.nb.add(self.tab_lineup, text="阵容")
        self.nb.add(self.tab_weights, text="全局权重")
        self.nb.add(self.tab_raw, text="原始 JSON")
        self._build_lineup()
        self._build_weights()
        self._build_raw()

        self.status = tk.Label(self.win, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 8))

    def _label_with_help(
        self,
        parent: tk.Misc,
        text: str,
        row: int,
        column: int = 0,
        columnspan: int = 1,
        sticky: str = "w",
        padx: tuple[int, int] | int = (12, 8),
        pady: int | tuple[int, int] = 4,
        help_text: str = "",
        fg: str = MUTED,
        font: tuple = FONT_SMALL,
    ) -> tk.Label:
        box = tk.Frame(parent, bg=parent.cget("bg"))
        box.grid(row=row, column=column, columnspan=columnspan, sticky=sticky, padx=padx, pady=pady)
        label = tk.Label(box, text=text, bg=parent.cget("bg"), fg=fg, font=font)
        label.pack(side="left")
        if help_text:
            mark = tk.Label(
                box,
                text="?",
                bg=parent.cget("bg"),
                fg=ACCENT,
                font=("Microsoft YaHei UI", 9, "bold"),
                cursor="hand2",
                padx=3,
            )
            mark.pack(side="left")
            Tooltip(mark, help_text)
            Tooltip(label, help_text)
        return label

    def _build_lineup(self) -> None:
        left = tk.Frame(self.tab_lineup, bg=BG)
        left.pack(side="left", fill="y", padx=(8, 6), pady=8)
        tk.Label(left, text="我的阵容", bg=BG, fg=MUTED, font=FONT_SMALL).pack(anchor="w")
        self.listbox = tk.Listbox(
            left, width=22, bg=PANEL, fg=TEXT, selectbackground=BORDER, selectforeground=ACCENT,
            relief="flat", bd=0, font=FONT, activestyle="none",
        )
        self.listbox.pack(fill="y", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        row = tk.Frame(left, bg=BG)
        row.pack(fill="x")
        for text, cmd in (("+", self.add), ("复制", self.duplicate), ("删除", self.delete)):
            tk.Button(
                row, text=text, command=cmd, bg=PANEL, fg=TEXT, relief="flat", bd=0,
                font=FONT_SMALL, padx=6, pady=3, cursor="hand2",
            ).pack(side="left", padx=(0, 4))

        right = tk.Frame(self.tab_lineup, bg=PANEL)
        right.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)
        canvas = tk.Canvas(right, bg=PANEL, highlightthickness=0)
        scroll = tk.Scrollbar(right, command=canvas.yview, bg=PANEL, troughcolor=PANEL, bd=0)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = tk.Frame(canvas, bg=PANEL)
        canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.form = form

        def field(label: str, key: str, row: int, width: int = 44, help_text: str = "") -> tk.Entry:
            self._label_with_help(form, label, row, 0, padx=(12, 8), pady=4, help_text=help_text)
            var = tk.StringVar()
            self.vars[key] = var
            entry = tk.Entry(
                form, textvariable=var, width=width, bg="#0a2024", fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=4, font=FONT,
            )
            entry.grid(row=row, column=1, sticky="we", padx=(0, 12), pady=4)
            return entry

        r = 0
        field("名称", "name", r); r += 1
        field("ID（唯一）", "id", r); r += 1
        field(
            "主核心",
            "primaryCore",
            r,
            help_text="阵容锚点。若识别到该核心被禁，整套阵容直接不推荐（0 分），不是只扣分。",
        ); r += 1
        field("模式（逗号分隔，可空）", "modes", r); r += 1
        field(
            "优先级（数字）",
            "priority",
            r,
            help_text=(
                "基础分，不是概率。最终评分 += 基础优先级权重 × 这个数字。\n"
                "默认权重 1.0 时：优先级 10 → +10 分，优先级 5 → +5 分。"
            ),
        ); r += 1

        self._label_with_help(
            form,
            "核心盟约目标",
            r,
            0,
            help_text=(
                "格式：盟约:目标层数（如 莱塔尼亚:6）。\n"
                "这个数字用于检查当前卡池够不够激活盟约，本身不直接加分；"
                "如果可用干员数低于门槛，会提示「可能无法激活」，并把激活权重项归零。"
            ),
        )
        self.core_label = tk.Label(form, text="（未设置）", bg=PANEL, fg=TEXT, font=FONT_SMALL, anchor="w")
        self.core_label.grid(row=r, column=1, sticky="w", padx=(0, 6))
        tk.Button(
            form, text="选择…", command=self.pick_core, bg=BG, fg=ACCENT, relief="flat", bd=0,
            font=FONT_SMALL, padx=8, pady=2, cursor="hand2",
        ).grid(row=r, column=2, sticky="w", padx=(0, 12))
        r += 1

        self._label_with_help(
            form,
            "附加盟约目标",
            r,
            0,
            help_text=(
                "格式：盟约:目标层数（如 奥术:2）。\n"
                "同样用于卡池可行性检查，不直接加分；全部附加盟约被禁时整条阵容不推荐。"
            ),
        )
        self.minor_label = tk.Label(form, text="（未设置）", bg=PANEL, fg=TEXT, font=FONT_SMALL, anchor="w")
        self.minor_label.grid(row=r, column=1, sticky="w", padx=(0, 6))
        tk.Button(
            form, text="选择…", command=self.pick_minor, bg=BG, fg=ACCENT, relief="flat", bd=0,
            font=FONT_SMALL, padx=8, pady=2, cursor="hand2",
        ).grid(row=r, column=2, sticky="w", padx=(0, 12))
        r += 1

        for label, key in (("必选干员", "required"), ("核心干员", "core"), ("可选干员", "optional")):
            self._label_with_help(
                form,
                label,
                r,
                0,
                help_text=(
                    "干员被禁的固定扣分（不受权重影响）：核心干员 -2/个，可选干员 -0.5/个；"
                    "必选干员被禁则整条阵容不推荐。"
                ),
            )
            var = tk.StringVar()
            self.vars[key] = var
            tk.Entry(
                form, textvariable=var, width=38, bg="#0a2024", fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=4, font=FONT,
            ).grid(row=r, column=1, sticky="we", padx=(0, 6), pady=4)
            tk.Button(
                form, text="选…", command=lambda k=key: self.pick_operators(k), bg=BG, fg=ACCENT,
                relief="flat", bd=0, font=FONT_SMALL, padx=6, pady=2, cursor="hand2",
            ).grid(row=r, column=2, sticky="w", padx=(0, 12))
            r += 1

        self._label_with_help(
            form,
            "敌人词条赋分（-3 被克制 … +3 克制）",
            r,
            0,
            columnspan=3,
            padx=12,
            pady=(10, 2),
            help_text=(
                "只对本局实际识别到的词条求和：\n"
                "· +3 表示阵容克制该词条，-3 表示被该词条克制，0 表示不评价；\n"
                "· 最终评分 += 敌人词条权重 × 这些值的和；\n"
                "· 没有填写的词条按内置克制表推算（克制 +0.8 / 被克制 -0.8），未出现的词条不计分。"
            ),
        )
        r += 1
        enemy_frame = tk.Frame(form, bg=PANEL)
        enemy_frame.grid(row=r, column=0, columnspan=3, sticky="we", padx=12, pady=(0, 8))
        for i, tag in enumerate(ENEMY_TAGS):
            var = tk.StringVar(value="0")
            self.enemy_vars[tag] = var
            tag_help = (
                f"「{tag}」：本局出现该词条时才计入敌人词条总分。\n"
                "正数=你的阵容克制它，负数=被它克制，0=不评价。\n"
                "建议范围 -3..+3；最终会乘以「敌人词条权重」。"
            )
            tag_label = tk.Label(enemy_frame, text=tag, bg=PANEL, fg=TEXT, font=FONT_SMALL)
            tag_label.grid(
                row=i // 3, column=(i % 3) * 2, sticky="e", padx=(0, 4), pady=2
            )
            Tooltip(tag_label, tag_help)
            spin = tk.Spinbox(
                enemy_frame, from_=-3, to=3, width=4, textvariable=var, bg="#0a2024", fg=TEXT,
                relief="flat", bd=2, font=FONT_SMALL, buttonbackground=PANEL,
            )
            spin.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(0, 14), pady=2)
            Tooltip(spin, tag_help)
        r += 1

        tk.Label(form, text="备注", bg=PANEL, fg=MUTED, font=FONT_SMALL).grid(
            row=r, column=0, sticky="nw", padx=(12, 8), pady=4
        )
        self.notes = tk.Text(
            form, height=4, width=52, bg="#0a2024", fg=TEXT, insertbackground=TEXT, relief="flat",
            bd=4, font=FONT, wrap="word",
        )
        self.notes.grid(row=r, column=1, columnspan=2, sticky="we", padx=(0, 12), pady=4)
        form.columnconfigure(1, weight=1)

        self.form_buttons = tk.Frame(form, bg=PANEL)
        self.form_buttons.grid(row=r + 1, column=0, columnspan=3, sticky="e", padx=12, pady=8)
        tk.Button(
            self.form_buttons, text="应用当前编辑", command=self.collect, bg=PANEL, fg=ACCENT,
            relief="flat", bd=0, font=FONT_SMALL, padx=10, pady=4, cursor="hand2",
        ).pack(side="right")

    def _build_weights(self) -> None:
        frame = tk.Frame(self.tab_weights, bg=PANEL)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.weight_vars: dict[str, tk.StringVar] = {}
        weights = self.data.get("weights") or {}
        defaults = {"priority": 1.0, "enemy": 2.0, "activation": 1.5, "banResilience": 0.5}
        help_texts = {
            "priority": (
                "乘在「优先级」字段上：最终评分 += 该权重 × 优先级。\n"
                "默认 1.0；调到 2.0 表示优先级的影响翻倍。"
            ),
            "enemy": (
                "乘在敌人词条赋分的总和上：最终评分 += 该权重 × Σ词条赋分。\n"
                "默认 2.0，所以某个词条填 +1，实际贡献 +2 分；-1 则贡献 -2 分。"
            ),
            "activation": (
                "乘在 0–1 的激活质量上：最终评分 += 该权重 × 激活质量。\n"
                "激活质量 = 计划盟约内可用的「持续叠加」干员数 / 10（封顶 1）；"
                "若当前卡池不足以激活任一目标盟约，则这一项直接按 0 计算。默认 1.5。"
            ),
            "banResilience": (
                "乘在 0–1 的抗 ban 比例上：最终评分 += 该权重 × 抗 ban 比例。\n"
                "抗 ban 比例 = 核心干员中拥有 ≥2 个核心盟约身份的人数 / 核心干员数；"
                "比例越高，被 ban 一个核心时越不伤。默认 0.5。"
            ),
        }
        for i, (key, label) in enumerate(
            (
                ("priority", "基础优先级权重"),
                ("enemy", "敌人词条权重"),
                ("activation", "盟约可激活/叠层权重"),
                ("banResilience", "多核心抗 ban 权重"),
            )
        ):
            self._label_with_help(
                frame,
                label,
                i,
                0,
                padx=12,
                pady=8,
                fg=TEXT,
                font=FONT,
                help_text=help_texts[key],
            )
            var = tk.StringVar(value=str(weights.get(key, defaults[key])))
            self.weight_vars[key] = var
            tk.Entry(frame, textvariable=var, width=10, bg="#0a2024", fg=TEXT, relief="flat", bd=4).grid(
                row=i, column=1, sticky="w", padx=12
            )

        scope = self.data.setdefault("scope", {})
        self._label_with_help(
            frame,
            "可用性规则",
            4,
            0,
            padx=12,
            pady=8,
            fg=TEXT,
            font=FONT,
            help_text=(
                "决定“哪些干员/盟约被视为被禁”，不是数值权重：\n"
                "· anyCoreBanned：任意核心被禁 → 视为不可用（默认，最保守）；\n"
                "· allCoresBanned：全部核心都被禁 → 才视为不可用；\n"
                "· coreAndAdditionalBanned：核心和附加都有被禁 → 才视为不可用。"
            ),
        )
        self.rule_var = tk.StringVar(value=scope.get("availabilityRule", "anyCoreBanned"))
        ttk.Combobox(
            frame,
            textvariable=self.rule_var,
            values=["anyCoreBanned", "allCoresBanned", "coreAndAdditionalBanned"],
            state="readonly",
            width=28,
        ).grid(row=4, column=1, sticky="w", padx=12)
        tk.Label(
            frame,
            text="默认 anyCoreBanned；识别到「本局禁用干员」列表时以列表为准（当前未读头像）。",
            bg=PANEL, fg=MUTED, font=FONT_SMALL, wraplength=620, justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        tk.Label(
            frame,
            text=(
                "最终评分 = 优先级权重×优先级 + 敌人权重×词条和 + 激活权重×激活质量 + 抗ban权重×抗ban比例\n"
                "固定扣分（不受权重影响）：核心盟约被禁 -2/个，附加盟约被禁 -1/个，"
                "核心干员被禁 -2/个，可选干员被禁 -0.5/个；主核心/必选干员被禁或模式不匹配则整条不推荐。"
            ),
            bg=PANEL, fg=MUTED, font=FONT_SMALL, wraplength=680, justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

    def _build_raw(self) -> None:
        frame = tk.Frame(self.tab_raw, bg=BG)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        self.raw = tk.Text(frame, bg="#0a2024", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10))
        self.raw.pack(fill="both", expand=True)
        btns = tk.Frame(frame, bg=BG)
        btns.pack(fill="x", pady=(6, 0))
        tk.Button(
            btns, text="从编辑器刷新", command=self._refresh_raw, bg=PANEL, fg=TEXT,
            relief="flat", bd=0, font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="left")
        tk.Button(
            btns, text="应用 JSON", command=self._apply_raw, bg=ACCENT, fg="#062022",
            relief="flat", bd=0, font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="left", padx=6)

    # ------------------------------------------------------------ selection
    def _refresh_list(self, select: int | None = None) -> None:
        target = self.current if select is None else select
        self.listbox.delete(0, "end")
        for item in self.lineups:
            self.listbox.insert("end", item.get("name") or item.get("id") or "未命名")
        if 0 <= target < len(self.lineups):
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(target)

    def _on_select(self, _event=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        index = sel[0]
        # Save the item we are leaving, but do not refresh/reset the listbox
        # selection to the old index before switching.  That reset made the
        # left highlight lag one click behind the form.
        self.collect(silent=True, refresh=False)
        self._select(index)
        self._refresh_list(select=index)

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self.lineups)):
            return
        self.current = index
        item = self.lineups[index]
        self.vars["name"].set(item.get("name", ""))
        self.vars["id"].set(item.get("id", ""))
        self.vars["primaryCore"].set(item.get("primaryCore", "") or "")
        self.vars["modes"].set("，".join(item.get("modes") or []))
        self.vars["priority"].set(str(item.get("priority", 0)))
        self.core_targets = dict(item.get("core") or {})
        self.minor_targets = dict(item.get("minor") or {})
        ops = item.get("operators") or {}
        self.vars["required"].set("，".join(ops.get("required") or []))
        self.vars["core"].set("，".join(ops.get("core") or []))
        self.vars["optional"].set("，".join(ops.get("optional") or []))
        for tag in ENEMY_TAGS:
            self.enemy_vars[tag].set(str((item.get("enemyFit") or {}).get(tag, 0)))
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", item.get("notes", ""))
        self._refresh_target_labels()

    def _refresh_target_labels(self) -> None:
        self.core_label.configure(text=_targets_text(self.core_targets) or "（未设置）")
        self.minor_label.configure(text=_targets_text(self.minor_targets) or "（未设置）")

    # ------------------------------------------------------------ editing
    def collect(self, silent: bool = False, refresh: bool = True) -> bool:
        if not (0 <= self.current < len(self.lineups)):
            return False
        item = self.lineups[self.current]
        item["name"] = self.vars["name"].get().strip()
        item["id"] = self.vars["id"].get().strip()
        item["primaryCore"] = self.vars["primaryCore"].get().strip() or None
        item["modes"] = _split(self.vars["modes"].get())
        try:
            item["priority"] = float(self.vars["priority"].get() or 0)
        except ValueError:
            if not silent:
                self.status.configure(text="优先级必须是数字", fg=BAD)
            return False
        item["core"] = dict(self.core_targets)
        item["minor"] = dict(self.minor_targets)
        item["operators"] = {
            "required": _split(self.vars["required"].get()),
            "core": _split(self.vars["core"].get()),
            "optional": _split(self.vars["optional"].get()),
        }
        item["enemyFit"] = {}
        for tag, var in self.enemy_vars.items():
            try:
                value = float(var.get() or 0)
            except ValueError:
                value = 0
            if value:
                item["enemyFit"][tag] = value
        item["notes"] = self.notes.get("1.0", "end").strip()
        if refresh:
            self._refresh_list(select=self.current)
        return True

    def add(self) -> None:
        self.collect(silent=True)
        self.lineups.append(
            {
                "id": f"lineup-{len(self.lineups) + 1}",
                "name": "新阵容",
                "modes": [],
                "core": {},
                "minor": {},
                "operators": {"required": [], "core": [], "optional": []},
                "enemyFit": {},
                "priority": 5,
                "notes": "",
            }
        )
        self.current = len(self.lineups) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current)
        self._select(self.current)

    def duplicate(self) -> None:
        if not (0 <= self.current < len(self.lineups)):
            return
        self.collect(silent=True)
        item = json.loads(json.dumps(self.lineups[self.current]))
        item["id"] = item.get("id", "lineup") + "-copy"
        item["name"] = item.get("name", "阵容") + " 副本"
        self.lineups.append(item)
        self.current = len(self.lineups) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current)
        self._select(self.current)

    def delete(self) -> None:
        if not (0 <= self.current < len(self.lineups)):
            return
        if not messagebox.askyesno("删除阵容", f"删除「{self.lineups[self.current].get('name')}」？", parent=self.win):
            return
        del self.lineups[self.current]
        self.current = max(0, min(self.current, len(self.lineups) - 1))
        self._refresh_list()
        if self.lineups:
            self.listbox.selection_set(self.current)
            self._select(self.current)

    def pick_core(self) -> None:
        values = CovenantPicker(self.win, "核心盟约目标", MAJORS, self.core_targets).show()
        if values is not None:
            self.core_targets = values
            self._refresh_target_labels()

    def pick_minor(self) -> None:
        values = CovenantPicker(self.win, "附加盟约目标", MINORS, self.minor_targets).show()
        if values is not None:
            self.minor_targets = values
            self._refresh_target_labels()

    def pick_operators(self, key: str) -> None:
        names = sorted(self.game.operators)
        current = _split(self.vars[key].get())
        values = OperatorPicker(self.win, names, current).show()
        if values is not None:
            self.vars[key].set("，".join(values))

    # ------------------------------------------------------------ save
    def _validate(self) -> str | None:
        self._warnings: list[str] = []
        if not self.lineups:
            return "至少需要一套阵容"
        ids: set[str] = set()
        for item in self.lineups:
            ident = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not ident:
                return "阵容 ID 不能为空"
            if ident in ids:
                return f"阵容 ID 重复：{ident}"
            ids.add(ident)
            if not name:
                return f"阵容 {ident} 缺少名称"
            for cov in list(item.get("core") or {}) + list(item.get("minor") or {}):
                if cov not in self.game.covenants:
                    return f"未知盟约：{cov}"
            for op in sum((list((item.get("operators") or {}).get(k) or []) for k in ("required", "core", "optional")), []):
                if op not in self.game.operators:
                    self._warnings.append(op)
        # Unknown operator names are common community nicknames; keep them,
        # warn instead of blocking the save.
        return None

    def save(self) -> None:
        if not self.collect(silent=True):
            return
        err = self._validate()
        if err:
            self.status.configure(text=err, fg=BAD)
            return
        self._write(self.path)
        warning = ""
        if getattr(self, "_warnings", None):
            warning = "（未知干员：" + "、".join(sorted(set(self._warnings))[:4]) + "）"
        self.status.configure(text=f"已保存：{self.path}{warning}", fg=MUTED)
        if self.on_saved:
            self.on_saved(self.path)

    def save_as(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            parent=self.win,
            title="另存预设",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=self.path.name,
        )
        if not path:
            return
        if not self.collect(silent=True):
            return
        self.path = Path(path)
        self.save()

    def _write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        self.data["scope"] = self.data.get("scope") or {}
        self.data["scope"]["availabilityRule"] = self.rule_var.get()
        self.data["weights"] = {}
        for key, var in self.weight_vars.items():
            try:
                self.data["weights"][key] = float(var.get())
            except ValueError:
                self.data["weights"][key] = 0
        self.data["lineups"] = self.lineups
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        self._refresh_raw()

    # ------------------------------------------------------------ raw
    def _refresh_raw(self) -> None:
        self.collect(silent=True)
        self.raw.delete("1.0", "end")
        self.raw.insert("1.0", json.dumps(self.data, ensure_ascii=False, indent=4))

    def _apply_raw(self) -> None:
        try:
            data = json.loads(self.raw.get("1.0", "end"))
            if not isinstance(data.get("lineups"), list):
                raise ValueError("缺少 lineups 数组")
        except Exception as exc:
            self.status.configure(text=f"JSON 无效：{exc}", fg=BAD)
            return
        self.data = data
        self.lineups = self.data.setdefault("lineups", [])
        self.current = 0
        self._refresh_list()
        if self.lineups:
            self.listbox.selection_set(0)
            self._select(0)
        self.status.configure(text="已应用 JSON，点保存写入", fg=MUTED)

    def close(self) -> None:
        try:
            self.win.grab_release()
        except Exception:
            pass
        self.win.destroy()


class CovenantPicker:
    def __init__(self, parent: tk.Misc, title: str, names: list[str], current: dict[str, int]):
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("620x560")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()
        self.names = names
        self.enabled: dict[str, tk.BooleanVar] = {}
        self.counts: dict[str, tk.StringVar] = {}
        self.result: dict[str, int] | None = None

        tk.Label(self.win, text=title, bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        frame = tk.Frame(self.win, bg=BG)
        frame.pack(fill="both", expand=True, padx=14)
        for i, name in enumerate(names):
            var = tk.BooleanVar(value=name in current)
            count = tk.StringVar(value=str(current.get(name, 1) or 1))
            self.enabled[name] = var
            self.counts[name] = count
            row, col = divmod(i, 2)
            cell = tk.Frame(frame, bg=BG)
            cell.grid(row=row, column=col, sticky="w", padx=(0, 18), pady=4)
            tk.Checkbutton(
                cell, text=name, variable=var, bg=BG, fg=TEXT, selectcolor=PANEL,
                activebackground=BG, activeforeground=ACCENT, font=FONT,
            ).pack(side="left")
            tk.Spinbox(
                cell, from_=1, to=6, width=3, textvariable=count, bg="#0a2024", fg=TEXT,
                relief="flat", bd=2, font=FONT_SMALL, buttonbackground=PANEL,
            ).pack(side="left", padx=(6, 0))
        btns = tk.Frame(self.win, bg=BG)
        btns.pack(fill="x", padx=14, pady=10)
        tk.Button(
            btns, text="清空", command=self._clear, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="left")
        tk.Button(
            btns, text="确定", command=self._ok, bg=ACCENT, fg="#062022", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=4,
        ).pack(side="right")
        tk.Button(
            btns, text="取消", command=self.win.destroy, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="right", padx=6)

    def _clear(self) -> None:
        for var in self.enabled.values():
            var.set(False)

    def _ok(self) -> None:
        result: dict[str, int] = {}
        for name, var in self.enabled.items():
            if var.get():
                try:
                    count = max(1, int(self.counts[name].get()))
                except ValueError:
                    count = 1
                result[name] = count
        self.result = result
        self.win.destroy()

    def show(self) -> dict[str, int] | None:
        self.win.wait_window()
        return self.result


class OperatorPicker:
    def __init__(self, parent: tk.Misc, names: list[str], current: list[str]):
        self.win = tk.Toplevel(parent)
        self.win.title("选择干员")
        self.win.geometry("620x650")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()
        self.names = names
        self.result: list[str] | None = None
        self.selected = set(current)

        tk.Label(self.win, text="选择干员（Ctrl/Shift 多选）", bg=BG, fg=ACCENT, font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        search = tk.StringVar()
        tk.Entry(
            self.win, textvariable=search, bg="#0a2024", fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=4, font=FONT,
        ).pack(fill="x", padx=14, pady=4)
        search.trace_add("write", lambda *_: self._filter(search.get()))
        frame = tk.Frame(self.win, bg=BG)
        frame.pack(fill="both", expand=True, padx=14, pady=4)
        self.listbox = tk.Listbox(
            frame, selectmode="extended", bg=PANEL, fg=TEXT, selectbackground=BORDER,
            selectforeground=ACCENT, relief="flat", bd=0, font=FONT,
        )
        scroll = tk.Scrollbar(frame, command=self.listbox.yview, bg=PANEL, troughcolor=PANEL, bd=0)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self._fill(self.names, current)
        btns = tk.Frame(self.win, bg=BG)
        btns.pack(fill="x", padx=14, pady=10)
        tk.Button(
            btns, text="清空", command=self._clear, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="left")
        tk.Button(
            btns, text="确定", command=self._ok, bg=ACCENT, fg="#062022", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10, "bold"), padx=14, pady=4,
        ).pack(side="right")
        tk.Button(
            btns, text="取消", command=self.win.destroy, bg=PANEL, fg=TEXT, relief="flat", bd=0,
            font=FONT_SMALL, padx=10, pady=4,
        ).pack(side="right", padx=6)
        self.search = search

    def _fill(self, names: list[str], selected: list[str]) -> None:
        self.listbox.delete(0, "end")
        self._visible = names
        sel = set(selected)
        for i, name in enumerate(names):
            self.listbox.insert("end", name)
            if name in sel:
                self.listbox.selection_set(i)

    def _filter(self, text: str) -> None:
        current = self._current_selection()
        self.selected.update(current)
        names = [n for n in self.names if text.strip() in n]
        self._fill(names, list(self.selected))

    def _current_selection(self) -> list[str]:
        return [self._visible[i] for i in self.listbox.curselection() if i < len(self._visible)]

    def _clear(self) -> None:
        self.selected.clear()
        self.listbox.selection_clear(0, "end")

    def _ok(self) -> None:
        self.selected.update(self._current_selection())
        self.result = [n for n in self.names if n in self.selected]
        self.win.destroy()

    def show(self) -> list[str] | None:
        self.win.wait_window()
        return self.result
