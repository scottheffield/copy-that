"""Where windows fit: the work area of a monitor, and sizing a window into it."""
import ctypes
import ctypes.wintypes as wintypes

TITLE_BAR = 40  # logical px Windows adds above a window's client area, with some room
MARGIN = 16     # logical px kept clear of the work area's edges


def work_area(x, y):
    """Work area (left, top, right, bottom) of the monitor containing a point."""
    try:
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # nearest
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def cursor():
    try:
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y
    except Exception:
        return 0, 0


def fit(area, scaling, width, height, min_width, min_height):
    """(width, height, min_width, min_height, x, y) for a window centered in area.

    Sizes are logical px, which CustomTkinter multiplies by scaling; the area
    and x, y are physical px. A window larger than the area shrinks to fit.
    """
    left, top, right, bottom = area
    room_w = int((right - left) / scaling) - 2 * MARGIN
    room_h = int((bottom - top) / scaling) - 2 * MARGIN - TITLE_BAR
    width, height = min(width, room_w), min(height, room_h)
    outer_w, outer_h = width * scaling, (height + TITLE_BAR) * scaling
    x = left + max(0, int((right - left - outer_w) / 2))
    y = top + max(0, int((bottom - top - outer_h) / 2))
    return width, height, min(min_width, width), min(min_height, height), x, y


def place(win, width, height, min_width, min_height):
    """Size a CustomTkinter window to fit the monitor the pointer is on (the tray's), centered."""
    try:
        scaling = win._get_window_scaling()
    except Exception:
        scaling = 1.0
    area = work_area(*cursor()) or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
    width, height, min_width, min_height, x, y = fit(area, scaling, width, height, min_width, min_height)
    win.minsize(min_width, min_height)
    win.geometry(f"{width}x{height}+{x}+{y}")
