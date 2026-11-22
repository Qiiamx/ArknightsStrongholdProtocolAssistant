"""Paths that work both from source and from a PyInstaller one-dir build."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
PKG_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else PKG_DIR.parent
DATA_DIR = PKG_DIR / "data"

if FROZEN:
    USER_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "SPAQuickLook"
else:
    USER_DIR = APP_DIR / "maa_user"


def ensure_user_dir() -> Path:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    return USER_DIR


def default_preset() -> Path:
    """User-writable preset; seed from the bundled example on first run."""
    ensure_user_dir()
    path = USER_DIR / "presets.json"
    if not path.exists():
        example = DATA_DIR / "presets.example.json"
        if example.exists():
            shutil.copy2(example, path)
    return path


def wincap_exe() -> Path:
    if FROZEN:
        for path in (APP_DIR / "wincap.exe", APP_DIR / "_internal" / "wincap.exe"):
            if path.exists():
                return path
        return APP_DIR / "wincap.exe"
    return APP_DIR / "capture" / "bin" / "wincap.exe"


def screenshots_dir() -> Path:
    if FROZEN:
        path = ensure_user_dir() / "shots"
    else:
        path = APP_DIR / "shots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def game_data_path() -> Path:
    return DATA_DIR / "covenants_operators_regulation.json"


def counter_data_path() -> Path:
    return DATA_DIR / "enemy_counter_matrix.json"
