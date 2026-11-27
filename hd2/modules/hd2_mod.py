"""Adapter that exposes the existing Helldivers live data as a game module.

`hd2/api.py` (905 lines, fields consumed by 20+ front-end spots) is deliberately
untouched.  The adapter only translates the snapshot the app already fetches into
one generic widget card, so the shell can render "some game" without knowing what
a major order or a liberation percentage is.
"""
from __future__ import annotations

import logging

from ..util import compact_number
from .base import BaseModule, GameInfo, WidgetCard

LOG = logging.getLogger(__name__)


class Hd2Module(BaseModule):
    """Observes snapshots pushed by `AppController.refresh_once`."""

    def __init__(self, info: GameInfo, services: dict[str, object] | None = None) -> None:
        super().__init__(info)
        # Shell services injected by AppController (e.g. settings, scheduler).
        # M0 only needs the snapshot, but the hook keeps later features cheap.
        self.services: dict[str, object] = dict(services or {})
        self._snapshot: dict | None = None
        self._updated_utc: str | None = None

    # -- data in ---------------------------------------------------------
    def observe(self, snapshot: dict | None) -> None:
        """Record the newest snapshot (called from the war job; no network here)."""
        if isinstance(snapshot, dict):
            self._snapshot = snapshot
            self._updated_utc = snapshot.get("fetched_at") or snapshot.get("generated_utc")

    # -- module contract -------------------------------------------------
    def refresh(self) -> None:
        """No-op: the war job in `app.py` already owns fetching (avoid duplicates)."""
        return None

    def widget_card(self) -> WidgetCard | None:
        snapshot = self._snapshot
        if not snapshot:
            return None
        source = snapshot.get("source") or {}
        war = snapshot.get("war") or {}
        major = snapshot.get("major_order") or {}
        status = str(source.get("status") or "").upper()
        metrics: list[tuple[str, str]] = []
        if war.get("players") is not None:
            metrics.append(("ONLINE", compact_number(war.get("players"))))
        if major.get("title"):
            metrics.append(("MO", str(major.get("title"))))
        if major.get("progress") is not None:
            metrics.append(("PROGRESS", f"{float(major.get('progress') or 0):.0f}%"))
        elif war.get("war_id") is not None:
            metrics.append(("WAR", str(war.get("war_id"))))
        return WidgetCard(
            module_id=self.info.id,
            title=self.info.cn,
            subtitle=str(source.get("name") or ""),
            status=status,
            metrics=tuple(metrics),
            updated_utc=self._updated_utc,
        )
