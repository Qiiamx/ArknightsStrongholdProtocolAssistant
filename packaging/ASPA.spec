# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-dir spec for ASPA.

Build prerequisites:
  - .build-venv with pyinstaller + rapidocr_onnxruntime + opencv-python-headless
  - build/wincap2/wincap.exe (self-contained)
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
DATA = ROOT / "spa_quicklook" / "data"
WINCAP = Path(os.environ.get("WINCAP_EXE", str(ROOT / "build" / "wincap2" / "wincap.exe")))

datas = [
    (str(DATA), "spa_quicklook/data"),
]
datas += collect_data_files("rapidocr_onnxruntime")

hiddenimports = collect_submodules("rapidocr_onnxruntime") + [
    "onnxruntime",
    "cv2",
    "numpy",
    "yaml",
    "shapely",
    "pyclipper",
    "spa_quicklook._build_version",
]

a = Analysis(
    [str(ROOT / "app.py")],    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "matplotlib",
        "pandas",
        "scipy",
        "IPython",
        "notebook",
        "torch",
        "tensorflow",
    ],
    noarchive=False,
)
# Only still-image decoding is needed; the bundled FFmpeg videoio DLL is ~40 MB.
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ASPA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ASPA",
)

