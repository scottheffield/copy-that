"""A meeting's transcript in one text area: time, speaker and words on each line, with the
words' wrapped lines indented under the words. One widget rather than three labels a line,
so an hour-long meeting opens at once, and a reader can select and copy any part of it."""

import tkinter as tk

import customtkinter as ctk

from ui import theme

FONT_SIZES = {"stamp": 13, "who": 14, "body": 15, "note": 13}  # Unscaled px, as CTkFont sizes
GAP = 14         # Between the time, speaker and words columns, unscaled px
LINE_GAP = 10    # Between transcript lines, unscaled px


class TranscriptView:
    """lines: (time, speaker, words, row, start seconds). row (from speaker_rows) makes the
    speaker a menu: on_speaker(row, (x, y)) gets the screen point under the name. With
    on_play, clicking a line's time or words calls on_play(seconds)."""

    def __init__(self, parent, on_speaker=None, on_play=None):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self._scale = ctk.ScalingTracker.get_widget_scaling(parent)
        self._fonts = {name: ctk.CTkFont(size=size, weight="bold" if name == "who" else "normal")
                       for name, size in FONT_SIZES.items()}
        self.text = tk.Text(self.frame, wrap="word", bd=0, highlightthickness=0, height=1, width=1,
                            cursor="arrow", padx=round(20 * self._scale), pady=0, undo=False,
                            font=self._font("body"), exportselection=True)
        self._bar = ctk.CTkScrollbar(self.frame, command=self.text.yview,
                                     button_color=theme.BORDER, button_hover_color=theme.MUTED)
        self._bar_shown = False
        self.text.configure(yscrollcommand=self._scrolled)
        self.text.pack(side="left", fill="both", expand=True)
        self._on_speaker, self._on_play = on_speaker, on_play
        self._colors = {}    # Tag: color pair, repainted on a theme change
        self.starts = []     # (start seconds, text line number) of each playable line
        self.speakers = []   # The speaker shown on each line, in order
        self.lit = None      # Index in starts of the line being played
        self.text.tag_bind("link", "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda _e: self.text.configure(cursor="arrow"))
        # Ctrl+A and Ctrl+C work on the read-only text.
        self.text.bind("<Control-a>", lambda _e: (self.text.tag_add("sel", "1.0", "end"), "break")[1])
        self.paint()

    def _font(self, name):
        return self._fonts[name].create_scaled_tuple(self._scale)

    def _px(self, value):
        return round(value * self._scale)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def destroy(self):
        self.frame.destroy()

    def exists(self):
        return self.frame.winfo_exists()

    # -- Content --------------------------------------------------------------

    def show(self, note=None, lines=(), colors=None, playable=False):
        """Fill the view: an optional muted note, then the lines. colors maps a row's label to
        its color pair; lines without one use the accent for Me and muted for others."""
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        self.starts, self.speakers, self.lit = [], [], None
        colors = colors or {}
        measure = lambda name, values: max((self._fonts[name].measure(v) for v in values), default=0)
        stamp_w = measure("stamp", [line[0] for line in lines]) + GAP
        who_w = stamp_w + measure("who", [line[1] + (" ▾" if line[3] else "") for line in lines]) + GAP
        text.tag_configure("line", tabs=(self._px(stamp_w), self._px(who_w)), lmargin2=self._px(who_w),
                           spacing3=self._px(LINE_GAP))
        text.tag_configure("note", font=self._font("note"), spacing3=self._px(12), lmargin2=0)
        text.tag_configure("stamp", font=self._font("stamp"))
        text.tag_configure("who", font=self._font("who"))
        self._colors = {"stamp": theme.MUTED, "note": theme.MUTED}
        if note:
            text.insert("end", note + "\n", "note")
        for index, (stamp, speaker, said, row, seconds) in enumerate(lines):
            number = int(text.index("end-1c").split(".")[0])
            pair = (colors.get(row["label"]) if row else None) or (theme.ACCENT if speaker == "Me" else theme.MUTED)
            color_tag = f"color{pair[0]}{pair[1]}".replace("#", "")
            self._colors[color_tag] = pair
            plays = playable and seconds is not None
            said_tags = ("line", f"say{index}") + (("link",) if plays else ())
            stamp_tags = ("line", "stamp", f"say{index}") + (("link",) if plays else ())
            who_tags = ("line", "who", color_tag) + ((f"who{index}", "link") if row else ())
            text.insert("end", stamp + "\t", stamp_tags, speaker + (" ▾" if row else "") + "\t", who_tags,
                        said, said_tags, "\n", ("line",))
            self.speakers.append(speaker + (" ▾" if row else ""))
            if plays:
                self.starts.append((seconds, number))
                text.tag_bind(f"say{index}", "<ButtonRelease-1>", lambda _e, s=seconds: self._clicked_line(s))
            if row and self._on_speaker:
                # On release, like a button: opened on the press, Windows gave focus back to the
                # window when the button came up, and the menu closed itself at once.
                text.tag_bind(f"who{index}", "<ButtonRelease-1>",
                              lambda _e, r=row, tag=f"who{index}": self._clicked_speaker(r, tag))
        text.delete("end-1c", "end")  # No empty line after the last one
        text.configure(state="disabled")
        self._bar_shown = False  # Shown again by the next scroll update if the text overflows
        self._bar.pack_forget()
        self.paint()

    def _clicked_line(self, seconds):
        if self.text.tag_ranges("sel"):
            return  # Selecting text, not asking to play
        if self._on_play:
            self._on_play(seconds)

    def _clicked_speaker(self, row, tag):
        self._on_speaker(row, self._point_under(tag))

    def speaker_point(self, index):
        """The screen point under line index's speaker."""
        return self._point_under(f"who{index}")

    def _point_under(self, tag):
        first = self.text.tag_ranges(tag)[0]
        self.text.see(first)
        self.text.update_idletasks()
        box = self.text.bbox(first) or (0, 0, 0, 0)
        return self.text.winfo_rootx() + box[0], self.text.winfo_rooty() + box[1] + box[3] + 2

    # -- Playing line --------------------------------------------------------

    def light(self, index, scroll=False):
        """Mark starts[index] as the line being played (None for none), keeping it in view."""
        if index == self.lit:
            return
        text = self.text
        text.tag_remove("lit", "1.0", "end")
        text.tag_remove("litstamp", "1.0", "end")
        self.lit = index
        if index is None:
            return
        number = self.starts[index][1]
        text.tag_add("lit", f"{number}.0", f"{number}.end+1c")  # With the newline: a band to the edge
        stamp_end = text.search("	", f"{number}.0", f"{number}.end") or f"{number}.end"
        text.tag_add("litstamp", f"{number}.0", stamp_end)
        text.tag_raise("lit")
        text.tag_raise("litstamp")
        if scroll and text.dlineinfo(f"{number}.0") is None:
            # Out of view: bring it a third of the way down.
            text.yview(f"{number}.0")
            info = text.dlineinfo(f"{number}.0")
            if info:
                text.yview_scroll(-(text.winfo_height() // max(1, info[3]) // 3), "units")

    def lit_text(self):
        ranges = self.text.tag_ranges("lit")
        return self.text.get(ranges[0], ranges[1]) if ranges else ""

    # -- Scrolling and colors ---------------------------------------------------

    def _scrolled(self, first, last):
        if not self._bar_shown and (float(first) > 0 or float(last) < 1):
            self._bar_shown = True
            self._bar.pack(side="right", fill="y", padx=(0, 4))
        self._bar.set(first, last)

    def yview(self):
        return self.text.yview()

    def yview_moveto(self, fraction):
        self.text.yview_moveto(fraction)

    def paint(self):
        """Apply the current light or dark colors (tk widgets don't follow CTk's appearance mode)."""
        c = theme.color
        self.text.configure(bg=c(theme.BG), fg=c(theme.TEXT), insertbackground=c(theme.TEXT),
                            selectbackground=c(theme.SELECTED), selectforeground=c(theme.TEXT),
                            inactiveselectbackground=c(theme.SELECTED))
        for tag, pair in self._colors.items():
            self.text.tag_configure(tag, foreground=c(pair))
        self.text.tag_configure("lit", background=c(theme.SELECTED))
        self.text.tag_configure("litstamp", foreground=c(theme.ACCENT))
