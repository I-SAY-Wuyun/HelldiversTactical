"""Small Windows integration helpers (taskbar flash, DPI, single instance)."""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

LOG = logging.getLogger(__name__)

# Every native entry point below is bound through a *private* WinDLL instance.
#
# Declaring argtypes on `ctypes.windll.user32` mutates a process-wide function
# object that other libraries also use, and that is exactly what broke window
# dragging: pywebview's winforms backend moves the window with
#
#     windll.user32.SetWindowPos(hwnd, None, x, y, None, None, flags)
#
# i.e. it passes None for cx/cy (ignored because of SWP_NOSIZE). With an
# argtypes declaration in place ctypes rejects those Nones with
# "ArgumentError: argument 5: TypeError: wrong type", the exception is swallowed
# by pywebview's JS bridge, and every drag move becomes a silent no-op.
if sys.platform == "win32":
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)
    _DWMAPI = ctypes.WinDLL("dwmapi", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - non-Windows hosts
    _USER32 = None
    _SHELL32 = None
    _DWMAPI = None
    _KERNEL32 = None

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_TYPES_READY = False


def _declare_types() -> None:
    """Give the private user32/shell32/dwmapi bindings real prototypes."""
    global _TYPES_READY
    if _TYPES_READY or _USER32 is None:
        return
    _USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _USER32.FindWindowW.restype = wintypes.HWND
    _USER32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    _USER32.EnumWindows.restype = wintypes.BOOL
    _USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _USER32.GetWindowTextLengthW.restype = ctypes.c_int
    _USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _USER32.GetWindowTextW.restype = ctypes.c_int
    _USER32.IsWindowVisible.argtypes = [wintypes.HWND]
    _USER32.IsWindowVisible.restype = wintypes.BOOL
    _USER32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _USER32.GetWindowLongW.restype = ctypes.c_long
    _USER32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _USER32.SetWindowLongW.restype = ctypes.c_long
    _USER32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _USER32.SetWindowPos.restype = wintypes.BOOL
    _USER32.MoveWindow.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.BOOL,
    ]
    _USER32.MoveWindow.restype = wintypes.BOOL
    _USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _USER32.ShowWindow.restype = wintypes.BOOL
    _USER32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.BYTE,
        wintypes.DWORD,
    ]
    _USER32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    _USER32.ReleaseCapture.argtypes = []
    _USER32.ReleaseCapture.restype = wintypes.BOOL
    _USER32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _USER32.SendMessageW.restype = ctypes.c_ssize_t  # LRESULT (LONG_PTR)
    _USER32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    _USER32.FlashWindowEx.restype = wintypes.BOOL
    _USER32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    _USER32.SystemParametersInfoW.restype = wintypes.BOOL
    _USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _USER32.GetSystemMetrics.restype = ctypes.c_int
    _USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    _USER32.GetWindowRect.restype = wintypes.BOOL
    _USER32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _USER32.GetWindow.restype = wintypes.HWND
    _USER32.GetForegroundWindow.argtypes = []
    _USER32.GetForegroundWindow.restype = wintypes.HWND
    _USER32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _USER32.GetClassNameW.restype = ctypes.c_int
    _USER32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _USER32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    _KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _KERNEL32.CreateMutexW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL
    _SHELL32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    _SHELL32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    _DWMAPI.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _DWMAPI.DwmSetWindowAttribute.restype = ctypes.c_long
    _TYPES_READY = True


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def find_window_handle(title: str) -> int:
    """Locate a top-level window by exact title.

    FindWindowW only matches top-level windows, so the WebView2 child windows
    (same title, different owner) are never returned.
    """
    if _USER32 is None:
        return 0
    try:
        _declare_types()
        hwnd = _USER32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
        # Fallback: the title may not be set yet while the form is
        # still being constructed; scan visible top-level windows.
        match: list[int] = []

        def _callback(hwnd_, _lparam: int) -> bool:
            length = int(_USER32.GetWindowTextLengthW(hwnd_))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            _USER32.GetWindowTextW(hwnd_, buffer, length + 1)
            if buffer.value == title and _USER32.IsWindowVisible(hwnd_):
                match.append(int(hwnd_))
                return False
            return True

        _USER32.EnumWindows(WNDENUMPROC(_callback), 0)
        return match[0] if match else 0
    except Exception as exc:  # noqa: BLE001
        LOG.debug("find_window_handle failed: %s", exc)
        return 0


def set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def set_app_id(app_id: str) -> None:
    if _SHELL32 is None:
        return
    try:
        _declare_types()
        _SHELL32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def work_area() -> tuple[int, int, int, int]:
    """Return the primary monitor work area (excluding the taskbar)."""
    if _USER32 is None:
        return 0, 0, 1920, 1080
    try:
        _declare_types()
        rect = RECT()
        if _USER32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    try:
        return 0, 0, _USER32.GetSystemMetrics(0), _USER32.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001
        return 0, 0, 1920, 1080


def get_window_rect(title: str) -> tuple[int, int, int, int] | None:
    """Return the window rect as (left, top, right, bottom), or None."""
    if _USER32 is None:
        return None
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return None
        rect = RECT()
        if not _USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception as exc:  # noqa: BLE001
        LOG.debug("get_window_rect failed: %s", exc)
        return None


def set_window_pos(hwnd: int, insert_after: int, flags: int) -> bool:
    """SetWindowPos wrapper (size/position untouched, caller passes the flags)."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        return bool(
            _USER32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(insert_after), 0, 0, 0, 0, flags)
        )
    except Exception as exc:  # noqa: BLE001
        LOG.debug("set_window_pos failed: %s", exc)
        return False


def set_window_geometry(
    title: str, width: int, height: int, x: int, y: int, topmost: bool | None = None
) -> bool:
    """Position a native window without going through the WinForms thread proxy."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        _USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        moved = _USER32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        # Geometry only, no z-order change: the topmost state is owned by
        # set_window_topmost(), and HWND_NOTOPMOST here would additionally
        # drop the widget to the bottom of the z-order.
        set_window_pos(hwnd, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0040)
        if topmost is not None:
            set_window_topmost(title, bool(topmost))
        return bool(moved)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("set_window_geometry failed: %s", exc)
        return False


def _show_state(hwnd: int, cmd: int) -> bool:
    """Apply SW_* state without touching z-order or focus."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        _USER32.ShowWindow(hwnd, cmd)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("ShowWindow(%s) failed: %s", cmd, exc)
        return False


def hide_window(title: str) -> bool:
    """Hide the widget so only the tray icon remains (process keeps running)."""
    hwnd = find_window_handle(title)
    return bool(hwnd) and _show_state(hwnd, 0)  # SW_HIDE


def show_window(title: str) -> bool:
    """Make a hidden widget visible without activating it or re-stacking it."""
    hwnd = find_window_handle(title)
    if not hwnd:
        return False
    # SW_SHOWNOACTIVATE keeps focus with the previously active window; a
    # plain SW_SHOW/SW_RESTORE would activate and re-insert the widget into
    # the z-order, which defeats an explicitly disabled "always on top".
    ok = _show_state(hwnd, 4)  # SW_SHOWNOACTIVATE
    set_window_pos(hwnd, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010)  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE
    return ok


def toggle_window_visibility(title: str) -> bool:
    """Show the widget if hidden, hide it if visible. Returns the new state."""
    hwnd = find_window_handle(title)
    if not hwnd:
        return False
    _declare_types()
    if _USER32.IsWindowVisible(hwnd):
        hide_window(title)
        return False
    show_window(title)
    return True


def set_window_opacity(title: str, percent: int) -> bool:
    """Apply whole-window alpha (WS_EX_LAYERED + LWA_ALPHA), 35-100 percent."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_ALPHA = 0x00000002
        alpha = max(0, min(255, round(int(percent) / 100 * 255)))
        ex_style = _USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _USER32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED)
        ok = _USER32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE: repaint without re-stacking,
        # so dragging the opacity slider never flips the window back on top.
        set_window_pos(hwnd, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("set_window_opacity failed: %s", exc)
        return False


# Z-order insert-after handles (winuser.h).
HWND_TOP = 0
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

ZORDER_VALUES = ("bottom", "normal", "top")

# Only these windows may sit below a "bottom" widget; anything else means an
# application window is underneath us, i.e. we are not pinned to the desktop.
_DESKTOP_CLASSES = ("Progman", "WorkerW")



def _window_class(hwnd: int) -> str:
    """Read a window class name (used to recognise the shell desktop)."""
    try:
        _declare_types()
        buffer = ctypes.create_unicode_buffer(64)
        if not _USER32.GetClassNameW(hwnd, buffer, 64):
            return ""
        return buffer.value
    except Exception:  # noqa: BLE001
        return ""


def _is_pinned_to_desktop(hwnd: int) -> bool:
    """True when no visible application window sits below hwnd in the z-order."""
    try:
        _declare_types()
        current = _USER32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: the window below us
        while current:
            if _USER32.IsWindowVisible(current):
                return _window_class(current) in _DESKTOP_CLASSES
            current = _USER32.GetWindow(current, 2)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_window_zorder(title: str, zorder: str) -> bool:
    """Pin the window to the top, to the normal band, or to the desktop.

    ``zorder`` is one of ``"top"`` / ``"normal"`` / ``"bottom"``.  The topmost
    bit is always cleared first: Windows keeps a topmost window inside the
    topmost band even when it is sent to HWND_BOTTOM, so "bottom" would
    silently stay on top otherwise.
    """
    if _USER32 is None:
        return False
    position = str(zorder or "").strip().lower()
    if position not in ZORDER_VALUES:
        position = "top"
    try:
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        flags = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
        ok = set_window_pos(hwnd, HWND_NOTOPMOST, flags)
        if position == "top":
            ok = set_window_pos(hwnd, HWND_TOPMOST, flags) and ok
        elif position == "bottom":
            ok = set_window_pos(hwnd, HWND_BOTTOM, flags) and ok
        elif _is_pinned_to_desktop(hwnd):
            # Leaving "bottom": HWND_NOTOPMOST is a no-op on an already
            # non-topmost window, so the widget would stay buried under every
            # application window.  HWND_TOP alone does not move it either, so
            # bounce through the topmost band and drop straight back out: the
            # window ends up at the top of the normal band with the bit clear.
            ok = set_window_pos(hwnd, HWND_TOPMOST, flags) and ok
            ok = set_window_pos(hwnd, HWND_NOTOPMOST, flags) and ok
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("set_window_zorder(%s) failed: %s", position, exc)
        return False


def set_window_topmost(title: str, topmost: bool) -> bool:
    """Boolean compatibility wrapper around :func:`set_window_zorder`."""
    return set_window_zorder(title, "top" if topmost else "normal")


def get_window_zorder(title: str) -> str:
    """Report the live z-order state (diagnostics / tests)."""
    if _USER32 is None:
        return "normal"
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return "normal"
        if _USER32.GetWindowLongW(hwnd, -20) & 0x00000008:  # WS_EX_TOPMOST
            return "top"
        return "bottom" if _is_pinned_to_desktop(hwnd) else "normal"
    except Exception:  # noqa: BLE001
        return "normal"


class ZOrderKeeper:
    """Re-assert a "bottom" window a few times per second.

    Windows has no persistent always-at-the-bottom bit: HWND_BOTTOM is a
    one-shot z-order move, and the next newly shown window re-stacks us above
    other applications.  This watchdog re-sends the demotion while the widget
    is in bottom mode.

    A click still activates the window, and the shell raises whatever it
    activates -- there is no window style that prevents this when applied
    after creation (Windows only honours WS_EX_NOACTIVATE on windows created
    with it).  So the keeper pulls the window back down even while it is the
    foreground window; the demotion lasts, because the activation is left
    alone and only the z-order moves.
    """

    def __init__(self, title: str, interval: float = 0.5) -> None:
        self.title = title
        self.interval = max(0.2, float(interval))
        self._position = "normal"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    @property
    def position(self) -> str:
        return self._position

    def set_position(self, zorder: str) -> None:
        self._position = zorder if zorder in ZORDER_VALUES else "normal"

    def start(self) -> None:
        if _USER32 is None:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hd2-zorder", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=1.5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                LOG.debug("ZOrderKeeper tick failed: %s", exc)

    def tick(self) -> None:
        """One enforcement pass; a no-op unless the position is "bottom"."""
        if self._position != "bottom" or _USER32 is None:
            return
        try:
            _declare_types()
            hwnd = find_window_handle(self.title)
            if not hwnd or not _USER32.IsWindowVisible(hwnd):
                return
            if _is_pinned_to_desktop(hwnd):
                return
            set_window_zorder(self.title, "bottom")
        except Exception as exc:  # noqa: BLE001
            LOG.debug("ZOrderKeeper.tick failed: %s", exc)


def hide_from_taskbar(title: str) -> bool:
    """Turn the native window into a tool window (no taskbar / Alt-Tab entry)."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        style = _USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        _USER32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        # NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED
        set_window_pos(hwnd, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("hide_from_taskbar failed: %s", exc)
        return False


def start_window_drag(title: str) -> bool:
    """Begin a native window move from a JS drag handle."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        _USER32.ReleaseCapture()
        # WM_NCLBUTTONDOWN, HTCAPTION
        _USER32.SendMessageW(hwnd, 0x00A1, 2, 0)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("start_window_drag failed: %s", exc)
        return False


def flash_window(title: str, count: int = 5) -> bool:
    """Flash the taskbar button without playing a sound or stealing focus."""
    if _USER32 is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        FLASHW_ALL = 0x00000003
        FLASHW_TIMERNOFG = 0x0000000C
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, count, 0)
        _USER32.FlashWindowEx(ctypes.byref(info))
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("FlashWindowEx failed: %s", exc)
        return False


def set_dark_titlebar(title: str) -> bool:
    """Apply the Windows 11 dark caption to the native pywebview frame."""
    if _DWMAPI is None:
        return False
    try:
        _declare_types()
        hwnd = find_window_handle(title)
        if not hwnd:
            return False
        value = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on current Windows 11, 19 on older builds)
        for attribute in (20, 19):
            result = _DWMAPI.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            if result == 0:
                break
        # Optional Windows 11 caption colors (COLORREF is 0x00BBGGRR).
        try:
            caption = ctypes.c_int(0x00060908)  # #080906
            text = ctypes.c_int(0x00D1E6E9)  # #E9E6D1
            _DWMAPI.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
            _DWMAPI.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        except Exception:
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Dark titlebar failed: %s", exc)
        return False


class SingleInstance:
    """Named-mutex guard that keeps a second copy of the app from starting."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        if _KERNEL32 is None:
            return True
        try:
            _declare_types()
            self.handle = _KERNEL32.CreateMutexW(None, False, self.name)
            error = ctypes.get_last_error()
            # ERROR_ALREADY_EXISTS: the mutex belonged to another process.
            return error != 183
        except Exception:  # noqa: BLE001
            return True

    def release(self) -> None:
        if self.handle:
            try:
                _KERNEL32.CloseHandle(self.handle)
            except Exception:  # noqa: BLE001
                pass
            self.handle = None

    @staticmethod
    def notify_running(title: str, text: str) -> None:
        if sys.platform == "win32":
            try:
                ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
            except Exception:  # noqa: BLE001
                pass
