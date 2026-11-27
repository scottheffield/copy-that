"""The Meetings page of the main window: recorded meetings, grouped by day, with a detail view."""
import os
from datetime import date
from pathlib import Path

import customtkinter as ctk

import meetings
from ui import theme
from ui.transcript import TranscriptView

SEARCH_DELAY_MS = 250
STATUS_TEXT = {
    "recording": "Recording",
    "transcribing": "Transcribing…",
    "failed": "Transcription failed",
    "interrupted": "Transcript not finished",
}


# Speaker colors (light, dark), in order of who speaks first; Me uses the accent.
SPEAKER_COLORS = (("#B4541A", "#F0A35E"), ("#2E7D4F", "#7FD39B"), ("#8A3FA8", "#CB9AF0"),
                  ("#A23B5A", "#F28DAA"), ("#1F7A8C", "#7AD0E0"), ("#7A6A12", "#E3CF6A"))


def speaker_colors(speakers):
    """{label: color pair} for [(label, name)]: Me the accent, the rest from SPEAKER_COLORS."""
    colors, n = {}, 0
    for label, _name in speakers:
        if label == "Me":
            colors[label] = theme.ACCENT
        else:
            colors[label] = SPEAKER_COLORS[n % len(SPEAKER_COLORS)]
            n += 1
    return colors


MENU_PATH_WIDTH = 340  # The Folder popover's path line, in unscaled px
PROGRESS_MS = 700      # How often a transcript or speaker progress line updates
PLAY_TICK_MS = 150     # How often the player bar and the playing line update
EXPORT_POLL_MS = 300   # How often "Saving… N%" updates


ICONS = ("\uf8ae", "\uf5b0")  # Solid pause and play in the Windows icon fonts
_icon_families = {}


def _icon_family(widget):
    """The Windows icon font (Segoe Fluent Icons, or Segoe MDL2 Assets on Windows 10), or None,
    in which case the play button says Play and Pause."""
    if "found" not in _icon_families:
        import tkinter.font
        available = set(tkinter.font.families(widget))
        _icon_families["found"] = next(
            (f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in available), None)
    return _icon_families["found"]


def clock_seconds(stamp):
    """Seconds for a transcript time such as 05:03 or 1:02:03; None if it isn't one."""
    try:
        parts = [int(p) for p in str(stamp).split(":")]
    except ValueError:
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def progress_text(progress):
    """The status line for (stage, value) from meeting_progress."""
    stage, value = progress
    if stage == "transcribing":
        return f"Transcribing\u2026 {int(value * 100)}%"
    if stage == "compressing":
        return f"Compressing the audio\u2026 {int(value * 100)}%"
    # Separation has two steps: finding where speech turns fall (no progress reported,
    # so the time so far), then matching voices (a real percentage).
    if stage == "speakers":
        return f"Finding speakers, step 2 of 2\u2026 {int(value * 100)}%"
    if stage == "speakers starting":
        return f"Finding speakers, step 1 of 2\u2026 {meetings.clock(value)}"
    return "Finding speakers\u2026"


def progress_fraction(progress):
    """The bar's fraction, or None while there's no real one yet."""
    return progress[1] if progress[0] in ("transcribing", "speakers", "compressing") else None


class Spinner:
    """A small turning arc beside a status line: work is going on, with no number to show yet."""
    SIZE, STEP_MS, STEP_DEGREES = 14, 60, 24

    def __init__(self, parent):
        import tkinter as tk
        scale = ctk.ScalingTracker.get_widget_scaling(parent)
        size = round(self.SIZE * scale)
        self.widget = tk.Canvas(parent, width=size, height=size, highlightthickness=0, bd=0,
                                bg=theme.color(theme.BG))
        pad = max(2, round(2 * scale))
        self._arc = self.widget.create_arc(pad, pad, size - pad, size - pad, start=0, extent=100,
                                           style="arc", width=max(2, round(2 * scale)),
                                           outline=theme.color(theme.ACCENT))
        self._angle, self._running = 0, False

    def show(self, on):
        if on and not self._running:
            self._running = True
            self.widget.pack(side="left", padx=(8, 0))
            self._turn()
        elif not on and self._running:
            self._running = False
            self.widget.pack_forget()

    def _turn(self):
        if not self._running or not self.widget.winfo_exists():
            return
        self._angle = (self._angle - self.STEP_DEGREES) % 360
        self.widget.itemconfigure(self._arc, start=self._angle)
        self.widget.after(self.STEP_MS, self._turn)


def _shorten_path(path, font, width):
    """Fit a long folder path in width (pixels) by replacing its middle folders with an
    ellipsis, keeping the drive and the last folder, as in C:/Users/.../Clients/Meetings."""
    path = str(path)
    if font.measure(path) <= width:
        return path
    parts = path.split("\\")
    head, tail = parts[:1], parts[1:]
    while len(tail) > 1:
        tail = tail[1:]
        candidate = "\\".join(head + ["…"] + tail)
        if font.measure(candidate) <= width:
            return candidate
    last = parts[-1]
    while last and font.measure("…" + last) > width:
        last = last[1:]
    return "…" + last


class MeetingsPage:
    """Renders into page (a plain frame): a fixed header (title, Record meeting, search,
    Folder menu) over a scrolling body. provider offers retranscribe_meeting(folder),
    meeting_in_use(folder), and for the header meeting_status() -> (state, elapsed) and
    toggle_meeting(); status(text, color) reports problems in the window's status line."""

    def __init__(self, page, root, meetings_dir, provider=None, status=None):
        self._outer, self._root = page, root
        self._header = ctk.CTkFrame(page, fg_color="transparent")
        from ui.controls import ScrollPage
        self._page = ScrollPage(page, fg_color="transparent",
                                            scrollbar_button_color=theme.BORDER,
                                            scrollbar_button_hover_color=theme.MUTED)
        self._page.pack(fill="both", expand=True)
        self._transcript = None  # The detail view's TranscriptView, in place of the scrolling page
        self._record = None
        self._tick_id = None
        self._meetings_dir = meetings_dir  # Callable: the folder can change in Settings
        self._provider = provider
        self._status = status or (lambda text, color="gray60": None)
        self._detail = None  # The folder shown in the detail view, or None for the list
        self._query = ""
        self._search_job = None
        self._results = None
        self._pending = None  # Saves a title being typed when the view changes underneath it
        self._live = []       # (folder, label, parts, bar) lines showing progress
        self._progress_id = None
        self._player = None   # playback.Player for the meeting shown, created on first play (#63)
        self._controls = None  # The player bar's widgets while it is on the page
        self._play_id = None
        self._export = None   # {"folder", "fraction", "done", "error"} while audio is being saved

    # -- Shared ---------------------------------------------------------------

    def save_pending(self):
        """Save a title that is being typed. Clicking a CTk button doesn't move focus, so leaving
        the page or the meeting gives no FocusOut."""
        pending, self._pending = self._pending, None
        if pending:
            pending()

    def render(self, keep_scroll=False):
        """Rebuild whichever view is showing, e.g. after a meeting finishes.
        keep_scroll: stay where the reader was (after naming a speaker), once lines are drawn."""
        scroll_to = None
        if keep_scroll and self._transcript is not None and self._transcript.exists():
            scroll_to = self._transcript.yview()[0]
        self.save_pending()
        if self._detail is not None and not (self._detail / "meta.json").exists():
            self._detail = None  # Deleted outside the app
        if self._player is not None and self._player.folder != self._detail:
            self._close_player()  # Left the meeting
        for child in self._page.winfo_children():
            child.destroy()
        if self._transcript is not None:
            self._transcript.destroy()
            self._transcript = None
        self._controls = None
        self._results = None
        self._stop_progress()
        # Both views pin a header above the body: the list's title, Record, and search over the
        # scrolling list; a meeting's title, actions, and player (#63) over its transcript.
        self._page.pack_forget()  # Re-pack so the header sits above the body
        self._header.pack(fill="x")
        if self._detail is None:
            self._page.pack(fill="both", expand=True)
            self._render_header()
            self._render_list()
            try:
                self._page._parent_canvas.yview_moveto(0)
            except Exception:
                pass
        else:
            self._render_detail(self._detail)
            if scroll_to is not None and self._transcript is not None:
                self._transcript.text.update_idletasks()
                self._transcript.yview_moveto(scroll_to)
        self._start_progress()

    def refresh_theme(self):
        """Light or dark changed: the transcript is plain Tk, so it repaints itself."""
        if self._transcript is not None and self._transcript.exists():
            self._transcript.paint()

    def _progress(self, folder):
        getter = getattr(self._provider, "meeting_progress", None)
        return getter(folder) if getter else None

    def _show_progress(self, folder, label, parts=None, bar=None):
        """Keep label (and bar) up to date while folder transcribes or finds speakers.
        parts: text shown before the progress, joined with a dot."""
        self._live.append((folder, label, parts, bar))
        self._update_line(folder, label, parts, bar, self._progress(folder))

    @staticmethod
    def _update_line(folder, label, parts, bar, progress):
        if not label.winfo_exists() or progress is None:
            return
        text = progress_text(progress)
        label.configure(text=" \u00b7 ".join([*parts, text]) if parts else text)
        if bar is not None and bar.winfo_exists():
            fraction = progress_fraction(progress)
            percent = getattr(bar, "percent", None)
            if percent is not None and percent.winfo_exists():
                percent.configure(text="" if fraction is None else f"{int(fraction * 100)}%")
            spinner = getattr(bar, "spinner", None)
            if spinner is not None and spinner.widget.winfo_exists():
                spinner.show(fraction is None)
            bar.set(fraction or 0)  # Still: empty until there is real progress

    def _start_progress(self):
        if self._live and self._progress_id is None:
            self._progress_id = self._outer.after(PROGRESS_MS, self._tick_progress)

    def _stop_progress(self):
        if self._progress_id:
            try:
                self._outer.after_cancel(self._progress_id)
            except Exception:
                pass
        self._progress_id = None
        for _, _, _, bar in self._live:
            if bar is not None and bar.winfo_exists():
                bar.stop()
        self._live = []

    def _tick_progress(self):
        """A stage that ends leaves its line as it is; the app redraws the page when it does."""
        self._progress_id = None
        still = []
        for folder, label, parts, bar in self._live:
            if not label.winfo_exists():
                continue
            progress = self._progress(folder)
            self._update_line(folder, label, parts, bar, progress)
            if progress is not None:
                still.append((folder, label, parts, bar))
        self._live = still
        self._start_progress()

    def _in_use(self, item):
        if item["status"] in meetings.BUSY:
            return True
        return bool(self._provider and self._provider.meeting_in_use(item["folder"]))

    def _button(self, parent, text, command, primary=False, state="normal"):
        # A disabled button keeps its fill and only dims the text, so a disabled primary
        # button would be washed-out blue. Draw it outlined, like the other disabled buttons.
        options = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT) \
            if primary and state != "disabled" else dict(
                fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
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

    def _render_header(self):
        """Title and Record meeting, then search and the Folder menu. It stays put while the list scrolls."""
        for child in self._header.winfo_children():
            child.destroy()
        top = ctk.CTkFrame(self._header, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(18, 10))
        ctk.CTkLabel(top, text="Meetings", text_color=theme.TEXT,
                     font=ctk.CTkFont(size=25, weight="bold")).pack(side="left")
        self._record = None
        if self._provider and hasattr(self._provider, "toggle_meeting"):
            self._record = ctk.CTkButton(top, text="", height=34, width=0, command=self._toggle_record,
                                         font=ctk.CTkFont(size=14, weight="bold"))
            self._record.pack(side="right")
            self._update_record()
        row = ctk.CTkFrame(self._header, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 10))
        folder = ctk.CTkButton(row, text="Folder  \u25be", width=0, height=34, command=self._folder_menu,
                               fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
                               border_color=theme.BORDER, border_width=1)
        folder.pack(side="right", padx=(8, 0))
        self._folder_button = folder
        self._search = ctk.CTkEntry(row, placeholder_text="Search titles and transcripts", height=34,
                                    fg_color=theme.FIELD, border_color=theme.BORDER, text_color=theme.TEXT)
        self._search.pack(side="left", fill="x", expand=True)
        if self._query:
            self._search.insert(0, self._query)
        self._search.bind("<KeyRelease>", self._on_search)
        ctk.CTkFrame(self._header, height=1, fg_color=theme.BORDER).pack(fill="x")

    def _update_record(self):
        """The Record button follows the meeting: Record, Stop with a timer, or Finishing."""
        if self._tick_id:
            try:
                self._outer.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        button = self._record
        if button is None or not button.winfo_exists():
            return
        state, elapsed = self._provider.meeting_status()
        if state == "recording":
            button.configure(text=f"\u25a0  Stop  \u00b7  {meetings.clock(elapsed)}", state="normal",
                             fg_color=theme.ERROR, hover_color=theme.ERROR, text_color="#FFFFFF")
            self._tick_id = self._outer.after(1000, self._update_record)
        elif state == "finishing":
            button.configure(text="Finishing\u2026", state="disabled", fg_color=theme.SURFACE,
                             text_color_disabled=theme.MUTED)
            self._tick_id = self._outer.after(1000, self._update_record)
        else:
            button.configure(text="\u25cf  Record meeting", state="normal", fg_color=theme.ACCENT,
                             hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT)

    def _toggle_record(self):
        self.save_pending()
        self._provider.toggle_meeting()
        self._update_record()

    def _folder_menu(self):
        """A small popover under the Folder button: where meetings are saved, Open meetings
        folder, and Change folder. Drawn in the app's own style, right-aligned to the button
        so it never runs past the window's edge."""
        import tkinter as tk
        self._close_menu()
        button = self._folder_button
        win = tk.Toplevel(self._outer)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        self._menu = win
        # Square corners on a 1 px border-colored window: rounded ones left the window's own
        # white background showing at the corners.
        win.configure(bg=theme.color(theme.BORDER), highlightthickness=0)
        frame = ctk.CTkFrame(win, fg_color=theme.SURFACE, border_width=0, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        ctk.CTkLabel(frame, text="Saved in", text_color=theme.MUTED, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=14, pady=(10, 0))
        path_font = ctk.CTkFont(size=13)
        ctk.CTkLabel(frame, text=_shorten_path(self._meetings_dir(), path_font,
                                               MENU_PATH_WIDTH * button._get_widget_scaling()),
                     text_color=theme.TEXT, anchor="w", font=path_font).pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkFrame(frame, height=1, fg_color=theme.BORDER).pack(fill="x", padx=8)
        actions = [("Open meetings folder", self._open_root)]
        if self._provider and hasattr(self._provider, "set_meetings_dir"):
            actions.append(("Change folder\u2026", self._change_root))
        self._menu_items = []
        for label, command in actions:
            item = ctk.CTkButton(frame, text=label, anchor="w", height=32, corner_radius=6,
                                 fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.TEXT,
                                 font=ctk.CTkFont(size=14),
                                 command=lambda c=command: (self._close_menu(), c()))
            item.pack(fill="x", padx=6, pady=(4 if not self._menu_items else 0, 2))
            self._menu_items.append(item)
        ctk.CTkFrame(frame, height=4, fg_color="transparent").pack()
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = button.winfo_rootx() + button.winfo_width() - w
        y = button.winfo_rooty() + button.winfo_height() + 4
        win.geometry(f"{w}x{h}+{max(0, x)}+{y}")
        win.bind("<Escape>", lambda _e: self._close_menu())
        win.bind("<FocusOut>", lambda _e: win.after(150, self._close_if_unfocused))
        win.focus_force()

    def _close_if_unfocused(self):
        menu = getattr(self, "_menu", None)
        if menu is not None and menu.winfo_exists():
            focus = menu.focus_get()
            if focus is None or not str(focus).startswith(str(menu)):
                self._close_menu()

    def _close_menu(self):
        menu, self._menu = getattr(self, "_menu", None), None
        if menu is not None and menu.winfo_exists():
            menu.destroy()

    def _render_list(self):
        self._results = ctk.CTkFrame(self._page, fg_color="transparent")
        self._results.pack(fill="x", padx=12, pady=(4, 16))
        self._fill_results()

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
        chosen = filedialog.askdirectory(parent=self._outer.winfo_toplevel(), title="Choose the meetings folder",
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
            ctk.CTkLabel(self._results, text="No meetings yet.\nChoose Record meeting to start one.",
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
            self._row(item, meetings.snippet(item, self._query) if self._query.strip() else None)

    def _row(self, item, found=None):
        """One meeting. found: (speaker, before, hit, after) shows the transcript line that
        matched the search, with the matching words in bold."""
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
        progress = self._progress(item["folder"])
        if progress is not None:
            color = theme.WARNING
        sub = ctk.CTkLabel(text, text=detail, text_color=color, anchor="w", font=ctk.CTkFont(size=13))
        sub.pack(fill="x")
        if progress is not None:
            parts = [] if status == "transcribing" else [f"{item['started']:%I:%M %p}".lstrip("0")]
            self._show_progress(item["folder"], sub, parts)
        clickable = []
        if found:
            speaker, before, hit, after = found
            line = ctk.CTkFrame(text, fg_color="transparent", cursor="hand2")
            line.pack(fill="x", pady=(2, 0))
            clickable.append(line)
            for part, color_, weight in ((f"{speaker}: {before}", theme.MUTED, "normal"),
                                         (hit, theme.TEXT, "bold"), (after, theme.MUTED, "normal")):
                if part:
                    label = ctk.CTkLabel(line, text=part, text_color=color_, anchor="w",
                                         font=ctk.CTkFont(size=13, weight=weight))
                    label.pack(side="left")
                    clickable.append(label)
        rename.configure(command=lambda: self._rename_in_row(item, title, sub, text))

        def enter(_event):
            row.configure(fg_color=theme.SELECTED)

        def leave(event):
            # Moving between the row's own labels also fires Leave; only clear when the pointer left the row.
            x, y = row.winfo_pointerxy()
            if not (row.winfo_rootx() <= x < row.winfo_rootx() + row.winfo_width()
                    and row.winfo_rooty() <= y < row.winfo_rooty() + row.winfo_height()):
                row.configure(fg_color="transparent")

        for widget in (row, text, title, sub, duration, rename, *clickable):
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
        page, head = self._page, self._header
        for child in head.winfo_children():
            child.destroy()  # Pinned above the transcript, so the controls stay in view
        item = self._item(folder)
        if item is None:
            self._detail = None
            self._page.pack(fill="both", expand=True)
            self._render_header()
            self._render_list()
            return
        ctk.CTkButton(head, text="‹  All meetings", command=self.show_list, anchor="w", width=0, height=28,
                      fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.ACCENT,
                      font=ctk.CTkFont(size=13)).pack(anchor="w", padx=14, pady=(14, 4))
        entry = self._title_entry(head, item, 22)
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
        progress = self._progress(folder)
        if item["status"] in STATUS_TEXT and not (progress and item["status"] == "transcribing"):
            parts.append(STATUS_TEXT[item["status"]])
        line = ctk.CTkFrame(head, fg_color="transparent")
        line.pack(anchor="w", padx=20, pady=(2, 4 if progress else 12))
        meta_line = ctk.CTkLabel(line, text=" · ".join(parts), text_color=theme.MUTED,
                                 font=ctk.CTkFont(size=13))
        meta_line.pack(side="left")
        if progress is not None:
            row = ctk.CTkFrame(head, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=(4, 12))
            bar = ctk.CTkProgressBar(row, height=10, corner_radius=5, progress_color=theme.ACCENT,
                                     fg_color=theme.BORDER)
            bar.set(0)
            bar.percent = ctk.CTkLabel(row, text="", width=44, anchor="e", text_color=theme.TEXT,
                                       font=ctk.CTkFont(size=13, weight="bold"))
            bar.percent.pack(side="right", padx=(10, 0))
            bar.pack(side="left", fill="x", expand=True)
            bar.spinner = Spinner(line)
            self._show_progress(folder, meta_line, parts, bar)

        text = meetings.read_transcript(folder)
        busy = self._in_use(item)
        try:
            speakers_stopped = (meetings.read_meta(folder).get("speakers") or {}).get("status") == "interrupted"
        except (OSError, ValueError, AttributeError):
            speakers_stopped = False
        # Unfinished work isn't restarted at launch (#66); Re-transcribe is the way on.
        restart = not busy and (item["status"] == "interrupted" or speakers_stopped)
        actions = ctk.CTkFrame(head, fg_color="transparent")
        actions.pack(fill="x", padx=20)
        # Greyed while busy, so the progress bar is the only accent on the page.
        copy = self._button(actions, "Copy as Markdown", None, primary=not restart,
                            state="normal" if text and not busy else "disabled")
        copy.configure(command=lambda: self._copy(folder, copy))
        self._button(actions, "Rename", lambda: (entry.focus_set(), entry.select_range(0, "end")))
        self._button(actions, "Open transcript", lambda: self._open(folder / "transcript.md"),
                     state="normal" if text else "disabled")
        self._button(actions, "Open folder", lambda: self._open(folder))
        has_audio = meetings.has_audio(folder)
        self._button(actions, "Re-transcribe", lambda: self._retranscribe(folder), primary=restart,
                     state="disabled" if busy or not has_audio or not self._provider else "normal")
        # Playable once recorded, and not while the audio is being replaced by its Opus copy.
        playable = has_audio and item["status"] not in ("recording",) and not (
            progress and progress[0] == "compressing")
        self._button(actions, "Delete", lambda: self._delete(folder, item),
                     state="disabled" if busy else "normal")
        if playable:
            self._render_player(head, folder, item)
        ctk.CTkFrame(head, height=1, fg_color=theme.BORDER).pack(fill="x", padx=20, pady=(8, 12))

        message = {
            "recording": "Recording now. The transcript appears here after you stop.",
            "transcribing": "Transcribing… The transcript appears here when it's ready.",
            "failed": "Transcription failed. Re-transcribe to try again.",
            "interrupted": "The transcript didn't finish. Click Re-transcribe to finish it.",
        }.get(item["status"])
        if text and item["status"] == "interrupted":
            message = "The re-transcribe didn't finish. The transcript below is the previous one. "                       "Click Re-transcribe to finish it."
        elif speakers_stopped and not busy:
            message = "Finding speakers didn't finish. Click Re-transcribe to label the speakers."
        if text and item["status"] == "transcribing":
            message = "Re-transcribing… The transcript below is the previous one until this finishes."
        if text and progress and progress[0] != "transcribing":
            message = "Finding who spoke. The lines below get Speaker 1, Speaker 2 and so on when it's done."
        if not (message and (speakers_stopped or not (text and item["status"] == "done" and not progress))):
            message = None
        lines, colors = [], {}
        if text:
            rows = meetings.speaker_rows(folder) if item["status"] == "done" else None
            if rows is not None:
                # Lines from the saved word times, so a speaker can be named or a line moved (#44).
                editable = not busy
                lines = [(meetings.clock(r["start"]), r["name"], r["text"], r if editable else None, r["start"])
                         for r in rows]
                colors = speaker_colors(meetings.speakers_in(rows))
            else:
                lines = [(*line, None, clock_seconds(line[0])) for line in meetings.transcript_lines(text)]
            self._rows, self._colors = rows or [], colors
            if not lines:
                message = "No speech was recognized."
        view = TranscriptView(self._outer, on_speaker=lambda row, at: self._speaker_menu(at, folder, row),
                              on_play=self._play_from)
        view.pack(fill="both", expand=True, pady=(0, 8))
        view.show(message, lines, colors, playable=self._controls is not None)
        self._transcript = view
        if self._player is not None and (self._player.playing or self._player.position):
            self._highlight(scroll=False)

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
        renamer = getattr(self._provider, "rename_meeting_folder", None)
        if renamer:
            renamer(folder)  # The folder follows the title, now or once the meeting is idle (#57)

    def meeting_moved(self, old, new):
        """A meeting's folder was renamed (#57): keep showing it, at its new path."""
        if self._detail == Path(old):
            self._detail = Path(new)
            self._pending = None
        if self._player is not None and self._player.folder == Path(old):
            self._player.folder = Path(new)  # Paused: a rename waits while it plays
        if self._page.winfo_exists():
            self._page.after_idle(lambda: self._page.winfo_exists() and self.render(keep_scroll=True))

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
        def go():
            if self._provider:
                self._provider.retranscribe_meeting(folder)
            if self._page.winfo_exists():
                self.render()
        if meetings.has_speaker_edits(folder):
            from ui.dialogs import confirm
            confirm(self._root, "Re-transcribe",
                    "Re-transcribing finds the speakers again. The names you gave them (except yours) "
                    "and any lines you moved are lost.", "Re-transcribe", "Cancel", go)
            return
        go()

    # -- Playback (#63) -----------------------------------------------------------

    def _render_player(self, page, folder, item):
        """The player bar: play or pause, ten seconds back and forward, the position bar,
        the time, and Save audio. The player itself outlives redraws of the same meeting."""
        import playback
        if self._player is None:
            self._player = playback.Player(folder, on_release=self._released)
        player = self._player
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 4))
        family = _icon_family(bar)
        play = ctk.CTkButton(bar, text="", width=40, height=30, command=self._toggle_play,
                             fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT,
                             font=ctk.CTkFont(family=family, size=14) if family else ctk.CTkFont(size=13))
        play.pack(side="left", padx=(0, 6))
        small = dict(width=52, height=30, fg_color=theme.SURFACE, hover_color=theme.SELECTED,
                     text_color=theme.TEXT, border_color=theme.BORDER, border_width=1, font=ctk.CTkFont(size=12))
        ctk.CTkButton(bar, text="− 10s", command=lambda: self._skip(-10), **small).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="+ 10s", command=lambda: self._skip(10), **small).pack(side="left", padx=(0, 10))
        saving = self._export is not None and self._export["folder"] == folder
        save = ctk.CTkButton(bar, text=self._save_text(), command=lambda: self._save_audio(folder, item),
                             state="disabled" if saving else "normal", **{**small, "width": 0, "font": ctk.CTkFont(size=13)})
        save.pack(side="right", padx=(12, 0))
        self._save_button = save
        time_label = ctk.CTkLabel(bar, text="", text_color=theme.MUTED, font=ctk.CTkFont(size=13), anchor="e")
        time_label.pack(side="right", padx=(10, 0))
        slider = ctk.CTkSlider(bar, from_=0, to=max(1.0, player.duration), height=16,
                               button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
                               progress_color=theme.ACCENT, fg_color=theme.BORDER,
                               command=self._on_slide)
        slider.pack(side="left", fill="x", expand=True)
        slider.bind("<ButtonRelease-1>", lambda _e: self._end_slide(), add="+")
        self._controls = {"play": play, "slider": slider, "time": time_label, "sliding": False}
        self._update_controls()
        if player.playing:
            self._start_play_tick()

    def _released(self):
        """The player closed the meeting's files: a folder rename that waited can go ahead."""
        release = getattr(self._provider, "playback_released", None)
        if release:
            self._outer.after_idle(release)

    def _close_player(self):
        if self._play_id:
            try:
                self._outer.after_cancel(self._play_id)
            except Exception:
                pass
            self._play_id = None
        player, self._player = self._player, None
        if player is not None:
            player.close()

    def stop_playback(self):
        """Pause, keeping the position: the window is leaving the Meetings page."""
        if self._player is not None:
            self._player.pause()
            self._update_controls()

    def close(self):
        """The window is closing."""
        self._close_player()

    def _toggle_play(self):
        player = self._player
        if player is None:
            return
        if not player.playing and self._dictating():
            self._status("Playback waits while you dictate.", "#FF9F0A")
            return
        if not player.toggle():
            self._status(player.error, "#FF453A")
        self._update_controls()
        self._start_play_tick()

    def _play_from(self, seconds):
        player = self._player
        if player is None:
            return
        player.seek(max(0.0, seconds - 0.3))  # A moment before, so the first word isn't clipped
        if not player.playing:
            self._toggle_play()
        else:
            self._update_controls()

    def _skip(self, seconds):
        if self._player is not None:
            self._player.skip(seconds)
            self._update_controls()

    def _on_slide(self, value):
        controls = self._controls
        if controls is None or self._player is None:
            return
        controls["sliding"] = True
        controls["time"].configure(text=f"{meetings.clock(value)} / {meetings.clock(self._player.duration)}")

    def _end_slide(self):
        controls = self._controls
        if controls is None or self._player is None or not controls["sliding"]:
            return
        controls["sliding"] = False
        self._player.seek(controls["slider"].get())
        self._update_controls()

    def _dictating(self):
        dictating = getattr(self._provider, "dictating", None)
        return bool(dictating and dictating())

    def _start_play_tick(self):
        if self._play_id is None and self._player is not None and self._player.playing:
            self._play_id = self._outer.after(PLAY_TICK_MS, self._play_tick)

    def _play_tick(self):
        self._play_id = None
        player = self._player
        if player is None:
            return
        player.tick()
        if player.playing and self._dictating():
            player.pause()  # Dictation pauses other media too; the mic mustn't hear the meeting
            self._status("Playback paused for dictation.", "gray60")
        self._update_controls()
        self._start_play_tick()

    def _update_controls(self):
        controls, player = self._controls, self._player
        if controls is None or player is None or not controls["play"].winfo_exists():
            return
        icons = ICONS if _icon_family(controls["play"]) else ("Pause", "Play")
        controls["play"].configure(text=icons[0] if player.playing else icons[1])
        if not controls["sliding"]:
            controls["slider"].set(player.position)
            controls["time"].configure(text=f"{meetings.clock(player.position)} / {meetings.clock(player.duration)}")
        if player.playing or player.position:
            self._highlight()

    def _highlight(self, scroll=True):
        """Mark the line being played, and keep it in view while playing."""
        import bisect
        player, view = self._player, self._transcript
        if player is None or view is None or not view.exists() or not view.starts:
            return
        index = bisect.bisect_right([start for start, _ in view.starts], player.position + 0.35) - 1
        view.light(index if index >= 0 else None, scroll=scroll and player.playing)

    # -- Save audio (#63) ---------------------------------------------------------

    def _save_text(self):
        export = self._export
        if export is None or export["folder"] != self._detail:
            return "Save audio…"
        return f"Saving… {int(export['fraction'] * 100)}%"

    def _save_audio(self, folder, item):
        """Ask where, then write the meeting as one mixed file (MP3 or Opus) in the background."""
        import threading
        from tkinter import filedialog

        import playback
        if self._export is not None:
            return
        downloads = Path.home() / "Downloads"
        path = filedialog.asksaveasfilename(
            parent=self._outer.winfo_toplevel(), title="Save the meeting's audio",
            initialdir=str(downloads if downloads.is_dir() else Path.home()),
            initialfile=f"{folder.name}.mp3", defaultextension=".mp3",
            filetypes=[("MP3 audio (plays everywhere)", "*.mp3"), ("Opus audio (smaller)", "*.opus")])
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() not in (".mp3", ".opus"):
            path = path.with_suffix(".mp3")
        state = self._export = {"folder": folder, "fraction": 0.0, "done": False, "error": None}

        def work():
            try:
                playback.export(folder, path, report=lambda f: state.__setitem__("fraction", f))
            except Exception as error:
                state["error"] = type(error).__name__
            state["done"] = True

        threading.Thread(target=work, daemon=True, name="meeting-export").start()
        self._watch_export(path)

    def _watch_export(self, path):
        state = self._export
        if state is None:
            return
        button = getattr(self, "_save_button", None)
        if not state["done"]:
            if button is not None and button.winfo_exists():
                button.configure(text=self._save_text(), state="disabled")
            self._outer.after(EXPORT_POLL_MS, lambda: self._watch_export(path))
            return
        self._export = None
        if button is not None and button.winfo_exists():
            button.configure(text=self._save_text(), state="normal")
        if state["error"]:
            self._status(f"Couldn't save the audio ({state['error']})", "#FF453A")
        else:
            self._status(f"Audio saved: {path.name}", "#30D158")
        self._released()

    # -- Speaker menu (#44) -----------------------------------------------------

    def _speaker_menu(self, at, folder, row):
        """A popover under a line's speaker, at (x, y) on screen: search or add a speaker,
        rename this one (every line), move this line to another, or move all of this
        speaker's lines."""
        import tkinter as tk
        self._close_menu()
        win = tk.Toplevel(self._outer)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        self._menu = win
        win.configure(bg=theme.color(theme.BORDER), highlightthickness=0)
        frame = ctk.CTkFrame(win, fg_color=theme.SURFACE, border_width=0, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        speakers = meetings.speakers_in(self._rows)
        names = dict(speakers)
        current = row["label"]
        state = {"rename": False, "open": None}
        title = ctk.CTkLabel(frame, text="Move this line, or rename", text_color=theme.MUTED, anchor="w",
                             font=ctk.CTkFont(size=12))
        title.pack(fill="x", padx=12, pady=(10, 4))
        entry = ctk.CTkEntry(frame, width=260, height=32, placeholder_text="Search or add a speaker",
                             fg_color=theme.FIELD, border_color=theme.BORDER, text_color=theme.TEXT)
        entry.pack(fill="x", padx=10, pady=(0, 6))
        listing = ctk.CTkFrame(frame, fg_color="transparent")
        listing.pack(fill="x", padx=6, pady=(0, 8))
        item_font = ctk.CTkFont(size=14)

        def done(change):
            self._close_menu()
            try:
                change()
            except (OSError, ValueError, KeyError) as error:
                self._status(f"Couldn't change the speaker ({type(error).__name__})", "#FF453A")
                return
            self.render(keep_scroll=True)

        def remember_me(label, name):
            if label == "Me" and self._provider and hasattr(self._provider, "set_meeting_my_name"):
                self._provider.set_meeting_my_name(name)

        def rename(name):
            name = " ".join(name.split())
            if not name or name == names.get(current):
                self._close_menu()
                return
            other = meetings.speaker_named(folder, name, besides=current)
            if other is None:
                done(lambda: meetings.name_speaker(folder, current, name))
                remember_me(current, name)
                return
            merge_into(other, f"\u201c{names.get(other, other)}\u201d is already a speaker. ")

        def merge_into(other, why=""):
            """Every line by this speaker goes to other, after a yes: it can't be undone in one step."""
            self._close_menu()
            from ui.dialogs import confirm
            confirm(self._root, "Merge speakers",
                    f"{why}Put every line by \u201c{names.get(current, current)}\u201d under "
                    f"\u201c{names.get(other, other)}\u201d?",
                    "Merge", "Cancel", lambda: done(lambda: meetings.merge_speakers(folder, current, other)))

        def move_to(label):
            done(lambda: meetings.move_line(folder, row["track"], row["first"], row["last"], label))

        def add(name):
            def change():
                label = meetings.add_speaker(folder, name)
                meetings.move_line(folder, row["track"], row["first"], row["last"], label)
            done(change)

        def start_rename():
            state["rename"] = True
            title.configure(text=f"Rename \u201c{names.get(current, current)}\u201d on every line")
            entry.delete(0, "end")
            entry.insert(0, names.get(current, current))
            entry.select_range(0, "end")
            entry.focus_set()
            fill()

        def fill(_event=None):
            for child in listing.winfo_children():
                child.destroy()
            if state["rename"]:
                ctk.CTkLabel(listing, text="Enter to save. A name another speaker has merges the two.",
                             text_color=theme.MUTED, font=ctk.CTkFont(size=12), anchor="w",
                             justify="left", wraplength=250).pack(fill="x", padx=6)
                return
            query = " ".join(entry.get().split())
            shown = [(label, name) for label, name in speakers if query.casefold() in name.casefold()]
            if query and not any(name.casefold() == query.casefold() for _, name in speakers):
                ctk.CTkButton(listing, text=f"Add \u201c{query}\u201d", anchor="w", height=30, corner_radius=6,
                              fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.ACCENT,
                              font=item_font, command=lambda q=query: add(q)).pack(fill="x", pady=1)
            for label, name in shown:
                line = ctk.CTkFrame(listing, fg_color="transparent")
                line.pack(fill="x", pady=1)
                pick = ctk.CTkButton(line, text=f"\u25cf  {name}", anchor="w", height=30, corner_radius=6,
                                     fg_color=theme.SELECTED if label == current else "transparent",
                                     hover_color=theme.SELECTED, font=item_font,
                                     text_color=self._colors.get(label, theme.TEXT),
                                     command=(start_rename if label == current else lambda l=label: move_to(l)))
                pick.pack(side="left", fill="x", expand=True)
                if label == current:
                    ctk.CTkButton(line, text="\u270e", width=30, height=30, corner_radius=6,
                                  fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.MUTED,
                                  font=item_font, command=start_rename).pack(side="right")
                    continue
                ctk.CTkButton(line, text="\u203a", width=30, height=30, corner_radius=6,
                              fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.MUTED,
                              font=item_font,
                              command=lambda l=label: (state.__setitem__("open", None if state["open"] == l else l),
                                                       fill())).pack(side="right")
                if state["open"] == label:
                    options = ctk.CTkFrame(listing, fg_color="transparent")
                    options.pack(fill="x", padx=(22, 0))
                    for text, command in (("Just this line", lambda l=label: move_to(l)),
                                          (f"All of {names.get(current, current)}\u2019s lines",
                                           lambda l=label: merge_into(l))):
                        ctk.CTkButton(options, text=text, anchor="w", height=28, corner_radius=6,
                                      fg_color="transparent", hover_color=theme.SELECTED, text_color=theme.TEXT,
                                      font=ctk.CTkFont(size=13), command=command).pack(fill="x")
            win.update_idletasks()
            win.geometry(f"{win.winfo_reqwidth()}x{win.winfo_reqheight()}")

        def enter(_event=None):
            text = " ".join(entry.get().split())
            if state["rename"]:
                rename(text)
                return
            if not text:
                return
            match = next((label for label, name in speakers if name.casefold() == text.casefold()), None)
            move_to(match) if match else add(text)

        entry.bind("<KeyRelease>", lambda e: None if e.keysym in ("Return", "Escape") or state["rename"] else fill())
        entry.bind("<Return>", enter)
        fill()
        win.update_idletasks()
        x, y = at
        win.geometry(f"{win.winfo_reqwidth()}x{win.winfo_reqheight()}+{max(0, x)}+{y}")
        win.bind("<Escape>", lambda _e: self._close_menu())
        win.bind("<FocusOut>", lambda _e: win.after(150, self._close_if_unfocused))
        win.focus_force()
        entry.focus_set()

    def _delete(self, folder, item):
        from ui.dialogs import confirm

        def go():
            if self._player is not None and self._player.folder == folder:
                self._close_player()  # Its files must be closed to move the folder
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
