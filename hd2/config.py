"""Application paths and persisted settings."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .util import atomic_write_json, read_json

APP_NAME = "Helldivers Tactical Command"
APP_NAME_ZH = "绝地潜兵战术指挥终端"
APP_ID = "HelldiversTactical"
VERSION = "1.0.0"
WINDOW_TITLE = "HELLDIVERS 2 // TACTICAL COMMAND"


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _can_use(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except (OSError, PermissionError):
        return False


def app_data_dir() -> Path:
    """Pick a writable per-user data directory.

    The normal location is %LOCALAPPDATA%\\HelldiversTactical.  A few locked-down
    Windows sessions deny writes there, so fall back to TEMP rather than failing
    to start.
    """
    candidates: list[Path] = []
    override = os.environ.get("HD2_DATA_DIR")
    if override:
        candidates.append(Path(override))
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        candidates.append(Path(root) / APP_ID)
    candidates.append(Path.home() / f".{APP_ID.lower()}")
    candidates.append(resource_dir() / "userdata")
    candidates.append(Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / APP_ID)
    for path in candidates:
        if _can_use(path):
            return path
    # Last resort: a directory beside the executable/resource bundle.
    fallback = resource_dir() / "userdata"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


RESOURCE_DIR = resource_dir()
WEB_DIR = RESOURCE_DIR / "web"
DATA_DIR = RESOURCE_DIR / "data"
USER_DIR = app_data_dir()
CACHE_DIR = USER_DIR / "cache"
STATE_DIR = USER_DIR / "state"
LOG_DIR = USER_DIR / "logs"
for _directory in (CACHE_DIR, STATE_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

SETTINGS_PATH = USER_DIR / "settings.json"
ALERT_STATE_PATH = STATE_DIR / "alerts.json"
SNAPSHOT_CACHE_PATH = CACHE_DIR / "snapshot.json"

# Window layer values, ordered from the desktop upwards.
ZORDER_VALUES = ("bottom", "normal", "top")


def normalize_zorder(value: Any, legacy: dict[str, Any] | None = None) -> str:
    """Coerce a stored window layer, migrating the old boolean if needed."""
    if isinstance(value, str) and value.strip().lower() in ZORDER_VALUES:
        return value.strip().lower()
    if isinstance(legacy, dict) and "always_on_top" in legacy:
        # v1 stored a boolean only: True meant topmost, False meant plain.
        return "top" if bool(legacy.get("always_on_top")) else "normal"
    return "top"

DEFAULT_SETTINGS: dict[str, Any] = {
    "alerts_enabled": True,
    "alert_focus": False,
    "poll_seconds": 60,
    "reduced_motion": False,
    "show_source_detail": True,
    "autostart": False,
    "last_autostart_check": 0,
    # Window layer: "bottom" (pinned to the desktop), "normal", or "top".
    "window_zorder": "top",
    # Legacy boolean mirror of window_zorder == "top"; kept so older
    # settings files (and anything reading the old key) keep working.
    "always_on_top": True,
    "window_opacity": 100,
    # Feature flags for the v2 expansion.  They stay off until the matching
    # feature ships, so the v1 widget behaves exactly as before.
    "features": {"notes": False, "events": False},
}


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    stored = read_json(SETTINGS_PATH, {})
    if isinstance(stored, dict):
        settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    settings["window_zorder"] = normalize_zorder(stored.get("window_zorder"), stored)
    settings["always_on_top"] = settings["window_zorder"] == "top"
    try:
        settings["window_opacity"] = max(35, min(100, int(settings.get("window_opacity", 100))))
    except (TypeError, ValueError):
        settings["window_opacity"] = 100
    features = dict(DEFAULT_SETTINGS["features"])
    stored_features = settings.get("features")
    if isinstance(stored_features, dict):
        features.update({key: bool(value) for key, value in stored_features.items() if key in features})
    settings["features"] = features
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    atomic_write_json(SETTINGS_PATH, settings)


def static_planets() -> dict[str, dict]:
    return read_json(DATA_DIR / "planets.json", {}) or {}


def static_tasks() -> dict[str, dict]:
    return read_json(DATA_DIR / "tasks.json", {"en": {}, "zh": {}}) or {"en": {}, "zh": {}}
