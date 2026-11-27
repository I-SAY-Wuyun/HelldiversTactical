"""Game modules: registry + per-game adapters."""
from .base import BaseModule, GameInfo, GameModule, WidgetCard
from .registry import ModuleRegistry, load_registry, parse_games

__all__ = [
    "BaseModule",
    "GameInfo",
    "GameModule",
    "WidgetCard",
    "ModuleRegistry",
    "load_registry",
    "parse_games",
]
