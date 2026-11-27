"""The Home page (#20): a greeting, your dictation numbers, a streak heatmap, Record meeting,
and the last few meetings. Meant to be fun to glance at, not a report."""
import tkinter as tk
from datetime import date, datetime

import customtkinter as ctk

import meetings
import usage_stats
from ui import theme

RECENT_MEETINGS = 3
CELL, GAP = 13, 3          # Heatmap squares and the space between them, in unscaled px
DAY_LABELS_W, MONTHS_H, LEGEND_H = 30, 16, 24
LEVEL_MIX = (0.0, 0.35, 0.6, 0.8, 1.0)   # How far each heatmap level is from empty toward the accent
INITIAL_TEXT = ("#FFFFFF", "#10151F")    # On the speaker colors: dark ones in light mode, light in dark
RECORD_DOT = "#C0392B"
FACE, FACE_OVERLAP = 28, 8               # Speaker circles on recent meetings, in unscaled px
FACES_W = FACE + 2 * (FACE - FACE_OVERLAP)
SPEAKER_SCAN_LINES = 400                 # Enough lines to find who spoke, without reading a long call


def windows_first_name():
    """The first word of the Windows display name, else of the sign-in name; "" if neither."""
    name = ""
    try:
        import ctypes
        size = ctypes.c_ulong(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.windll.secur32.GetUserNameExW(3, buffer, ctypes.byref(size)):  # NameDisplay
            name = buffer.value
    except (AttributeError, OSError):
        pass
    if not name.strip():
        import getpass
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    first = name.replace(".", " ").replace("_", " ").split()
    if not first:
        return ""
    word = first[0]
    return word.capitalize() if word.islower() else word


def greeting(name, now=None):
    hour = (now or datetime.now()).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


def streak_line(summary):
    days = summary["streak"]
    if not days:
        return "Dictate today to start a streak."
    if summary["dictated_today"]:
        return f"{days}-day streak. Keep it going."
    return f"{days}-day streak. Dictate today to keep it going."


def meeting_when(item, today=None):
    """Today, 9:02 AM · 42 min / Yesterday · 28 min / Tue · 15 min / Nov 3 · 1 hr 5 min."""
    today = today or date.today()
    started, day = item["started"], item["started"].date()
    if day == today:
        when = f"Today, {started:%I:%M %p}".replace(", 0", ", ")
    elif (today - day).days == 1:
        when = "Yesterday"
    elif 0 < (today - day).days < 7:
        when = f"{started:%a}"
    else:
        when = f"{started:%b} {day.day}" + (f", {day.year}" if day.year != today.year else "")
    if item["status"] == "recording":
        return f"{when} · Recording"
    minutes = max(1, round((item["duration"] or 0) / 60))
    length = f"{minutes} min" if minutes < 60 else (
        f"{minutes // 60} hr" + (f" {minutes % 60} min" if minutes % 60 else ""))
    return f"{when} · {length}"


def meeting_people(folder, my_name, limit=3):
    """[(initial, color pair)] for the first few speakers of a meeting, you first, in the
    Meetings page's speaker colors."""
    from ui.meetings_page import SPEAKER_COLORS
    names = []
    for _time, speaker, _text in meetings.transcript_lines(meetings.read_transcript(folder), SPEAKER_SCAN_LINES):
        if speaker not in names:
            names.append(speaker)
    mine = [n for n in names if n == "Me" or (my_name and n == my_name)]
    others = [n for n in names if n not in mine and n != "Others"] or [n for n in names if n == "Others"]
    people = [((my_name or "Me")[0].upper(), theme.ACCENT)] if mine else []
    for n, name in enumerate(others):
        # An unnamed "Speaker 2" shows its number, so it doesn't look like someone named S.
        unnamed = name.startswith("Speaker ") and name[8:].isdigit()
        people.append((name[8:] if unnamed else name[0].upper(), SPEAKER_COLORS[n % len(SPEAKER_COLORS)]))
    return people[:limit]


def _mix(a, b, t):
    start = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    end = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(start, end))


class HomePage:
    """Renders into page (a ScrollPage). stats is a UsageStats; provider gives meeting_status
    and toggle_meeting; config is read for the name and whether stats are kept."""

    def __init__(self, page, root, stats, config, meetings_dir, provider=None,
                 open_meeting=None, open_meetings=None):
        self._page, self._root = page, root
        self._stats, self._config = stats, config
        self._meetings_dir = meetings_dir
        self._provider = provider
        self._open_meeting = open_meeting or (lambda folder: None)
        self._open_meetings = open_meetings or (lambda: None)
        self._canvas = None
        self._weeks = []
        self._record = None
        self._tick_id = None
        self._dot = None

    # -- Building --------------------------------------------------------------

    def render(self):
        self._stop_tick()
        for child in self._page.winfo_children():
            child.destroy()
        self._canvas = None
        config = self._config()
        name = config.get("meeting_my_name") or windows_first_name()
        keep = config.get("usage_stats", True) and self._stats is not None
        summary = self._stats.summary() if self._stats is not None else None

        top = ctk.CTkFrame(self._page, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(20, 0))
        ctk.CTkLabel(top, text=greeting(name), text_color=theme.TEXT, anchor="w",
                     font=ctk.CTkFont(size=25, weight="bold")).pack(side="left")
        if self._provider and hasattr(self._provider, "toggle_meeting"):
            self._record = ctk.CTkButton(top, text="", height=36, width=0, corner_radius=18,
                                         border_width=1, font=ctk.CTkFont(size=14, weight="bold"),
                                         command=self._toggle_record)
            self._record.pack(side="right")
            self._update_record()
        if keep:
            ctk.CTkLabel(self._page, text=streak_line(summary), text_color=theme.MUTED, anchor="w",
                         font=ctk.CTkFont(size=14)).pack(fill="x", padx=20, pady=(2, 0))
            if summary["milestone"]:
                banner = ctk.CTkFrame(self._page, fg_color=theme.SURFACE, border_color=theme.BORDER,
                                      border_width=1, corner_radius=10)
                banner.pack(fill="x", padx=20, pady=(14, 0))
                ctk.CTkLabel(banner, text=f"New milestone: you passed {summary['milestone']:,} words.",
                             text_color=theme.TEXT, anchor="w", font=ctk.CTkFont(size=13, weight="bold")
                             ).pack(fill="x", padx=14, pady=8)
            self._cards(summary)
            self._heatmap_card(summary)
        else:
            off = ctk.CTkFrame(self._page, fg_color=theme.SURFACE, border_color=theme.BORDER,
                               border_width=1, corner_radius=12)
            off.pack(fill="x", padx=20, pady=(16, 0))
            ctk.CTkLabel(off, text="Usage stats are off. Turn on Keep usage stats in Settings, General, "
                         "to see your words, speed and streak here.", text_color=theme.MUTED,
                         anchor="w", justify="left", wraplength=500).pack(fill="x", padx=16, pady=14)
        self._recent_card(name)

    def _card(self, parent, column, label, number, unit, note):
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, border_color=theme.BORDER,
                            border_width=1, corner_radius=12)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        ctk.CTkLabel(card, text=label.upper(), text_color=theme.MUTED, anchor="w", height=16,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x", padx=14, pady=(12, 0))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14)
        ctk.CTkLabel(row, text=number, text_color=theme.TEXT, font=ctk.CTkFont(size=28, weight="bold")
                     ).pack(side="left")
        if unit:
            ctk.CTkLabel(row, text=f" {unit}", text_color=theme.MUTED,
                         font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", anchor="s", pady=(0, 6))
        ctk.CTkLabel(card, text=note, text_color=theme.MUTED, anchor="w", justify="left", wraplength=150,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=(0, 12))

    def _cards(self, summary):
        grid = ctk.CTkFrame(self._page, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(16, 0))
        for column in range(3):
            grid.grid_columnconfigure(column, weight=1, uniform="stat")
        words = summary["words"]
        self._card(grid, 0, "Words dictated", f"{words:,}", "",
                   usage_stats.words_like(words) if words else "Hold Ctrl + Win to start")
        wpm = summary["wpm"]
        speed = (f"{summary['times_faster']:g}× faster than typing" if summary["times_faster"] > 1
                 else f"Typing is about {usage_stats.TYPING_WPM} wpm")
        self._card(grid, 1, "Speaking speed", str(wpm) if wpm else "–", "wpm" if wpm else "", speed)
        number, unit = usage_stats.time_saved(summary["saved_minutes"])
        self._card(grid, 2, "Time saved", number, unit, f"Versus typing at {usage_stats.TYPING_WPM} wpm")

    def _heatmap_card(self, summary):
        card = ctk.CTkFrame(self._page, fg_color=theme.SURFACE, border_color=theme.BORDER,
                            border_width=1, corner_radius=12)
        card.pack(fill="x", padx=20, pady=(12, 0))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(head, text="Last 6 months", text_color=theme.TEXT,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        if summary["best_streak"]:
            ctk.CTkLabel(head, text=f"BEST: {summary['best_streak']} DAYS" if summary["best_streak"] > 1
                         else "BEST: 1 DAY", text_color=theme.MUTED,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(side="right")
        self._weeks = summary["weeks"]
        scale = ctk.ScalingTracker.get_widget_scaling(card)
        width = DAY_LABELS_W + len(self._weeks) * (CELL + GAP)
        height = MONTHS_H + 7 * (CELL + GAP) + LEGEND_H
        self._scale = scale
        self._canvas = tk.Canvas(card, width=round(width * scale), height=round(height * scale),
                                 highlightthickness=0, bd=0)
        self._canvas.pack(anchor="w", padx=16, pady=(0, 10))
        self._paint_heatmap()

    def _paint_heatmap(self):
        canvas = self._canvas
        if canvas is None or not canvas.winfo_exists():
            return
        s = self._scale
        surface, muted = theme.color(theme.SURFACE), theme.color(theme.MUTED)
        empty, accent = theme.color(theme.BORDER), theme.color(theme.ACCENT)
        colors = [_mix(empty, accent, t) for t in LEVEL_MIX]
        canvas.configure(bg=surface)
        canvas.delete("all")
        font = ("Segoe UI", 8)
        for row, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
            y = MONTHS_H + row * (CELL + GAP) + CELL / 2
            canvas.create_text(0, y * s, text=label, anchor="w", fill=muted, font=font)
        today = date.today()
        for week, column in enumerate(self._weeks):
            x = DAY_LABELS_W + week * (CELL + GAP)
            # A month's name sits over the week it starts in; the first week is named too,
            # unless a month starts right after it and the names would touch.
            starts = [day for day, _level in column if day.day == 1]
            soon = any(day.day == 1 for later in self._weeks[1:3] for day, _level in later)
            month = starts[0] if starts else (column[0][0] if week == 0 and not soon else None)
            if month:
                canvas.create_text(x * s, 0, text=f"{month:%b}", anchor="nw", fill=muted, font=font)
            for row, (day, level) in enumerate(column):
                if level is None:
                    continue
                y = MONTHS_H + row * (CELL + GAP)
                canvas.create_rectangle(x * s, y * s, (x + CELL) * s, (y + CELL) * s,
                                        fill=colors[level], outline="")
                if day == today:
                    canvas.create_rectangle((x - 2) * s, (y - 2) * s, (x + CELL + 2) * s, (y + CELL + 2) * s,
                                            outline=accent, width=max(1, round(1.5 * s)))
        y = MONTHS_H + 7 * (CELL + GAP) + 8
        x = DAY_LABELS_W
        canvas.create_text(x * s, (y + 5) * s, text="Less", anchor="w", fill=muted, font=font)
        x += 30
        for color in colors:
            canvas.create_rectangle(x * s, y * s, (x + 11) * s, (y + 11) * s, fill=color, outline="")
            x += 14
        canvas.create_text((x + 4) * s, (y + 5) * s, text="More", anchor="w", fill=muted, font=font)

    def _recent_card(self, name):
        card = ctk.CTkFrame(self._page, fg_color=theme.SURFACE, border_color=theme.BORDER,
                            border_width=1, corner_radius=12)
        card.pack(fill="x", padx=20, pady=(12, 20))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(head, text="Recent meetings", text_color=theme.TEXT,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="See all", width=0, height=26, fg_color="transparent",
                      hover_color=theme.SELECTED, text_color=theme.ACCENT, font=ctk.CTkFont(size=13),
                      command=self._open_meetings).pack(side="right")
        items = meetings.list_meetings(self._meetings_dir())[:RECENT_MEETINGS]
        if not items:
            ctk.CTkLabel(card, text="No meetings yet. Choose Record meeting to start one.",
                         text_color=theme.MUTED, anchor="w").pack(fill="x", padx=16, pady=(0, 14))
            return
        my_name = self._config().get("meeting_my_name", "")
        for item in items:
            self._meeting_row(card, item, my_name)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    def _meeting_row(self, card, item, my_name):
        row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=8, cursor="hand2")
        row.pack(fill="x", padx=8, pady=1)
        people = meeting_people(item["folder"], my_name)
        scale = ctk.ScalingTracker.get_widget_scaling(row)
        faces = tk.Canvas(row, width=round(FACES_W * scale), height=round(FACE * scale), highlightthickness=0, bd=0)
        faces.pack(side="left", padx=(8, 4), pady=10)
        self._paint_faces(faces, people, theme.SURFACE, scale)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True, padx=(6, 8), pady=6)
        title = ctk.CTkLabel(text, text=meetings.display_title(item), text_color=theme.TEXT, anchor="w",
                             font=ctk.CTkFont(size=14, weight="bold"))
        title.pack(fill="x")
        when = ctk.CTkLabel(text, text=meeting_when(item), text_color=theme.MUTED, anchor="w",
                            font=ctk.CTkFont(size=12))
        when.pack(fill="x")
        arrow = ctk.CTkLabel(row, text="›", text_color=theme.MUTED, font=ctk.CTkFont(size=20), width=20)
        arrow.pack(side="right", padx=(0, 10))
        folder = item["folder"]

        def enter(_event):
            row.configure(fg_color=theme.SELECTED)
            self._paint_faces(faces, people, theme.SELECTED, scale)

        def leave(event):
            # Moving onto a child also fires Leave on the row; stay lit while inside it.
            x, y = row.winfo_pointerxy()
            inside = row.winfo_rootx() <= x < row.winfo_rootx() + row.winfo_width() and \
                row.winfo_rooty() <= y < row.winfo_rooty() + row.winfo_height()
            if not inside:
                row.configure(fg_color="transparent")
                self._paint_faces(faces, people, theme.SURFACE, scale)

        def widgets(widget):
            yield widget
            for child in widget.winfo_children():
                yield from widgets(child)

        for widget in widgets(row):
            widget.bind("<Button-1>", lambda _e: self._open_meeting(folder), add="+")
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")

    @staticmethod
    def _paint_faces(canvas, people, background, scale):
        """Overlapping speaker circles, each ringed in the row's color so they read as separate."""
        bg = theme.color(background)
        canvas.configure(bg=bg)
        canvas.delete("all")
        for n, (initial, pair) in enumerate(people):
            x = n * (FACE - FACE_OVERLAP)
            canvas.create_oval(x * scale, 0, (x + FACE) * scale, FACE * scale, fill=bg, outline="")
            canvas.create_oval((x + 2) * scale, 2 * scale, (x + FACE - 2) * scale, (FACE - 2) * scale,
                               fill=theme.color(pair), outline="")
            canvas.create_text((x + FACE / 2) * scale, FACE / 2 * scale, text=initial,
                               fill=theme.color(INITIAL_TEXT), font=("Segoe UI", 9, "bold"))

    # -- Record meeting ----------------------------------------------------------

    def _record_dot(self):
        if self._dot is None:
            from PIL import Image, ImageDraw
            image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((4, 4, 36, 36), fill=RECORD_DOT)
            self._dot = ctk.CTkImage(light_image=image, dark_image=image, size=(10, 10))
        return self._dot

    def _update_record(self):
        """Quiet Record meeting with a red dot; Stop with the time while recording; Finishing after."""
        self._stop_tick()
        button = self._record
        if button is None or not button.winfo_exists():
            return
        state, elapsed = self._provider.meeting_status()
        if state == "recording":
            button.configure(text=f"■  Stop  ·  {meetings.clock(elapsed)}", image=None, state="normal",
                             fg_color=theme.ERROR, hover_color=theme.ERROR, text_color="#FFFFFF",
                             border_color=theme.ERROR)
            self._tick_id = button.after(1000, self._update_record)
        elif state == "finishing":
            button.configure(text="Finishing…", image=None, state="disabled", fg_color=theme.SURFACE,
                             border_color=theme.BORDER, text_color_disabled=theme.MUTED)
            self._tick_id = button.after(1000, self._update_record)
        else:
            button.configure(text="Record meeting", image=self._record_dot(), compound="left", state="normal",
                             fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
                             border_color=theme.BORDER)

    def _stop_tick(self):
        if self._tick_id and self._record is not None:
            try:
                self._record.after_cancel(self._tick_id)
            except Exception:
                pass
        self._tick_id = None

    def _toggle_record(self):
        self._provider.toggle_meeting()
        self._update_record()

    def refresh_theme(self):
        """Light or dark changed: the heatmap and speaker circles are plain Tk, so redraw.
        A hidden Home redraws when it is next shown."""
        if self._page.winfo_ismapped():
            self.render()

    def close(self):
        self._stop_tick()
