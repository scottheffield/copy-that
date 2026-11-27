"""The tray panel (#90): a small window above the tray icon for what you reach for most,
without opening the main window. Record or stop a meeting (mute while it records), see
your numbers at a glance, rename the last few meetings in place, open their folders,
or open CopyThat.

Left-click on the tray icon opens it; clicking the icon again, pressing Escape, or
clicking anywhere else closes it. The right-click menu is unchanged.
"""
import time

import customtkinter as ctk

import meetings
from ui import theme
from ui.home_page import ON_STOP, RECORD_DOT, meeting_when, streak_line

WIDTH = 360
RECENT = 3
EDGE = 12               # Logical px kept clear of the work area's edges
REOPEN_GUARD = 0.4      # A tray click right after a focus-loss close is the same click: stay closed


class TrayPanel:
    """provider is the app: meeting_status, toggle_meeting, meeting_muted, toggle_meeting_mute,
    dictation_status, rename_meeting, open_meeting, open_folder, open_main.
    stats is a UsageStats or None; config and meetings_dir are callables."""

    def __init__(self, root, provider, stats, config, meetings_dir):
        self._root, self._provider = root, provider
        self._stats, self._config, self._meetings_dir = stats, config, meetings_dir
        self._win = None
        self._anchor = None
        self._closing = False
        self._closed_at = 0.0
        self._tick_id = None
        self._record = self._mute = self._status = None
        self._editing = None     # The title entry being edited, if any
        self._dot = None

    @property
    def is_open(self):
        return self._win is not None and self._win.winfo_exists()

    # -- Opening and closing ------------------------------------------------------

    def toggle(self, anchor=None):
        """The tray's left-click. anchor is the pointer position (physical px), near the icon."""
        if self.is_open:
            self.close()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD:
            self.show(anchor)

    def show(self, anchor=None):
        if self.is_open:
            self._win.lift()
            return
        win = ctk.CTkToplevel(self._root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(fg_color=theme.BORDER)
        self._win = win
        self._frame = ctk.CTkFrame(win, fg_color=theme.BG, corner_radius=0)
        self._frame.pack(fill="both", expand=True, padx=1, pady=1)
        self._build()
        win.update_idletasks()
        self._anchor = anchor
        self._place(anchor)
        win.deiconify()
        self._round_corners()
        win.bind("<Escape>", self._on_escape)
        win.bind("<FocusOut>", self._on_focus_out, add="+")
        self._take_focus()
        self._tick()

    def close(self):
        self._stop_tick()
        self._closing = True  # A title saved on the way out shouldn't rebuild the panel
        try:
            if self._editing is not None:
                self._commit(self._editing)
            if self.is_open:
                self._win.destroy()
        finally:
            self._closing = False
        self._win = None
        self._closed_at = time.monotonic()

    def _take_focus(self):
        """Take the foreground so a click elsewhere closes the panel (FocusOut)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self._win.focus_force()

    def _on_focus_out(self, _event):
        # Focus moving between the panel's own widgets fires this too; only close once
        # it has left the panel for another app.
        self._root.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self):
        if not self.is_open:
            return
        try:
            focused = self._win.focus_get()
        except (KeyError, Exception):
            focused = None
        if focused is None or not str(focused).startswith(str(self._win)):
            self.close()

    def _on_escape(self, _event):
        if self._editing is not None:
            return  # The entry's own Escape puts the title back
        self.close()

    def _place(self, anchor):
        """Above the tray: next to the pointer, kept inside the work area of its monitor.
        With the taskbar at the top, the panel opens below it instead."""
        from ui.screen import cursor, work_area
        win = self._win
        x, y = anchor or cursor()
        area = work_area(x, y) or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        left, top, right, bottom = area
        scale = win._get_window_scaling()
        edge = round(EDGE * scale)
        # CTk scales a geometry's size by the window scaling, not its position: the size
        # goes in logical px, the position in physical.
        w, h = round(WIDTH * scale), win.winfo_reqheight()
        px = min(max(left + edge, x - w // 2), right - edge - w)
        py = top + edge if y < top else bottom - edge - h
        win.geometry(f"{WIDTH}x{round(h / scale)}+{int(px)}+{int(max(top, py))}")

    def _round_corners(self):
        """Windows 11 rounds a popup's corners when asked; elsewhere this does nothing."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
            value = ctypes.c_int(3)  # DWMWCP_ROUNDSMALL
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    # -- Building -------------------------------------------------------------------

    def _build(self):
        frame = self._frame
        for child in frame.winfo_children():
            child.destroy()
        self._editing = None
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=(14, 12))

        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x")
        from ui.icons import render_app_icon
        self._logo = ctk.CTkImage(light_image=render_app_icon(20), dark_image=render_app_icon(20), size=(20, 20))
        ctk.CTkLabel(head, image=self._logo, text="").pack(side="left")
        ctk.CTkLabel(head, text="CopyThat", text_color=theme.TEXT,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left", padx=(8, 0))
        self._status = ctk.CTkLabel(head, text="", text_color=theme.MUTED, font=ctk.CTkFont(size=12))
        self._status.pack(side="right")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(12, 0))
        self._record = ctk.CTkButton(row, text="", height=36, corner_radius=18, border_width=1,
                                     font=ctk.CTkFont(size=14, weight="bold"), command=self._toggle_record)
        self._record.pack(side="left", fill="x", expand=True)
        self._mute = ctk.CTkButton(row, text="", width=96, height=36, corner_radius=18, border_width=1,
                                   font=ctk.CTkFont(size=13), command=self._toggle_mute)
        self._update_meeting()

        self._stats_strip(inner)
        self._recent(inner)

        foot = ctk.CTkFrame(inner, fg_color="transparent")
        foot.pack(fill="x", pady=(10, 0))
        link = dict(width=0, height=28, fg_color="transparent", hover_color=theme.SELECTED,
                    font=ctk.CTkFont(size=13))
        ctk.CTkButton(foot, text="Meetings folder", text_color=theme.MUTED,
                      command=lambda: self._then_close(self._provider.open_folder, None), **link
                      ).pack(side="left")
        ctk.CTkButton(foot, text="Open CopyThat", text_color=theme.ACCENT,
                      command=lambda: self._then_close(self._provider.open_main), **link).pack(side="right")

    def _stats_strip(self, parent):
        config = self._config()
        if not (config.get("usage_stats", True) and self._stats is not None):
            return
        summary = self._stats.summary()
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, border_color=theme.BORDER,
                            border_width=1, corner_radius=10)
        card.pack(fill="x", pady=(12, 0))
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=(8, 2))
        streak = summary["streak"]
        cells = ((f"{summary['words']:,}", "words"),
                 (str(summary["wpm"]) if summary["wpm"] else "–", "wpm"),
                 (str(streak), "day streak" if streak == 1 else "days streak"))
        for column, (number, label) in enumerate(cells):
            grid.grid_columnconfigure(column, weight=1, uniform="stat")
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="w")
            ctk.CTkLabel(cell, text=number, text_color=theme.TEXT, height=24,
                         font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(cell, text=label, text_color=theme.MUTED, height=16,
                         font=ctk.CTkFont(size=11)).pack(anchor="w")
        if not summary["dictated_today"]:  # A nudge; once you have, the number says it
            ctk.CTkLabel(card, text=streak_line(summary), text_color=theme.MUTED, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(fill="x", padx=12, pady=(0, 8))
        else:
            ctk.CTkFrame(card, fg_color="transparent", height=6).pack()

    def _recent(self, parent):
        ctk.CTkLabel(parent, text="RECENT MEETINGS", text_color=theme.MUTED, anchor="w", height=16,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x", pady=(14, 4))
        try:
            items = meetings.list_meetings(self._meetings_dir())[:RECENT]
        except OSError:
            items = []
        if not items:
            ctk.CTkLabel(parent, text="No meetings yet.", text_color=theme.MUTED, anchor="w").pack(fill="x")
            return
        for item in items:
            self._meeting_row(parent, item)

    def _meeting_row(self, parent, item):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        folder = item["folder"]
        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.pack(side="right", padx=(6, 0))
        small = dict(width=0, height=26, fg_color="transparent", hover_color=theme.SELECTED,
                     font=ctk.CTkFont(size=12))
        ctk.CTkButton(actions, text="Folder", text_color=theme.MUTED,
                      command=lambda: self._then_close(self._provider.open_folder, folder), **small
                      ).pack(side="left")
        ctk.CTkButton(actions, text="Open", text_color=theme.ACCENT,
                      command=lambda: self._then_close(self._provider.open_meeting, folder), **small
                      ).pack(side="left")
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        untitled = item["title"] in ("", "Untitled")
        # A borderless field that looks like the title until you click into it.
        entry = ctk.CTkEntry(text, height=26, border_width=1, corner_radius=6, fg_color=theme.BG,
                             border_color=theme.BG, text_color=theme.TEXT,
                             placeholder_text=meetings.display_title(item) if untitled else None,
                             placeholder_text_color=theme.MUTED, font=ctk.CTkFont(size=13, weight="bold"))
        if not untitled:
            entry.insert(0, item["title"])
        entry.pack(fill="x")
        entry._meeting = item
        ctk.CTkLabel(text, text=meeting_when(item), text_color=theme.MUTED, anchor="w", height=16,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=(7, 0))
        entry.bind("<FocusIn>", lambda _e: self._start_edit(entry), add="+")
        entry.bind("<Return>", lambda _e: self._finish(entry), add="+")
        entry.bind("<Escape>", lambda _e: self._cancel(entry), add="+")
        entry.bind("<FocusOut>", lambda _e: self._commit(entry), add="+")

    # -- Renaming in place -------------------------------------------------------------

    def _start_edit(self, entry):
        self._editing = entry
        entry.configure(border_color=theme.ACCENT, fg_color=theme.FIELD)

    def _end_edit(self, entry):
        if self._editing is entry:
            self._editing = None
        if entry.winfo_exists():
            entry.configure(border_color=theme.BG, fg_color=theme.BG)

    def _commit(self, entry):
        """Save a changed title. An empty field keeps the old one."""
        if self._editing is not entry:
            return
        self._end_edit(entry)
        item = entry._meeting
        title = " ".join(entry.get().split()) if entry.winfo_exists() else ""
        if title and title != item["title"]:
            item["title"] = title
            self._provider.rename_meeting(item["folder"], title)

    def _finish(self, entry):
        self._commit(entry)
        self._frame.focus_set()  # Out of the field, still in the panel
        return "break"

    def _cancel(self, entry):
        item = entry._meeting
        entry.delete(0, "end")
        if item["title"] not in ("", "Untitled"):
            entry.insert(0, item["title"])
        self._end_edit(entry)
        self._frame.focus_set()
        return "break"

    # -- Live state ------------------------------------------------------------------------

    def _record_dot(self):
        if self._dot is None:
            from PIL import Image, ImageDraw
            image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((4, 4, 36, 36), fill=RECORD_DOT)
            self._dot = ctk.CTkImage(light_image=image, dark_image=image, size=(10, 10))
        return self._dot

    def _update_meeting(self):
        button = self._record
        if button is None or not button.winfo_exists():
            return
        state, elapsed = self._provider.meeting_status()
        if state == "recording":
            button.configure(text=f"■  Stop  ·  {meetings.clock(elapsed)}", image=None, state="normal",
                             fg_color=theme.ERROR, hover_color=theme.ERROR, text_color=ON_STOP,
                             border_color=theme.ERROR)
            muted = self._provider.meeting_muted()
            self._mute.configure(text="Unmute mic" if muted else "Mute mic",
                                 fg_color=theme.SELECTED if muted else theme.SURFACE,
                                 hover_color=theme.SELECTED, text_color=theme.WARNING if muted else theme.TEXT,
                                 border_color=theme.WARNING if muted else theme.BORDER)
            if not self._mute.winfo_manager():
                self._mute.pack(side="left", padx=(8, 0))
        else:
            if self._mute.winfo_manager():
                self._mute.pack_forget()
            if state == "finishing":
                button.configure(text="Finishing the transcript…", image=None, state="disabled",
                                 fg_color=theme.SURFACE, border_color=theme.BORDER, text_color_disabled=theme.MUTED)
            else:
                button.configure(text="Record meeting", image=self._record_dot(), compound="left", state="normal",
                                 fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
                                 border_color=theme.BORDER)
        if self._status is not None and self._status.winfo_exists():
            self._status.configure(text=self._provider.dictation_status())

    def _tick(self):
        self._stop_tick()
        if not self.is_open:
            return
        self._update_meeting()
        self._tick_id = self._win.after(1000, self._tick)

    def _stop_tick(self):
        if self._tick_id and self.is_open:
            try:
                self._win.after_cancel(self._tick_id)
            except Exception:
                pass
        self._tick_id = None

    def _toggle_record(self):
        self._provider.toggle_meeting()
        self._update_meeting()

    def _toggle_mute(self):
        self._provider.toggle_meeting_mute()
        self._update_meeting()

    def _then_close(self, action, *args):
        self.close()
        action(*args)

    # -- Refreshing ---------------------------------------------------------------------------

    def refresh(self):
        """Meetings or stats changed. Rebuild, unless a title is being edited."""
        if self.is_open and self._editing is None and not self._closing:
            self._build()
            self._win.update_idletasks()
            self._place(self._anchor)  # Its height may have changed

    def refresh_theme(self):
        """Light, dark or the highlight changed: the panel is short-lived, so just close it."""
        if self.is_open:
            self.close()
