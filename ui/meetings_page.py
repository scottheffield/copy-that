"""The Meetings page of the main window: recorded meetings, grouped by day, with a detail view."""
import os
from datetime import date
from pathlib import Path

import customtkinter as ctk

import meetings
from ui import theme

PREVIEW_LINES = 14
SEARCH_DELAY_MS = 250
STATUS_TEXT = {
    "recording": "Recording",
    "transcribing": "Transcribing…",
    "failed": "Transcription failed",
    "interrupted": "Transcript not finished",
}


def _wrap(label, frame, margin=24):
    """Wrap label to frame's width. Event widths are physical pixels; CTk scales wraplength."""
    frame.bind("<Configure>", lambda e: label.configure(
        wraplength=max(100, e.width / label._get_widget_scaling() - margin)), add="+")


class MeetingsPage:
    """Renders into page (a CTkScrollableFrame). provider offers retranscribe_meeting(folder)
    and meeting_in_use(folder); status(text, color) reports problems in the window's status line."""

    def __init__(self, page, root, meetings_dir, provider=None, status=None):
        self._page, self._root = page, root
        self._meetings_dir = meetings_dir  # Callable: the folder can change in Settings
        self._provider = provider
        self._status = status or (lambda text, color="gray60": None)
        self._detail = None  # The folder shown in the detail view, or None for the list
        self._query = ""
        self._search_job = None
        self._results = None
        self._pending = None  # Saves a title being typed when the view changes underneath it

    # -- Shared ---------------------------------------------------------------

    def save_pending(self):
        """Save a title that is being typed. Clicking a CTk button doesn't move focus, so leaving
        the page or the meeting gives no FocusOut."""
        pending, self._pending = self._pending, None
        if pending:
            pending()

    def render(self):
        """Rebuild whichever view is showing, e.g. after a meeting finishes."""
        self.save_pending()
        if self._detail is not None and not (self._detail / "meta.json").exists():
            self._detail = None  # Deleted outside the app
        for child in self._page.winfo_children():
            child.destroy()
        self._results = None
        if self._detail is None:
            self._render_list()
        else:
            self._render_detail(self._detail)
        try:
            self._page._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def _in_use(self, item):
        if item["status"] in meetings.BUSY:
            return True
        return bool(self._provider and self._provider.meeting_in_use(item["folder"]))

    def _button(self, parent, text, command, primary=False, state="normal"):
        options = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT) \
            if primary else dict(fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
                                 border_color=theme.BORDER, border_width=1)
        button = ctk.CTkButton(parent, text=text, command=command, height=30, width=0, state=state, **options)
        button.pack(side="left", padx=(0, 8), pady=(0, 8))
        return button

    def _open(self, path):
        try:
            os.startfile(path)
        except OSError as error:
            self._status(f"Couldn't open {Path(path).name} ({type(error).__name__})", "#FF453A")

    # -- List -----------------------------------------------------------------

    def _render_list(self):
        page = self._page
        ctk.CTkLabel(page, text="Meetings", text_color=theme.TEXT,
                     font=ctk.CTkFont(size=25, weight="bold")).pack(anchor="w", padx=20, pady=(20, 2))
        ctk.CTkLabel(page, text="Your meetings, transcribed on this PC.", text_color=theme.MUTED,
                     wraplength=460, justify="left").pack(anchor="w", padx=20, pady=(0, 12))
        self._search = ctk.CTkEntry(page, placeholder_text="Search titles and transcripts", height=34,
                                    fg_color=theme.FIELD, border_color=theme.BORDER, text_color=theme.TEXT)
        self._search.pack(fill="x", padx=20, pady=(0, 8))
        if self._query:
            self._search.insert(0, self._query)
        self._search.bind("<KeyRelease>", self._on_search)
        self._results = ctk.CTkFrame(page, fg_color="transparent")
        self._results.pack(fill="x", padx=12)
        self._fill_results()
        footer = ctk.CTkFrame(page, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(16, 16))
        note = ctk.CTkLabel(footer, text=f"Meetings are saved on this PC in {self._meetings_dir()}.",
                            text_color=theme.MUTED, justify="left", anchor="w",
                            font=ctk.CTkFont(size=12), wraplength=460)
        note.pack(fill="x")
        _wrap(note, footer, 0)
        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.pack(fill="x", pady=(8, 0))
        self._button(actions, "Open meetings folder", self._open_root)
        if self._provider and hasattr(self._provider, "set_meetings_dir"):
            self._button(actions, "Change folder…", self._change_root)

    def _open_root(self):
        folder = Path(self._meetings_dir())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._open(folder)

    def _change_root(self):
        """Pick a new meetings folder, and offer to move the meetings already saved."""
        from tkinter import filedialog
        from ui.dialogs import confirm
        if self._provider.meetings_busy():
            self._status("Wait until the meeting finishes recording or transcribing, then change the folder.",
                         "#FF9F0A")
            return
        old = self._meetings_dir()
        chosen = filedialog.askdirectory(parent=self._page.winfo_toplevel(), title="Choose the meetings folder",
                                         initialdir=old if Path(old).is_dir() else str(Path.home()))
        if not chosen or meetings.same_folder(chosen, old):
            return
        chosen = str(Path(chosen))
        count = len(meetings.list_meetings(old))

        def apply(move):
            if self._provider.meetings_busy():  # A meeting started while the dialog was open
                self._status("Wait until the meeting finishes, then change the folder.", "#FF9F0A")
                return
            if move:
                self._status(f"Moving {count} meeting{'s' * (count != 1)}…")
            self._provider.set_meetings_dir(chosen, move, done)

        def done(moved, failed):
            if failed < 0:
                self._status("Couldn't move the meetings. They are still in the old folder.", "#FF453A")
            elif failed:
                self._status(f"Moved {moved}. {failed} couldn't move and stayed in {old}.", "#FF9F0A")
            elif moved:
                self._status(f"Moved {moved} meeting{'s' * (moved != 1)} to {chosen}.", "#30D158")
            else:
                self._status(f"New meetings will be saved in {chosen}.", "#30D158")
            if self._page.winfo_exists():
                self.render()

        if not count:
            apply(False)
            return
        plural = "s" if count != 1 else ""
        confirm(self._root, "Move your meetings?",
                f"{count} meeting{plural} saved in {old}. Move {'them' if count != 1 else 'it'} to the new "
                f"folder, or leave {'them' if count != 1 else 'it'} where {'they are' if count != 1 else 'it is'}? "
                "Meetings left behind don't show here unless you switch back.",
                "Move", "Leave", lambda: apply(True), on_cancel=lambda: apply(False))

    def _on_search(self, _event=None):
        if self._search_job:
            self._page.after_cancel(self._search_job)
        self._search_job = self._page.after(SEARCH_DELAY_MS, self._apply_search)

    def _apply_search(self):
        self._search_job = None
        self.save_pending()
        if self._results is None or not self._results.winfo_exists():
            return
        self._query = self._search.get()
        for child in self._results.winfo_children():
            child.destroy()
        self._fill_results()

    def _fill_results(self):
        items = meetings.list_meetings(self._meetings_dir())
        if not items:
            ctk.CTkLabel(self._results, text="No meetings yet.\nChoose Record meeting from the tray icon to start one.",
                         text_color=theme.MUTED, font=ctk.CTkFont(size=15), justify="center").pack(pady=48)
            return
        shown = [item for item in items if meetings.matches(item, self._query)]
        if not shown:
            ctk.CTkLabel(self._results, text=f"No meetings match “{self._query.strip()}”.",
                         text_color=theme.MUTED, font=ctk.CTkFont(size=15)).pack(pady=48)
            return
        today, heading = date.today(), None
        for item in shown:
            label = meetings.day_label(item["started"], today)
            if label != heading:
                heading = label
                ctk.CTkLabel(self._results, text=label.upper(), text_color=theme.MUTED,
                             font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=(12, 4))
            self._row(item)

    def _row(self, item):
        row = ctk.CTkFrame(self._results, fg_color="transparent", corner_radius=8, cursor="hand2")
        row.pack(fill="x", pady=1)
        status = item["status"]
        alert = status in ("recording", "failed", "interrupted")
        color = theme.ERROR if alert else theme.WARNING if status == "transcribing" else theme.MUTED
        duration = ctk.CTkLabel(row, text="—" if status == "recording" else meetings.clock(item["duration"]),
                                text_color=theme.MUTED, font=ctk.CTkFont(size=14), width=60, anchor="e")
        duration.pack(side="right", padx=(8, 12))
        rename = ctk.CTkButton(row, text="Rename", width=0, height=26, font=ctk.CTkFont(size=12),
                               fg_color="transparent", hover_color=theme.SURFACE, text_color=theme.ACCENT)
        rename.pack(side="right")
        text = ctk.CTkFrame(row, fg_color="transparent", cursor="hand2")
        text.pack(side="left", fill="x", expand=True, padx=12, pady=8)
        title = ctk.CTkLabel(text, text=meetings.display_title(item), text_color=theme.TEXT, anchor="w",
                             font=ctk.CTkFont(size=15, weight="bold"))
        title.pack(fill="x")
        detail = STATUS_TEXT.get(status) or f"{item['started']:%I:%M %p}".lstrip("0")
        sub = ctk.CTkLabel(text, text=detail, text_color=color, anchor="w", font=ctk.CTkFont(size=13))
        sub.pack(fill="x")
        rename.configure(command=lambda: self._rename_in_row(item, title, sub, text))

        def enter(_event):
            row.configure(fg_color=theme.SELECTED)

        def leave(event):
            # Moving between the row's own labels also fires Leave; only clear when the pointer left the row.
            x, y = row.winfo_pointerxy()
            if not (row.winfo_rootx() <= x < row.winfo_rootx() + row.winfo_width()
                    and row.winfo_rooty() <= y < row.winfo_rooty() + row.winfo_height()):
                row.configure(fg_color="transparent")

        for widget in (row, text, title, sub, duration, rename):
            if widget is rename:
                widget.bind("<Enter>", enter, add="+")
                widget.bind("<Leave>", leave, add="+")
                continue
            widget.bind("<Button-1>", lambda _e, f=item["folder"]: self.show_detail(f))
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)

    def _title_entry(self, parent, item, size):
        """A title field: shows "Add a title" when untitled; Esc restores the saved title."""
        entry = ctk.CTkEntry(parent, placeholder_text="Add a title", height=size * 2 - 4,
                             font=ctk.CTkFont(size=size, weight="bold"), text_color=theme.TEXT,
                             placeholder_text_color=theme.MUTED, fg_color=theme.FIELD,
                             border_color=theme.BORDER, border_width=1)
        if item["title"] not in ("", "Untitled"):
            entry.insert(0, item["title"])
        entry.bind("<FocusIn>", lambda _e: entry.configure(border_color=theme.ACCENT), add="+")
        entry.bind("<FocusOut>", lambda _e: entry.configure(border_color=theme.BORDER)
                   if entry.winfo_exists() else None, add="+")
        return entry

    def _restore(self, entry, item):
        entry.delete(0, "end")
        if item["title"] not in ("", "Untitled"):
            entry.insert(0, item["title"])

    def _rename_in_row(self, item, title, sub, text):
        """Turn a row's title into a field; Enter or clicking away saves, Esc cancels."""
        entry = self._title_entry(text, item, 15)
        title.pack_forget()
        entry.pack(fill="x", before=sub)
        state = {"done": False}

        def finish(save, redraw=True):
            if state["done"]:
                return
            state["done"] = True
            self._pending = None
            if save:
                self._rename(item["folder"], entry.get(), item)
            if redraw:
                self._apply_search()

        self._pending = lambda: finish(True, redraw=False)

        entry.bind("<Return>", lambda _e: finish(True))
        entry.bind("<Escape>", lambda _e: finish(False))
        entry.bind("<FocusOut>", lambda _e: finish(True), add="+")
        entry.focus_set()

    # -- Detail ---------------------------------------------------------------

    def show_detail(self, folder):
        self._detail = Path(folder)
        self.render()

    def show_list(self):
        self._detail = None
        self.render()

    def _item(self, folder):
        return next((item for item in meetings.list_meetings(folder.parent) if item["folder"] == folder), None)

    def _render_detail(self, folder):
        page = self._page
        item = self._item(folder)
        if item is None:
            self._detail = None
            self._render_list()
            return
        ctk.CTkButton(page, text="‹  All meetings", command=self.show_list, anchor="w", width=0, height=28,
                      fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.ACCENT,
                      font=ctk.CTkFont(size=13)).pack(anchor="w", padx=14, pady=(14, 4))
        entry = self._title_entry(page, item, 22)
        entry.pack(fill="x", padx=20, pady=(4, 0))
        entry.bind("<Return>", lambda _e: page.focus_set())
        entry.bind("<Escape>", lambda _e: (self._restore(entry, item), page.focus_set()))
        entry.bind("<FocusOut>", lambda _e: self._rename(folder, entry.get(), item)
                   if entry.winfo_exists() else None, add="+")
        self._title_field = entry
        self._pending = lambda: self._rename(folder, entry.get(), item) if entry.winfo_exists() else None
        if item["title"] in ("", "Untitled"):
            entry.after(80, lambda: entry.focus_set() if entry.winfo_exists() else None)
        started = item["started"]
        when = "Today" if started.date() == date.today() else f"{started:%a, %b} {started.day}"
        parts = [when, f"{started:%I:%M %p}".lstrip("0")]
        if item["status"] != "recording":
            parts.append(meetings.clock(item["duration"]))
        if item["status"] in STATUS_TEXT:
            parts.append(STATUS_TEXT[item["status"]])
        ctk.CTkLabel(page, text=" · ".join(parts), text_color=theme.MUTED,
                     font=ctk.CTkFont(size=13)).pack(anchor="w", padx=20, pady=(2, 12))

        text = meetings.read_transcript(folder)
        busy = self._in_use(item)
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=20)
        copy = self._button(actions, "Copy as Markdown", None, primary=True,
                            state="normal" if text else "disabled")
        copy.configure(command=lambda: self._copy(folder, copy))
        self._button(actions, "Rename", lambda: (entry.focus_set(), entry.select_range(0, "end")))
        self._button(actions, "Open transcript", lambda: self._open(folder / "transcript.md"),
                     state="normal" if text else "disabled")
        self._button(actions, "Open folder", lambda: self._open(folder))
        has_audio = (folder / "mic.wav").exists() or (folder / "others.wav").exists()
        self._button(actions, "Re-transcribe", lambda: self._retranscribe(folder),
                     state="disabled" if busy or not has_audio or not self._provider else "normal")
        self._button(actions, "Delete", lambda: self._delete(folder, item),
                     state="disabled" if busy else "normal")
        ctk.CTkFrame(page, height=1, fg_color=theme.BORDER).pack(fill="x", padx=20, pady=(8, 12))

        message = {
            "recording": "Recording now. The transcript appears here after you stop.",
            "transcribing": "Transcribing… The transcript appears here when it's ready.",
            "failed": "Transcription failed. Re-transcribe to try again.",
            "interrupted": "The transcript wasn't finished. It finishes on the next launch, or Re-transcribe now.",
        }.get(item["status"])
        if message and not (text and item["status"] == "done"):
            note = ctk.CTkLabel(page, text=message, text_color=theme.MUTED, justify="left", anchor="w")
            note.pack(fill="x", padx=20, pady=(0, 12))
            _wrap(note, page, 40)
        if not text:
            return
        lines = meetings.transcript_lines(text)
        if not lines:
            ctk.CTkLabel(page, text="No speech was recognized.", text_color=theme.MUTED).pack(anchor="w", padx=20)
            return
        preview = ctk.CTkFrame(page, fg_color="transparent")
        preview.pack(fill="x", padx=20)
        preview.grid_columnconfigure(2, weight=1)
        for row, (stamp, speaker, said) in enumerate(lines[:PREVIEW_LINES]):
            ctk.CTkLabel(preview, text=stamp, text_color=theme.MUTED, font=ctk.CTkFont(size=13),
                         anchor="nw").grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=(0, 10))
            ctk.CTkLabel(preview, text=speaker, anchor="nw", font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=theme.ACCENT if speaker == "Me" else theme.MUTED,
                         ).grid(row=row, column=1, sticky="nw", padx=(0, 10), pady=(0, 10))
            body = ctk.CTkLabel(preview, text=said, text_color=theme.TEXT, justify="left", anchor="nw",
                                font=ctk.CTkFont(size=15), wraplength=360)
            body.grid(row=row, column=2, sticky="new", pady=(0, 10))
            _wrap(body, preview, 150)
        if len(lines) > PREVIEW_LINES:
            ctk.CTkLabel(page, text="The preview shows the start of the meeting. Open transcript for all of it.",
                         text_color=theme.MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=20, pady=(4, 16))

    def _rename(self, folder, title, item):
        title = " ".join(title.split())
        if not title or title == item["title"]:
            return
        try:
            meetings.rename_meeting(folder, title)
        except (OSError, ValueError) as error:
            self._status(f"Couldn't rename the meeting ({type(error).__name__})", "#FF453A")
            return
        item["title"] = title
        self._status("Meeting renamed.", "#30D158")

    def _copy(self, folder, button):
        text = meetings.read_transcript(folder)
        if not text:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            self._root.clipboard_clear()
            self._root.clipboard_append(text)
        button.configure(text="Copied")
        button.after(1200, lambda: button.configure(text="Copy as Markdown") if button.winfo_exists() else None)

    def _retranscribe(self, folder):
        if self._provider:
            self._provider.retranscribe_meeting(folder)
        self.render()

    def _delete(self, folder, item):
        from ui.dialogs import confirm

        def go():
            if meetings.trash_folder(folder):
                self._status("Meeting moved to the Recycle Bin.", "#30D158")
                self._detail = None
            else:
                self._status("Couldn't delete the meeting. A file in it may be open.", "#FF453A")
            if self._page.winfo_exists():
                self.render()

        confirm(self._root, "Delete meeting",
                f"“{meetings.display_title(item)}” and its recording go to the Recycle Bin.",
                "Delete", "Cancel", go)
