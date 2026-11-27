"""Reads `data/games.json` and binds module implementations to entries.

The registry is deliberately forgiving: a broken entry, an unknown module name or
a module that raises during construction must not stop the app.  Every problem is
logged and the entry degrades to "listed but unavailable", which the UI can show.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import DATA_DIR
from .base import BaseModule, GameInfo, GameModule, WidgetCard

LOG = logging.getLogger(__name__)

REGISTRY_PATH = DATA_DIR / "games.json"
#: (game, injected services) -> module instance
ModuleFactory = Callable[[GameInfo, dict[str, Any]], GameModule]


def parse_games(payload: Any, source: str = "games.json") -> list[GameInfo]:
    """Validate and normalise the registry payload; bad entries are skipped."""
    raw_games: Iterable[Any] = ()
    if isinstance(payload, dict):
        raw_games = payload.get("games") or ()
    elif isinstance(payload, list):
        raw_games = payload
    games: list[GameInfo] = []
    seen: set[str] = set()
    for raw in raw_games:
        if not isinstance(raw, dict):
            LOG.warning("%s: skipping non-object entry", source)
            continue
        game_id = str(raw.get("id") or "").strip().lower()
        if not game_id or game_id in seen:
            LOG.warning("%s: skipping entry with missing/duplicate id: %r", source, raw.get("id"))
            continue
        module_id = raw.get("module")
        module_id = str(module_id).strip().lower() if module_id else None
        kinds = raw.get("kind") or raw.get("kinds") or ()
        if isinstance(kinds, str):
            kinds = (kinds,)
        kinds_tuple = tuple(str(kind).strip().lower() for kind in kinds if str(kind).strip())
        extra = {key: value for key, value in raw.items() if key not in {"id", "name", "cn", "kind", "kinds", "enabled", "accent", "module"}}
        seen.add(game_id)
        games.append(
            GameInfo(
                id=game_id,
                name=str(raw.get("name") or game_id).strip(),
                cn=str(raw.get("cn") or raw.get("name") or game_id).strip(),
                kinds=kinds_tuple,
                enabled=bool(raw.get("enabled")),
                accent=str(raw.get("accent") or "").strip(),
                module=module_id,
                extra=extra,
            )
        )
    return games


class ModuleRegistry:
    """Holds the game list plus whatever module implementations loaded."""

    def __init__(self, games: Iterable[GameInfo], modules: dict[str, GameModule] | None = None, error: str = "") -> None:
        self._games = list(games)
        self._modules = dict(modules or {})
        self.error = error

    # -- lookups ---------------------------------------------------------
    def games(self) -> list[GameInfo]:
        return list(self._games)

    def get(self, game_id: str) -> GameModule | None:
        return self._modules.get(str(game_id).lower())

    def enabled(self) -> list[GameModule]:
        return [module for module in self._modules.values() if module.info.enabled]

    def by_kind(self, kind: str) -> list[GameInfo]:
        return [game for game in self._games if str(kind).lower() in game.kinds]

    # -- shell surface ---------------------------------------------------
    def describe(self) -> list[dict]:
        rows = []
        for game in self._games:
            module = self._modules.get(game.id)
            rows.append({**game.as_dict(), "available": module is not None})
        return rows

    def cards(self) -> list[dict]:
        cards: list[dict] = []
        for module in self.enabled():
            try:
                card: WidgetCard | None = module.widget_card()
            except Exception as exc:  # noqa: BLE001 - one bad module must not break the widget
                LOG.warning("Module %r widget_card failed: %s", module.info.id, exc)
                continue
            if card:
                cards.append(card.as_dict())
        return cards

    def bootstrap(self) -> None:
        for module in self.enabled():
            try:
                module.bootstrap()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Module %r bootstrap failed: %s", module.info.id, exc)


def default_factories() -> dict[str, ModuleFactory]:
    from .hd2_mod import Hd2Module

    return {"hd2": lambda info, services: Hd2Module(info, services)}


def load_registry(
    services: dict[str, Any] | None = None,
    path: Path | None = None,
    factories: dict[str, ModuleFactory] | None = None,
) -> ModuleRegistry:
    """Load the registry; never raises - failures come back on `registry.error`."""
    source = Path(path) if path is not None else REGISTRY_PATH
    services = services or {}
    factories = factories if factories is not None else default_factories()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOG.warning("Game registry unavailable (%s): %s", source, exc)
        return ModuleRegistry([], {}, error=f"registry unreadable: {exc}")
    try:
        games = parse_games(payload, source.name)
    except Exception as exc:  # noqa: BLE001 - malformed registry must not be fatal
        LOG.exception("Game registry invalid")
        return ModuleRegistry([], {}, error=f"registry invalid: {exc}")
    modules: dict[str, GameModule] = {}
    for game in games:
        if not game.module:
            continue
        factory = factories.get(game.module)
        if factory is None:
            LOG.info("Game %r wants module %r which is not implemented yet", game.id, game.module)
            continue
        try:
            modules[game.id] = factory(game, services)
        except Exception as exc:  # noqa: BLE001 - one broken module must not stop the app
            LOG.warning("Module %r failed to load: %s", game.module, exc)
            continue
    LOG.info("Game registry: %d game(s), %d module(s) loaded", len(games), len(modules))
    return ModuleRegistry(games, modules)
