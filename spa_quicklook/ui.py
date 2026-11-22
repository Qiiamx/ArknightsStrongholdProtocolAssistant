"""Sticky side panel: follows the Arknights window and shows the last scan."""

from __future__ import annotations

import tempfile
import threading
import tkinter as tk
import sys
from pathlib import Path
from typing import Any

from .capture import capture_client_area
from .cli import _to_recognition_dict
from .ocr import OcrEngine
from .paths import counter_data_path, game_data_path, screenshots_dir
from .recognize import read_image, recognize
from .game import GameData, Recognition, banned_operators_by_rule
from .stackers import StackerPreset, group_by_tier, remaining, tier_roman
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


class StackerFilterDialog:
    """Manual exclusions for the preset stacker list."""

    def __init__(
        self,
        parent: tk.Misc,
        game: GameData,
        names: list[str],
        manual_banned: set[str],
        inferred_banned: set[str],
    ):
        self.game = game
        self.manual = set(manual_banned)
        self.inferred = set(inferred_banned)
        self.result: set[str] | None = None
        self.vars: dict[str, tk.BooleanVar] = {}

        self.win = tk.Toplevel(parent)
        self.win.title("手动排除叠层手")
        self.win.geometry("620x680")
        self.win.configure(bg=BG)
        self.win.transient(parent)
        self.win.attributes("-topmost", True)
        self.win.grab_set()

        tk.Label(
            self.win, text="手动排除叠层手", bg=BG, fg=ACCENT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(
            self.win,
            text="取消勾选 = 本局手动排除。标灰的是按盟约推断被 ban 的，不能重新启用。",
            bg=BG, fg=MUTED, font=FONT_SMALL, wraplength=580, justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        wrap = tk.Frame(self.win, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0)
        scroll = tk.Scrollbar(wrap, command=canvas.yview, bg=PANEL, troughcolor=PANEL, bd=0)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=PANEL)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        ops = [game.operators[n] for n in names if n in game.operators]
        for tier, group in group_by_tier(ops):
            tk.Label(
                body, text=f"等级 {tier_roman(tier)}", bg=BG, fg=ACCENT,
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(anchor="w", padx=8, pady=(8, 2))
            grid = tk.Frame(body, bg=PANEL)
            grid.pack(fill="x", padx=6)
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)
            for i, op in enumerate(group):
                var = tk.BooleanVar(value=op.name not in self.manual and op.name not in self.inferred)
                self.vars[op.name] = var
                cb = tk.Checkbutton(
                    grid,
                    text=f"{op.name}   {'/'.join(op.covenants) or '—'}",
                    variable=var,
                    anchor="w",
                    justify="left",
                    bg=PANEL,
                    fg=MUTED if op.name in self.inferred else TEXT,
                    activebackground=PANEL,
                    activeforeground=ACCENT,
                    selectcolor="#0a2024",
                    font=FONT,
                    bd=0,
                    highlightthickness=0,
                )
                if op.name in self.inferred:
                    cb.configure(state="disabled")
                cb.grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=2)

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
            btns, text="全部保留", command=self._keep_all, bg=PANEL, fg=TEXT,
            relief="flat", bd=0, font=FONT_SMALL, padx=12, pady=4, cursor="hand2",
        ).pack(side="left")

        self.win.wait_window(self.win)

    def _keep_all(self) -> None:
        for name, var in self.vars.items():
            if name not in self.inferred:
                var.set(True)

    def _apply(self) -> None:
        self.result = {
            name for name, var in self.vars.items() if name not in self.inferred and not var.get()
        }
        self.win.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.win.destroy()

    def show(self) -> set[str] | None:
        return self.result


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
        self.manual_banned: set[str] = set()
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
            text="编辑叠层手",
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
        self.status = tk.Label(sub, text=f"v{__version__} · 准备就绪", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
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
        self.text = tk.Text(
            body,
            wrap="word",
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            font=FONT,
            spacing1=1,
            spacing3=3,
            highlightthickness=0,
        )
        self.scroll = tk.Scrollbar(body, command=self.text.yview, bg=PANEL, troughcolor=PANEL, bd=0)
        self.text.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("title", foreground=ACCENT, font=("Microsoft YaHei UI", 11, "bold"), spacing1=6)
        self.text.tag_configure("body", foreground=TEXT)
        self.text.tag_configure("dim", foreground=MUTED)
        self.text.tag_configure("ok", foreground=OK)
        self.text.tag_configure("bad", foreground=BAD)
        self.text.tag_configure("warn", foreground=WARN)
        self.text.configure(state="disabled")

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

        self.exclude_btn = tk.Button(
            footer,
            text="排除",
            command=self._open_exclusion,
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
        self.exclude_btn.pack(side="left", padx=(6, 0))

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

        self._set_text(
            [
                ("等待截图", "title"),
                ("把游戏停在「确认本局信息」页，点下方「截图识别」。", "body"),
                ("工具只抓游戏客户区，不发送任何输入。", "dim"),
                ("", "body"),
                ("提示：窗口最小化时无法截图；点标题栏可拖动，点「贴边」恢复跟随。", "dim"),
            ]
        )

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
                self.status.configure(text="游戏未运行，等待窗口…", fg=WARN)
                self.root.deiconify()
                self._text_if_empty()
            elif is_minimized(self.hwnd):
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
    def _set_text(self, parts: list[tuple[str, str]]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for text, tag in parts:
            self.text.insert("end", text + "\n", tag)
        self.text.configure(state="disabled")
        self.text.see("1.0")

    def _text_if_empty(self) -> None:
        if not self.last_rec:
            self._set_text(
                [
                    ("等待游戏窗口", "title"),
                    ("启动明日方舟后，把游戏停在「确认本局信息」页。", "body"),
                    ("本面板会自动贴到游戏右侧。", "dim"),
                ]
            )

    def _load_game_data(self) -> None:
        try:
            self.game = GameData(game_data_path(), counter_data_path())
        except Exception as exc:
            self.status.configure(text=f"数据加载失败：{exc}", fg=BAD)
            self.game = None

    def _reload_preset(self) -> None:
        self.status.configure(text="叠层手名单已重载（下次识别生效）", fg=MUTED)

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
        self.update_btn.configure(state="normal", text="更新")
        if manual:
            self.status.configure(text=f"检查更新失败：{message[:60]}", fg=BAD)

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
        info = self.update_info

        def worker() -> None:
            try:
                download_and_apply(info, on_status=lambda text: self.root.after(0, lambda: self.status.configure(text=text, fg=WARN)))
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
        self.status.configure(text="识别失败", fg=BAD)
        self._set_text(
            [
                ("识别失败", "title"),
                (message, "bad"),
                ("", "body"),
                ("请停在「确认本局信息」页、窗口不要最小化；调试图：shots\\last_scan_debug.png", "dim"),
            ]
        )

    def _show_result(self, rec: dict, rec_dict: dict) -> None:
        self.busy = False
        self.last_rec, self.last_rec_dict = rec, rec_dict
        self.scan_btn.configure(state="normal", text="截图识别")
        self.status.configure(text=f"识别完成 · 置信度 {rec.get('confidence')}", fg=MUTED)
        parts: list[tuple[str, str]] = []

        enemy = rec.get("enemy") or {}
        parts.append(("本次识别", "title"))
        parts.append((f"敌人：{enemy.get('leader') or '—'} · {'/'.join(enemy.get('tags') or []) or '未读到'}", "body"))
        major = rec["covenants"]["major"]
        minor = rec["covenants"]["minor"]
        parts.append(("核心可用：" + ("、".join(major["available"]) or "—"), "ok"))
        parts.append(("核心禁用：" + ("、".join(major["disabled"]) or "—"), "bad"))
        if major["unknown"]:
            parts.append(("核心未识别：" + "、".join(major["unknown"]), "warn"))
        if minor["available"] or minor["disabled"]:
            parts.append(("附加可用：" + ("、".join(minor["available"]) or "—"), "ok"))
            parts.append(("附加禁用：" + ("、".join(minor["disabled"]) or "—"), "bad"))
        else:
            parts.append(("附加盟约未显示：滚动准备页后重新截图", "warn"))

        parts.append(("", "body"))
        parts.append(("剩余叠层手", "title"))
        if self.game is None:
            parts.append(("数据未加载", "bad"))
            self._set_text(parts)
            return

        try:
            preset = StackerPreset.load(self.preset_path, self.game)
            recognized = Recognition.from_dict(rec_dict)
            available, banned, unknown = remaining(
                self.game, recognized, preset, self.manual_banned
            )
        except Exception as exc:
            parts.append((f"读取叠层手名单失败：{exc}", "bad"))
            self._set_text(parts)
            return

        if not preset.names:
            parts.append(("名单为空。点「编辑叠层手」勾选本局要关注的角色。", "warn"))
        else:
            tiers = sorted(
                {
                    self.game.operators[name].tier
                    for name in preset.names
                    if name in self.game.operators
                },
                reverse=True,
            )
            grouped = dict(group_by_tier(available))
            for tier in tiers:
                ops = grouped.get(tier, [])
                parts.append((f"等级 {tier_roman(tier)} · {len(ops)} 人", "ok" if ops else "dim"))
                for op in ops:
                    parts.append((f"  {op.name}   {'/'.join(op.covenants) or '—'}", "body"))
        if banned:
            parts.append((f"已排除：{len(banned)} 人（盟约推断 + 手动）", "dim"))
        if unknown:
            parts.append(("名单中无法识别的叠层手：" + "、".join(unknown[:8]), "warn"))
        parts.append(("点「排除」可手动移除推断遗漏的干员。", "dim"))
        self._set_text(parts)

    def _open_exclusion(self) -> None:
        if self.game is None:
            self.status.configure(text="数据未加载，无法排除", fg=BAD)
            return
        try:
            preset = StackerPreset.load(self.preset_path, self.game)
        except Exception as exc:
            self.status.configure(text=f"读取名单失败：{exc}", fg=BAD)
            return
        names = [n for n in preset.names if n in self.game.operators]
        if not names:
            self.status.configure(text="名单为空，先编辑叠层手", fg=WARN)
            return
        inferred: set[str] = set()
        if self.last_rec_dict:
            try:
                inferred = banned_operators_by_rule(
                    self.game, Recognition.from_dict(self.last_rec_dict), preset.availability_rule
                )
            except Exception:
                inferred = set()
        dialog = StackerFilterDialog(self.root, self.game, names, self.manual_banned, inferred)
        result = dialog.show()
        if result is not None:
            self.manual_banned = result
            if self.last_rec is not None and self.last_rec_dict is not None:
                self._show_result(self.last_rec, self.last_rec_dict)

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
