"""GitHub Releases updater for the packaged app.

The updater only runs in a frozen (PyInstaller) build.  It downloads the
release zip, verifies its `.sha256` asset when present, then hands off to a
detached cmd script which waits for the app to exit, replaces the install
directory and restarts the exe.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import APP_DIR, USER_DIR, ensure_user_dir
from .version import __version__


@dataclass
class UpdateInfo:
    version: str
    tag: str
    notes: str
    asset_url: str
    asset_name: str
    sha_url: str | None = None


def _config() -> dict[str, Any]:
    path = APP_DIR / "update.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _repo() -> str:
    return str(_config().get("repo") or "").strip()


def _asset_name() -> str:
    return str(_config().get("asset") or "ASPA-win-x64.zip")


def _opener() -> urllib.request.OpenerDirector:
    proxy = str(_config().get("proxy") or "").strip()
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()


def _version_tuple(text: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in text.strip().lstrip("vV").replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            out.append(int(digits))
    return tuple(out)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _version_tuple(remote) > _version_tuple(local)


def check_latest() -> UpdateInfo | None:
    """Return the latest release if it is newer, else None. Raises on network."""
    repo = _repo()
    if not repo:
        return None
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": f"ASPA/{__version__}", "Accept": "application/vnd.github+json"})
    with _opener().open(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if not tag or not is_newer(tag):
        return None
    assets = data.get("assets") or []
    wanted = _asset_name()
    asset = next((a for a in assets if a.get("name") == wanted), None)
    if asset is None:
        asset = next((a for a in assets if str(a.get("name", "")).endswith(".zip")), None)
    if asset is None:
        return None
    sha = next((a for a in assets if str(a.get("name", "")).endswith(".sha256")), None)
    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or ""),
        asset_url=str(asset.get("browser_download_url")),
        asset_name=str(asset.get("name")),
        sha_url=str(sha.get("browser_download_url")) if sha else None,
    )


def download_and_apply(info: UpdateInfo, on_status=None) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("仅发布版支持自动更新")
    ensure_user_dir()
    work = Path(tempfile.mkdtemp(prefix="spa-update-"))
    zip_path = work / info.asset_name
    if on_status:
        on_status("下载更新中…")
    with _opener().open(info.asset_url, timeout=180) as resp, open(zip_path, "wb") as f:
        shutil.copyfileobj(resp, f)
    if info.sha_url:
        if on_status:
            on_status("校验更新包…")
        with _opener().open(info.sha_url, timeout=30) as resp:
            expected = resp.read().decode("utf-8", "replace").split()[0].strip().lower()
        actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if expected and expected != actual:
            raise RuntimeError("更新包 SHA256 不匹配")
    if on_status:
        on_status("准备重启更新…")
    script = work / "apply_update.cmd"
    exe = APP_DIR / "ASPA.exe"
    script.write_text(
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'set "ZIP={zip_path}"\r\n'
        f'set "APP={APP_DIR}"\r\n'
        'set "TMP=%TEMP%\\spa-update-extract"\r\n'
        'if exist "%TMP%" rmdir /s /q "%TMP%"\r\n'
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath \'%ZIP%\' -DestinationPath \'%TMP%\' -Force"\r\n'
        'robocopy "%TMP%" "%APP%" /MIR /NFL /NDL /NJH /NJS /NP >nul\r\n'
        f'start "" "{exe}"\r\n'
        'rmdir /s /q "%TMP%"\r\n'
        'del "%ZIP%"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True)
    if on_status:
        on_status("即将重启…")
