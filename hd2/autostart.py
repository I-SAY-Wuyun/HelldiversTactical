"""Windows current-user startup shortcut management."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import APP_NAME, APP_ID, RESOURCE_DIR, WINDOW_TITLE

SHORTCUT_NAME = "Helldivers Tactical Command.lnk"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup"


def _desktop_dir() -> Path:
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        onedrive = Path(os.environ.get("OneDrive", "")) / "Desktop"
        if onedrive.is_dir():
            return onedrive
        return Path(userprofile) / "Desktop"
    return Path.home() / "Desktop"


def _target() -> tuple[str, str, str]:
    """Return (target, arguments, working_directory) for the current runtime."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return str(exe), "", str(exe.parent)
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    target = pythonw if pythonw.exists() else python
    app = RESOURCE_DIR / "app.py"
    return str(target), f'"{app}"', str(RESOURCE_DIR)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _make_shortcut(path: Path, description: str) -> None:
    target, arguments, workdir = _target()
    icon = RESOURCE_DIR / "assets" / "icon.ico"
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut({_ps_quote(str(path))})
$sc.TargetPath = {_ps_quote(target)}
$sc.Arguments = {_ps_quote(arguments)}
$sc.WorkingDirectory = {_ps_quote(workdir)}
$sc.IconLocation = {_ps_quote(str(icon))}
$sc.Description = {_ps_quote(description)}
$sc.WindowStyle = 1
$sc.Save()
"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=25,
        creationflags=flags,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "PowerShell shortcut creation failed")


def startup_shortcut() -> Path:
    return _startup_dir() / SHORTCUT_NAME


def desktop_shortcut() -> Path:
    return _desktop_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    return startup_shortcut().exists()


def enable() -> bool:
    _make_shortcut(startup_shortcut(), f"{APP_NAME} - 开机自启")
    return is_enabled()


def disable() -> bool:
    path = startup_shortcut()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"无法删除开机启动快捷方式: {exc}") from exc
    return not path.exists()


def create_desktop_shortcut() -> bool:
    path = desktop_shortcut()
    _make_shortcut(path, f"{APP_NAME} - 绝地潜兵战术指挥终端")
    return path.exists()


def launch_target() -> str:
    target, args, _ = _target()
    return f"{target} {args}".strip()
