"""Theme changes must not interrupt a take or lose unsaved settings."""
from unittest.mock import patch, MagicMock

import customtkinter as ctk
import pytest

from dictation_engine import DEFAULT_CONFIG, validate_config
from ui.theme import ThemeController
from ui.settings import SettingsWindow
from ui.overlay import OverlayWindow


@pytest.fixture(scope="module")
def root():
    # A single Tcl interpreter avoids stale native CTk callbacks across roots.
    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()
    ctk.set_appearance_mode("system")


def test_follow_system_and_manual_override(root):
    changes = []
    with patch.object(ctk.AppearanceModeTracker, "detect_appearance_mode", return_value=0) as detect:
        controller = ThemeController(root, "system", changes.append)
        assert changes == ["light"]
        detect.return_value = 1
        ctk.AppearanceModeTracker.update()
        controller._refresh()
        assert changes == ["light", "dark"]
        controller.set_mode("light")
        ctk.AppearanceModeTracker.update()
        controller._refresh()
        assert changes == ["light", "dark", "light"]
        controller.set_mode("system")
        assert changes[-1] == "dark"
        controller.close()
        assert controller._timer is None


def test_settings_survive_appearance_change(root):
    saved = []
    settings = SettingsWindow(root, {**DEFAULT_CONFIG, "device": "CPU"}, [], saved.append)
    settings.show()
    settings._dictionary_box.insert("end", "\ncopy that = CopyThat")
    for mode in ("dark", "light"):
        ctk.set_appearance_mode(mode)
        settings.refresh_theme()
        for page in settings._pages:
            settings._select_page(page)
            root.update_idletasks()
            assert settings._pages[page]._parent_frame.pack_info()["fill"] == "both"
        settings._theme_var.set(mode.title())
        settings._apply()
        assert saved[-1]["theme"] == mode
        assert saved[-1]["device"] == "CPU"
        assert saved[-1]["dictionary"]["copy that"] == "CopyThat"
        validate_config(saved[-1])
    settings._win.destroy()


def test_theme_keeps_active_pill_geometry_and_state(root):
    overlay = OverlayWindow(root)
    overlay.show_recording()
    overlay.set_field(True, (400, 200, 402, 220))
    overlay.set_recording_mode("locked")
    overlay.set_live_text("Let's move the review to Thursday.")
    root.update_idletasks()
    original = (overlay._win.geometry(), overlay._rec_start,
                overlay._bubble_text, overlay._rec_hint, overlay._state)
    for mode in ("light", "dark"):
        ctk.set_appearance_mode(mode)
        overlay.set_theme(mode)
        root.update_idletasks()
        assert (overlay._win.geometry(), overlay._rec_start,
                overlay._bubble_text, overlay._rec_hint, overlay._state) == original
    overlay.show_ready()
    overlay._win.destroy()


def test_history_both_themes_and_empty_state(root):
    for entries in ([], [{"text": "Sample transcript " * 80, "raw": "raw", "duration": 3}]):
        window = SettingsWindow(root, dict(DEFAULT_CONFIG), [], history=lambda e=entries: e)
        window.show("Dictation")
        for mode in ("light", "dark"):
            ctk.set_appearance_mode(mode)
            window.refresh_theme()
            root.update_idletasks()
            assert window.is_open and window.page == "Dictation"
        window._win.destroy()


def test_one_window_apply_only_on_settings_pages(root):
    pages, entries = [], [{"text": "First take", "raw": "first take", "duration": 1}]
    window = SettingsWindow(root, dict(DEFAULT_CONFIG), [], history=lambda: entries,
                            on_page_change=pages.append)
    window.show()
    root.update_idletasks()
    assert window._win.title() == "CopyThat"
    assert window.page == "General" and window._apply_btn.winfo_manager() == "pack"
    window.show("Dictation")
    root.update_idletasks()
    assert window.page == "Dictation" and window._apply_btn.winfo_manager() == ""
    labels = [w.cget("text") for w in window._pages["Dictation"].winfo_children()[-1].winfo_children()
              if isinstance(w, ctk.CTkLabel)]
    assert "First take" in labels
    entries.append({"text": "Second take", "raw": "second take", "duration": 1})
    window._select_page("General")
    window._select_page("Dictation")
    newest = window._pages["Dictation"].winfo_children()[3]
    assert "Second take" in [w.cget("text") for w in newest.winfo_children() if isinstance(w, ctk.CTkLabel)]
    window._select_page("Dictionary")
    assert window._apply_btn.winfo_manager() == "pack"
    assert pages == ["General", "Dictation", "General", "Dictation", "Dictionary"]
    window._win.destroy()


def test_window_reopens_at_last_page(root):
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "main_window_page": "History"}, [])
    window.show()
    assert window.page == "Dictation"  # 0.3.x saved the old name
    window._win.destroy()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "main_window_page": "Gone"}, [])
    window.show()
    assert window.page == "General"
    window._win.destroy()


def test_theme_validation():
    assert DEFAULT_CONFIG["theme"] == "system"
    with pytest.raises(ValueError, match="theme"):
        validate_config({**DEFAULT_CONFIG, "theme": "automatic"})


def test_apply_theme_does_not_reload_engine():
    from app import GUIApp
    from dictation_engine import AppState
    app = GUIApp.__new__(GUIApp)
    app._config = dict(DEFAULT_CONFIG)
    app._engine = MagicMock(active=False, state=AppState.READY)
    app._overlay = MagicMock()
    app._tray = MagicMock()
    app._settings_win = None
    app._engine_started, app._offer_delete = True, []
    app._theme_controller = MagicMock()
    with patch("app.save_config") as save:
        app._on_settings_apply({**DEFAULT_CONFIG, "theme": "dark"})
        save.assert_called_once()
    app._engine.stop.assert_not_called()
    app._theme_controller.set_mode.assert_called_once_with("dark")


class FakeModels:
    """Model provider for card scenarios: parakeet and 4B installed, small downloading."""
    def __init__(self):
        self.calls = []
        self.states = {("speech", "parakeet"): ("installed", 1.0), ("cleanup", "4b"): ("removable", 1.0),
                       ("speech", "small"): ("downloading", 0.4)}

    def model_state(self, kind, key):
        return self.states.get((kind, key), ("available", 0.0))

    def download_model(self, kind, key):
        self.calls.append(("download", kind, key))

    def cancel_download(self, kind, key):
        self.calls.append(("cancel", kind, key))

    def remove_model(self, kind, key):
        self.calls.append(("remove", kind, key))


def test_model_cards_share_one_design_and_actions(root):
    models = FakeModels()
    saved = []
    settings = SettingsWindow(root, {**DEFAULT_CONFIG, "model_size": "parakeet", "device": "GPU"},
                              [], saved.append, models=models)
    settings.show()
    root.update_idletasks()
    cards = settings._cards
    assert cards[("speech", "parakeet")].state == "installed"
    assert cards[("speech", "small")].state == "downloading"
    assert cards[("speech", "base")].state == "available"
    assert cards[("cleanup", "4b")].state == "removable"
    assert cards[("cleanup", "8b")].state == "available"
    # A model that is not on disk cannot be selected.
    assert cards[("speech", "base")]._radio.cget("state") == "disabled"
    cards[("speech", "base")]._on_download()
    cards[("cleanup", "4b")]._on_remove()
    assert models.calls == [("download", "speech", "base"), ("remove", "cleanup", "4b")]
    settings._cleanup_var.set("4b")
    settings._on_cleanup_change()
    settings._apply()
    assert saved[-1]["cleanup_enabled"] is True and saved[-1]["cleanup_model_path"].endswith("Qwen3-4B-int4-ov")
    settings._cleanup_var.set("off")
    settings._apply()
    assert saved[-1]["cleanup_enabled"] is False
    settings._win.destroy()


def test_welcome_downloads_on_one_click(root):
    from ui.onboarding import WelcomeWindow
    models, started = FakeModels(), []
    models.states = {}
    welcome = WelcomeWindow(root, models, lambda: started.append(True))
    welcome.show()
    root.update_idletasks()
    assert [card.state for card in welcome._cards.values()] == ["queued", "queued"]
    welcome._primary_click()
    assert started == [True]
    models.states = {("speech", "parakeet"): ("installed", 1.0), ("cleanup", "4b"): ("downloading", 0.3)}
    welcome._poll()
    assert welcome._primary.cget("text") == "Close"
    welcome._win.destroy()


def make_meeting(root_dir, name, started, title="Untitled", status="done", lines=()):
    import json
    folder = root_dir / name
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"title": title, "started": started,
        "duration_seconds": 458.0, "status": status}), encoding="utf-8")
    (folder / "mic.wav").write_bytes(b"")
    if status == "done":
        body = "\n\n".join(f"**[00:{i:02d}] {who}:** {said}" for i, (who, said) in enumerate(lines))
        (folder / "transcript.md").write_text(f"# {title}\n\n{started[:16]} · 07:38\n\n{body}\n",
                                              encoding="utf-8")
    return folder


class FakeMeetings:
    def __init__(self):
        self.retranscribed = []

    def retranscribe_meeting(self, folder):
        self.retranscribed.append(folder)

    def meeting_in_use(self, folder):
        return False


def page_texts(widget):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkLabel):
            out.append(child.cget("text"))
        out.extend(page_texts(child))
    return out


def test_meetings_page_lists_searches_and_renames(root, tmp_path):
    review = make_meeting(tmp_path, "2026-11-24 1300", "2026-11-24T13:00:00", "Design review",
                          lines=[("Me", "Let's start with onboarding."), ("Others", "The second screen feels crowded.")])
    make_meeting(tmp_path, "2026-11-21 0815", "2026-11-21T08:15:00", status="failed")
    provider = FakeMeetings()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=provider)
    window.show("Meetings")
    root.update_idletasks()
    page = window._meetings_page
    texts = page_texts(window._pages["Meetings"])
    assert "Design review" in texts and "Untitled · 8:15 AM" in texts and "Transcription failed" in texts
    assert window._apply_btn.winfo_manager() == ""
    # Search reaches transcript text, not just titles.
    page._search.insert(0, "crowded")
    page._apply_search()
    texts = page_texts(page._results)
    assert "Design review" in texts and "Untitled · 8:15 AM" not in texts
    page._search.delete(0, "end")
    page._search.insert(0, "nothing like this")
    page._apply_search()
    assert any(t.startswith("No meetings match") for t in page_texts(page._results))
    # Detail view: preview, rename, re-transcribe.
    page.show_detail(review)
    texts = page_texts(window._pages["Meetings"])
    assert "Let's start with onboarding." in texts and "Others" in texts
    page._rename(review, "  Onboarding   review ", {"title": "Design review"})
    from meetings import read_meta, read_transcript
    assert read_meta(review)["title"] == "Onboarding review"
    assert read_transcript(review).startswith("# Onboarding review\n")
    page._retranscribe(review)
    assert provider.retranscribed == [review]
    # An untitled meeting shows an empty, outlined title field; typing then leaving saves it.
    untitled = tmp_path / "2026-11-21 0815"
    page.show_detail(untitled)
    assert page._title_field.get() == "" and page._title_field.cget("placeholder_text") == "Add a title"
    page._title_field.insert(0, "Standup")
    page._query = ""  # Clear the earlier search
    page.show_list()
    assert read_meta(untitled)["title"] == "Standup"
    # Rename from the list without opening the meeting.
    def widgets(parent, kind):
        found = []
        for child in parent.winfo_children():
            if isinstance(child, kind):
                found.append(child)
            found.extend(widgets(child, kind))
        return found
    renames = [b for b in widgets(page._results, ctk.CTkButton) if b.cget("text") == "Rename"]
    assert len(renames) == 2
    standup = next(b for b in renames if "Standup" in page_texts(b.master))
    standup.invoke()
    field = widgets(standup.master, ctk.CTkEntry)[0]
    assert field.get() == "Standup"
    field.delete(0, "end")
    field.insert(0, "Daily standup")
    page.save_pending()
    assert read_meta(untitled)["title"] == "Daily standup"
    page.show_list()
    for mode in ("dark", "light"):
        ctk.set_appearance_mode(mode)
        root.update_idletasks()
    window._win.destroy()


def test_meetings_page_empty_state(root, tmp_path):
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path / "none")}, [])
    window.show("Meetings")
    assert any(t.startswith("No meetings yet") for t in page_texts(window._pages["Meetings"]))
    window._win.destroy()
