"""Version and release metadata.

Builds may generate ``_build_version.py`` with the release tag; source runs
fall back to 0.1.0.
"""

from __future__ import annotations

try:
    from ._build_version import __version__  # type: ignore
except Exception:
    __version__ = "0.1.4"

APP_NAME = "ASPA"

