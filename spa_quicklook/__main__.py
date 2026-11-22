"""Entry point with a friendly dependency check.

Running `python -m spa_quicklook` with a system Python that has no deps used to
produce a raw `ModuleNotFoundError: numpy`; explain the fix instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _check() -> list[str]:
    missing: list[str] = []
    for module, package in (
        ("numpy", "numpy"),
        ("cv2", "opencv-python"),
        ("rapidocr_onnxruntime", "rapidocr_onnxruntime"),
    ):
        try:
            __import__(module)
        except Exception:
            missing.append(package)
    return missing


def main() -> int:
    missing = _check()
    if missing:
        print("SPA QuickLook 缺少 Python 依赖：" + ", ".join(missing), file=sys.stderr)
        print("", file=sys.stderr)
        print("推荐用仓库自带的启动脚本（会自动使用 .venv）：", file=sys.stderr)
        print(r"  .\run.ps1 --image <截图.png>" if sys.platform == "win32" else "  ./run.cmd --image <截图.png>", file=sys.stderr)
        print("", file=sys.stderr)
        print("或者手动安装：", file=sys.stderr)
        print(f'  "{sys.executable}" -m pip install -r "{ROOT / "spa_quicklook" / "requirements.txt"}"', file=sys.stderr)
        print("注意：不要用 Windows 应用商店的 python 别名（可能是 3.15 且没有依赖）。", file=sys.stderr)
        return 2

    from .cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
