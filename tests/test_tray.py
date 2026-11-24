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
    assert "History" in visible and "Settings" in visible
