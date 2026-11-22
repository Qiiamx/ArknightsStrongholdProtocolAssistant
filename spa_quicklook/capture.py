"""Windows window capture for SPA QuickLook, via the wincap helper.

Read-only: this module only takes screenshots. It never sends input to the game.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .paths import wincap_exe

DEFAULT_EXE = wincap_exe()
GAME_TITLE = "明日方舟"


class CaptureError(RuntimeError):
    pass


def find_exe() -> Path:
    for cand in (os.environ.get("WINCAP_EXE"), DEFAULT_EXE):
        if cand and Path(cand).is_file():
            return Path(cand)
    raise CaptureError("wincap.exe not found; build capture/ or set WINCAP_EXE")


def _run(args: list[str]) -> dict[str, Any]:
    p = subprocess.run(
        [str(find_exe()), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    text = (p.stdout or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        lines = [x for x in text.splitlines() if x.strip().startswith("{")]
        if not lines:
            raise CaptureError(f"wincap failed ({p.returncode}): {p.stderr or text}")
        data = json.loads(lines[-1])
    data["exitCode"] = p.returncode
    return data


def list_windows() -> list[dict[str, Any]]:
    return list(_run(["list", "--json"]).get("windows") or [])


def find_game() -> dict[str, Any]:
    """Find the Arknights window; prefer the configured title, then UnityWndClass."""
    windows = list_windows()
    for w in windows:
        if w.get("title") == GAME_TITLE:
            return w
    for w in windows:
        if w.get("title", "").startswith(GAME_TITLE):
            return w
    raise CaptureError("Arknights window not found; is the game running and not minimized?")


def capture_client_area(out: Path | str, pid: int | None = None) -> dict[str, Any]:
    """Capture the game's client area to `out` (PNG). Raises if minimized."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    target = ["--pid", str(pid)] if pid else ["--title", GAME_TITLE]
    info = _run(["info", *target, "--json"])
    window = info.get("window") or {}
    if window.get("minimized"):
        raise CaptureError("游戏窗口已最小化，请先恢复窗口再识别")
    result = _run(
        ["capture", *target, "--client-area", "--out", str(out), "--json"]
    )
    if not result.get("ok"):
        raise CaptureError(f"capture failed: {result}")
    return result
