"""Live data acquisition and normalization.

Primary source: https://api.helldivers2.dev/api/v1 (community API)
Fallback 1:   https://api.live.prod.thehelldiversgame.com/api (ArrowHead direct)
Fallback 2:   https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import gzip
import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from .config import DATA_DIR, SNAPSHOT_CACHE_PATH, static_planets, static_tasks
from .util import (
    FACTION_BY_ID,
    atomic_write_json,
    clamp,
    faction,
    faction_id,
    human_duration,
    iso,
    localized,
    now_utc,
    pct,
    read_json,
)

LOG = logging.getLogger(__name__)

COMMUNITY_BASE = "https://api.helldivers2.dev/api/v1"
OFFICIAL_BASE = "https://api.live.prod.thehelldiversgame.com/api"
COMPANION_URL = "https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live"

USER_AGENT = "HelldiversTactical/1.0 (+desktop companion; contact: local user)"


class SourceError(RuntimeError):
    pass


def _decode_body(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        return gzip.decompress(raw)
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def http_json(url: str, timeout: float = 10.0, attempts: int = 1) -> Any:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "zh-Hans, zh-Hant;q=0.9, en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "X-Super-Client": "HelldiversTactical",
        "X-Super-Contact": "local-desktop-app",
    }
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            context = ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
                raw = _decode_body(raw, response.headers.get("Content-Encoding", ""))
                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type.lower() and raw[:1] not in (b"[", b"{"):
                    raise SourceError(f"unexpected content type: {content_type}")
                return json.loads(raw.decode("utf-8-sig"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, SourceError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.8)
    raise SourceError(f"{url}: {last_error}") from last_error


def _tagless(value: Any) -> str:
    text = localized(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _task_objectives(assignment: dict, planets_by_index: dict[str, dict]) -> list[dict]:
    settings = assignment.get("setting") or {}
    tasks = settings.get("tasks") or assignment.get("tasks") or []
    progress = assignment.get("progress") or []
    mapping_en = static_tasks().get("en", {})
    mapping_zh = static_tasks().get("zh", {})
    output = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_type = task.get("type")
        values = task.get("values") or []
        value_types = task.get("valueTypes") or []
        goal = 1
        planet_index: int | None = None
        for value, value_type in zip(values, value_types):
            if value_type == 3:
                goal = max(1, int(value))
            elif value_type == 12:
                try:
                    planet_index = int(value)
                except (TypeError, ValueError):
                    planet_index = None
        planet = planets_by_index.get(str(planet_index), {}) if planet_index is not None else {}
        type_en = mapping_en.get(str(task_type), "OBJECTIVE")
        type_zh = mapping_zh.get(str(task_type), "战术目标")
        if planet:
            label = f"{type_zh} {planet.get('name_zh') or planet.get('name')}"
        else:
            label = type_zh
        current = progress[index] if index < len(progress) else 0
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 0
        output.append({
            "index": index,
            "type": task_type,
            "type_en": type_en,
            "type_zh": type_zh,
            "label": label,
            "goal": goal,
            "current": min(current, goal),
            "done": current >= goal,
            "planet_index": planet_index,
            "planet_name": planet.get("name") or "",
            "planet_name_zh": planet.get("name_zh") or "",
        })
    return output


def _empty_snapshot(source_name: str = "NONE") -> dict:
    return {
        "ok": False,
        "fetched_at": iso(now_utc()),
        "source": {"name": source_name, "status": "OFFLINE", "endpoint": "", "latency_ms": 0},
        "war": {},
        "major_order": None,
        "events": [],
        "planets": [],
        "news": [],
        "global_events": [],
        "dss": None,
        "stats": {},
    }


def _base_snapshot(planets: list[dict], source: dict, fetched_at: dt.datetime) -> dict:
    events = [p["event"] for p in planets if p.get("event")]
    events.sort(key=lambda e: (e.get("remaining_seconds") or 0))
    players = sum(int(p.get("players") or 0) for p in planets)
    humans = sum(1 for p in planets if p.get("owner_id") == 1)
    return {
        "ok": True,
        "fetched_at": iso(fetched_at),
        "source": source,
        "planets": planets,
        "events": events,
        "war": {
            "id": source.get("war_id"),
            "players": players,
            "human_planets": humans,
            "total_planets": len(planets),
            "active_events": len(events),
        },
        "major_order": None,
        "news": [],
        "global_events": [],
        "dss": None,
        "stats": {},
    }


def _planet_entry(
    index: int,
    info: dict,
    status: dict,
    static: dict,
    event: dict | None,
    dss_planet: int | None,
    attack_sources: list[int],
    now: dt.datetime,
    world_clock: int,
) -> dict:
    owner_id = faction_id(status.get("owner"))
    owner = faction(owner_id)
    max_health = info.get("maxHealth") or status.get("maxHealth") or 1_000_000
    health = status.get("health") or 0
    pos = info.get("position") or status.get("position") or {}
    x = float(pos.get("x") or 0)
    y = float(pos.get("y") or 0)
    players = int(status.get("players") or 0)
    event_out = None
    if event:
        e_health = event.get("health") or 0
        e_max = event.get("maxHealth") or 1
        e_race = faction_id(event.get("race") or event.get("faction"))
        remaining = None
        if event.get("_remaining_seconds") is not None:
            remaining = int(event["_remaining_seconds"])
            expires_at = now + dt.timedelta(seconds=max(0, remaining))
        else:
            end = event.get("_end_dt")
            expires_at = end
            remaining = max(0, int((end - now).total_seconds())) if end else None
        progress = clamp((1 - e_health / e_max) * 100) if e_max else 0.0
        event_out = {
            "id": event.get("id"),
            "planet_index": index,
            "event_type": event.get("eventType") or event.get("type") or 1,
            "faction": faction(e_race),
            "health": e_health,
            "max_health": e_max,
            "progress": round(progress, 1),
            "remaining_seconds": remaining,
            "remaining": human_duration(remaining or 0),
            "expires_at": iso(expires_at),
            "started_at": event.get("_start_iso"),
            "campaign_id": event.get("campaignId"),
            "joint_operation_ids": event.get("jointOperationIds") or [],
            "is_defense": owner_id == 1,
            "players": players,
            "planet_name": static.get("name") or f"PLANET {index}",
            "planet_name_zh": static.get("name_zh") or "",
            "severity": "CRITICAL" if (e_max >= 1_000_000 or players >= 5000) else "HIGH",
        }
    if event_out:
        liberation = event_out["progress"]
        status_label = "DEFENSE" if owner_id == 1 else "LIBERATION"
    elif owner_id == 1:
        liberation = 100.0
        status_label = "SECURED"
    else:
        liberation = clamp((1 - health / max_health) * 100) if max_health else 0.0
        status_label = "LIBERATION"
    return {
        "index": index,
        "name": static.get("name") or f"PLANET {index}",
        "name_zh": static.get("name_zh") or "",
        "sector": static.get("sector") or "",
        "biome": static.get("biome") or "",
        "type": static.get("type") or "",
        "environmentals": static.get("environmentals") or [],
        "x": x,
        "y": y,
        "waypoints": info.get("waypoints") or [],
        "max_health": max_health,
        "health": health,
        "players": players,
        "regen": status.get("regenPerSecond") or 0,
        "owner_id": owner_id,
        "owner": owner,
        "liberation": round(liberation, 1),
        "status": status_label,
        "event": event_out,
        "attack_sources": attack_sources,
        "has_dss": index == dss_planet,
    }


def normalize_arrowhead(
    status: dict,
    warinfo: dict,
    summary: dict | None,
    news: list | None,
    assignments: list | None,
    source_name: str,
    endpoint: str,
    latency_ms: int,
    space_stations: list | None = None,
    war_id: int | None = None,
) -> dict:
    now = now_utc()
    fetched_at = now
    static = static_planets()
    world_clock = int(status.get("time") or 0)
    war_start_ts = warinfo.get("startDate") or 0
    war_start = dt.datetime.fromtimestamp(war_start_ts, dt.timezone.utc) if war_start_ts else None
    infos = {int(p.get("index")): p for p in (warinfo.get("planetInfos") or []) if p.get("index") is not None}
    statuses = {int(p.get("index")): p for p in (status.get("planetStatus") or []) if p.get("index") is not None}
    dss_planet = None
    stations = space_stations if space_stations is not None else (status.get("spaceStations") or [])
    if stations:
        try:
            dss_planet = int(stations[0].get("planetIndex"))
        except (TypeError, ValueError, IndexError):
            dss_planet = None
    attack_sources: dict[int, list[int]] = {}
    for attack in status.get("planetAttacks") or []:
        try:
            target = int(attack.get("target"))
            source = int(attack.get("source"))
        except (TypeError, ValueError):
            continue
        attack_sources.setdefault(target, []).append(source)

    events_by_planet: dict[int, dict] = {}
    for event in status.get("planetEvents") or []:
        try:
            planet_index = int(event.get("planetIndex"))
        except (TypeError, ValueError):
            continue
        copy = dict(event)
        if copy.get("expireTime") is not None:
            copy["_remaining_seconds"] = max(0, int(copy["expireTime"]) - world_clock)
        elif copy.get("endTime"):
            copy["_end_dt"] = _parse_event_dt(copy.get("endTime"))
        if copy.get("startTime") is not None and isinstance(copy.get("startTime"), (int, float)) and world_clock:
            copy["_start_iso"] = iso(now - dt.timedelta(seconds=max(0, world_clock - float(copy["startTime"]))))
        else:
            copy["_start_iso"] = iso(_parse_event_dt(copy.get("startTime")))
        events_by_planet[planet_index] = copy

    planets = []
    for index, st in statuses.items():
        info = infos.get(index) or {}
        static_info = static.get(str(index), {})
        planets.append(_planet_entry(index, info, st, static_info, events_by_planet.get(index), dss_planet, attack_sources.get(index, []), now, world_clock))
    planets.sort(key=lambda p: p["index"])
    snapshot = _base_snapshot(planets, {
        "name": source_name,
        "status": "LIVE",
        "endpoint": endpoint,
        "latency_ms": latency_ms,
        "war_id": war_id or status.get("warId") or warinfo.get("warId"),
        "localized": source_name == "ARROWHEAD DIRECT",
    }, fetched_at)

    planets_by_index = {str(p["index"]): p for p in planets}
    assignment = _pick_assignment(assignments or [])
    global_events_raw = [g for g in (status.get("globalEvents") or []) if isinstance(g, dict)]
    active_global = []
    for g in global_events_raw:
        expire = g.get("expireTime")
        remaining = int(expire) - world_clock if isinstance(expire, (int, float)) else None
        if remaining is not None and remaining <= 0:
            continue
        active_global.append({
            "event_id": g.get("eventId"),
            "id32": g.get("id32"),
            "title": _tagless(g.get("title")),
            "message": _tagless(g.get("message")),
            "flag": g.get("flag"),
            "assignment_id": g.get("assignmentId32"),
            "remaining_seconds": remaining,
            "remaining": human_duration(remaining or 0),
            "expires_at": iso(now + dt.timedelta(seconds=max(0, remaining or 0))) if remaining is not None else None,
        })
    snapshot["global_events"] = active_global

    if assignment:
        matched = next((g for g in active_global if g.get("assignment_id") == assignment.get("id32")), None)
        settings = assignment.get("setting") or {}
        title = (matched or {}).get("title") or _tagless(settings.get("overrideTitle")) or "MAJOR ORDER"
        briefing = (matched or {}).get("message") or _tagless(settings.get("overrideBrief")) or _tagless(settings.get("taskDescription"))
        expires_in = assignment.get("expiresIn")
        expires_at = now + dt.timedelta(seconds=max(0, int(expires_in))) if isinstance(expires_in, (int, float)) else None
        objectives = _task_objectives(assignment, planets_by_index)
        total = len(objectives) or 1
        complete = sum(1 for o in objectives if o["done"])
        reward_raw = settings.get("reward") or {}
        if not reward_raw and settings.get("rewards"):
            reward_raw = settings["rewards"][0]
        snapshot["major_order"] = {
            "id": assignment.get("id32") or assignment.get("id"),
            "title": title,
            "briefing": briefing,
            "expires_at": iso(expires_at),
            "remaining_seconds": int(expires_in) if isinstance(expires_in, (int, float)) else None,
            "remaining": human_duration(expires_in or 0),
            "reward": {"type": _reward_name(reward_raw.get("type")), "amount": reward_raw.get("amount") or 0},
            "objectives": objectives,
            "progress": round(complete / total * 100, 1),
            "complete_objectives": complete,
            "total_objectives": len(objectives),
            "global_event": matched,
        }

    stats = (summary or {}).get("galaxy_stats") or {}
    war_stats = {
        "missions_won": stats.get("missionsWon") or 0,
        "missions_lost": stats.get("missionsLost") or 0,
        "kills": (stats.get("bugKills") or 0) + (stats.get("automatonKills") or 0) + (stats.get("illuminateKills") or 0),
        "bug_kills": stats.get("bugKills") or 0,
        "automaton_kills": stats.get("automatonKills") or 0,
        "illuminate_kills": stats.get("illuminateKills") or 0,
        "deaths": stats.get("deaths") or 0,
        "accuracy": stats.get("accurracy") or stats.get("accuracy") or 0,
        "success_rate": stats.get("missionSuccessRate") or 0,
        "impact": status.get("impactMultiplier") or 0,
        "war_started": iso(war_start),
    }
    snapshot["stats"] = war_stats
    snapshot["war"].update({
        "started": iso(war_start),
        "impact": war_stats["impact"],
        "missions_won": war_stats["missions_won"],
        "kills": war_stats["kills"],
        "accuracy": war_stats["accuracy"],
        "success_rate": war_stats["success_rate"],
    })

    news_items = []
    seen_news: set = set()
    for item in news or []:
        if not isinstance(item, dict):
            continue
        news_id = item.get("id")
        if news_id in seen_news:
            continue
        seen_news.add(news_id)
        published = item.get("published")
        published_at = _news_time(published, now, world_clock, war_start)
        news_items.append({
            "id": news_id,
            "published_at": iso(published_at),
            "message": _tagless(item.get("message")),
            "type": item.get("type") or 0,
        })
    news_items.sort(key=lambda n: n.get("published_at") or "", reverse=True)
    snapshot["news"] = news_items[:50]

    if stations:
        station = dict(stations[0])
        station_planet = planets_by_index.get(str(dss_planet), {})
        snapshot["dss"] = {
            "id": station.get("id32"),
            "planet_index": dss_planet,
            "planet_name": station_planet.get("name") or "",
            "planet_name_zh": station_planet.get("name_zh") or "",
            "election_end": station.get("currentElectionEndWarTime"),
            "tactical_actions": station.get("tacticalActions") or [],
        }

    if snapshot["major_order"] is None and news_items:
        # Keep the panel useful if the assignment endpoint is temporarily unavailable.
        snapshot["major_order"] = None
    return snapshot


def _parse_event_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
        return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _news_time(value: Any, now: dt.datetime, world_clock: int, war_start: dt.datetime | None) -> dt.datetime:
    if isinstance(value, str):
        parsed = _parse_event_dt(value)
        if parsed:
            return parsed
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1_500_000_000:
            return dt.datetime.fromtimestamp(number, dt.timezone.utc)
        if world_clock and number <= world_clock:
            return now - dt.timedelta(seconds=max(0, world_clock - number))
        if war_start:
            return war_start + dt.timedelta(seconds=number)
    return now


def _pick_assignment(assignments: list) -> dict | None:
    best = None
    for item in assignments or []:
        if not isinstance(item, dict):
            continue
        settings = item.get("setting") or {}
        if settings.get("type") == 4 or item.get("type") == 4:
            return item
        best = best or item
    return best


def _reward_name(value: Any) -> str:
    return {1: "MEDALS", 2: "SUPER CREDITS", 3: "REQUISITION", 4: "SAMPLES"}.get(value, "REWARD")


def normalize_community(assignments: list, planets_raw: list, war: dict, dispatches: list, source_endpoint: str, latency_ms: int) -> dict:
    now = now_utc()
    static = static_planets()
    planets = []
    for raw in planets_raw:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        static_info = static.get(str(index), {})
        name = localized(raw.get("name"))
        name_en = static_info.get("name") or name.upper()
        owner_id = faction_id(raw.get("currentOwner"))
        pos = raw.get("position") or {}
        planet_stats = raw.get("statistics") or {}
        player_count = int(raw.get("players") or planet_stats.get("playerCount") or 0)
        event_raw = raw.get("event")
        event_out = None
        if isinstance(event_raw, dict):
            e_faction = event_raw.get("faction") or event_raw.get("race")
            e_max = event_raw.get("maxHealth") or 1
            e_health = event_raw.get("health") or 0
            end_dt = _parse_event_dt(event_raw.get("endTime"))
            remaining = max(0, int((end_dt - now).total_seconds())) if end_dt else None
            event_out = {
                "id": event_raw.get("id"),
                "planet_index": index,
                "event_type": event_raw.get("eventType") or 1,
                "faction": faction(e_faction),
                "health": e_health,
                "max_health": e_max,
                "progress": round(clamp((1 - e_health / e_max) * 100), 1) if e_max else 0,
                "remaining_seconds": remaining,
                "remaining": human_duration(remaining or 0),
                "expires_at": iso(end_dt),
                "started_at": event_raw.get("startTime"),
                "campaign_id": event_raw.get("campaignId"),
                "joint_operation_ids": event_raw.get("jointOperationIds") or [],
                "is_defense": owner_id == 1,
                "players": player_count,
                "planet_name": name_en,
                "planet_name_zh": static_info.get("name_zh") or "",
                "severity": "CRITICAL" if (e_max >= 1_000_000 or player_count >= 5000) else "HIGH",
            }
        max_health = raw.get("maxHealth") or static_info.get("max_health") or 1_000_000
        health = raw.get("health") or 0
        if event_out:
            liberation = event_out["progress"]
        elif owner_id == 1:
            liberation = 100.0
        else:
            liberation = clamp((1 - health / max_health) * 100) if max_health else 0
        planets.append({
            "index": index,
            "name": name_en,
            "name_zh": static_info.get("name_zh") or (name if any("\u4e00" <= c <= "\u9fff" for c in name) else ""),
            "sector": static_info.get("sector") or localized(raw.get("sector")),
            "biome": static_info.get("biome") or (raw.get("biome") or {}).get("name", "") if isinstance(raw.get("biome"), dict) else static_info.get("biome", ""),
            "type": static_info.get("type") or "",
            "environmentals": static_info.get("environmentals") or [],
            "x": float(pos.get("x") or 0),
            "y": float(pos.get("y") or 0),
            "waypoints": raw.get("waypoints") or [],
            "max_health": max_health,
            "health": health,
            "players": player_count,
            "regen": raw.get("regenPerSecond") or 0,
            "owner_id": owner_id,
            "owner": faction(owner_id),
            "liberation": round(liberation, 1),
            "status": "DEFENSE" if event_out and owner_id == 1 else ("LIBERATION" if owner_id != 1 else "SECURED"),
            "event": event_out,
            "attack_sources": [],
            "has_dss": False,
        })
    planets.sort(key=lambda p: p["index"])
    snapshot = _base_snapshot(planets, {
        "name": "COMMUNITY API",
        "status": "LIVE",
        "endpoint": source_endpoint,
        "latency_ms": latency_ms,
        "war_id": war.get("id") or war.get("warId"),
        "localized": True,
    }, now)
    planets_by_index = {str(p["index"]): p for p in planets}
    assignment = _pick_assignment(assignments or [])
    if assignment:
        settings = assignment.get("setting") or {}
        title = _tagless(assignment.get("title")) or _tagless(settings.get("overrideTitle")) or ""
        briefing = _tagless(assignment.get("briefing")) or _tagless(settings.get("overrideBrief")) or _tagless(settings.get("taskDescription")) or ""
        if not title:
            for dispatch in dispatches or []:
                if not isinstance(dispatch, dict):
                    continue
                text = _tagless(dispatch.get("message"))
                if any(token in text for token in ("NEW MAJOR ORDER", "新重要指令", "MAJOR ORDER")):
                    title = "新重要指令" if "新重要指令" in text else "NEW MAJOR ORDER"
                    briefing = briefing or text
                    break
        title = title or "MAJOR ORDER"
        expiration = _parse_event_dt(assignment.get("expiration"))
        remaining = max(0, int((expiration - now).total_seconds())) if expiration else None
        objectives = _task_objectives(assignment, planets_by_index)
        complete = sum(1 for o in objectives if o["done"])
        total = len(objectives) or 1
        reward_raw = settings.get("reward") or (settings.get("rewards") or [{}])[0] or {}
        if not reward_raw:
            reward_raw = assignment.get("reward") or {}
        snapshot["major_order"] = {
            "id": assignment.get("id"),
            "title": title,
            "briefing": briefing,
            "expires_at": iso(expiration),
            "remaining_seconds": remaining,
            "remaining": human_duration(remaining or 0),
            "reward": {"type": _reward_name(reward_raw.get("type")), "amount": reward_raw.get("amount") or 0},
            "objectives": objectives,
            "progress": round(complete / total * 100, 1),
            "complete_objectives": complete,
            "total_objectives": len(objectives),
            "global_event": None,
        }
    stats = war.get("statistics") or {}
    war_stats = {
        "missions_won": stats.get("missionsWon") or 0,
        "missions_lost": stats.get("missionsLost") or 0,
        "kills": (stats.get("terminidKills") or 0) + (stats.get("automatonKills") or 0) + (stats.get("illuminateKills") or 0),
        "bug_kills": stats.get("terminidKills") or 0,
        "automaton_kills": stats.get("automatonKills") or 0,
        "illuminate_kills": stats.get("illuminateKills") or 0,
        "deaths": stats.get("deaths") or 0,
        "accuracy": stats.get("accuracy") or 0,
        "success_rate": stats.get("missionSuccessRate") or 0,
        "impact": war.get("impactMultiplier") or 0,
        "war_started": war.get("started"),
    }
    snapshot["stats"] = war_stats
    snapshot["war"].update({
        "started": war.get("started"),
        "impact": war_stats["impact"],
        "missions_won": war_stats["missions_won"],
        "kills": war_stats["kills"],
        "accuracy": war_stats["accuracy"],
        "success_rate": war_stats["success_rate"],
        "players": stats.get("playerCount") or sum(int(p.get("players") or 0) for p in planets),
    })
    news_items = []
    for item in dispatches or []:
        if not isinstance(item, dict):
            continue
        news_items.append({
            "id": item.get("id"),
            "published_at": iso(_parse_event_dt(item.get("published"))),
            "message": _tagless(item.get("message")),
            "type": item.get("type") or 0,
        })
    news_items.sort(key=lambda n: n.get("published_at") or "", reverse=True)
    snapshot["news"] = news_items[:50]
    return snapshot


class LiveDataService:
    """Fetches, normalizes and caches the war snapshot with fallbacks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._community_failures = 0
        self._community_next_try = 0.0
        self.snapshot = _empty_snapshot()
        self.last_error = ""

    def load_cached(self) -> dict | None:
        cached = read_json(SNAPSHOT_CACHE_PATH, None)
        archived = False
        if not (isinstance(cached, dict) and cached.get("ok")):
            cached = read_json(DATA_DIR / "fallback_snapshot.json", None)
            archived = True
        if isinstance(cached, dict) and cached.get("ok"):
            cached = dict(cached)
            cached["source"] = dict(cached.get("source") or {})
            cached["source"]["status"] = "ARCHIVE" if archived else "CACHED"
            if archived:
                cached["source"]["name"] = "ARCHIVE SNAPSHOT"
            else:
                cached["source"]["name"] = (cached["source"].get("name") or "CACHE") + " // CACHE"
            cached["cached"] = True
            with self._lock:
                self.snapshot = cached
            return cached
        return None

    def _save(self, snapshot: dict) -> None:
        atomic_write_json(SNAPSHOT_CACHE_PATH, snapshot)

    def fetch(self) -> dict:
        errors = []
        now = time.time()
        try_community = now >= self._community_next_try
        community_snapshot: dict | None = None
        official_snapshot: dict | None = None
        community_started = official_started = 0.0

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            community_future = pool.submit(self._fetch_community) if try_community else None
            official_future = pool.submit(self._fetch_official)
            if community_future:
                community_started = time.time()
                try:
                    community_snapshot = community_future.result()
                    community_snapshot["source"]["latency_ms"] = int((time.time() - community_started) * 1000)
                    self._community_failures = 0
                    self._community_next_try = 0
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"community: {exc}")
                    self._community_failures += 1
                    self._community_next_try = now + min(900, 60 * (2 ** min(4, self._community_failures)))
                    LOG.warning("Community API unavailable: %s", exc)
            else:
                errors.append("community: backoff")
            official_started = time.time()
            try:
                official_snapshot = official_future.result()
                official_snapshot["source"]["latency_ms"] = int((time.time() - official_started) * 1000)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"arrowhead: {exc}")
                LOG.warning("ArrowHead API unavailable: %s", exc)

        if community_snapshot and official_snapshot:
            merged = self._merge_dual(community_snapshot, official_snapshot)
            self._finish(merged)
            return merged
        if community_snapshot:
            self._finish(community_snapshot)
            return community_snapshot
        if official_snapshot:
            self._finish(official_snapshot)
            return official_snapshot

        try:
            started = time.time()
            snapshot = self._fetch_companion()
            snapshot["source"]["latency_ms"] = int((time.time() - started) * 1000)
            self._finish(snapshot)
            return snapshot
        except Exception as exc:  # noqa: BLE001
            errors.append(f"companion: {exc}")
            LOG.warning("Companion relay unavailable: %s", exc)

        self.last_error = " | ".join(errors)
        cached = self.load_cached()
        if cached:
            cached["source"]["status"] = "CACHED"
            cached["source"]["name"] = (cached["source"].get("name") or "CACHE") + " // CACHE"
            cached["source"]["error"] = self.last_error
            return cached
        raise SourceError(self.last_error)

    @staticmethod
    def _merge_dual(community: dict, official: dict) -> dict:
        """Use community planets/tempo plus ArrowHead's localized MO narrative."""
        merged = dict(official)
        if community.get("planets"):
            merged["planets"] = community["planets"]
            merged["events"] = community.get("events") or []
        merged["war"] = dict(official.get("war") or {})
        for key, value in (community.get("war") or {}).items():
            if value not in (None, "", 0):
                merged["war"][key] = value
        merged["war"]["id"] = (official.get("war") or {}).get("id")
        merged["war"]["players"] = (community.get("war") or {}).get("players") or (official.get("war") or {}).get("players") or 0
        merged["stats"] = {**(community.get("stats") or {}), **{k: v for k, v in (official.get("stats") or {}).items() if v}}
        dss = official.get("dss")
        if dss:
            for planet in merged["planets"]:
                if planet.get("index") == dss.get("planet_index"):
                    planet["has_dss"] = True
                    break
        merged["dss"] = dss
        merged["major_order"] = official.get("major_order") or community.get("major_order")
        merged["global_events"] = official.get("global_events") or []
        merged["news"] = official.get("news") or community.get("news") or []
        source = dict(community.get("source") or {})
        source.update({
            "name": "COMMUNITY API",
            "status": "LIVE",
            "endpoint": COMMUNITY_BASE,
            "secondary": "ARROWHEAD DIRECT",
            "official_endpoint": OFFICIAL_BASE,
            "war_id": (official.get("source") or {}).get("war_id"),
            "community_latency_ms": source.get("latency_ms", 0),
            "latency_ms": (official.get("source") or {}).get("latency_ms", 0),
        })
        merged["source"] = source
        return merged

    def _finish(self, snapshot: dict) -> None:
        snapshot["ok"] = True
        snapshot["cached"] = False
        snapshot["source"]["last_success"] = iso(now_utc())
        self._save(snapshot)
        with self._lock:
            self.snapshot = snapshot

    def _fetch_community(self) -> dict:
        assignments = http_json(f"{COMMUNITY_BASE}/assignments", timeout=6.0)
        if not isinstance(assignments, list):
            raise SourceError("assignments payload is not a list")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                "planets": pool.submit(http_json, f"{COMMUNITY_BASE}/planets", 12.0),
                "war": pool.submit(http_json, f"{COMMUNITY_BASE}/war", 12.0),
                "dispatches": pool.submit(http_json, f"{COMMUNITY_BASE}/dispatches", 12.0),
            }
            results = {}
            for key, future in futures.items():
                results[key] = future.result()
        planets = results["planets"]
        war = results["war"]
        dispatches = results["dispatches"]
        if not isinstance(planets, list) or not isinstance(war, dict):
            raise SourceError("invalid community payload")
        return normalize_community(assignments, planets, war, dispatches if isinstance(dispatches, list) else [], COMMUNITY_BASE, 0)

    def _fetch_official(self) -> dict:
        war_id_payload = http_json(f"{OFFICIAL_BASE}/WarSeason/current/WarID", timeout=10.0)
        war_id = int((war_id_payload or {}).get("id") or 801)
        jobs = {
            "status": (f"{OFFICIAL_BASE}/WarSeason/{war_id}/Status", 15.0),
            "warinfo": (f"{OFFICIAL_BASE}/WarSeason/{war_id}/WarInfo", 15.0),
            "summary": (f"{OFFICIAL_BASE}/Stats/war/{war_id}/summary", 25.0),
            "news": (f"{OFFICIAL_BASE}/NewsFeed/{war_id}", 15.0),
            "assignments": (f"{OFFICIAL_BASE}/v2/Assignment/War/{war_id}", 15.0),
        }
        results: dict[str, Any] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {key: pool.submit(http_json, url, timeout) for key, (url, timeout) in jobs.items()}
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("Official endpoint %s failed: %s", key, exc)
                    results[key] = [] if key in ("news", "assignments") else {}
        status = results.get("status") or {}
        warinfo = results.get("warinfo") or {}
        if not status.get("planetStatus") or not warinfo.get("planetInfos"):
            raise SourceError("official status/warinfo unavailable")
        news = list(results.get("news") or [])
        # ArrowHead's NewsFeed can lag behind the current high-command dispatches.
        # The Companion relay exposes the complete feed; merge the newer records in.
        try:
            companion = http_json(COMPANION_URL, timeout=8.0)
            extra = companion.get("news") or [] if isinstance(companion, dict) else []
            known = {item.get("id") for item in news if isinstance(item, dict)}
            for item in extra:
                if isinstance(item, dict) and item.get("id") not in known:
                    news.append(item)
                    known.add(item.get("id"))
        except Exception as exc:  # noqa: BLE001
            LOG.info("Companion news supplement unavailable: %s", exc)
        return normalize_arrowhead(
            status,
            warinfo,
            results.get("summary") or {},
            news,
            results.get("assignments") or [],
            "ARROWHEAD DIRECT",
            OFFICIAL_BASE,
            0,
            status.get("spaceStations") or [],
            war_id,
        )

    def _fetch_companion(self) -> dict:
        payload = http_json(COMPANION_URL, timeout=20.0)
        if not isinstance(payload, dict):
            raise SourceError("invalid companion payload")
        status = payload.get("warStatus") or {}
        warinfo = payload.get("warInfo") or {}
        if not status.get("planetStatus") or not warinfo.get("planetInfos"):
            raise SourceError("companion status/warinfo unavailable")
        return normalize_arrowhead(
            status,
            warinfo,
            payload.get("warStats") or {},
            payload.get("news") or [],
            payload.get("majorOrders") or [],
            "COMPANION RELAY",
            COMPANION_URL,
            0,
            payload.get("spaceStations") or status.get("spaceStations") or [],
            payload.get("warId") or status.get("warId"),
        )
