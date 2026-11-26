"""The main CopyThat window: Dictation and Meetings, plus settings grouped by task, in shared light and dark colors."""

import customtkinter as ctk
import tkinter as tk
from pathlib import Path


from ui import theme
from ui.model_cards import ModelCard

_HEADER_BG = _CONTENT_BG = theme.BG
_SECTION_TEXT = _BTN_SEC_TEXT = theme.TEXT
_DESC_TEXT = theme.MUTED
_CARD_BG = _BTN_SEC_BG = theme.SURFACE
_CARD_BORDER = _INPUT_BORDER = theme.BORDER
_INPUT_BG = theme.FIELD
_ACCENT = theme.ACCENT
_ACCENT_HOVER = theme.ACCENT_HOVER
_BTN_SEC_HOVER = theme.SELECTED


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


SETTINGS_PAGES = ("General", "Speech", "Dictionary", "Advanced")
PAGES = ("Dictation", "Meetings") + SETTINGS_PAGES
RENAMED_PAGES = {"History": "Dictation"}  # Saved by 0.3.x builds


class SettingsWindow:
    """The main window. Dictation and Meetings sit above the settings pages; Apply shows only on settings pages."""

    def __init__(self, root: ctk.CTk, config: dict, input_devices: list[dict],
                 on_apply=None, models=None, history=None, on_page_change=None, meetings=None):
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
            on_page_change: Callback(page_name) so the app can reopen at the last page.
        """
        self._root = root
        self._config = dict(config)
        self._input_devices = input_devices
        self._on_apply = on_apply
        self._models = models
        self._history = history or (lambda: [])
        self._meetings = meetings
        self._meetings_page = None
        self._on_page_change = on_page_change
        self._page = None
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
        """Open the window, or bring it forward, at page (default: the last page used)."""
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

        # Shared dropdown styling
        dd_opts = dict(
            fg_color=_INPUT_BG, button_color=_ACCENT,
            button_hover_color=_ACCENT_HOVER, text_color=_SECTION_TEXT,
            dropdown_fg_color=_INPUT_BG, dropdown_hover_color=_CARD_BG,
            dropdown_text_color=_SECTION_TEXT,
        )

        # --- Bottom bar (packed first to reserve space) ---
        bottom = ctk.CTkFrame(self._win, fg_color=_CONTENT_BG)
        bottom.pack(side="bottom", fill="x")

        # One row: the status on the left, Apply and Close on the right on settings pages.
        # Other pages have no buttons; the window's own close button closes it.
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(padx=20, pady=(6, 8), fill="x")
        self._apply_btn = ctk.CTkButton(
            btn_frame, text="Apply changes", command=self._apply, width=100,
            fg_color=_ACCENT, hover_color=_ACCENT_HOVER, text_color=theme.ON_ACCENT,
        )
        self._close_btn = ctk.CTkButton(
            btn_frame, text="Close", command=self._close, width=100,
            fg_color=_BTN_SEC_BG, hover_color=_BTN_SEC_HOVER,
            text_color=_BTN_SEC_TEXT, border_color=_CARD_BORDER, border_width=1,
        )
        self._close_btn.pack(side="right")
        self._status_label = ctk.CTkLabel(
            btn_frame, text="", font=ctk.CTkFont(size=12), height=28, anchor="w",
            text_color=_DESC_TEXT, fg_color="transparent",
        )
        self._status_label.pack(side="left", fill="x", expand=True)

        body = ctk.CTkFrame(self._win, fg_color=_CONTENT_BG)
        body.pack(fill="both", expand=True)
        sidebar = ctk.CTkFrame(body, fg_color=theme.SURFACE, width=140, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        container = ctk.CTkFrame(body, fg_color=_CONTENT_BG)
        container.pack(side="left", fill="both", expand=True)
        self._pages, self._nav_buttons = {}, {}
        for name in ("Dictation", "Meetings"):
            # Meetings scrolls inside its own page, under a fixed header.
            self._pages[name] = ctk.CTkFrame(container, fg_color=_CONTENT_BG) if name == "Meetings" else                 ctk.CTkScrollableFrame(container, fg_color=_CONTENT_BG,
                scrollbar_button_color=_CARD_BORDER, scrollbar_button_hover_color=_INPUT_BORDER)
            button = ctk.CTkButton(sidebar, text=name, anchor="w", width=116,
                height=36, fg_color="transparent", hover_color=theme.SELECTED,
                text_color=theme.TEXT, command=lambda n=name: self._select_page(n))
            button.pack(padx=12, pady=(16 if name == "Dictation" else 3, 3))
            self._nav_buttons[name] = button
        from ui.meetings_page import MeetingsPage
        self._meetings_page = MeetingsPage(
            self._pages["Meetings"], self._root,
            lambda: self._config.get("meetings_dir", str(Path.home() / "CopyThat Meetings")),
            provider=self._meetings, status=self.update_status)
        # A plain Tk frame: a CTkFrame this thin draws nothing. refresh_theme repaints it.
        self._nav_divider = tk.Frame(sidebar, height=1, bd=0, highlightthickness=0,
                                     bg=theme.color(_CARD_BORDER))
        self._nav_divider.pack(fill="x", padx=16, pady=12)
        descriptions = {
            "General": "Make yourself heard. Set up your everyday dictation.",
            "Speech": "Choose how your words become text.",
            "Dictionary": "Keep names and familiar phrases spelled your way.",
            "Advanced": "Adjust performance and local recording options.",
        }
        for name, description in descriptions.items():
            page = ctk.CTkScrollableFrame(container, fg_color=_CONTENT_BG,
                scrollbar_button_color=_CARD_BORDER,
                scrollbar_button_hover_color=_INPUT_BORDER)
            self._pages[name] = page
            button = ctk.CTkButton(sidebar, text=name, anchor="w", width=116,
                height=36, fg_color="transparent", hover_color=theme.SELECTED,
                text_color=theme.TEXT, command=lambda n=name: self._select_page(n))
            button.pack(padx=12, pady=3)
            self._nav_buttons[name] = button
            ctk.CTkLabel(page, text=name, text_color=theme.TEXT,
                font=ctk.CTkFont(size=25, weight="bold")).pack(anchor="w", padx=20, pady=(20, 2))
            ctk.CTkLabel(page, text=description, text_color=theme.MUTED,
                wraplength=460, justify="left").pack(anchor="w", padx=20, pady=(0, 12))
        scroll = self._pages["General"]
        ctk.CTkLabel(scroll, text="Appearance", text_color=theme.TEXT,
            font=ctk.CTkFont(size=13, weight="bold")).pack(**pad, anchor="w")
        self._theme_var = ctk.StringVar(value=self._config.get("theme", "system").title())
        self._theme_dropdown = ctk.CTkOptionMenu(scroll, values=["System", "Light", "Dark"],
            variable=self._theme_var, **dd_opts)
        self._theme_dropdown.pack(padx=20, pady=4, fill="x")
        ctk.CTkLabel(scroll, text="System follows Windows, including changes while the app is running.",
            text_color=theme.MUTED, wraplength=460, justify="left").pack(padx=20, anchor="w")
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
        self._lang_dropdown = ctk.CTkOptionMenu(
            scroll, values=lang_display, variable=self._lang_var,
            command=self._on_language_change, **dd_opts,
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
        self._cleanup_when_dropdown = ctk.CTkOptionMenu(
            scroll, values=list(self._cleanup_when), variable=self._cleanup_when_var, **dd_opts,
        )
        self._cleanup_when_dropdown.pack(padx=20, pady=(4, 12), fill="x")
        self._screen_var = ctk.BooleanVar(value=self._config.get("screen_context", True))
        ctk.CTkCheckBox(scroll, text="Use names on screen (file names, people) to fix spelling",
                        variable=self._screen_var, fg_color=_ACCENT, border_color=_INPUT_BORDER,
                        hover_color=_ACCENT_HOVER, text_color=_SECTION_TEXT,
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
        self._mic_dropdown = ctk.CTkOptionMenu(
            scroll, values=mic_names, variable=self._mic_var, **dd_opts,
        )
        self._mic_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        # --- Balloon font size ---
        ctk.CTkLabel(
            scroll, text="Balloon font size", fg_color="transparent",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_SECTION_TEXT,
        ).pack(**pad, anchor="w")
        font_sizes = ["12", "14", "16", "18", "20", "24"]
        self._font_size_var = ctk.StringVar(
            value=str(self._config.get("balloon_font_size", 16)))
        self._font_size_dropdown = ctk.CTkOptionMenu(
            scroll, values=font_sizes, variable=self._font_size_var, **dd_opts,
        )
        self._font_size_dropdown.pack(padx=20, pady=(4, 0), fill="x")

        # --- Recording bubble position ---
        ctk.CTkLabel(
            scroll, text="Recording bubble", fg_color="transparent",
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
        self._bubble_dropdown = ctk.CTkOptionMenu(
            scroll, values=list(self._bubble_choices), variable=self._bubble_var, **dd_opts,
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
        self._priority_dropdown = ctk.CTkOptionMenu(
            scroll, values=list(self._priority_choices), variable=self._priority_var, **dd_opts,
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
            fg_color=_ACCENT, border_color=_INPUT_BORDER,
            hover_color=_ACCENT_HOVER, text_color=_SECTION_TEXT,
            checkmark_color=theme.ON_ACCENT,
        )
        self._beep_var = ctk.BooleanVar(value=self._config.get("beep_on_start", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Sound cues",
            variable=self._beep_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._media_var = ctk.BooleanVar(value=self._config.get("pause_media", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Pause media while dictating",
            variable=self._media_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._skip_dictation_var = ctk.BooleanVar(value=self._config.get("meeting_skip_dictation", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Leave dictation out of meeting transcripts",
            variable=self._skip_dictation_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._speakers_var = ctk.BooleanVar(value=self._config.get("meeting_speakers", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Label speakers in meeting transcripts",
            variable=self._speakers_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._enter_var =ctk.BooleanVar(value=self._config.get("auto_enter", False))
        ctk.CTkCheckBox(
            toggles_frame, text="Press Enter after paste (auto-submit)",
            variable=self._enter_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._space_var = ctk.BooleanVar(value=self._config.get("append_space", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Add a space after dictation (except auto-submit)",
            variable=self._space_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._balloon_var = ctk.BooleanVar(value=self._config.get("show_balloon", True))
        ctk.CTkCheckBox(
            toggles_frame, text="Show text bubble (live and result)",
            variable=self._balloon_var, **chk_opts,
        ).pack(anchor="w", pady=2)

        self._literal_var = ctk.BooleanVar(value=self._config.get("literal_mode", False))
        self._commands_var = ctk.BooleanVar(value=self._config.get("spoken_commands", False))
        self._log_var = ctk.BooleanVar(value=self._config.get("take_log_enabled", False))
        toggles_frame = ctk.CTkFrame(self._pages["Advanced"], fg_color="transparent")
        toggles_frame.pack(padx=20, pady=18, fill="x")
        for label, variable in (
            ("Literal mode (raw recognized text)", self._literal_var),
            ("Spoken commands (new paragraph, scratch that)", self._commands_var),
            ("Save takes locally for replay (audio and text)", self._log_var),
        ):
            ctk.CTkCheckBox(toggles_frame, text=label, variable=variable, **chk_opts).pack(anchor="w", pady=2)
        from version import label as version_label
        ctk.CTkLabel(self._pages["Advanced"], text=f"CopyThat {version_label()}",
                     text_color=_DESC_TEXT).pack(padx=20, pady=(4, 18), anchor="w")
        scroll = self._pages["Dictionary"]
        ctk.CTkLabel(scroll, text="Dictionary: one spoken phrase = replacement per line",
                     text_color=_SECTION_TEXT).pack(**pad, anchor="w")
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

        last = RENAMED_PAGES.get(self._config.get("main_window_page"), self._config.get("main_window_page"))
        self._select_page(requested or (last if last in PAGES else "General"))

    def _select_page(self, name):
        name = RENAMED_PAGES.get(name, name)
        if self._meetings_page and self._page == "Meetings":
            self._meetings_page.save_pending()
        if name not in self._pages:
            name = "General"
        for key, page in self._pages.items():
            page.pack_forget()
            self._nav_buttons[key].configure(fg_color=theme.SELECTED if key == name else "transparent")
        if name == "Dictation":
            from ui.history import build_history_page
            build_history_page(self._pages[name], self._root, list(self._history()))
        elif name == "Meetings":
            self._meetings_page.render()
        if name in SETTINGS_PAGES:
            self._close_btn.pack(side="right", before=self._status_label)
            self._apply_btn.pack(side="right", padx=(8, 0), before=self._close_btn)
        else:
            self._apply_btn.pack_forget()
            self._close_btn.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        if name != self._page:
            self._page = name
            self._config["main_window_page"] = name
            if self._on_page_change:
                self._on_page_change(name)

    def _close(self):
        if self._meetings_page:
            self._meetings_page.save_pending()
        self._win.destroy()

    def refresh_meetings(self):
        """Redraw the Meetings page if it is showing, e.g. when a transcript finishes."""
        if self.is_open and self._page == "Meetings" and self._meetings_page:
            self._meetings_page.render()

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
            self._nav_divider.configure(bg=theme.color(_CARD_BORDER))

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

    def _apply(self):
        """Apply settings without closing the window."""
        try:
            new_config = self._get_new_config()
        except ValueError as error:
            self.update_status(str(error), "#FF453A")
            return
        if self._on_apply:
            self._on_apply(new_config)
