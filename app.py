"""Helldivers Tactical Command - desktop entry point."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import webview

from hd2 import autostart, store
from hd2.alerts import AlertEngine
from hd2.api import LiveDataService, SourceError, _empty_snapshot
from hd2.config import (
    APP_ID,
    APP_NAME,
    LOG_DIR,
    RESOURCE_DIR,
    USER_DIR,
    VERSION,
    WEB_DIR,
    WINDOW_TITLE,
    load_settings,
    normalize_zorder,
    save_settings,
)
from hd2.modules import ModuleRegistry, load_registry
from hd2.scheduler import Scheduler
from hd2.util import iso, now_utc
from hd2.winflash import (
    SingleInstance,
    ZOrderKeeper,
    flash_window,
    get_window_rect,
    hide_from_taskbar,
    hide_window,
    set_app_id,
    set_dpi_awareness,
    set_window_geometry,
    set_window_opacity,
    set_window_zorder,
    start_window_drag,
    work_area,
)

try:
    import pystray
    from PIL import Image as PILImage
except Exception:  # pragma: no cover - tray is optional
    pystray = None
    PILImage = None

LOG = logging.getLogger("hd2")
MUTEX_NAME = "Local\\HD2TacticalCommand-v1-8D4B21"
WIDGET_W, WIDGET_H = 380, 160
WIDGET_MIN_W, WIDGET_MIN_H = 340, 140  # keep in sync with webview min_size
EXPANDED_MAX_W, EXPANDED_MAX_H = 1440, 900


def setup_logging() -> None:
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=1_500_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    LOG.addHandler(handler)
    if sys.stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        LOG.addHandler(stream)


class AppController:
    def __init__(self) -> None:
        self.service = LiveDataService()
        self.alerts = AlertEngine()
        self.settings = load_settings()
        self.window = None
        self._stop = threading.Event()
        self._refresh_lock = threading.Lock()
        self.snapshot = self.service.load_cached() or _empty_snapshot()
        self.last_error = ""
        self.last_attempt = 0.0
        self.mode = "widget"
        # Size/position the small window had before it was expanded, so MINI
        # can put it back where the user left it instead of the default corner.
        self._widget_geometry: tuple[int, int, int, int] | None = None
        self.quitting = False
        self.tray = None
        self._tray_hint_shown = False
        # --- M0 foundations -------------------------------------------------
        # A durable store, one scheduler for all periodic work and the game
        # registry.  None of them may stop the widget from starting: a broken
        # database or registry degrades to v1 behaviour and is reported in
        # bootstrap() instead.
        self.store_error = ""
        try:
            store.connect()
        except Exception as exc:  # noqa: BLE001
            self.store_error = f"{type(exc).__name__}: {exc}"
            LOG.warning("Storage unavailable: %s", exc)
        self.features = dict(self.settings.get("features") or {})
        self.registry = load_registry({"service": self.service})
        self.scheduler = Scheduler(tick=1.0)
        # Re-demotes the window while "bottom" is selected: HWND_BOTTOM is a
        # one-shot move and any activation would otherwise raise the widget
        # back above normal windows.
        self._zorder_keeper = ZOrderKeeper(WINDOW_TITLE, interval=0.2)

    def bind(self, window) -> None:
        self.window = window

    def on_closing(self) -> bool:
        if self.quitting:
            return True
        try:
            hide_window(WINDOW_TITLE)
        except Exception:
            pass
        return False

    def on_closed(self) -> None:
        self.stop()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass

    def _remember_widget_geometry(self) -> None:
        """Snapshot the small window's rect while it is still widget-sized.

        Called only on the widget -> expanded edge: once the window has grown,
        its rect says nothing about where the user had dragged the widget.
        """
        rect = get_window_rect(WINDOW_TITLE)
        if not rect:
            return
        rect_left, rect_top, rect_right, rect_bottom = rect
        width, height = rect_right - rect_left, rect_bottom - rect_top
        if width < WIDGET_MIN_W or height < WIDGET_MIN_H:
            return
        self._widget_geometry = (width, height, rect_left, rect_top)

    def _widget_target(self, left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
        """Return (width, height, x, y) for the small window.

        Restores the remembered rect when there is one (clamped into the
        current work area, in case the display layout changed) and otherwise
        falls back to the default bottom-right corner.
        """
        work_w, work_h = max(1, right - left), max(1, bottom - top)
        saved = self._widget_geometry
        if saved:
            width = max(WIDGET_MIN_W, min(saved[0], work_w))
            height = max(WIDGET_MIN_H, min(saved[1], work_h))
            x = min(max(saved[2], left), max(left, right - width))
            y = min(max(saved[3], top), max(top, bottom - height))
            return width, height, x, y
        return (
            WIDGET_W,
            WIDGET_H,
            max(left, right - WIDGET_W - 16),
            max(top, bottom - WIDGET_H - 16),
        )

    def set_mode(self, mode: str, notify_js: bool = True) -> dict:
        mode = "expanded" if mode == "expanded" else "widget"
        previous = self.mode
        if not self.window:
            self.mode = mode
            return {"mode": mode}
        if mode == "expanded" and previous == "widget":
            self._remember_widget_geometry()
        self.mode = mode
        left, top, right, bottom = work_area()
        work_w, work_h = max(640, right - left), max(480, bottom - top)
        # Geometry only: the topmost style is applied by apply_visual_settings()
        # so a resize can never re-pin a window whose "always on top" is off.
        if mode == "widget":
            width, height, x, y = self._widget_target(left, top, right, bottom)
            set_window_geometry(WINDOW_TITLE, width, height, x, y)
        else:
            width = min(EXPANDED_MAX_W, max(1000, work_w - 80))
            height = min(EXPANDED_MAX_H, max(680, work_h - 80))
            x = left + max(0, (work_w - width) // 2)
            y = top + max(0, (work_h - height) // 2)
            set_window_geometry(WINDOW_TITLE, width, height, x, y)
        hide_from_taskbar(WINDOW_TITLE)
        self.apply_visual_settings()
        LOG.info("Window mode=%s", mode)
        return {"mode": mode}

    def apply_visual_settings(self) -> None:
        if not self.window:
            return
        zorder = normalize_zorder(self.settings.get("window_zorder"), self.settings)
        # Native first: it is idempotent and synchronous, so switching the
        # layer actually takes effect even if the WinForms proxy below is
        # unavailable or raises.
        if not set_window_zorder(WINDOW_TITLE, zorder):
            LOG.warning("Native z-order change failed (zorder=%s)", zorder)
        set_window_opacity(WINDOW_TITLE, int(self.settings.get("window_opacity", 100)))
        # Also drive pywebview's own TopMost property so its WinForms form
        # state agrees with the native style; otherwise the next form-level
        # restack (Show/Restore/Activate) would put the window back on top.
        try:
            self.window.on_top = zorder == "top"
        except Exception as exc:  # noqa: BLE001
            LOG.debug("pywebview on_top update failed: %s", exc)
        # "bottom" is not a persistent Win32 style, so keep re-asserting it.
        self._zorder_keeper.set_position(zorder)

    def hide_to_tray(self) -> dict:
        try:
            hide_window(WINDOW_TITLE)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        # First hide per session: say where the window went.  Without this the
        # frameless, taskbar-less window vanishes with no way back.
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                if self.tray:
                    self.tray.notify(
                        "已收起到系统托盘。单击托盘图标即可重新打开",
                        "HELLDIVERS 2 // TACTICAL COMMAND",
                    )
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True}

    def quit(self) -> dict:
        self.quitting = True
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        try:
            if self.window:
                self.window.destroy()
        except Exception:
            pass
        return {"ok": True}

    def start(self) -> None:
        # "war" is the only periodic fetch in M0.  Event providers get their own
        # jobs when those features land; note reminders are disabled while
        # features.notes is off.
        self.scheduler.add(
            "war",
            interval=lambda: max(30, min(300, int(self.settings.get("poll_seconds") or 60))),
            fn=self.refresh_once,
            first_delay=1.5,  # let the webview register its JS bridge first
        )
        self.registry.bootstrap()
        self.scheduler.start()
        self._zorder_keeper.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._zorder_keeper.stop()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Z-order keeper stop failed: %s", exc)
        try:
            self.scheduler.stop()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Scheduler stop failed: %s", exc)

    def refresh_once(self) -> dict:
        if not self._refresh_lock.acquire(blocking=False):
            return self.snapshot
        try:
            self.last_attempt = time.time()
            try:
                snapshot = self.service.fetch()
                self.last_error = ""
            except SourceError as exc:
                self.last_error = str(exc)
                LOG.warning("All live sources unavailable: %s", exc)
                snapshot = self.service.load_cached() or self.snapshot
                snapshot = dict(snapshot)
                snapshot["source"] = dict(snapshot.get("source") or {})
                snapshot["source"]["status"] = "OFFLINE"
                snapshot["source"]["error"] = self.last_error
            self.snapshot = snapshot
            for module in self.registry.enabled():
                observe = getattr(module, "observe", None)
                if not callable(observe):
                    continue
                try:
                    observe(snapshot)
                except Exception as exc:  # noqa: BLE001 - a module must not break the war job
                    LOG.debug("Module %r observe failed: %s", module.info.id, exc)
            new_alerts: list[dict] = []
            if self.settings.get("alerts_enabled", True):
                new_alerts = self.alerts.evaluate(snapshot)
            self._push({
                "type": "state",
                "state": snapshot,
                "history": self.alerts.history(),
                "error": self.last_error,
            })
            if new_alerts:
                if self.settings.get("alert_focus"):
                    try:
                        if self.window:
                            self.window.restore()
                    except Exception:
                        pass
                flash_window(WINDOW_TITLE, 6)
                self._push({"type": "alerts", "alerts": new_alerts, "history": self.alerts.history()})
            return snapshot
        finally:
            self._refresh_lock.release()

    def _push(self, payload: dict) -> None:
        if not self.window:
            return
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
            self.window.evaluate_js(f"window.HD2 && window.HD2.receive({encoded});")
        except Exception as exc:  # noqa: BLE001
            LOG.debug("JS push failed: %s", exc)

    def bootstrap(self) -> dict:
        return {
            "state": self.snapshot,
            "settings": self.settings,
            "history": self.alerts.history(),
            "autostart": autostart.is_enabled(),
            "version": VERSION,
            "mode": self.mode,
            "error": self.last_error,
            "features": self.features,
            "modules": self.registry.describe(),
            "scheduler": self.scheduler.snapshot(),
            "paths": {"data": str(USER_DIR), "db": str(store.db_path())},
        }


class TrayController:
    """Small system-tray icon; lives in the hidden-icons overflow."""

    def __init__(self, controller: AppController) -> None:
        self.controller = controller
        self.icon = None
        self._thread = None

    def start(self) -> None:
        if pystray is None or PILImage is None:
            LOG.info("pystray/Pillow unavailable; tray disabled")
            return
        if self.icon:
            return
        try:
            icon_path = RESOURCE_DIR / "assets" / "icon.png"
            image = PILImage.open(icon_path).convert("RGBA")
        except Exception:
            image = PILImage.new("RGBA", (64, 64), (255, 232, 26, 255))
        menu = pystray.Menu(
            pystray.MenuItem("打开指挥台", lambda: self._dispatch("expand"), default=True),
            pystray.MenuItem("收起到桌面挂件", lambda: self._dispatch("collapse")),
            pystray.MenuItem("立即刷新", lambda: self._dispatch("refresh")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "窗口层级",
                pystray.Menu(
                    pystray.MenuItem(
                        "置底（钉在桌面）",
                        lambda: self._dispatch("zorder:bottom"),
                        checked=lambda item: self._zone() == "bottom",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "通常",
                        lambda: self._dispatch("zorder:normal"),
                        checked=lambda item: self._zone() == "normal",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "置顶",
                        lambda: self._dispatch("zorder:top"),
                        checked=lambda item: self._zone() == "top",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: self._dispatch("quit")),
        )
        self.icon = pystray.Icon("HelldiversTactical", image, "HELLDIVERS 2 // TACTICAL COMMAND", menu)
        self._thread = threading.Thread(target=self._run, name="hd2-tray", daemon=True)
        self._thread.start()
        LOG.info("Tray icon started")

    def _run(self) -> None:
        try:
            self.icon.run()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Tray icon stopped: %s", exc)

    def _zone(self) -> str:
        """Current window layer, for the tray radio group."""
        try:
            value = str(self.controller.settings.get("window_zorder") or "").strip().lower()
        except Exception:  # noqa: BLE001
            return "top"
        return value if value in {"bottom", "normal", "top"} else "top"

    def notify(self, message: str, title: str = "HELLDIVERS 2 // TACTICAL COMMAND") -> None:
        """Show a tray balloon; no-op when the tray is unavailable."""
        try:
            if self.icon:
                self.icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Tray notify failed: %s", exc)

    def _dispatch(self, action: str) -> None:
        try:
            if self.controller.window:
                self.controller.window.evaluate_js(f"window.HD2 && window.HD2.trayAction({json.dumps(action)});")
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None


class Bridge:
    def __init__(self, controller: AppController) -> None:
        self._controller = controller

    def bootstrap(self) -> dict:
        return self._controller.bootstrap()

    def get_state(self) -> dict:
        return {
            "state": self._controller.snapshot,
            "history": self._controller.alerts.history(),
            "error": self._controller.last_error,
            "mode": self._controller.mode,
        }

    def expand(self) -> dict:
        return self._controller.set_mode("expanded")

    def collapse(self) -> dict:
        return self._controller.set_mode("widget")

    def hide_to_tray(self) -> dict:
        return self._controller.hide_to_tray()

    def start_drag(self) -> dict:
        return {"ok": start_window_drag(WINDOW_TITLE)}

    def quit(self) -> dict:
        return self._controller.quit()

    def refresh(self) -> dict:
        threading.Thread(target=self._controller.refresh_once, name="hd2-manual-refresh", daemon=True).start()
        return {"started": True}

    def list_modules(self) -> dict:
        """Registry entry point: which games exist, which loaded, current cards."""
        registry: ModuleRegistry = self._controller.registry
        return {
            "modules": registry.describe(),
            "cards": registry.cards(),
            "error": registry.error,
        }

    def scheduler_status(self) -> dict:
        """Diagnostics for the job loop (also surfaced in bootstrap)."""
        return {
            "jobs": self._controller.scheduler.snapshot(),
            "db": str(store.db_path()),
            "storage_error": self._controller.store_error,
        }

    def set_setting(self, key: str, value: Any) -> dict:
        allowed = {
            "alerts_enabled",
            "alert_focus",
            "poll_seconds",
            "reduced_motion",
            "show_source_detail",
            "window_zorder",
            "window_opacity",
        }
        if key == "always_on_top":
            # Legacy boolean API (Bridge.set_topmost): True -> top, False -> normal.
            key = "window_zorder"
            value = "top" if bool(value) else "normal"
        if key not in allowed:
            return {"ok": False, "error": "unknown setting"}
        if key == "window_zorder":
            value = normalize_zorder(value, self._controller.settings)
        elif key == "poll_seconds":
            try:
                value = max(30, min(300, int(value)))
            except (TypeError, ValueError):
                value = 60
        elif key == "window_opacity":
            try:
                value = max(35, min(100, int(value)))
            except (TypeError, ValueError):
                value = 100
        else:
            value = bool(value)
        self._controller.settings[key] = value
        if key == "window_zorder":
            # Keep the legacy boolean in sync for anything that still reads it.
            self._controller.settings["always_on_top"] = value == "top"
        save_settings(self._controller.settings)
        if key in {"window_zorder", "window_opacity"}:
            self._controller.apply_visual_settings()
        return {"ok": True, "settings": self._controller.settings}

    def set_autostart(self, enabled: bool) -> dict:
        try:
            if enabled:
                autostart.enable()
            else:
                autostart.disable()
            self._controller.settings["autostart"] = bool(enabled)
            save_settings(self._controller.settings)
            return {"ok": True, "enabled": autostart.is_enabled()}
        except Exception as exc:  # noqa: BLE001
            LOG.exception("Autostart change failed")
            return {"ok": False, "enabled": autostart.is_enabled(), "error": str(exc)}

    def autostart_status(self) -> dict:
        return {"enabled": autostart.is_enabled(), "target": autostart.launch_target()}

    def create_desktop_shortcut(self) -> dict:
        try:
            return {"ok": True, "path": str(autostart.desktop_shortcut())} if autostart.create_desktop_shortcut() else {"ok": False}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def acknowledge(self, alert_id: str) -> dict:
        return {"ok": self._controller.alerts.acknowledge(str(alert_id))}

    def open_url(self, url: str) -> dict:
        import webbrowser

        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False}

    def minimize(self) -> dict:
        try:
            if self._controller.window:
                self._controller.window.minimize()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def quit(self) -> dict:
        return self._controller.quit()

    def set_topmost(self, value: bool) -> dict:
        """Legacy boolean entry point; prefer set_zorder()."""
        return self.set_setting("always_on_top", value)

    def set_zorder(self, value: str) -> dict:
        """Window layer: "bottom" (desktop), "normal", or "top" (always on top)."""
        return self.set_setting("window_zorder", value)


def _dump_state(path: str) -> int:
    service = LiveDataService()
    try:
        snapshot = service.fetch()
    except Exception as exc:  # noqa: BLE001
        LOG.error("dump failed: %s", exc)
        return 1
    Path(path).write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    LOG.info("Wrote snapshot to %s", path)
    return 0


def _handle_cli() -> int | None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    parser.add_argument("--desktop-shortcut", action="store_true")
    parser.add_argument("--dump-state", metavar="PATH")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        print(f"{APP_NAME} {VERSION}")
        return 0
    if args.dump_state:
        return _dump_state(args.dump_state)
    if args.install_autostart:
        autostart.enable()
        print(f"autostart enabled: {autostart.startup_shortcut()}")
        return 0
    if args.remove_autostart:
        autostart.disable()
        print("autostart disabled")
        return 0
    if args.desktop_shortcut:
        autostart.create_desktop_shortcut()
        print(f"desktop shortcut: {autostart.desktop_shortcut()}")
        return 0
    return None


def main() -> int:
    setup_logging()
    cli_result = _handle_cli()
    if cli_result is not None:
        return cli_result
    set_dpi_awareness()
    set_app_id(APP_ID)
    if os.environ.get("HD2_DEVTOOLS"):
        webview.settings["REMOTE_DEBUGGING_PORT"] = 9223
    instance = SingleInstance(MUTEX_NAME)
    acquired = instance.acquire()
    LOG.info("Single-instance mutex acquired=%s", acquired)
    if not acquired:
        LOG.info("Another instance is already running; exiting")
        SingleInstance.notify_running(WINDOW_TITLE, "绝地潜兵战术指挥终端已在运行。\n请在系统托盘或桌面挂件中打开。")
        return 0
    try:
        controller = AppController()
        bridge = Bridge(controller)
        url = str((WEB_DIR / "index.html").resolve())
        cache_bust = int(time.time())
        left, top, right, bottom = work_area()
        start_x = max(left, right - WIDGET_W - 16)
        start_y = max(top, bottom - WIDGET_H - 16)
        window = webview.create_window(
            WINDOW_TITLE,
            url=f"{url}?mode=widget&v={cache_bust}",
            js_api=bridge,
            width=WIDGET_W,
            height=WIDGET_H,
            x=start_x,
            y=start_y,
            min_size=(340, 140),
            background_color="#080906",
            text_select=True,
            confirm_close=False,
            frameless=True,
            easy_drag=False,
            shadow=True,
            on_top=True,
            zoomable=False,
            resizable=True,
        )
        controller.bind(window)
        controller.tray = TrayController(controller)

        def on_loaded() -> None:
            LOG.info("Webview loaded (widget mode)")
            hide_from_taskbar(WINDOW_TITLE)
            controller.set_mode("widget", notify_js=False)
            controller.tray.start()
            controller.start()

        window.events.loaded += on_loaded
        window.events.closing += controller.on_closing
        window.events.closed += controller.on_closed
        webview.start(
            icon=str(RESOURCE_DIR / "assets" / "icon.ico"),
            debug=False,
            private_mode=False,
            storage_path=str(USER_DIR / "webview"),
            gui="edgechromium",
        )
        controller.stop()
        return 0
    except Exception:  # noqa: BLE001
        LOG.exception("Fatal application error")
        SingleInstance.notify_running(APP_NAME, "应用启动失败，详情见日志：\n" + str(LOG_DIR / "app.log"))
        return 1
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
