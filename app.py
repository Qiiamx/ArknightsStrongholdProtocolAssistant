"""Windowed entry point for the packaged SPA QuickLook."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # PyInstaller one-dir: keep imports inside main so --help/debug imports fail
    # with a readable message rather than a partially built window.
    from spa_quicklook.paths import default_preset
    from spa_quicklook.ui import launch_ui

    return launch_ui(default_preset())


if __name__ == "__main__":
    sys.exit(main())
