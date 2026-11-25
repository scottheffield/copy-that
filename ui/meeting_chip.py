"""The meeting chip: a bubble that stays on screen while a meeting records.

It borrows the dictation bubble's look (52 px tall, stop button on the left,
bold timer, level bars) so it reads as the same app, but it is a separate
window: the pill stays free for dictation during the meeting. Two labeled
waveforms, Me and Others, show at a glance that both sides are being heard.

A mic button next to the timer mutes your side of the meeting; the
other side keeps recording. Muted, it turns amber with a slash, and the
Me waveform reads "Muted".

It sits top right by default, can be dragged anywhere, and remembers where it
was left (its right edge, so it grows leftward when its contents change).
"""
import tkinter as tk
from collections import deque

from ui import theme
from ui.glass import (TRANSPARENT_COLOR, TRANSPARENT_RGB, _hex_to_rgba, composite_on_transparent,
                      pil_to_photo, render_button, render_dot, render_icon_mic_toggle, render_icon_stop,
                      render_pill)
from ui.screen import work_area

FONT = "Segoe UI"


def clock(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


class MeetingChip:
    H = 52              # The dictation bubble's height
    RADIUS = 26
    BORDER = 2
    BTN_CX = 26         # Stop button / status dot center, as in the dictation bubble
    TEXT_X = 46         # Timer or label, as in the dictation bubble
    MIC_D = 26          # Mic button diameter
    BARS = 10
    BAR_W, BAR_GAP = 3, 3
    PAD = 16
    NOTE_GAP = 6
    NOTE_H = 36
    READY_SECONDS = 20
    EDGE = 16           # px from the work area's top-right corner by default

    def __init__(self, root, on_stop=None, on_open=None, on_consent_seen=None,
                 position=None, on_moved=None, mode=None, on_mute=None):
        self._root = root
        self._on_stop, self._on_open, self._on_mute = on_stop, on_open, on_mute
        self.muted = False
        self._hover_mic = False
        self._mic_box = None    # (x1, y1, x2, y2) of the mic button, for its tooltip
        self._on_consent_seen, self._on_moved = on_consent_seen, on_moved
        self._right, self._top = (position or (None, None))
        self.state = "hidden"   # hidden, recording, finishing, ready, failed
        self._progress = None   # Finishing: percent of the transcript done
        self._consent = False
        self._elapsed = 0.0
        self._waves = (deque(maxlen=self.BARS), deque(maxlen=self.BARS))  # Me, Others
        self._problem = None
        self._hover = False
        self._devices = None    # (mic, speaker, hint), shown on hover while recording
        self._hits = []         # (x1, y1, x2, y2, callback)
        self._photos = []
        self._backgrounds = {}
        self._hide_id = None
        self._drag = None
        self._colors(mode)
        self._build()

    # -- Theme --------------------------------------------------------------

    def _colors(self, mode=None):
        c = lambda pair: theme.color(pair, mode)
        self.BG, self.TEXT, self.MUTED = c(theme.BG), c(theme.TEXT), c(theme.MUTED)
        self.EDGE_COLOR, self.DIVIDER, self.ACCENT = c(theme.PILL_BORDER), c(theme.BORDER), c(theme.ACCENT)
        self.RED, self.AMBER, self.GREEN = c(theme.ERROR), c(theme.WARNING), c(theme.SUCCESS)
        # The dictation bubble's stop button
        self._stop_img = render_button(26, self.RED, c(theme.STOP_BG), render_icon_stop(12, self.RED))
        self._mic_imgs = {
            False: render_button(self.MIC_D, self.EDGE_COLOR, self.BG, render_icon_mic_toggle(16, self.TEXT)),
            True: render_button(self.MIC_D, self.AMBER, self.BG, render_icon_mic_toggle(16, self.AMBER, slashed=True)),
        }

    def set_theme(self, mode):
        self._colors(mode)
        self._backgrounds.clear()
        if self.state != "hidden":
            self._draw()

    # -- Window ---------------------------------------------------------------

    def _build(self):
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=TRANSPARENT_COLOR)
        try:
            self._win.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self._canvas = tk.Canvas(self._win, bg=TRANSPARENT_COLOR, highlightthickness=0,
                                 width=300, height=self.H)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Enter>", lambda e: self._set_hover(True))
        self._canvas.bind("<Leave>", lambda e: self._set_hover(False))
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._win.update_idletasks()
        self._no_activate()
        self._win.withdraw()

    def _no_activate(self):
        """Never take focus from the meeting app or the field being dictated into."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style | 0x08000000 | 0x00000080) & ~0x00040000  # NOACTIVATE, TOOLWINDOW, not APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
        except Exception:
            pass

    def _default_corner(self):
        area = work_area(self._win.winfo_screenwidth() - 1, 0) or (
            0, 0, self._win.winfo_screenwidth(), self._win.winfo_screenheight())
        return area[2] - self.EDGE, area[1] + self.EDGE

    def _place(self, w, h):
        if self._right is None:
            self._right, self._top = self._default_corner()
        self._canvas.configure(width=w, height=h)
        self._win.geometry(f"{w}x{h}+{int(self._right - w)}+{int(self._top)}")

    # -- Public state API -----------------------------------------------------

    def show_recording(self, consent=False):
        self._cancel_hide()
        self.state, self._consent = "recording", consent
        self.muted, self._hover_mic = False, False
        self._elapsed, self._problem = 0.0, None
        for wave in self._waves:
            wave.clear()
        self._draw()
        self._win.deiconify()

    def update(self, elapsed, me, others, problem=None, devices=None, muted=False):
        """Called a few times a second while recording: time, 0-1 levels, a problem to
        show, (mic, speaker, hint) for the hover panel, and whether the mic is muted."""
        if self.state != "recording":
            return
        self._elapsed, self._problem, self.muted = elapsed, problem, muted
        if devices is not None:
            self._devices = devices
        for wave, level in zip(self._waves, (me, others)):
            # Light smoothing, as in the dictation bubble
            wave.append(wave[-1] * 0.2 + level * 0.8 if wave else level)
        self._draw()

    def show_finishing(self, duration):
        self._cancel_hide()
        self.state, self._elapsed, self._consent = "finishing", duration, False
        self._progress = None
        self._draw()
        self._win.deiconify()

    def set_progress(self, fraction):
        """Finishing... 42%: how much of the transcript is done (None shows the meeting's length)."""
        value = None if fraction is None else int(fraction * 100)
        if self.state == "finishing" and value != self._progress:
            self._progress = value
            self._draw()

    def show_ready(self):
        self.state = "ready"
        self._draw()
        self._win.deiconify()
        self._hide_id = self._root.after(self.READY_SECONDS * 1000, self.hide)

    def show_failed(self):
        self.state = "failed"
        self._draw()
        self._win.deiconify()

    def hide(self):
        self._cancel_hide()
        self.state = "hidden"
        self._win.withdraw()

    def _cancel_hide(self):
        if self._hide_id:
            self._root.after_cancel(self._hide_id)
            self._hide_id = None

    # -- Drawing ------------------------------------------------------------------

    def _measure(self, text, font):
        probe = self._canvas.create_text(0, 0, text=text, font=font, anchor="nw")
        x1, _, x2, _ = self._canvas.bbox(probe)
        self._canvas.delete(probe)
        return x2 - x1

    def _background(self, w, h, radius, border, icon=None, divider=None, mic=None):
        """mic: (x, muted) to draw the mic button into the pill, so its edge blends with the chip."""
        key = (w, h, radius, border, icon, divider, mic)
        photo_src = self._backgrounds.get(key)
        if photo_src is None:
            img = render_pill(w, h, radius=radius, border_color_rgba=_hex_to_rgba(border),
                              bg_top=_hex_to_rgba(self.BG)[:3], bg_bottom=_hex_to_rgba(self.BG)[:3],
                              border_width=self.BORDER)
            if icon == "stop":
                img.alpha_composite(self._stop_img, (self.BTN_CX - 13, h // 2 - 13))
            elif icon:
                img.alpha_composite(render_dot(10, _hex_to_rgba(icon)), (self.BTN_CX - 5, h // 2 - 5))
            if mic:
                img.alpha_composite(self._mic_imgs[mic[1]], (mic[0], h // 2 - self.MIC_D // 2))
            if divider:
                from PIL import ImageDraw
                ImageDraw.Draw(img).line([(divider, 12), (divider, h - 13)],
                                         fill=_hex_to_rgba(self.DIVIDER), width=1)
            photo_src = composite_on_transparent(img)
            if len(self._backgrounds) > 24:
                self._backgrounds.clear()
            self._backgrounds[key] = photo_src
        return photo_src

    def _draw(self):
        c = self._canvas
        c.delete("all")
        self._photos, self._hits = [], []
        mid = self.H // 2
        bold, label_font, small = (FONT, 14, "bold"), (FONT, 10, "bold"), (FONT, 10)
        wave_w = self.BARS * (self.BAR_W + self.BAR_GAP) - self.BAR_GAP

        # Layout first, so the background can be sized and cached.
        if self.state == "recording":
            timer_w = max(self._measure("00:00", bold), self._measure(clock(self._elapsed), bold))
            mic_x = self.TEXT_X + timer_w + 10
            divider = mic_x + self.MIC_D + 12
            if self._problem:
                content = [("text", self._problem, self.RED, label_font)]
            elif self.muted:
                content = [("text", "Muted", self.AMBER, label_font), ("wave", 0),
                           ("label", "Others"), ("wave", 1)]
            else:
                content = [("label", "Me"), ("wave", 0), ("label", "Others"), ("wave", 1)]
            x = divider + 14
            placed = []
            for kind, value, *rest in content:
                if kind == "wave":
                    width = wave_w
                elif kind == "label":
                    width = self._measure(value, small)
                else:
                    width = self._measure(value, rest[1])
                placed.append((kind, value, rest, x, width))
                x += width + (6 if kind == "label" or (kind == "text" and value == "Muted") else 16)
            w = x - 16 + self.PAD
            icon, border = "stop", (self.RED if self._problem else self.EDGE_COLOR)
        else:
            divider = mic_x = None
            label = {"finishing": "Finishing…", "ready": "Transcript ready",
                     "failed": "Transcript failed"}[self.state]
            x = self.TEXT_X + self._measure(label, label_font) + 12
            extra = []
            if self.state == "finishing":
                shown = clock(self._elapsed) if self._progress is None else f"{self._progress}%"
                extra.append(("muted", shown, x))
                x += self._measure(shown, small) + 12
            if self.state == "ready":
                open_w = self._measure("Open", label_font) + 20
                extra.append(("open", "Open", x, open_w))
                x += open_w + 6
            if self.state in ("ready", "failed"):
                extra.append(("close", None, x))
                x += 24
            w = x + (6 if self.state in ("ready", "failed") else self.PAD - 12)
            icon = {"finishing": self.AMBER, "ready": None, "failed": self.RED}[self.state]
            border = self.EDGE_COLOR

        # A panel under the chip: the consent note (first meeting), else the devices on hover.
        note_text = "Recording others may need their consent."
        lines = []
        if self._consent:
            note_w = self._measure(note_text, small) + self._measure("Got it", label_font) + 3 * self.PAD + 8
            note_h = self.NOTE_H
        elif self._hover and self.state == "recording" and (self._devices or self._hover_mic):
            if self._hover_mic:
                lines = [("Unmute your mic" if self.muted else "Mute your mic in this meeting", self.TEXT),
                         ("The other side keeps recording.", self.MUTED)]
            else:
                mic, speaker, hint = self._devices
                lines = [(f"Me: {mic or 'unknown'}", self.MUTED),
                         (f"Others: {speaker or 'unknown'}", self.MUTED)]
                if hint:
                    lines.append((hint, self.AMBER))
            note_w = max(self._measure(text, small) for text, _ in lines) + 2 * self.PAD
            note_h = 12 + 18 * len(lines)
        else:
            note_w = note_h = 0
        total_w = max(w, note_w)
        h = self.H + (self.NOTE_GAP + note_h if note_h else 0)
        dx = total_w - w  # The chip hugs the right edge; the panel may be wider

        mic = (mic_x, self.muted) if self.state == "recording" else None
        top = self._background(w, self.H, self.RADIUS, border, icon, divider, mic)
        if note_h:
            from PIL import Image
            full = Image.new("RGB", (total_w, h), TRANSPARENT_RGB)
            full.paste(top, (dx, 0))
            note = render_pill(note_w, note_h, radius=12, border_color_rgba=_hex_to_rgba(self.EDGE_COLOR),
                               bg_top=_hex_to_rgba(self.BG)[:3], bg_bottom=_hex_to_rgba(self.BG)[:3],
                               border_width=self.BORDER)
            full.paste(composite_on_transparent(note), (total_w - note_w, self.H + self.NOTE_GAP))
            top = full
        photo = pil_to_photo(top)
        self._photos.append(photo)
        c.create_image(0, 0, image=photo, anchor="nw")

        if self.state == "recording":
            c.create_text(dx + self.TEXT_X, mid, text=clock(self._elapsed), fill=self.TEXT, font=bold, anchor="w")
            self._hits.append((dx, 0, dx + self.BTN_CX + 16, self.H, self._stop))
            self._mic_box = (dx + mic_x - 4, 0, dx + mic_x + self.MIC_D + 4, self.H)
            self._hits.append((*self._mic_box, self._toggle_mute))
            for kind, value, rest, x, width in placed:
                x += dx
                if kind == "label":
                    c.create_text(x, mid, text=value, fill=self.MUTED, font=small, anchor="w")
                elif kind == "text":
                    c.create_text(x, mid, text=value, fill=rest[0], font=rest[1], anchor="w")
                else:
                    self._draw_wave(x, mid, self._waves[value])
        else:
            if self.state == "ready":
                cx = dx + self.BTN_CX
                c.create_line(cx - 6, mid, cx - 2, mid + 4, cx + 6, mid - 4, fill=self.GREEN, width=2,
                              capstyle="round", joinstyle="round")
            c.create_text(dx + self.TEXT_X, mid, text=label, fill=self.TEXT, font=label_font, anchor="w")
            for kind, value, x, *size in extra:
                x += dx
                if kind == "muted":
                    c.create_text(x, mid, text=value, fill=self.MUTED, font=small, anchor="w")
                elif kind == "open":
                    c.create_text(x + size[0] // 2, mid, text=value, fill=self.ACCENT, font=label_font)
                    self._hits.append((x, 6, x + size[0], self.H - 6, self._open))
                else:
                    k, cx = 5, x + 12
                    for x1, y1, x2, y2 in ((cx - k, mid - k, cx + k, mid + k), (cx - k, mid + k, cx + k, mid - k)):
                        c.create_line(x1, y1, x2, y2, fill=self.MUTED, width=2, capstyle="round")
                    self._hits.append((x, 0, x + 24, self.H, self.hide))

        if self._consent:
            ny = self.H + self.NOTE_GAP + self.NOTE_H // 2
            c.create_text(total_w - note_w + self.PAD, ny, text=note_text, fill=self.MUTED, font=small, anchor="w")
            gx = total_w - self.PAD - self._measure("Got it", label_font)
            c.create_text(gx, ny, text="Got it", fill=self.ACCENT, font=label_font, anchor="w")
            self._hits.append((gx - 10, self.H + self.NOTE_GAP, total_w, h, self._consent_seen))
        for i, (text, color) in enumerate(lines):
            c.create_text(total_w - note_w + self.PAD, self.H + self.NOTE_GAP + 15 + 18 * i,
                          text=text, fill=color, font=small, anchor="w")
        self._place(total_w, h)

    def _set_hover(self, hover):
        if not hover:
            self._canvas.configure(cursor="")
            self._hover_mic = False
        if hover != self._hover:
            self._hover = hover
            if self.state == "recording":
                self._draw()

    def _draw_wave(self, x, mid, wave):
        levels = list(wave)[-self.BARS:]
        levels = [0.0] * (self.BARS - len(levels)) + levels
        tallest = self.H - 24
        for i, level in enumerate(levels):
            half = max(2, int(min(1.0, level) * tallest) // 2)
            bx = x + i * (self.BAR_W + self.BAR_GAP)
            self._canvas.create_rectangle(bx, mid - half, bx + self.BAR_W, mid + half,
                                          fill=self.ACCENT, outline="")

    # -- Input --------------------------------------------------------------------

    def _hit(self, event):
        return next((cb for x1, y1, x2, y2, cb in self._hits
                     if x1 <= event.x <= x2 and y1 <= event.y <= y2), None)

    def _on_motion(self, event):
        self._canvas.configure(cursor="hand2" if self._hit(event) else "fleur")
        box = self._mic_box
        over = bool(box) and self.state == "recording" and box[0] <= event.x <= box[2] and event.y <= self.H
        if over != self._hover_mic:
            self._hover_mic = over
            self._draw()

    def _on_press(self, event):
        callback = self._hit(event)
        if callback:
            self._drag = None
            callback()
            return
        self._drag = (event.x_root, event.y_root, self._right, self._top)

    def _on_drag(self, event):
        if not self._drag:
            return
        x0, y0, right, top = self._drag
        self._right, self._top = right + event.x_root - x0, top + event.y_root - y0
        self._win.geometry(f"+{int(self._right - self._win.winfo_width())}+{int(self._top)}")

    def _on_release(self, event):
        if self._drag and self._on_moved and (event.x_root, event.y_root) != self._drag[:2]:
            self._on_moved((int(self._right), int(self._top)))
        self._drag = None

    def _stop(self):
        if self._on_stop:
            self._on_stop()

    def _toggle_mute(self):
        self.muted = not self.muted
        if self._on_mute:
            self._on_mute(self.muted)
        self._draw()

    def _open(self):
        self.hide()
        if self._on_open:
            self._on_open()

    def _consent_seen(self):
        self._consent = False
        if self._on_consent_seen:
            self._on_consent_seen()
        self._draw()
