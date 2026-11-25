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
