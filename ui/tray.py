"""System tray icon manager using pystray."""

import threading
import pystray
from ui.icons import STATE_ICONS

TOOLTIP_MAX = 127   # Windows cuts a tray tooltip at 128 characters, the terminator included


def tooltip(version, status, meeting=None):
    """One line per part of the app: the name, then Dictation, then Meeting while one runs."""
    lines = [f"CopyThat {version}", f"Dictation: {status}"]
    if meeting:
        lines.append(meeting)
    text = "\n".join(lines)
    return text if len(text) <= TOOLTIP_MAX else text[:TOOLTIP_MAX - 1] + "…"


class TrayManager:
    """Manages the system tray icon and context menu."""

    def __init__(self, on_toggle, on_quit, on_settings=None, on_history=None, on_open=None,
                 on_startup=None, startup_enabled=None,
                 on_meeting=None, meeting_recording=None, on_meetings_folder=None,
                 on_meeting_mute=None, meeting_muted=None, on_panel=None, meeting_line=None):
        self._on_panel = on_panel
        self._meeting_line = meeting_line or (lambda: None)
        self._on_meeting = on_meeting
        self._on_meeting_mute = on_meeting_mute
        self._meeting_muted = meeting_muted or (lambda: False)
        self._meeting_recording = meeting_recording or (lambda: False)
        self._on_meetings_folder = on_meetings_folder
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._on_history = on_history
        self._on_open = on_open
        self._on_startup = on_startup
        self._startup_enabled = startup_enabled
        self._state = "loading"
        self.status = "Loading…"
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def _build_menu(self):
        """Build the right-click context menu with dynamic state text."""
        items = []
        if self._on_open:
            # The plain way into the main window, first as in other Windows tray menus. It is
            # also the left-click action, shown bold, unless the tray panel has left-click.
            items += [pystray.MenuItem("Open CopyThat", lambda: self._on_open(), default=not self._on_panel),
                      pystray.Menu.SEPARATOR]
        items += [
            pystray.MenuItem(
                lambda _: "Stop dictation" if self._state == "recording" else "Start dictation",
                self._on_toggle_click,
                enabled=lambda _: self._state in ("ready", "recording", "processing"),
            ),
        ]
        if self._on_meeting:
            items.append(pystray.MenuItem(
                lambda _: "Stop meeting recording" if self._meeting_recording() else "Record meeting",
                lambda: self._on_meeting()))
        if self._on_meeting_mute:
            # Only while a meeting records; checked while the mic is left out.
            items.append(pystray.MenuItem(
                "Mute my mic in the meeting", lambda: self._on_meeting_mute(),
                checked=lambda _: self._meeting_muted(), visible=lambda _: self._meeting_recording()))
        if self._on_meetings_folder:
            items.append(pystray.MenuItem("Open meetings folder", lambda: self._on_meetings_folder()))
        items.append(pystray.Menu.SEPARATOR)

        if self._on_history:
            items.append(pystray.MenuItem("Dictation history", lambda: self._on_history()))

        if self._on_settings:
            items.append(pystray.MenuItem("Settings", lambda: self._on_settings(),
                                          default=self._on_open is None))
        if self._on_panel:
            # Left-click opens the tray panel (#90); it never starts a recording. Hidden from
            # the menu, which has Open CopyThat for the main window.
            items.append(pystray.MenuItem("CopyThat panel", lambda: self._on_panel(),
                                          default=True, visible=False))
        if self._on_startup:
            items.append(pystray.MenuItem("Start with Windows", lambda: self._on_startup(),
                                          checked=lambda _: self._startup_enabled()))

        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit_click),
        ])

        return pystray.Menu(*items)

    def _on_toggle_click(self, icon=None, item=None):
        self._on_toggle()

    def _on_quit_click(self, icon=None, item=None):
        self._on_quit()

    def start(self):
        """Start the tray icon in a daemon thread."""
        initial_icon = STATE_ICONS[self._state]()
        self._icon = pystray.Icon(
            name="CopyThat",
            icon=initial_icon,
            title=self.tooltip(),
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def tooltip(self):
        from version import VERSION
        return tooltip(VERSION, self.status, self._meeting_line())

    def update_state(self, state_name: str, status: str | None = None):
        """Update the tray icon and tooltip. status is dictation's, such as "Ready";
        the meeting's line comes from meeting_line."""
        self._state = state_name
        if status:
            self.status = status

        if self._icon and self._icon.visible:
            icon_fn = STATE_ICONS.get(state_name)
            if icon_fn:
                self._icon.icon = icon_fn()
            self._icon.title = self.tooltip()
            # Force menu rebuild so dynamic text updates
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def stop(self):
        """Stop the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
