from ui.tray import TrayManager


def test_left_click_opens_settings_not_recording():
    opened = []
    tray = TrayManager(on_toggle=lambda: opened.append("toggle"), on_quit=lambda: None,
                       on_settings=lambda: opened.append("settings"))
    defaults = [item for item in tray._build_menu().items if item.default]
    assert [item.text for item in defaults] == ["Settings"]
    defaults[0](None)
    assert opened == ["settings"]


def test_left_click_opens_main_window_at_last_page():
    opened = []
    tray = TrayManager(on_toggle=lambda: opened.append("toggle"), on_quit=lambda: None,
                       on_settings=lambda: opened.append("settings"),
                       on_history=lambda: opened.append("history"),
                       on_open=lambda: opened.append("open"))
    menu = tray._build_menu()
    defaults = [item for item in menu.items if item.default]
    assert [item.text for item in defaults] == ["Open CopyThat"]
    assert not defaults[0].visible
    menu(None)  # What pystray runs on a left-click
    assert opened == ["open"]
    visible = [item.text for item in menu.items if item.visible]
    assert "Dictation history" in visible and "Settings" in visible


def test_mute_item_shows_only_during_a_meeting_and_is_checked_when_muted():
    state = {"recording": False, "muted": False}
    toggled = []
    tray = TrayManager(on_toggle=lambda: None, on_quit=lambda: None, on_meeting=lambda: None,
                       meeting_recording=lambda: state["recording"],
                       on_meeting_mute=lambda: toggled.append(True), meeting_muted=lambda: state["muted"])
    mute = next(item for item in tray._build_menu().items if item.text == "Mute my mic in the meeting")
    assert not mute.visible
    state["recording"] = True
    assert mute.visible and not mute.checked
    state["muted"] = True
    assert mute.checked
    mute(None)
    assert toggled == [True]


def test_left_click_opens_the_tray_panel_when_there_is_one():
    opened = []
    tray = TrayManager(on_toggle=lambda: opened.append("toggle"), on_quit=lambda: None,
                       on_open=lambda: opened.append("open"), on_panel=lambda: opened.append("panel"))
    menu = tray._build_menu()
    defaults = [item for item in menu.items if item.default]
    assert len(defaults) == 1 and not defaults[0].visible
    menu(None)  # What pystray runs on a left-click
    assert opened == ["panel"]


def test_tooltip_has_a_line_per_part_and_fits_windows():
    from ui.tray import TOOLTIP_MAX, tooltip
    assert tooltip("0.4.1", "Ready") == "CopyThat 0.4.1\nDictation: Ready"
    assert tooltip("0.4.1", "Transcribing…", "Meeting: recording since 9:02 AM") == \
        "CopyThat 0.4.1\nDictation: Transcribing…\nMeeting: recording since 9:02 AM"
    long = tooltip("0.4.1", "Error: " + "x" * 60, "Meeting: recording since 10:02 AM, mic muted" + "y" * 40)
    assert len(long) == TOOLTIP_MAX and long.endswith("…")


def test_tooltip_follows_the_meeting():
    line = {"text": None}
    tray = TrayManager(on_toggle=lambda: None, on_quit=lambda: None, meeting_line=lambda: line["text"])
    tray.update_state("ready", "Ready")
    assert tray.tooltip().endswith("Dictation: Ready")
    line["text"] = "Meeting: recording since 9:02 AM"
    assert tray.tooltip().splitlines()[-1] == "Meeting: recording since 9:02 AM"
