"""Silent visual alert engine for major orders, offensives and planetary changes."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from .config import ALERT_STATE_PATH
from .util import atomic_write_json, iso, now_utc, read_json

LOG = logging.getLogger(__name__)

MAX_HISTORY = 80


class AlertEngine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = read_json(ALERT_STATE_PATH, {})
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.setdefault("history", [])
        self.state.setdefault("event_ids", [])
        self.state.setdefault("global_event_ids", [])
        self.state.setdefault("news_ids", [])
        self.state.setdefault("owners", {})
        self.last_fetched_at = None

    def _save(self) -> None:
        atomic_write_json(ALERT_STATE_PATH, self.state)

    def history(self) -> list[dict]:
        items = list(self.state.get("history") or [])
        items.sort(key=lambda a: a.get("timestamp") or "", reverse=True)
        return items[:MAX_HISTORY]

    def acknowledge(self, alert_id: str) -> bool:
        with self._lock:
            for item in self.state.get("history") or []:
                if item.get("id") == alert_id:
                    item["acknowledged"] = True
                    self._save()
                    return True
        return False

    def _new_alert(self, kind: str, severity: str, title: str, body: str, **extra: Any) -> dict:
        alert = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "severity": severity,
            "title": title,
            "body": body,
            "timestamp": iso(now_utc()),
            "acknowledged": False,
        }
        alert.update(extra)
        return alert

    def evaluate(self, snapshot: dict) -> list[dict]:
        if not snapshot or not snapshot.get("ok"):
            return []
        fetched_at = snapshot.get("fetched_at")
        if fetched_at == self.last_fetched_at:
            return []
        with self._lock:
            self.last_fetched_at = fetched_at
            first_run = not self.state.get("initialized")
            alerts: list[dict] = []
            mo = snapshot.get("major_order") or {}
            events = snapshot.get("events") or []
            global_events = snapshot.get("global_events") or []
            planets = snapshot.get("planets") or []
            dss = snapshot.get("dss") or {}

            event_ids = set(int(x) for x in (self.state.get("event_ids") or []) if str(x).isdigit())
            global_ids = set(int(x) for x in (self.state.get("global_event_ids") or []) if str(x).isdigit())
            news_ids = set(int(x) for x in (self.state.get("news_ids") or []) if str(x).isdigit())
            owners = self.state.get("owners") or {}

            if first_run:
                LOG.info("Alert baseline established; no alerts on first snapshot")
                self.state["initialized"] = True
                self.state["mo_id"] = mo.get("id")
                self.state["mo_expiry_alerted"] = False
                self.state["event_ids"] = [e.get("id") for e in events if e.get("id") is not None]
                self.state["global_event_ids"] = [g.get("event_id") for g in global_events if g.get("event_id") is not None]
                self.state["news_ids"] = [n.get("id") for n in (snapshot.get("news") or [])[:20] if n.get("id") is not None]
                self.state["owners"] = {str(p.get("index")): p.get("owner_id") for p in planets}
                self.state["dss_planet"] = dss.get("planet_index")
                self._save()
                return []

            # New Major Order
            mo_id = mo.get("id")
            if mo_id is not None and mo_id != self.state.get("mo_id"):
                objectives = mo.get("objectives") or []
                lines = [f"优先级目标：{o.get('label')}" for o in objectives[:3]]
                alerts.append(self._new_alert(
                    "MAJOR_ORDER",
                    "critical",
                    "新重要指令 // NEW MAJOR ORDER",
                    (mo.get("title") or "超级地球最高指挥部发布新的重要指令。") + ("\n" + "\n".join(lines) if lines else ""),
                    major_order_id=mo_id,
                ))
                self.state["mo_id"] = mo_id
                self.state["mo_expiry_alerted"] = False

            # MO final hours
            remaining = mo.get("remaining_seconds")
            if isinstance(remaining, (int, float)) and 0 < remaining <= 6 * 3600 and not self.state.get("mo_expiry_alerted"):
                alerts.append(self._new_alert(
                    "MO_EXPIRING",
                    "high",
                    "重要指令进入终局 // FINAL HOURS",
                    f"{mo.get('title') or '重要指令'} 剩余 {mo.get('remaining')}。所有绝地潜兵立即归队。",
                    major_order_id=mo_id,
                ))
                self.state["mo_expiry_alerted"] = True

            # New planetary events
            for event in events:
                event_id = event.get("id")
                if event_id is None:
                    continue
                try:
                    event_id = int(event_id)
                except (TypeError, ValueError):
                    continue
                if event_id in event_ids:
                    continue
                planet_name = event.get("planet_name_zh") or event.get("planet_name") or f"星球 {event.get('planet_index')}"
                severity = "critical" if event.get("severity") == "CRITICAL" else "high"
                defense = "防御" if event.get("is_defense") else "解放"
                body = (
                    f"{planet_name} // {event.get('faction', {}).get('zh', '未知')} // {defense}进度 "
                    f"{event.get('progress', 0):.0f}% // 在线 {event.get('players', 0):,} // 剩余 {event.get('remaining', '--')}"
                )
                alerts.append(self._new_alert(
                    "PLANETARY_EVENT",
                    severity,
                    f"{'行星防御' if event.get('is_defense') else '行星攻势'} // {planet_name}",
                    body,
                    planet_index=event.get("planet_index"),
                ))
                event_ids.add(event_id)
            self.state["event_ids"] = list(event_ids)[-400:]

            # New major galactic event / offensive
            for ge in global_events:
                event_id = ge.get("event_id")
                if event_id is None:
                    continue
                try:
                    event_id = int(event_id)
                except (TypeError, ValueError):
                    continue
                if event_id in global_ids:
                    continue
                if ge.get("flag") == 1:
                    alerts.append(self._new_alert(
                        "GALACTIC_EVENT",
                        "critical",
                        f"银河级事件 // {ge.get('title') or 'GRAND OFFENSIVE'}",
                        (ge.get("message") or "")[:520],
                    ))
                global_ids.add(event_id)
            self.state["global_event_ids"] = list(global_ids)[-100:]

            # Owner changes, aggregated
            lost, liberated = [], []
            new_owners = {}
            mo_targets = {o.get("planet_index") for o in (mo.get("objectives") or [])}
            event_planets = {e.get("planet_index") for e in events}
            for planet in planets:
                index = str(planet.get("index"))
                owner = planet.get("owner_id")
                new_owners[index] = owner
                previous = owners.get(index)
                if previous is None or previous == owner:
                    continue
                if int(planet.get("players") or 0) < 200 and planet.get("index") not in mo_targets and planet.get("index") not in event_planets:
                    continue
                label = planet.get("name_zh") or planet.get("name") or f"星球 {index}"
                if previous == 1 and owner != 1:
                    lost.append(label)
                elif previous != 1 and owner == 1:
                    liberated.append(label)
            if lost or liberated:
                parts = []
                if lost:
                    parts.append("失守：" + "、".join(lost[:4]))
                if liberated:
                    parts.append("解放：" + "、".join(liberated[:4]))
                alerts.append(self._new_alert(
                    "PLANET_CHANGE",
                    "critical" if lost else "high",
                    "银河战线变动 // FRONTLINE SHIFT",
                    " // ".join(parts),
                ))
            self.state["owners"] = new_owners

            # DSS relocation
            dss_planet = dss.get("planet_index")
            if dss_planet and self.state.get("dss_planet") and dss_planet != self.state.get("dss_planet"):
                name = dss.get("planet_name_zh") or dss.get("planet_name") or f"星球 {dss_planet}"
                alerts.append(self._new_alert(
                    "DSS",
                    "medium",
                    "民主空间站重新部署 // DSS REDEPLOYED",
                    f"民主空间站已进入 {name} 轨道。",
                    planet_index=dss_planet,
                ))
            if dss_planet:
                self.state["dss_planet"] = dss_planet

            # Track high-priority news without raising an alert for every dispatch.
            # Major orders and planetary events are covered above with stronger signals.
            for item in (snapshot.get("news") or [])[:20]:
                news_id = item.get("id")
                try:
                    news_ids.add(int(news_id))
                except (TypeError, ValueError):
                    continue
            self.state["news_ids"] = list(news_ids)[-300:]

            if alerts:
                history = list(self.state.get("history") or [])
                history.extend(alerts)
                history = history[-MAX_HISTORY:]
                self.state["history"] = history
                self._save()
            else:
                # Persist baseline changes even when no alert fired.
                self._save()
            return alerts
