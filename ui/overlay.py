"""iPhone Dynamic Island-style floating overlay for dictation state.

Uses PIL 2x supersampled rendering via ui.glass for anti-aliased shapes.
"""

import time
import tkinter as tk
from collections import deque

from ui.glass import (
    TRANSPARENT_COLOR, PillCache, composite_on_transparent, pil_to_photo,
    render_button, render_dot, render_icon_mic, render_icon_stop,
    _hex_to_rgba,
)

def _work_area(x, y):
    """Work area (left, top, right, bottom) of the monitor containing a point."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes

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


# Color used for window transparency (never appears in UI)
_TRANSPARENT = TRANSPARENT_COLOR


class OverlayWindow:
    """Floating pill visible only during a take or a brief result notice.

    During a take it becomes one combined bubble: recording controls on the
    left, live text on the right, opened just below the caret when it is
    known in time and at the pill's spot otherwise.
    """

    # --- Dimensions ---
    COMPACT_W = 150
    COMPACT_H = 38
    HOVER_H = 54
    EXPANDED_W = 300
    RADIUS = 19  # half compact height -> perfect capsule ends
    BTN_CX = 26  # button center x
    BORDER = 2   # border thickness

    # --- iOS-inspired dark palette ---
    BG = "#0A0A0A"
    BORDER_COLOR = "#5E5E63"  # #333333 disappeared on very dark pages
    DIVIDER = "#3A3A3C"
    TEXT = "#FFFFFF"
    TEXT_DIM = "#8E8E93"
    GREEN = "#30D158"
    RED = "#FF453A"
    AMBER = "#FF9F0A"
    GRAY = "#48484A"
    WAVE_COLOR = "#A1A1A6"  # Soft neutral; the stop button alone says "recording"
    LIVE_TEXT = "#C7C7CC"

    # The dot button hit area (left side of the pill)
    _DOT_HIT_X = 38

    # --- Combined bubble ---
    BUBBLE_H = 52           # one-line height; the recording controls stay in this row
    TIMER_X = 46
    BARS_X = 102            # leaves room for a "300.0s" timer
    BARS = 12
    BAR_W, BAR_GAP = 3, 3
    LEFT_W = 213            # recording controls and the X; the divider sits here
    TEXT_MIN_W = 180
    TEXT_MAX_W = 360
    TEXT_PAD_X = 16
    CLOSE_W = 28            # the X, just left of the divider: it never moves as text grows
    CLOSE_SIZE = 5          # half the X's arm length
    TEXT_PAD_Y = 12
    LINE_GAP = 4
    CARET_GAP = 8           # px between the caret line and the bubble
    CARET_WAIT_MS = 250     # the caret usually arrives in under 60ms; never jump mid-take
    BALLOON_FONT_SIZE = 16
    RESULT_DURATION = 6000  # ms an unpasted result stays
    LIVE_CHARS = 140        # latest provisional text shown while recording

    # Gesture hints shown in the bubble while recording.
    HINTS = {
        "held": "Press Space for hands-free",
        "locked": "Hands-free  ·  Ctrl+Win to finish",
        "toggle": "Click the stop button to finish",
    }
    NO_FIELD_HINT = "No text field  ·  Shift+Alt+Z pastes it later"
    RETAINED_HINT = "Click into a text field, then press Shift+Alt+Z"

    def __init__(self, root, on_toggle=None, on_close=None):
        """
        Args:
            root: Parent Tk/CTk window.
            on_toggle: Callback() to toggle recording on dot click.
            on_close: Callback() for the X: cancel the take and free stuck keys.
        """
        self._root = root
        self._on_toggle = on_toggle
        self._on_close = on_close
        self._close_box = None  # (x1, y1, x2, y2) of the X while it shows
        self._float_left = None  # A bubble with no caret keeps this left edge while it widens
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None

        # Animated size (compact pill states)
        self._cur_w = float(self.COMPACT_W)
        self._cur_h = float(self.COMPACT_H)
        self._tgt_w = float(self.COMPACT_W)
        self._tgt_h = float(self.COMPACT_H)

        # Position (None = auto-center, set on first drag)
        self._pos_x: int | None = None
        self._pos_y: int = 10

        # Drag state
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        # State
        self._state = "loading"
        self._hover = False
        self._rec_start = 0.0
        self._rec_hint = ""
        self._no_field = False
        self._anchor = None  # caret (left, top, right, bottom) of the destination field
        self._wave: deque[float] = deque(maxlen=26)
        self._show_balloon = True
        self._at_cursor = True

        # Combined bubble
        self._bubble = False     # the window currently shows the bubble
        self._revealed = False   # placed for this take; it never moves afterwards
        self._bubble_text = ""
        self._bubble_status = "recording"  # recording, processing, retained, notice
        self._notice_label = ""
        self._waiting = False  # The hotkey was pressed while models loaded
        self._text_w = self.TEXT_MIN_W
        self._bar_ids: list = []
        self._timer_id = None
        self._frames: dict = {}  # (w, h, status) -> PhotoImage, for this take

        # Timer IDs
        self._anim_id = None
        self._rec_id = None
        self._auto_hide_id = None
        self._reveal_id = None

        # PIL rendering state
        self._pill_cache = PillCache()
        self._bubble_cache = PillCache()
        self._photo_refs: list = []  # prevent GC of PhotoImages
        self._pre_render_buttons()

        self._build()
        self.hide()

    def _pre_render_buttons(self):
        """Pre-render mic and stop button images."""
        # Green mic button: ring + dark bg + mic icon
        mic_icon = render_icon_mic(12, self.GREEN)
        from ui import theme
        self._mic_button_img = render_button(24, self.GREEN, theme.color(theme.READY_BG), mic_icon)

        # Red stop button: ring + dark bg + stop icon
        stop_icon = render_icon_stop(12, self.RED)
        self._stop_button_img = render_button(26, self.RED, theme.color(theme.STOP_BG), stop_icon)

    def set_theme(self, mode):
        """Recolor in place; preserve take state, geometry, placement and timers."""
        from ui import theme
        for name, pair in {
            "BG": theme.BG, "BORDER_COLOR": theme.PILL_BORDER,
            "DIVIDER": theme.BORDER, "TEXT": theme.TEXT, "TEXT_DIM": theme.MUTED,
            "GREEN": theme.SUCCESS, "RED": theme.ERROR, "AMBER": theme.WARNING,
            "GRAY": theme.MUTED, "WAVE_COLOR": theme.ACCENT, "LIVE_TEXT": theme.TEXT,
        }.items():
            setattr(self, name, theme.color(pair, mode))
        self._pill_cache.invalidate()
        self._bubble_cache.invalidate()
        self._frames.clear()
        self._pre_render_buttons()
        if self._bubble:
            self._layout()
        else:
            self._redraw()

    # --- Window setup -----------------------------------------------------

    def _build(self):
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=_TRANSPARENT)

        try:
            self._win.attributes("-transparentcolor", _TRANSPARENT)
        except Exception:
            pass  # Non-Windows fallback: square corners

        self._canvas = tk.Canvas(
            self._win, bg=_TRANSPARENT, highlightthickness=0,
            width=self.COMPACT_W, height=self.COMPACT_H,
        )
        self._canvas.pack(fill="both", expand=True)

        self._canvas.bind("<Enter>", lambda e: self._set_hover(True))
        self._canvas.bind("<Leave>", self._on_leave)
        self._canvas.bind("<Motion>", self._on_mouse_move)
        self._canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self._canvas.bind("<B1-Motion>", self._on_drag_move)

        self._win.update_idletasks()
        self._position()
        self._apply_window_flags()
        self._redraw()

    def _apply_window_flags(self):
        """Prevent focus stealing and hide from taskbar (Windows)."""
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def _get_screen_width(self) -> int:
        """Get the primary screen width reliably."""
        try:
            import ctypes
            return ctypes.windll.user32.GetSystemMetrics(0)
        except Exception:
            return self._win.winfo_screenwidth()

    def _position(self):
        """Position the window, keeping it centered on its anchor point."""
        w = int(self._cur_w)
        h = int(self._cur_h)
        if self._pos_x is None:
            # Default: center horizontally at top of screen
            x = (self._get_screen_width() - w) // 2
        else:
            # Keep centered on the user's chosen position
            x = self._pos_x - w // 2
        y = self._pos_y
        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._canvas.configure(width=w, height=h)

    # --- PIL-based drawing ------------------------------------------------
    # All elements are composited onto the pill RGBA image first, then
    # the final result is flattened onto TRANSPARENT_COLOR and placed as
    # one single canvas image.  This avoids the transparent-color
    # punch-through that happens when layering separate images.

    @staticmethod
    def _paste_centered(base, overlay, cx, cy):
        """Alpha-composite overlay onto base, centered at (cx, cy)."""
        ow, oh = overlay.size
        x = cx - ow // 2
        y = cy - oh // 2
        base.alpha_composite(overlay, (x, y))

    def _redraw(self):
        """Compact pill states; a take draws the combined bubble instead."""
        if self._bubble:
            return
        c = self._canvas
        c.delete("all")
        self._photo_refs.clear()
        self._close_box = None
        w, h = int(self._cur_w), int(self._cur_h)
        r = min(self.RADIUS, h // 2)
        mid = h // 2

        # Start with glass pill as the base image
        border_rgba = _hex_to_rgba(self.BORDER_COLOR)
        pill = self._pill_cache.get(
            w, h, radius=r, border_color_rgba=border_rgba,
            bg_top=_hex_to_rgba(self.BG)[:3], bg_bottom=_hex_to_rgba(self.BG)[:3],
            border_width=self.BORDER)
        # Work on a copy so the cache stays clean
        frame = pill.copy()

        bx = self.BTN_CX
        text_items = []  # (x, y, text, fill, font, anchor) — drawn after image

        if self._state == "loading":
            dot = render_dot(10, _hex_to_rgba(self.GRAY))
            self._paste_centered(frame, dot, bx, mid)
            text_items.append((w // 2 + 6, mid, "Loading...",
                               self.TEXT_DIM, ("Segoe UI", 10), "center"))

        elif self._state == "ready":
            if self._hover and w > self.COMPACT_W + 20:
                self._paste_centered(frame, self._mic_button_img, bx, mid)
                text_items.append((w // 2 + 10, mid, "Start recording",
                                   self.TEXT_DIM, ("Segoe UI", 10), "center"))
            else:
                dot = render_dot(10, _hex_to_rgba(self.GREEN))
                self._paste_centered(frame, dot, bx, mid)
                text_items.append((w // 2 + 6, mid, "Ready",
                                   self.TEXT, ("Segoe UI", 10, "bold"), "center"))

        elif self._state == "processing":
            dot = render_dot(10, _hex_to_rgba(self.AMBER))
            self._paste_centered(frame, dot, bx, mid)
            text_items.append((w // 2 + 6, mid, "Transcribing...",
                               self.TEXT, ("Segoe UI", 10, "bold"), "center"))

        elif self._state == "error":
            dot = render_dot(10, _hex_to_rgba(self.RED))
            self._paste_centered(frame, dot, bx, mid)
            text_items.append((w // 2 + 6, mid, "Error",
                               self.RED, ("Segoe UI", 10, "bold"), "center"))

        # Flatten to RGB on transparent background and place as one image
        composited = composite_on_transparent(frame)
        photo = pil_to_photo(composited)
        self._photo_refs.append(photo)
        c.create_image(0, 0, image=photo, anchor="nw")

        # Draw text on top (ClearType AA handled by tkinter)
        for tx, ty, text, fill, font, anchor in text_items:
            c.create_text(tx, ty, text=text, fill=fill, font=font,
                          anchor=anchor)
        if self._state == "processing" and w > self.COMPACT_W + 20:
            self._draw_close(w - 24, mid, h)

    # --- Drag to reposition -----------------------------------------------

    def _on_drag_start(self, event):
        """Click on dot -> toggle recording. Click elsewhere -> start drag."""
        self._drag_is_click = True
        if self._on_close_hit(event):
            self._close_clicked()
            return
        if event.x <= self._DOT_HIT_X and self._state in ("ready", "recording"):
            # Clicked the dot button — toggle recording
            if self._on_toggle:
                self._on_toggle()
            return
        if self._state == "result":
            self.show_ready()  # Click dismisses an unpasted result.
            return
        if self._bubble and self._anchor:
            return  # Anchored at the caret; the pill's own spot stays where it was.
        self._drag_is_click = False
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y

    def _on_drag_move(self, event):
        """Move window to follow the mouse."""
        if getattr(self, "_drag_is_click", False):
            return  # Was a button click, not a drag
        x = self._win.winfo_x() + event.x - self._drag_offset_x
        y = self._win.winfo_y() + event.y - self._drag_offset_y
        w = self._win.winfo_width() if self._bubble else int(self._cur_w)
        # Store the center x so resizing stays anchored to the drag position
        self._pos_x = x + w // 2
        self._pos_y = y
        if self._bubble:
            self._float_left = x
        self._win.geometry(f"+{x}+{y}")

    def _on_close_hit(self, event):
        box = self._close_box
        return bool(box) and box[0] <= event.x <= box[2] and box[1] <= event.y <= box[3]

    def _close_clicked(self):
        """Recording or transcribing: cancel, as Escape does. Not pasted: dismiss."""
        dismiss = self._state == "result"
        if self._on_close:
            self._on_close()  # Also frees a modifier Windows still holds, in every state.
        if dismiss:
            self.show_ready()

    def _draw_close(self, cx, cy, h):
        """An X centered at (cx, cy); its hit area spans the full height."""
        c, k = self._canvas, self.CLOSE_SIZE
        for x1, y1, x2, y2 in ((cx - k, cy - k, cx + k, cy + k), (cx - k, cy + k, cx + k, cy - k)):
            c.create_line(x1, y1, x2, y2, fill=self.TEXT_DIM, width=2, capstyle="round")
        half = self.CLOSE_W // 2
        self._close_box = (cx - half, 0, cx + half, h)

    # --- Hover & cursor ---------------------------------------------------

    def _on_leave(self, event):
        self._canvas.configure(cursor="")
        self._set_hover(False)

    def _on_mouse_move(self, event):
        """Show hand cursor when over the dot button area."""
        if (event.x <= self._DOT_HIT_X and self._state in ("ready", "recording")) or self._on_close_hit(event):
            self._canvas.configure(cursor="hand2")
        else:
            self._canvas.configure(cursor="")

    def _set_hover(self, hovered):
        if self._state != "ready":
            return
        self._hover = hovered
        if hovered:
            self._animate(self.EXPANDED_W, self.HOVER_H)
        else:
            self._animate(self.COMPACT_W, self.COMPACT_H)

    # --- Animation --------------------------------------------------------

    def _animate(self, tw, th):
        self._tgt_w = float(tw)
        self._tgt_h = float(th)
        if self._anim_id:
            self._root.after_cancel(self._anim_id)
        self._anim_tick()

    def _anim_tick(self):
        dw = self._tgt_w - self._cur_w
        dh = self._tgt_h - self._cur_h
        if abs(dw) < 1.5 and abs(dh) < 1.5:
            self._cur_w = self._tgt_w
            self._cur_h = self._tgt_h
            self._position()
            self._redraw()
            self._anim_id = None
            return
        self._cur_w += dw * 0.25
        self._cur_h += dh * 0.25
        self._position()
        self._redraw()
        self._anim_id = self._root.after(16, self._anim_tick)

    # --- Public state API -------------------------------------------------

    def _cancel_timers(self, keep_bubble=False):
        for attr in ("_anim_id", "_rec_id", "_auto_hide_id", "_reveal_id"):
            tid = getattr(self, attr, None)
            if tid:
                self._root.after_cancel(tid)
                setattr(self, attr, None)
        if not keep_bubble:
            self._close_bubble()

    def show_loading(self):
        """Gray dot — model loading."""
        self._cancel_timers()
        self._state = "loading"
        self._hover = False
        self._animate(self.COMPACT_W, self.COMPACT_H)
        self._win.withdraw()

    def show_ready(self):
        """Green dot — idle, waiting for hotkey."""
        self._cancel_timers()
        self._state = "ready"
        self._hover = False
        self._win.withdraw()

    def show_recording(self):
        """Open the bubble at the caret if it arrives in time, else at the pill's spot."""
        self._cancel_timers()
        self._win.withdraw()  # A previous take's pill never shows while the caret is found.
        self._state = "recording"
        self._rec_start = time.time()
        self._rec_hint = self.HINTS["held"]
        self._no_field, self._anchor = False, None
        self._wave.clear()
        self._bubble, self._revealed = True, False
        self._bubble_text, self._bubble_status = "", "recording"
        self._text_w = self.TEXT_MIN_W
        self._frames.clear()
        self._reveal_id = self._root.after(self.CARET_WAIT_MS, self._reveal)
        self._tick_recording()

    def set_recording_mode(self, mode: str):
        """Show how to continue or finish the current recording."""
        if self._state == "recording":
            self._rec_hint = self.HINTS.get(mode, "")
            if self._revealed:
                self._layout()

    def set_field(self, editable, caret):
        """Where the text will land: open the bubble at the caret, or warn."""
        if self._state != "recording":
            return
        self._no_field = editable is False
        if self._revealed:
            self._layout()  # A late caret never moves the bubble; the warning still shows.
            return
        if self._at_cursor and caret:
            self._anchor = caret
        self._reveal()

    def set_bubble_position(self, position: str):
        """'cursor' opens takes at the caret when known; 'top' always at the pill's spot."""
        self._at_cursor = position != "top"

    def _reveal(self):
        if self._reveal_id:
            self._root.after_cancel(self._reveal_id)
            self._reveal_id = None
        if self._revealed or self._state != "recording":
            return
        self._revealed = True
        self._layout()
        self._win.deiconify()

    def _tick_recording(self):
        if self._state != "recording":
            return
        if self._revealed:
            self._update_meter()
        self._rec_id = self._root.after(80, self._tick_recording)

    def show_processing(self):
        """Amber dot — transcribing; an open bubble keeps its place and live text."""
        self._cancel_timers(keep_bubble=True)
        self._state = "processing"
        if self._revealed:
            self._bubble_status = "processing"
            self._layout()
            return
        self._close_bubble()
        self._win.deiconify()
        self._animate(self.EXPANDED_W, self.COMPACT_H)

    def show_result(self, text: str, linger: bool = True):
        """Pasted text is visible where it landed; an unpasted result stays in the bubble.

        linger=False closes at once: the text is already in the field.
        """
        self._cancel_timers(keep_bubble=True)
        if not (linger and self._show_balloon and text.strip()):
            self.show_ready()
            return
        self._state = "result"
        if not self._bubble:
            self._anchor = None
            self._text_w = self.TEXT_MIN_W
            self._frames.clear()
        self._bubble = self._revealed = True
        self._bubble_status, self._bubble_text = "retained", " ".join(text.split())
        self._layout()
        self._win.deiconify()
        self._auto_hide_id = self._root.after(self.RESULT_DURATION, self.show_ready)

    def show_notice(self, text: str, label: str, seconds: float = 3.0):
        """A short message at the pill's spot, then back to the quiet state:
        "Still loading" when the hotkey comes too early."""
        loading = self._state == "loading" or (self._state == "notice" and self._waiting)
        self._cancel_timers()
        self._state = "notice"
        self._anchor = None
        self._text_w = self.TEXT_MIN_W
        self._frames.clear()
        self._bubble = self._revealed = True
        self._bubble_status, self._bubble_text, self._notice_label = "notice", text, label
        self._layout()
        self._win.deiconify()
        self._auto_hide_id = self._root.after(int(seconds * 1000), self.show_loading if loading else self.show_ready)

    def note_waiting(self, waiting: bool):
        self._waiting = waiting

    @property
    def waiting(self):
        return self._waiting

    def show_error(self):
        """Red dot — error state."""
        self._cancel_timers()
        self._win.deiconify()
        self._state = "error"
        self._animate(self.COMPACT_W, self.COMPACT_H)
        self._auto_hide_id = self._root.after(2500, self.show_ready)

    def update_audio_level(self, level: float):
        """Feed audio level (0.0-1.0) for waveform visualization."""
        # Light smoothing — responsive to speech but not jittery
        if self._wave:
            prev = self._wave[-1]
            level = prev * 0.2 + level * 0.8
        self._wave.append(level)

    def set_show_balloon(self, enabled: bool):
        """Enable or disable live and unpasted text in the bubble."""
        self._show_balloon = enabled

    def set_balloon_font_size(self, size: int):
        """Set the font size for bubble text."""
        self.BALLOON_FONT_SIZE = max(10, min(size, 32))

    def set_live_text(self, text: str):
        """Provisional words while recording, updated in place in the bubble."""
        text = " ".join(text.split())
        if self._state != "recording" or not self._show_balloon or not text:
            return
        if len(text) > self.LIVE_CHARS:
            # Show the latest words, starting at a word boundary.
            text = "…" + text[-self.LIVE_CHARS:].split(" ", 1)[-1]
        self._bubble_text = text
        if self._revealed:
            self._layout()

    def clear_balloon(self):
        """Close the bubble just before the text is pasted at the caret."""
        self._close_bubble()

    def hide(self):
        """Hide the idle overlay and its transient notices."""
        self.show_ready()

    # --- Combined bubble --------------------------------------------------

    def _close_bubble(self):
        if not self._bubble:
            return
        self._bubble = self._revealed = False
        self._bubble_text = ""
        self._anchor = None
        self._bar_ids, self._timer_id = [], None
        self._close_box = None
        self._float_left = None
        self._canvas.delete("all")
        self._win.withdraw()
        # The compact pill redraws from its own size next time it shows.
        self._cur_w, self._cur_h = float(self.COMPACT_W), float(self.COMPACT_H)

    def _text_lines(self):
        """(text, color, font) for the right side, top to bottom."""
        lines = []
        recording = self._state == "recording"
        if recording and self._no_field:
            lines.append((self.NO_FIELD_HINT, self.AMBER, ("Segoe UI", 9, "bold")))
        if self._bubble_text:
            color = self.TEXT if self._bubble_status in ("retained", "notice") else self.LIVE_TEXT
            lines.append((self._bubble_text, color, ("Segoe UI", self.BALLOON_FONT_SIZE)))
        elif recording and self._show_balloon:
            lines.append(("Listening…", self.TEXT_DIM, ("Segoe UI", self.BALLOON_FONT_SIZE)))
        hint = self._rec_hint if recording else (
            self.RETAINED_HINT if self._bubble_status == "retained" else "")
        if hint:
            lines.append((hint, self.TEXT_DIM, ("Segoe UI", 9)))
        return lines

    def _frame(self, w, h):
        """Background, left controls and divider as one image, cached for this take."""
        key = (w, h, self._bubble_status)
        photo = self._frames.get(key)
        if photo is None:
            base = self._bubble_cache.get(
                w, h, radius=min(self.BUBBLE_H // 2, h // 2),
                bg_top=_hex_to_rgba(self.BG)[:3], bg_bottom=_hex_to_rgba(self.BG)[:3],
                border_color_rgba=_hex_to_rgba(self.BORDER_COLOR), border_width=self.BORDER)
            frame = base.copy()
            row = self.BUBBLE_H // 2
            if self._bubble_status == "recording":
                self._paste_centered(frame, self._stop_button_img, self.BTN_CX, row)
            else:
                self._paste_centered(frame, render_dot(10, _hex_to_rgba(self.AMBER)), self.BTN_CX, row)
            from PIL import ImageDraw
            ImageDraw.Draw(frame).line([(self.LEFT_W, 10), (self.LEFT_W, h - 11)],
                                       fill=_hex_to_rgba(self.DIVIDER), width=1)
            photo = pil_to_photo(composite_on_transparent(frame))
            if len(self._frames) > 12:
                self._frames.clear()
            self._frames[key] = photo
        return photo

    def _layout(self):
        """Size, draw and place the bubble; only recording controls change between calls."""
        c = self._canvas
        wrap = self.TEXT_MAX_W - 2 * self.TEXT_PAD_X
        measured = []
        for text, color, font in self._text_lines():
            probe = c.create_text(0, 0, text=text, font=font, width=wrap, anchor="nw")
            x1, y1, x2, y2 = c.bbox(probe)
            c.delete(probe)
            measured.append((text, color, font, y2 - y1))
            # Width only grows during a take, in 8px steps, so streaming words
            # never make it jitter and few background sizes are rendered.
            need = -(-(x2 - x1 + 2 * self.TEXT_PAD_X) // 8) * 8
            self._text_w = max(self._text_w, min(self.TEXT_MAX_W, need))
        text_h = sum(m[3] for m in measured) + self.LINE_GAP * max(0, len(measured) - 1)
        closable = self._bubble_status in ("recording", "processing", "retained")
        w = self.LEFT_W + 1 + self._text_w
        h = max(self.BUBBLE_H, text_h + 2 * self.TEXT_PAD_Y)

        c.delete("all")
        self._close_box = None
        photo = self._frame(w, h)
        self._photo_refs = [photo]
        c.create_image(0, 0, image=photo, anchor="nw")
        row = self.BUBBLE_H // 2
        self._bar_ids, self._timer_id = [], None
        if self._bubble_status == "recording":
            self._timer_id = c.create_text(self.TIMER_X, row, text="", fill=self.TEXT,
                                           font=("Segoe UI", 14, "bold"), anchor="w")
            for i in range(self.BARS):
                x = self.BARS_X + i * (self.BAR_W + self.BAR_GAP)
                self._bar_ids.append(c.create_rectangle(x, row - 2, x + self.BAR_W, row + 2,
                                                        fill=self.WAVE_COLOR, outline=""))
            self._update_meter()
        else:
            label = {"processing": "Transcribing", "notice": self._notice_label}.get(self._bubble_status, "Not pasted")
            c.create_text(self.TIMER_X, row, text=label, fill=self.TEXT,
                          font=("Segoe UI", 10, "bold"), anchor="w")
        y = (h - text_h) // 2
        for text, color, font, line_h in measured:
            c.create_text(self.LEFT_W + 1 + self.TEXT_PAD_X, y, text=text, fill=color,
                          font=font, width=wrap, anchor="nw")
            y += line_h + self.LINE_GAP
        if closable:
            self._draw_close(self.LEFT_W - self.CLOSE_W // 2 - 2, self.BUBBLE_H // 2, self.BUBBLE_H)
        self._place(w, h)

    def _update_meter(self):
        """Timer and level bars only: no image work on the 80ms tick."""
        c = self._canvas
        if self._timer_id is not None:
            c.itemconfigure(self._timer_id, text=f"{time.time() - self._rec_start:.1f}s")
        levels = list(self._wave)[-self.BARS:]
        levels = [0.0] * (self.BARS - len(levels)) + levels
        row = self.BUBBLE_H // 2
        tallest = self.BUBBLE_H - 24
        for item, level, i in zip(self._bar_ids, levels, range(self.BARS)):
            half = max(2, int(min(1.0, level) * tallest) // 2)
            x = self.BARS_X + i * (self.BAR_W + self.BAR_GAP)
            c.coords(item, x, row - half, x + self.BAR_W, row + half)

    def _place(self, w, h):
        """Live text starts under the caret; above the line near the bottom edge."""
        if self._anchor:
            left, top, right, bottom = self._anchor
            al, at, ar, ab = _work_area(left, top) or (-10**6, -10**6, 10**6, 10**6)
            x = left - (self.LEFT_W + 1 + self.TEXT_PAD_X)
            x = max(al, min(x, ar - w))
            y = bottom + self.CARET_GAP
            if y + h > ab:
                y = top - self.CARET_GAP - h
            y = max(y, at)
        else:
            if self._float_left is None:
                center = self._get_screen_width() // 2 if self._pos_x is None else self._pos_x
                self._float_left = center - w // 2
            # Grows to the right, so the X and the controls never move under the pointer.
            x, y = self._float_left, self._pos_y
        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._canvas.configure(width=w, height=h)
