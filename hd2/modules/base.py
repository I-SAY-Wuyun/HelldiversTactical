"""Contract shared by every game module.

A module is the *only* thing the shell knows about a game: it can bootstrap,
refresh its own data and describe itself as one widget card.  Implementations
stay free to keep their existing service classes; the module is an adapter, not
a rewrite (see `hd2_mod.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class GameInfo:
    """One entry of `data/games.json`."""

    id: str
    name: str
    cn: str
    kinds: tuple[str, ...] = ()
    enabled: bool = False
    accent: str = ""
    module: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cn": self.cn,
            "kinds": list(self.kinds),
            "enabled": self.enabled,
            "accent": self.accent,
            "module": self.module,
        }


@dataclass(frozen=True)
class WidgetCard:
    """What the shell needs to draw a module: no game-specific fields."""

    module_id: str
    title: str
    subtitle: str = ""
    status: str = ""
    metrics: tuple[tuple[str, str], ...] = ()
    updated_utc: str | None = None

    def as_dict(self) -> dict:
        return {
            "module": self.module_id,
            "title": self.title,
            "subtitle": self.subtitle,
            "status": self.status,
            "metrics": [{"label": label, "value": value} for label, value in self.metrics],
            "updated_utc": self.updated_utc,
        }


@runtime_checkable
class GameModule(Protocol):
    """Everything the scheduler / bridge may call on a module."""

    info: GameInfo

    def bootstrap(self) -> None: ...

    def refresh(self) -> None: ...

    def widget_card(self) -> WidgetCard | None: ...


class BaseModule:
    """No-op base class; subclasses override what they need."""

    def __init__(self, info: GameInfo) -> None:
        self.info = info

    @property
    def id(self) -> str:
        return self.info.id

    def bootstrap(self) -> None:
        return None

    def refresh(self) -> None:
        return None

    def widget_card(self) -> WidgetCard | None:
        return None
