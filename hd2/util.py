"""Shared helpers for the Helldivers Tactical Command app."""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

FACTION_BY_ID = {
    1: {"key": "humans", "en": "HUMANS", "zh": "人类", "short": "SE"},
    2: {"key": "terminids", "en": "TERMINIDS", "zh": "终结族", "short": "TER"},
    3: {"key": "automaton", "en": "AUTOMATON", "zh": "机器人", "short": "AUT"},
    4: {"key": "illuminate", "en": "ILLUMINATE", "zh": "光能者", "short": "ILL"},
}

FACTION_ALIASES = {
    "human": 1, "humans": 1, "super earth": 1, "se": 1,
    "terminid": 2, "terminids": 2, "bug": 2, "bugs": 2,
    "automaton": 3, "automatons": 3, "bot": 3, "bots": 3,
    "illuminate": 4, "illuminates": 4, "squid": 4, "squids": 4,
}


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(dt: _dt.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> _dt.datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc)
    except ValueError:
        return None


def localized(value: Any, prefer: str = "zh-Hans") -> str:
    """Handle ArrowHead localized fields ({'zh-Hans': ..., 'en-US': ...})."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        keys = [prefer, "zh-Hans", "zh-Hant", "en-US", "en-GB"]
        for key in keys:
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in value.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return str(value).strip()


def faction_id(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value in FACTION_BY_ID else 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text.isdigit():
            return faction_id(int(text))
        return FACTION_ALIASES.get(text, 0)
    return 0


def faction(value: Any) -> dict:
    fid = faction_id(value)
    return dict(FACTION_BY_ID.get(fid, {"key": "unknown", "en": "UNKNOWN", "zh": "未知", "short": "UNK"}), id=fid)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def pct(part: float, total: float) -> float:
    try:
        if not total:
            return 0.0
        return clamp(part / total * 100.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def compact_number(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0"
    neg = n < 0
    n = abs(n)
    for unit, size in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= size:
            text = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return ("-" if neg else "") + text + unit
    return ("-" if neg else "") + str(int(n))


def human_duration(seconds: Any) -> str:
    try:
        s = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return "--:--:--"
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}D {hours:02d}:{mins:02d}:{secs:02d}"
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            try:
                os.unlink(temp)
            except OSError:
                pass


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default
