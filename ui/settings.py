"""The main CopyThat window: Meetings, Dictation and Dictionary in the sidebar, Settings pinned
below them with a tab for each group, in shared light and dark colors."""

import customtkinter as ctk
import tkinter as tk
from pathlib import Path


from ui import theme
from ui.controls import Dropdown, ScrollPage
from ui.model_cards import ModelCard

_HEADER_BG = _CONTENT_BG = theme.BG
_SECTION_TEXT = _BTN_SEC_TEXT = theme.TEXT
_DESC_TEXT = theme.MUTED
_CARD_BG = _BTN_SEC_BG = theme.SURFACE
_CARD_BORDER = _INPUT_BORDER = theme.BORDER
_INPUT_BG = theme.FIELD


def _make_wide_dropdown(om, values, on_select):
    """Patch CTkOptionMenu to show a full-width popup aligned with the field."""
    _state = {"win": None}

    def _open():
        if _state["win"] and _state["win"].winfo_exists():
            _close()
            return

        x = om.winfo_rootx()
        y = om.winfo_rooty() + om.winfo_height() + 2
        w = om.winfo_width()

        win = tk.Toplevel(om)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        _state["win"] = win

        # Size from the rendered rows: CTk scales widgets with Windows DPI,
        # but Toplevel geometry is in physical pixels.
        frame = ctk.CTkFrame(win, fg_color=_INPUT_BG,
                             border_color=_CARD_BORDER,
                             border_width=1, corner_radius=6)
        frame.pack(fill="both", expand=True)
        rows = frame
        win.update_idletasks()
        below = om.winfo_screenheight() - y - 56  # Keep clear of the taskbar
        above = om.winfo_rooty() - 8
        probe = ctk.CTkButton(frame, text="", height=28, font=ctk.CTkFont(size=13))
        probe.pack(fill="x", padx=3, pady=1)
        win.update_idletasks()
        row_h = probe.winfo_reqheight() + 2
        probe.destroy()
        needed = len(values) * row_h + 12
        room = max(below, above)
        if needed > room:
            rows = ctk.CTkScrollableFrame(frame, fg_color=_INPUT_BG)
            rows.pack(fill="both", expand=True, padx=2, pady=2)

        for val in values:
            ctk.CTkButton(
                rows, text=val, anchor="w", corner_radius=4,
                fg_color="transparent", hover_color=theme.SELECTED,
                text_color=_SECTION_TEXT, height=28,
                font=ctk.CTkFont(size=13),
                command=lambda v=val: _select(v),
            ).pack(fill="x", padx=3, pady=1)

        h = min(needed, room)
        if h > below:
            y = om.winfo_rooty() - h - 2  # Open upward when there's more room above
        win.geometry(f"{w}x{h}+{x}+{y}")

        win.grab_set()
        win.bind("<Button-1>", _on_click)
        win.bind("<Escape>", lambda e: _close())

    def _select(value):
        om.set(value)
        _close()
        if on_select:
            on_select(value)

    def _on_click(event):
        if not _state["win"]:
            return
        win = _state["win"]
        wx, wy = win.winfo_rootx(), win.winfo_rooty()
        ww, wh = win.winfo_width(), win.winfo_height()
        if not (wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh):
            _close()

    def _close():
        if _state["win"] and _state["win"].winfo_exists():
            try:
                _state["win"].grab_release()
            except Exception:
                pass
            _state["win"].destroy()
        _state["win"] = None

    om._open_dropdown_menu = _open


# Settings tabs; the keys differ from their labels where a page shares the name.
SETTINGS_TABS = {"General": "General", "Speech": "Models", "Meeting settings": "Meetings", "Advanced": "Advanced"}
APPLY_PAGES = tuple(SETTINGS_TABS) + ("Dictionary",)  # Pages that edit settings show Apply
NAV_PAGES = ("Home", "Meetings", "Dictation", "Dictionary")
PAGES = NAV_PAGES + tuple(SETTINGS_TABS)
FIRST_PAGE = "Home"  # A plain open always lands here
RENAMED_PAGES = {"History": "Dictation"}  # Saved by 0.3.x builds


class SettingsWindow:
    """The main window. Dictation and Meetings sit above the settings pages; Apply shows only on settings pages."""

    def __init__(self, root: ctk.CTk, config: dict, input_devices: list[dict],
                 on_apply=None, models=None, history=None, on_page_change=None, meetings=None, stats=None,
                 startup=None):
        """
        Args:
            root: Parent CTk window.
            config: Current config dict (will be copied, not mutated directly).
            input_devices: List of dicts with 'index' and 'name' keys.
            on_apply: Callback(new_config) called when user clicks Apply.
            models: Provides model_state, download_model, cancel_download and
                remove_model(kind, key). Without it, cards only show what is installed.
            history: Callable returning this session's takes for the Dictation page.
            meetings: Provides retranscribe_meeting(folder) and meeting_in_use(folder).
            stats: The UsageStats shown on Home; the app's reset_stats clears it.
            on_page_change: Callback(page_name) so the app can reopen at the last page.
            startup: (enabled, set_enabled) for Start with Windows. set_enabled is None
                where it can't change, such as a dev build; without it, no checkbox.
        """
        self._startup = startup
        self._root = root
        self._config = dict(config)
        self._input_devices = input_devices
        self._on_apply = on_apply
        self._models = models
        self._history = history or (lambda: [])
        self._meetings = meetings
        self._meetings_page = None
        self._home = None
        self._stats = stats
        self._on_page_change = on_page_change
        self._page = None
        # Settings reopens at the last tab, also after the window was closed.
        last = config.get("main_window_page")
        self._settings_tab = last if last in SETTINGS_TABS else next(iter(SETTINGS_TABS))
        self._win: ctk.CTkToplevel | None = None
        self._model_radio_var = ctk.StringVar(value=config.get("model_size", "base"))
        self._model_rows_frame: ctk.CTkFrame | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._apply_btn: ctk.CTkButton | None = None

    @property
    def is_open(self) -> bool:
        return self._win is not None and self._win.winfo_exists()

    @property
    def page(self):
        return self._page

    FRONT_MS = 300  # How long the window stays on top when opened, to come in front

    def _to_front(self):
        """Bring the window in front of other apps, then let it behave like any window.
        It used to stay on top for good, so Alt-Tab couldn't put anything over it."""
        win = self._win
        win.lift()
        win.attributes("-topmost", True)
        win.after(self.FRONT_MS, lambda: win.winfo_exists() and win.attributes("-topmost", False))

    def show(self, page=None):
        """Open the window at page, or at Home; an open window comes forward at page, or as it was."""
        if self._win is not None and self._win.winfo_exists():
            if page:
                self._select_page(page)
            self._win.deiconify()
            self._to_front()
            self._win.focus_force()
            return
        requested = page  # The page loop below reuses the name

        self._win = ctk.CTkToplevel(self._root)
        self._win.title("CopyThat")
        self._win.protocol("WM_DELETE_WINDOW", self._close)
        from ui import screen
        screen.place(self._win, 760, 760, 720, 620)  # Shrinks to fit a short or scaled screen
        self._win.update_idletasks()
        if screen.adopt_monitor_scaling(self._win):
            screen.place(self._win, 760, 760, 720, 620)  # Sized again at the monitor's scaling
        self._win.configure(fg_color=_CONTENT_BG)

        self._win.update_idletasks()

        # Title bar color via DWM
        theme.title_bar(self._win)

        # Window icon — set _iconbitmap_method_called to prevent CTkToplevel
        # from overriding with its default icon at 200ms
        from ui.icons import render_app_icon
        from PIL import ImageTk
        self._icon_photo = ImageTk.PhotoImage(render_app_icon(32))
        self._win._iconbitmap_method_called = True
        self._win.iconphoto(False, self._icon_photo)

        self._to_front()

        # --- Brand header ---
        header = ctk.CTkFrame(self._win, fg_color=_HEADER_BG, corner_radius=0,
                              height=48)
        header.pack(fill="x")
        header.pack_propagate(False)

        self._logo_photo = ctk.CTkImage(
            light_image=render_app_icon(22), dark_image=render_app_icon(22), size=(22, 22))
        ctk.CTkLabel(header, image=self._logo_photo, text="",
                     fg_color="transparent").pack(side="left", padx=(16, 0))
        ctk.CTkLabel(
            header, text="CopyThat", fg_color="transparent",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            header, text="Dictation and meetings, on your PC", fg_color="transparent",
            font=ctk.CTkFont(size=12), text_color=theme.MUTED,
        ).pack(side="left", padx=(12, 0))

        pad ={"padx": 20, "pady": (10, 0)}

        # --- Sidebar and content column; the sidebar runs the full height of the window ---
        body = ctk.CTkFrame(self._win, fg_color=_CONTENT_BG)
        body.pack(fill="both", expand=True)
        sidebar = ctk.CTkFrame(body, fg_color=theme.SURFACE, width=164, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._sidebar = sidebar
        container = ctk.CTkFrame(body, fg_color=_CONTENT_BG)
        container.pack(side="left", fill="both", expand=True)

        # --- Bottom bar, under the pages only (packed first to reserve space) ---
        bottom = ctk.CTkFrame(container, fg_color=_CONTENT_BG)
        bottom.pack(side="bottom", fill="x")

        # One row: the status on the left, Apply and Close on the right on settings pages.
        # Other pages have no buttons; the window's own close button closes it.
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(6, 8), fill="x")
        self._apply_btn = ctk.CTkButton(
            btn_frame, text="Apply changes", command=self._apply, width=100,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT,
        )
        self._close_btn = ctk.CTkButton(
            btn_frame, text="Close", command=self._close, width=100,
            fg_color=_BTN_SEC_BG, hover_color=theme.SELECTED,
            text_color=_BTN_SEC_TEXT, border_color=_CARD_BORDER, border_width=1,
        )
        self._close_btn.pack(side="right")
        self._status_label = ctk.CTkLabel(
            btn_frame, text="", font=ctk.CTkFont(size=12), height=28, anchor="w",
            text_color=_DESC_TEXT, fg_color="transparent",
        )
        self._status_label.pack(side="left", fill="x", expand=True)

        self._pages, self._nav_buttons = {}, {}

        def scrolling(parent):
            return ScrollPage(parent, fg_color=_CONTENT_BG,
                scrollbar_button_color=_CARD_BORDER, scrollbar_button_hover_color=_INPUT_BORDER)

        def page_title(page, title, description):
            ctk.CTkLabel(page, text=title, text_color=theme.TEXT,
                font=ctk.CTkFont(size=25, weight="bold")).pack(anchor="w", padx=20, pady=(20, 2))
            ctk.CTkLabel(page, text=description, text_color=theme.MUTED,
                wraplength=460, justify="left").pack(anchor="w", padx=20, pady=(0, 12))

        # Your things on top; Settings pinned to the bottom of the sidebar.
        for name in NAV_PAGES:
            # Meetings scrolls inside its own page, under a fixed header; the others scroll whole.
            self._pages[name] = ctk.CTkFrame(container, fg_color=_CONTENT_BG) if name == "Meetings" else scrolling(container)
            self._nav_buttons[name] = self._nav_button(sidebar, name)
            self._nav_buttons[name].pack(padx=12, pady=(16 if name == NAV_PAGES[0] else 3, 3))
        self._nav_buttons["Settings"] = self._nav_button(sidebar, "Settings")
        self._nav_buttons["Settings"].pack(side="bottom", padx=12, pady=16)
        page_title(self._pages["Dictionary"], "Dictionary", "Keep names and familiar phrases spelled your way.")
        from ui.meetings_page import MeetingsPage
        self._meetings_page = MeetingsPage(
            self._pages["Meetings"], self._root,
            lambda: self._config.get("meetings_dir", str(Path.home() / "CopyThat Meetings")),
            provider=self._meetings, status=self.update_status)
        from ui.home_page import HomePage
        self._home = HomePage(
            self._pages["Home"], self._root, self._stats, lambda: self._config,
            lambda: self._config.get("meetings_dir", str(Path.home() / "CopyThat Meetings")),
            provider=self._meetings, open_meeting=self.show_meeting,
            open_meetings=lambda: self._select_page("Meetings"))

        # Settings: one page, with a tab for each group.
        self._settings_frame = ctk.CTkFrame(container, fg_color=_CONTENT_BG)
        ctk.CTkLabel(self._settings_frame, text="Settings", text_color=theme.TEXT,
            font=ctk.CTkFont(size=25, weight="bold")).pack(anchor="w", padx=20, pady=(20, 6))
        tab_row = ctk.CTkFrame(self._settings_frame, fg_color="transparent")
        tab_row.pack(fill="x", padx=20)
        # Plain Tk frames: a CTkFrame this thin draws nothing. _paint_tabs repaints them.
        self._tab_rule = tk.Frame(self._settings_frame, height=1, bd=0, highlightthickness=0)
        self._tab_rule.pack(fill="x", padx=20)
        tab_body = ctk.CTkFrame(self._settings_frame, fg_color=_CONTENT_BG)
        tab_body.pack(fill="both", expand=True)
        self._tabs = {}
        descriptions = {
            "General": "Make yourself heard. Set up your everyday dictation.",
            "Speech": "Choose the AI that turns your voice into text and polishes it. All of it runs on this PC.",
            "Meeting settings": "Choose what goes into meeting transcripts and how audio is kept.",
            "Advanced": "Adjust performance and local recording options.",
        }
        for name, label in SETTINGS_TABS.items():
            tab = ctk.CTkFrame(tab_row, fg_color="transparent")
            tab.pack(side="left", padx=(0, 8))
            button = ctk.CTkButton(tab, text=label, width=0, height=36, fg_color="transparent",
                hover_color=theme.SELECTED, font=ctk.CTkFont(size=14, weight="bold"),
                command=lambda n=name: self._select_page(n))
            button.pack(padx=2)
            line = tk.Frame(tab, height=2, bd=0, highlightthickness=0)
            line.pack(fill="x")
            self._tabs[name] = (button, line)
            page = scrolling(tab_body)
            self._pages[name] = page
            ctk.CTkLabel(page, text=descriptions[name], text_color=theme.MUTED,
                wraplength=460, justify="left").pack(anchor="w", padx=20, pady=(14, 4))
        scroll = self._pages["General"]
        ctk.CTkLabel(scroll, text="Your name", text_color=theme.TEXT,
            font=ctk.CTkFont(size=13, weight="bold")).pack(**pad, anchor="w")
        from ui.home_page import windows_first_name
        self._name_var = ctk.StringVar(value=self._config.get("meeting_my_name", ""))
        ctk.CTkEntry(scroll, textvariable=self._name_var, placeholder_text=windows_first_name() or "Your name",
                     fg_color=_INPUT_BG, border_color=_INPUT_BORDER, text_color=_SECTION_TEXT,
                     height=30).pack(padx=20, pady=4, fill="x")
        ctk.CTkLabel(scroll, text="Home greets you by it, and new meeting transcripts use it instead of Me.",
            text_color=theme.MUTED, wraplength=460, justify="left").pack(padx=20, anchor="w")
        ctk.CTkLabel(scroll, text="Appearance", text_color=theme.TEXT,
            font=ctk.CTkFont(size=13, weight="bold")).pack(**pad, anchor="w")
        self._theme_var = ctk.StringVar(value=self._config.get("theme", "system").title())
        self._theme_dropdown = Dropdown(scroll, values=["System", "Light", "Dark"],
            variable=self._theme_var)
        self._theme_dropdown.pack(padx=20, pady=4, fill="x")
        ctk.CTkLabel(scroll, text="System follows Windows, including changes while the app is running.",
            text_color=theme.MUTED, wraplength=460, justify="left").pack(padx=20, anchor="w")
        ctk.CTkLabel(scroll, text="Highlight color", text_color=theme.TEXT,
            font=ctk.CTkFont(size=13, weight="bold")).pack(**pad, anchor="w")
        self._highlight_var = ctk.StringVar(value=self._config.get("highlight", "blue"))
        swatch_row = ctk.CTkFrame(scroll, fg_color="transparent")
        swatch_row.pack(padx=20, pady=(6, 0), anchor="w")
        self._swatches = {}
        for key, info in theme.HIGHLIGHTS.items():
            cell = ctk.CTkFrame(swatch_row, fg_color="transparent")
            cell.pack(side="left", padx=(0, 18))
            swatch = ctk.CTkButton(cell, text="", width=30, height=30, corner_radius=15, border_width=2,
                fg_color=info["ACCENT"], hover_color=info["ACCENT_HOVER"],
                command=lambda k=key: self._pick_highlight(k))
            swatch.keep_colors = True  # theme.recolor leaves the swatches alone
            swatch.pack()
            ctk.CTkLabel(cell, text=info["name"], text_color=theme.MUTED, height=18,
                font=ctk.CTkFont(size=12)).pack(pady=(2, 0))
            self._swatches[key] = swatch
        self._paint_swatches()
        scroll = self._pages["Speech"]

        # --- Language dropdown ---
        ctk.CTkLabel(
            scroll, text="Language", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")

        from dictation_engine import LANGUAGES
        self._lang_codes = list(LANGUAGES.keys())
        lang_display = list(LANGUAGES.values())
        current_lang = self._config.get("language", "en")
        current_display = LANGUAGES.get(current_lang, current_lang)

        self._lang_var = ctk.StringVar(value=current_display)
        self._lang_dropdown = Dropdown(
            scroll, values=lang_display, variable=self._lang_var,
            command=self._on_language_change,
        )
        self._lang_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        # --- Speech and polish (cleanup) share one card design ---
        ctk.CTkLabel(
            scroll, text="Speech recognition", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        ctk.CTkLabel(scroll, text="Turns your voice into words. Runs on this PC.",
                     text_color=_DESC_TEXT).pack(padx=20, anchor="w")

        self._model_rows_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._model_rows_frame.pack(padx=20, pady=(4, 0), fill="x")
        self._cards = {}

        self._build_model_list()

        from dictation_engine import CLEANUP_REGISTRY, MODEL_DIR
        saved_path = self._config.get("cleanup_model_path", "")
        current_cleanup = "off"
        if self._config.get("cleanup_enabled", False):
            current_cleanup = next((key for key, info in CLEANUP_REGISTRY.items()
                                    if Path(MODEL_DIR / info["local_dir"]).resolve() == Path(saved_path).resolve()),
                                   "custom")
        ctk.CTkLabel(scroll, text="Polish", fg_color="transparent",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT).pack(**pad, anchor="w")
        ctk.CTkLabel(scroll, text="Fixes punctuation, stutters and spoken corrections "
                     "(“Tuesday, no, Wednesday”) after you speak. Also runs on this PC.",
                     text_color=_DESC_TEXT, anchor="w", justify="left", wraplength=480).pack(padx=20, anchor="w", fill="x")
        self._cleanup_var = ctk.StringVar(value=current_cleanup)
        cleanup_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        cleanup_frame.pack(padx=20, pady=(4, 0), fill="x")
        self._card("cleanup", None, cleanup_frame, "Off", "Dictionary and filler removal only. No delay.",
                   self._cleanup_var, "off")
        for key in ("4b", "8b"):
            info = CLEANUP_REGISTRY[key]
            self._card("cleanup", key, cleanup_frame, info["name"], info["summary"],
                       self._cleanup_var, key, info["size_gb"])
        self._card("cleanup", None, cleanup_frame, "Custom model",
                   "A downloaded OpenVINO folder under the CopyThat models directory.", self._cleanup_var, "custom")
        # The gate skips the model on takes the rules already handle.
        self._cleanup_when = {
            "Only when needed (stutters, corrections, fillers)": True,
            "Every take (more accurate, about half a second slower)": False,
        }
        gate = self._config.get("cleanup_gate", True)
        self._cleanup_when_var = ctk.StringVar(
            value=next(label for label, value in self._cleanup_when.items() if value == gate))
        self._cleanup_custom_frame = ctk.CTkFrame(cleanup_frame, fg_color="transparent")
        ctk.CTkLabel(self._cleanup_custom_frame, text="Custom model folder", text_color=_SECTION_TEXT).pack(anchor="w")
        self._cleanup_path_var = ctk.StringVar(value=saved_path if current_cleanup == "custom" else "")
        ctk.CTkEntry(self._cleanup_custom_frame, textvariable=self._cleanup_path_var,
                     fg_color=_INPUT_BG, border_color=_INPUT_BORDER,
                     text_color=_SECTION_TEXT).pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(scroll, text="When to polish", fg_color="transparent",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT).pack(**pad, anchor="w")
        self._cleanup_when_dropdown = Dropdown(
            scroll, values=list(self._cleanup_when), variable=self._cleanup_when_var,
        )
        self._cleanup_when_dropdown.pack(padx=20, pady=(4, 12), fill="x")
        self._screen_var = ctk.BooleanVar(value=self._config.get("screen_context", True))
        ctk.CTkCheckBox(scroll, text="Use names on screen (file names, people) to fix spelling",
                        variable=self._screen_var, fg_color=theme.ACCENT, border_color=_INPUT_BORDER,
                        hover_color=theme.ACCENT_HOVER, text_color=_SECTION_TEXT,
                        checkmark_color=theme.ON_ACCENT).pack(padx=20, anchor="w")
        ctk.CTkLabel(scroll, text="Reads the window's text while you talk, on this PC. "
                     "Turn off on slower computers.", text_color=_DESC_TEXT, anchor="w", justify="left",
                     wraplength=480).pack(padx=20, pady=(0, 12), anchor="w", fill="x")
        self._on_cleanup_change()
        self._poll_models()

        scroll = self._pages["General"]
        # --- Hotkey ---
        ctk.CTkLabel(
            scroll, text="Hotkey", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        self._hotkey_var = ctk.StringVar(value=self._config.get("hotkey", "ctrl+win"))
        ctk.CTkLabel(scroll, text="Hold Ctrl + Win to speak. Release to finish.",
            fg_color=theme.SURFACE, text_color=theme.TEXT, corner_radius=6,
            anchor="w", padx=12, height=36).pack(padx=20, pady=4, fill="x")
        ctk.CTkLabel(scroll, text="Space while holding: hands-free · Escape: cancel",
            text_color=theme.MUTED).pack(padx=20, anchor="w")

        # --- Microphone ---
        ctk.CTkLabel(
            scroll, text="Microphone", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        mic_names = ([d.get("label", d["name"]) for d in self._input_devices]
                     if self._input_devices else ["(default)"])
        mic_names = ["(default)"] + [name for name in mic_names if name != "(default)"]
        from dictation_runtime import find_input_device
        key = self._config.get("input_device_key")
        saved = (find_input_device(self._input_devices, key) if key else
                 next((d for d in self._input_devices if d["index"] == self._config.get("input_device")), None))
        selected_mic = saved.get("label", saved["name"]) if saved else "(default)"
        self._mic_var = ctk.StringVar(value=selected_mic)
        self._mic_dropdown = Dropdown(
            scroll, values=mic_names, variable=self._mic_var,
        )
        self._mic_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        # --- Bubble text size ---
        ctk.CTkLabel(
            scroll, text="Bubble text size", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        font_sizes = ["12", "14", "16", "18", "20", "24"]
        self._font_size_var = ctk.StringVar(
            value=str(self._config.get("balloon_font_size", 16)))
        self._font_size_dropdown = Dropdown(
            scroll, values=font_sizes, variable=self._font_size_var,
        )
        self._font_size_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        # --- Bubble position ---
        ctk.CTkLabel(
            scroll, text="Bubble position", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        self._bubble_choices = {
            "At the text cursor (top of the screen when unknown)": "cursor",
            "Always at the top of the screen": "top",
        }
        position = self._config.get("bubble_position", "cursor")
        self._bubble_var = ctk.StringVar(
            value=next((label for label, value in self._bubble_choices.items() if value == position),
                       next(iter(self._bubble_choices))))
        self._bubble_dropdown = Dropdown(
            scroll, values=list(self._bubble_choices), variable=self._bubble_var,
        )
        self._bubble_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        scroll = self._pages["Advanced"]
        # --- Priority while dictating ---
        ctk.CTkLabel(
            scroll, text="Priority while dictating", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        self._priority_choices = {
            "Normal": "normal",
            "Above normal (recommended)": "above_normal",
            "High (other apps may stutter while you dictate)": "high",
        }
        level = self._config.get("dictation_priority", "above_normal")
        self._priority_var = ctk.StringVar(
            value=next((label for label, value in self._priority_choices.items() if value == level), "Normal"))
        self._priority_dropdown = Dropdown(
            scroll, values=list(self._priority_choices), variable=self._priority_var,
        )
        self._priority_dropdown.pack(padx=20, pady=(4, 0), fill="x")
        ctk.CTkLabel(scroll, text="Applies from the hotkey until the paste; normal the rest of the time.",
                     text_color=_DESC_TEXT, anchor="w", justify="left", wraplength=450,
                     ).pack(padx=20, pady=(4, 0), fill="x")

        scroll = self._pages["General"]
        # --- Toggles ---
        toggles_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        toggles_frame.pack(padx=20, pady=(10, 10), fill="x")

        chk_opts = dict(
            fg_color=theme.ACCENT, border_color=_INPUT_BORDER,
            hover_color=theme.ACCENT_HOVER, text_color=_SECTION_TEXT,
            checkmark_color=theme.ON_ACCENT,
        )
        def checkboxes(frame, rows):
            """A short label for each checkbox, with any detail on a muted line under it."""
            for label, variable, hint in rows:
                ctk.CTkCheckBox(frame, text=label, variable=variable, **chk_opts).pack(
                    anchor="w", pady=(4, 0 if hint else 4))
                if hint:
                    # Lined up with the label, past the 24 px box and its gap.
                    ctk.CTkLabel(frame, text=hint, text_color=_DESC_TEXT, font=ctk.CTkFont(size=12),
                                 anchor="w", justify="left", height=18).pack(anchor="w", padx=(30, 0), pady=(0, 4))

        self._beep_var = ctk.BooleanVar(value=self._config.get("beep_on_start", True))
        self._media_var = ctk.BooleanVar(value=self._config.get("pause_media", True))
        self._enter_var = ctk.BooleanVar(value=self._config.get("auto_enter", False))
        self._space_var = ctk.BooleanVar(value=self._config.get("append_space", True))
        self._balloon_var = ctk.BooleanVar(value=self._config.get("show_balloon", True))
        checkboxes(toggles_frame, (
            ("Play sounds for start, stop, and other key events", self._beep_var,
             "Pasting, the time-limit warning, and meeting start and stop."),
            ("Pause music and videos while dictating", self._media_var, None),
            ("Press Enter after pasting", self._enter_var, "Sends the message in chat apps."),
            ("Add a space after each dictation", self._space_var, "Not when Enter is pressed after pasting."),
            ("Show your words in the bubble", self._balloon_var, "Live while you speak, then the result."),
        ))
        self._startup_var = None
        if self._startup:
            # Read from Windows, not config: the installer can turn it on too.
            is_on, set_on = self._startup
            ctk.CTkLabel(scroll, text="Startup", text_color=theme.TEXT,
                font=ctk.CTkFont(size=13, weight="bold")).pack(padx=20, pady=(4, 0), anchor="w")
            startup_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            startup_frame.pack(padx=20, pady=(0, 4), fill="x")
            self._startup_var = ctk.BooleanVar(value=bool(is_on()))
            checkboxes(startup_frame, (
                ("Start CopyThat when Windows starts", self._startup_var,
                 "It waits in the tray, ready to dictate." if set_on else "Only in the installed app."),
            ))
            if not set_on:
                next(w for w in startup_frame.winfo_children()
                     if isinstance(w, ctk.CTkCheckBox)).configure(state="disabled")
        ctk.CTkLabel(scroll, text="Usage stats", text_color=theme.TEXT,
            font=ctk.CTkFont(size=13, weight="bold")).pack(padx=20, pady=(4, 0), anchor="w")
        stats_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        stats_frame.pack(padx=20, pady=(0, 4), fill="x")
        self._stats_var = ctk.BooleanVar(value=self._config.get("usage_stats", True))
        checkboxes(stats_frame, (
            ("Keep usage stats for Home", self._stats_var,
             "Numbers only, on this PC: words, speaking time, and days you dictated."),
        ))
        ctk.CTkButton(scroll, text="Reset stats\u2026", width=0, height=30, command=self._reset_stats,
            fg_color=_BTN_SEC_BG, hover_color=theme.SELECTED, text_color=_BTN_SEC_TEXT,
            border_color=_CARD_BORDER, border_width=1).pack(padx=20, pady=(0, 18), anchor="w")

        meeting_frame = ctk.CTkFrame(self._pages["Meeting settings"], fg_color="transparent")
        meeting_frame.pack(padx=20, pady=(10, 10), fill="x")
        self._skip_dictation_var = ctk.BooleanVar(value=self._config.get("meeting_skip_dictation", True))
        self._speakers_var = ctk.BooleanVar(value=self._config.get("meeting_speakers", True))
        self._compress_var = ctk.BooleanVar(value=self._config.get("meeting_compress_audio", True))
        checkboxes(meeting_frame, (
            ("Leave dictation out of meeting transcripts", self._skip_dictation_var, None),
            ("Label speakers in meeting transcripts", self._speakers_var, None),
            ("Compress meeting audio after transcribing", self._compress_var,
             "Several times smaller. Off keeps the original WAV files."),
        ))

        self._literal_var = ctk.BooleanVar(value=self._config.get("literal_mode", False))
        self._commands_var = ctk.BooleanVar(value=self._config.get("spoken_commands", False))
        self._log_var = ctk.BooleanVar(value=self._config.get("take_log_enabled", False))
        toggles_frame = ctk.CTkFrame(self._pages["Advanced"], fg_color="transparent")
        toggles_frame.pack(padx=20, pady=18, fill="x")
        checkboxes(toggles_frame, (
            ("Literal mode", self._literal_var, "Pastes exactly what was heard: no dictionary, rules or Polish."),
            ("Spoken commands", self._commands_var,
             "Experimental. Say “new line”, “new paragraph” or “scratch that”."),
            ("Save takes for replay", self._log_var,
             "Keeps each take's audio, text and the names read from the screen on this PC, for testing."),
        ))
        from version import label as version_label
        # The version, and which processor runs speech recognition (the tray menu used to say).
        ctk.CTkLabel(self._pages["Advanced"], text=f"CopyThat {version_label()} · {self._config.get('device', 'GPU')}",
                     text_color=_DESC_TEXT).pack(padx=20, pady=(4, 18), anchor="w")
        scroll = self._pages["Dictionary"]
        ctk.CTkLabel(scroll, text="One entry per line: what you say = how it should be written.",
                     text_color=_SECTION_TEXT).pack(**pad, anchor="w")
        ctk.CTkLabel(scroll, text="copy that = CopyThat\nacme corp = ACME Corp.", text_color=_SECTION_TEXT,
                     fg_color=theme.SURFACE, corner_radius=6, anchor="w", justify="left", padx=12,
                     font=ctk.CTkFont(family="Consolas", size=13)).pack(padx=20, pady=(6, 4), fill="x")
        ctk.CTkLabel(scroll, text="Matches whole words and ignores case. The longest match wins, "
                     "and a replacement is never replaced again.", text_color=_DESC_TEXT, anchor="w",
                     justify="left", wraplength=480).pack(padx=20, pady=(0, 8), anchor="w", fill="x")
        self._dictionary_box = ctk.CTkTextbox(
            scroll, height=280, fg_color=_INPUT_BG, text_color=_SECTION_TEXT,
            border_color=_INPUT_BORDER, border_width=1)
        self._dictionary_box.pack(padx=20, pady=4, fill="x")
        self._dictionary_box.insert("1.0", "\n".join(f"{k} = {v}" for k, v in self._config.get("dictionary", {}).items()))

        # Patch dropdowns to show full-width popups
        _make_wide_dropdown(self._lang_dropdown, lang_display,
                            self._on_language_change)
        _make_wide_dropdown(self._theme_dropdown, ["System", "Light", "Dark"], None)
        _make_wide_dropdown(self._mic_dropdown, mic_names, None)
        _make_wide_dropdown(self._font_size_dropdown, font_sizes, None)
        _make_wide_dropdown(self._cleanup_when_dropdown, list(self._cleanup_when), None)
        _make_wide_dropdown(self._priority_dropdown, list(self._priority_choices), None)
        _make_wide_dropdown(self._bubble_dropdown, list(self._bubble_choices), None)

        self._select_page(requested or FIRST_PAGE)

    def _nav_button(self, parent, name):
        """A sidebar entry with its icon. Settings opens the last tab used.
        Meetings always opens the list, even from a meeting or after leaving one open."""
        from ui.icons import GLYPHS, render_glyph
        images = {}
        for state, pair in (("normal", theme.TEXT), ("selected", theme.ACCENT)):
            light, dark = (render_glyph(GLYPHS[name], color, 40) for color in pair)
            images[state] = ctk.CTkImage(light_image=light, dark_image=dark, size=(18, 18)) if light else None
        self._nav_images = getattr(self, "_nav_images", {})
        self._nav_images[name] = images

        def click():
            if name == "Settings":
                self._select_page(self._settings_tab)
            elif name == "Meetings" and self._page == "Meetings" and self._meetings_page:
                self._meetings_page.save_pending()
                self._meetings_page.stop_playback()
                self._meetings_page.show_list()
            else:
                self._select_page(name)
        return ctk.CTkButton(parent, text=f"  {name}", image=images["normal"], compound="left",
            anchor="w", width=140, height=40, fg_color="transparent", hover_color=theme.SELECTED,
            text_color=theme.TEXT, font=ctk.CTkFont(size=14), command=click)

    def _pick_highlight(self, key):
        self._highlight_var.set(key)
        self._paint_swatches()

    def _paint_swatches(self):
        """Ring the chosen swatch; the others get a ring in the page color, so none shifts."""
        for key, swatch in self._swatches.items():
            swatch.configure(border_color=theme.TEXT if key == self._highlight_var.get() else _CONTENT_BG)

    def refresh_highlight(self):
        """The highlight color changed: redraw what theme.recolor can't reach (icons, plain Tk lines)."""
        if not self.is_open:
            return
        for name, button in self._nav_buttons.items():
            button.destroy()
        sidebar = self._sidebar
        for name in NAV_PAGES:
            self._nav_buttons[name] = self._nav_button(sidebar, name)
            self._nav_buttons[name].pack(padx=12, pady=(16 if name == NAV_PAGES[0] else 3, 3))
        self._nav_buttons["Settings"] = self._nav_button(sidebar, "Settings")
        self._nav_buttons["Settings"].pack(side="bottom", padx=12, pady=16)
        page, self._page = self._page, None  # Reselect without telling the app it changed
        self._select_page(page)

    def _paint_tabs(self):
        """Underline the open settings tab; the rule and lines are plain Tk, so repaint them by hand."""
        self._tab_rule.configure(bg=theme.color(_CARD_BORDER))
        for key, (button, line) in self._tabs.items():
            selected = key == self._page
            button.configure(text_color=theme.TEXT if selected else theme.MUTED)
            line.configure(bg=theme.color(theme.ACCENT if selected else _CONTENT_BG))

    def _select_page(self, name):
        name = RENAMED_PAGES.get(name, name)
        if name == "Settings":
            name = self._settings_tab
        if self._meetings_page and self._page == "Meetings":
            self._meetings_page.save_pending()
            if name != "Meetings":
                self._meetings_page.stop_playback()
        elif self._meetings_page and name == "Meetings":
            self._meetings_page.forget_detail()  # Coming from another page: the list, not the meeting left open
        if name not in self._pages:
            name = FIRST_PAGE
        nav = "Settings" if name in SETTINGS_TABS else name
        for page in self._pages.values():
            page.pack_forget()
        self._settings_frame.pack_forget()
        for key, button in self._nav_buttons.items():
            images = self._nav_images[key]
            button.configure(fg_color=theme.SELECTED if key == nav else "transparent",
                             image=images["selected" if key == nav else "normal"])
        if name == "Dictation":
            from ui.history import build_history_page
            build_history_page(self._pages[name], self._root, list(self._history()))
        elif name == "Meetings":
            self._meetings_page.render()
        elif name == "Home":
            self._home.render()
        if name in APPLY_PAGES:
            self._close_btn.pack(side="right", before=self._status_label)
            self._apply_btn.pack(side="right", padx=(8, 0), before=self._close_btn)
        else:
            self._apply_btn.pack_forget()
            self._close_btn.pack_forget()
        if name in SETTINGS_TABS:
            self._settings_tab = name
            self._settings_frame.pack(fill="both", expand=True)
        self._pages[name].pack(fill="both", expand=True)
        if name != self._page:
            self._page = name
            self._config["main_window_page"] = name
            if self._on_page_change:
                self._on_page_change(name)
        self._paint_tabs()

    def _close(self):
        if self._home:
            self._home.close()
        if self._meetings_page:
            self._meetings_page.save_pending()
            self._meetings_page.close()
        self._win.destroy()

    def refresh_meetings(self):
        """Redraw the Meetings page if it is showing, e.g. when a transcript finishes."""
        if self.is_open and self._page == "Meetings" and self._meetings_page:
            self._meetings_page.render()

    def refresh_home(self):
        """Redraw Home if it is showing, e.g. after a take or when a meeting starts or finishes."""
        if self.is_open and self._page == "Home" and self._home:
            self._home.render()

    def _reset_stats(self):
        from ui.dialogs import confirm

        def reset():
            if self._meetings and hasattr(self._meetings, "reset_stats"):
                self._meetings.reset_stats()
            elif self._stats is not None:
                self._stats.reset()
            self.update_status("Usage stats reset.", "#30D158")
        confirm(self._root, "Reset stats",
                "Forget every word count, your speed and your streak? This can't be undone.",
                "Reset", "Cancel", reset)

    def meeting_moved(self, old, new):
        """A meeting's folder was renamed: the Meetings page follows it."""
        if self._meetings_page:
            self._meetings_page.meeting_moved(old, new)

    def show_meeting(self, folder):
        """Open the Meetings page at one meeting's detail."""
        self.show("Meetings")
        if self._meetings_page:
            self._meetings_page.show_detail(folder)

    def refresh_theme(self):
        if self.is_open:
            theme.title_bar(self._win)
            self._paint_tabs()
            if self._meetings_page:
                self._meetings_page.refresh_theme()
            if self._home:
                self._home.refresh_theme()

    def _card(self, kind, key, parent, title, summary, variable, value, size_gb=None):
        """Add a model card. Downloadable cards (key set) track download state."""
        actions = {}
        if key is not None and self._models is not None:
            actions = dict(on_download=lambda: self._models.download_model(kind, key),
                           on_cancel=lambda: self._models.cancel_download(kind, key),
                           on_remove=lambda: self._models.remove_model(kind, key))
        on_select = self._on_cleanup_change if kind == "cleanup" else None
        card = ModelCard(parent, title, summary, size_gb, variable, value,
                         on_select=on_select, **actions).pack(fill="x", pady=3)
        if key is None:
            card.set_state("none")
        else:
            self._cards[(kind, key)] = card
        return card

    def _refresh_cards(self):
        """Show each card's download progress or installed state."""
        from dictation_engine import is_cleanup_downloaded, is_model_downloaded
        for (kind, key), card in self._cards.items():
            if self._models is not None:
                card.set_state(*self._models.model_state(kind, key))
            else:
                done = is_model_downloaded(key) if kind == "speech" else is_cleanup_downloaded(key)
                card.set_state("installed" if done else "available")

    def _poll_models(self):
        if self.is_open:
            self._refresh_cards()
            self._win.after(400, self._poll_models)

    def _on_cleanup_change(self):
        choice = self._cleanup_var.get()
        self._cleanup_when_dropdown.configure(state="disabled" if choice == "off" else "normal")
        if choice == "custom":
            self._cleanup_custom_frame.pack(fill="x", pady=(4, 0))
        else:
            self._cleanup_custom_frame.pack_forget()

    def config_changed(self, key, value):
        """The app changed a setting outside this window (a meeting rename gave "Me" a name, or
        the meetings folder moved). Keep this window's copy and any field showing it in step,
        so Apply doesn't put the old value back. An edit typed into the field is left alone."""
        old = self._config.get(key)
        self._config[key] = value
        if key == "meeting_my_name" and self.is_open and getattr(self, "_name_var", None) is not None:
            if " ".join(self._name_var.get().split()) == (old or ""):
                self._name_var.set(value)

    def update_status(self, text: str, color: str = "gray60"):
        """Update the status label (called from app.py on state changes)."""
        if self._status_label and self._win and self._win.winfo_exists():
            self._status_label.configure(text=text, text_color={"#FF453A": theme.ERROR, "#FF9F0A": theme.WARNING,
                "#30D158": theme.SUCCESS, "gray60": theme.MUTED}.get(color, color))

    def set_apply_enabled(self, enabled: bool):
        """Enable/disable the Apply button during model loading."""
        if self._apply_btn and self._win and self._win.winfo_exists():
            self._apply_btn.configure(state="normal" if enabled else "disabled")

    def _get_selected_lang_code(self) -> str:
        """Convert display name back to language code."""
        from dictation_engine import LANGUAGES
        display = self._lang_var.get()
        for code, name in LANGUAGES.items():
            if name == display:
                return code
        return "en"

    def _on_language_change(self, value: str):
        """Rebuild model list when language changes."""
        current_model = self._model_radio_var.get()
        self._build_model_list()
        from dictation_engine import get_models_for_language
        lang_code = self._get_selected_lang_code()
        available = get_models_for_language(lang_code)
        if current_model not in available:
            first = next(iter(available))
            self._model_radio_var.set(first)

    def _build_model_list(self):
        """Build/rebuild the model radio button list based on selected language."""
        from dictation_engine import get_models_for_language

        for child in self._model_rows_frame.winfo_children():
            child.destroy()
        for key in [k for k in self._cards if k[0] == "speech"]:
            self._cards.pop(key)

        models = get_models_for_language(self._get_selected_lang_code())
        # Recommended first, then Whisper from smallest to largest.
        for key in sorted(models, key=lambda k: (k != "parakeet", models[k]["size_gb"])):
            info = models[key]
            self._card("speech", key, self._model_rows_frame, info["name"], info["summary"],
                       self._model_radio_var, key, info["size_gb"])
        self._refresh_cards()

    def _get_new_config(self) -> dict:
        """Build new config dict from current UI state."""
        new_config = dict(self._config)
        new_config["theme"] = self._theme_var.get().lower()
        new_config["highlight"] = self._highlight_var.get()
        my_name = " ".join(self._name_var.get().split())[:60]
        new_config["meeting_my_name"] = "" if my_name.casefold() == "me" else my_name
        new_config["usage_stats"] = self._stats_var.get()
        new_config["model_size"] = self._model_radio_var.get()
        new_config["hotkey"] = self._hotkey_var.get().strip()
        new_config["language"] = self._get_selected_lang_code()
        new_config["beep_on_start"] = self._beep_var.get()
        new_config["auto_enter"] = self._enter_var.get()
        new_config["append_space"] = self._space_var.get()
        new_config["show_balloon"] = self._balloon_var.get()
        new_config["literal_mode"] = self._literal_var.get()
        from dictation_engine import CLEANUP_REGISTRY, MODEL_DIR
        cleanup = self._cleanup_var.get()
        new_config["cleanup_enabled"] = cleanup != "off"
        new_config["cleanup_gate"] = self._cleanup_when[self._cleanup_when_var.get()]
        new_config["dictation_priority"] = self._priority_choices[self._priority_var.get()]
        new_config["bubble_position"] = self._bubble_choices[self._bubble_var.get()]
        new_config["spoken_commands"] = self._commands_var.get()
        new_config["take_log_enabled"] = self._log_var.get()
        new_config["screen_context"] = self._screen_var.get()
        new_config["pause_media"] = self._media_var.get()
        new_config["meeting_skip_dictation"] = self._skip_dictation_var.get()
        new_config["meeting_speakers"] = self._speakers_var.get()
        new_config["meeting_compress_audio"] = self._compress_var.get()
        if cleanup == "custom":
            new_config["cleanup_model_path"] = self._cleanup_path_var.get().strip()
        elif cleanup != "off":
            new_config["cleanup_model_path"] = str(MODEL_DIR / CLEANUP_REGISTRY[cleanup]["local_dir"])
        selected = next((d for d in self._input_devices if d.get("label", d["name"]) == self._mic_var.get()), None)
        new_config["input_device"] = selected["index"] if selected else None
        new_config["input_device_key"] = selected.get("key") if selected else None
        dictionary = {}
        for line in self._dictionary_box.get("1.0", "end").splitlines():
            if line.strip():
                if "=" not in line:
                    raise ValueError("Dictionary entries need phrase = replacement")
                phrase, replacement = line.split("=", 1)
                if not phrase.strip():
                    raise ValueError("Dictionary phrase cannot be empty")
                dictionary[phrase.strip()] = replacement.strip()
        new_config["dictionary"] = dictionary
        try:
            new_config["balloon_font_size"] = int(self._font_size_var.get())
        except ValueError:
            new_config["balloon_font_size"] = 16
        from dictation_engine import MODEL_REGISTRY, launch_device
        if new_config["model_size"] != self._config["model_size"]:
            if new_config["model_size"] in MODEL_REGISTRY:
                new_config["device"] = launch_device(new_config)
            else:
                new_config["device"] = "CPU"
        return new_config

    def _apply_startup(self):
        """Turn Start with Windows on or off if the checkbox changed. False if Windows refused."""
        if self._startup_var is None or self._startup[1] is None:
            return True
        is_on, set_on = self._startup
        wanted = self._startup_var.get()
        if wanted == bool(is_on()):
            return True
        try:
            set_on(wanted)
        except OSError:
            self._startup_var.set(not wanted)
            self.update_status("Could not change Start with Windows.", "#FF453A")
            return False
        return True

    def _apply(self):
        """Apply settings without closing the window."""
        try:
            new_config = self._get_new_config()
        except ValueError as error:
            self.update_status(str(error), "#FF453A")
            return
        if not self._apply_startup():
            return
        if self._on_apply:
            self._on_apply(new_config)
