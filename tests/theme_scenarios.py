"""Theme changes must not interrupt a take or lose unsaved settings."""
from unittest.mock import patch, MagicMock

import customtkinter as ctk
import pytest

from dictation_engine import DEFAULT_CONFIG, validate_config
from ui import theme
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
        assert settings._nav_divider.cget("bg") == theme.color(theme.BORDER, mode)
        for page in settings._pages:
            settings._select_page(page)
            root.update_idletasks()
            shown = settings._pages[page]
            assert getattr(shown, "_parent_frame", shown).pack_info()["fill"] == "both"
        settings._theme_var.set(mode.title())
        settings._apply()
        assert saved[-1]["theme"] == mode
        assert saved[-1]["device"] == "CPU"
        assert saved[-1]["dictionary"]["copy that"] == "CopyThat"
        validate_config(saved[-1])
    assert saved[-1]["meeting_speakers"] is True
    settings._speakers_var.set(False)
    settings._apply()
    assert saved[-1]["meeting_speakers"] is False
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
    # On top only while it comes in front, then an ordinary window Alt-Tab can cover.
    assert window._win.attributes("-topmost")
    root.after(window.FRONT_MS + 100, root.quit)
    root.mainloop()
    assert not window._win.attributes("-topmost")
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


def test_retranscribing_meeting_shows_percent_and_explains_the_old_transcript(root, tmp_path):
    folder = make_meeting(tmp_path, "2026-11-25 1030", "2026-11-25T10:30:00", "Call",
                          lines=[("Me", "Hello there.")])
    from meetings import read_meta, write_meta
    meta = read_meta(folder)
    meta["status"] = "transcribing"
    write_meta(folder, meta)

    class Busy(FakeMeetings):
        def meeting_in_use(self, folder):
            return True

        def meeting_progress(self, folder):
            return "transcribing", 0.09

    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=Busy())
    window.show("Meetings")
    window._meetings_page.show_detail(folder)
    root.update_idletasks()
    texts = page_texts(window._pages["Meetings"])
    assert "9%" in texts and "Hello there." in texts
    assert any(t.startswith("Re-transcribing… The transcript below is the previous one") for t in texts)
    copy = [b for b in all_widgets(window._pages["Meetings"])
            if isinstance(b, ctk.CTkButton) and b.cget("text") == "Copy as Markdown"]
    assert copy and copy[0].cget("state") == "disabled"
    assert copy[0].cget("fg_color") != theme.ACCENT  # Outlined while disabled, not washed-out blue
    window._meetings_page._stop_progress()
    window._win.destroy()


def all_widgets(widget):
    for child in widget.winfo_children():
        yield child
        yield from all_widgets(child)


def test_meetings_page_empty_state(root, tmp_path):
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path / "none")}, [])
    window.show("Meetings")
    assert any(t.startswith("No meetings yet") for t in page_texts(window._pages["Meetings"]))
    window._win.destroy()


def test_meetings_page_changes_the_folder_and_moves_meetings(root, tmp_path, monkeypatch):
    from tkinter import filedialog
    import ui.dialogs
    old, new = tmp_path / "old", tmp_path / "new"
    make_meeting(old, "2026-11-24 1300", "2026-11-24T13:00:00", "Design review")

    class Provider(FakeMeetings):
        busy = False
        calls = []

        def meetings_busy(self):
            return self.busy

        def set_meetings_dir(self, folder, move, on_done):
            self.calls.append((folder, move))
            window._config["meetings_dir"] = folder
            on_done(1 if move else 0, 0)

    provider = Provider()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(old)}, [], meetings=provider)
    window.show("Meetings")
    page = window._meetings_page
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: str(new))
    choices = []
    monkeypatch.setattr(ui.dialogs, "confirm", lambda *a, on_cancel=None: choices.append((a, on_cancel)))

    provider.busy = True
    page._change_root()
    assert not provider.calls and window._status_label.cget("text").startswith("Wait until")
    provider.busy = False
    page._change_root()
    (title, message), on_cancel = choices[-1][0][1:3], choices[-1][1]
    assert title == "Move your meetings?" and message.startswith("1 meeting saved in")
    choices[-1][0][5]()  # Move
    assert provider.calls == [(str(new), True)]
    assert page._meetings_dir() == str(new)
    window._win.destroy()


def test_meetings_header_records_and_holds_the_folder_menu(root, tmp_path, monkeypatch):
    make_meeting(tmp_path, "2026-11-24 1300", "2026-11-24T13:00:00", "Design review")

    class Provider(FakeMeetings):
        state, elapsed, toggles = "idle", 0, 0

        def meeting_status(self):
            return self.state, self.elapsed

        def toggle_meeting(self):
            self.toggles += 1
            self.state, self.elapsed = ("recording", 222) if self.state == "idle" else ("finishing", 0)

        def set_meetings_dir(self, folder, move, on_done):
            pass

    provider = Provider()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=provider)
    window.show("Meetings")
    page = window._meetings_page
    # The header sits outside the scrolling body, so it stays put however long the list is.
    assert page._record.master.master is page._header
    assert "Meetings" in page_texts(page._header) and "Design review" not in page_texts(page._header)
    assert "Design review" in page_texts(page._page)
    assert page._record.cget("text").endswith("Record meeting")
    page._record.invoke()
    assert provider.toggles == 1 and page._record.cget("text").endswith("Stop  ·  03:42")
    page._record.invoke()
    assert page._record.cget("text") == "Finishing…" and page._record.cget("state") == "disabled"
    provider.state = "idle"
    page._update_record()
    assert page._record.cget("text").endswith("Record meeting")

    page._folder_menu()
    root.update_idletasks()
    texts = page_texts(page._menu)
    assert "Saved in" in texts and any(t.endswith(tmp_path.name) for t in texts)
    assert [item.cget("text") for item in page._menu_items] == ["Open meetings folder", "Change folder…"]
    page._close_menu()
    assert page._menu is None
    # The detail view drops the header; going back restores it.
    page.show_detail(tmp_path / "2026-11-24 1300")
    assert not page._header.winfo_ismapped() or not page._header.winfo_manager()
    page.show_list()
    assert page._header.winfo_manager() == "pack"
    window._win.destroy()


def test_long_meeting_folder_paths_are_shortened_in_the_middle(root):
    from ui.meetings_page import _shorten_path
    font = ctk.CTkFont(size=13)
    short = r"C:\Users\someone\CopyThat Meetings"
    assert _shorten_path(short, font, 10_000) == short
    long = r"C:\Users\someone\OneDrive - Company\Documents\Clients\Acme Corporation\2026\Meetings"
    fitted = _shorten_path(long, font, 300)
    assert fitted.split(chr(92))[:2] == ["C:", chr(0x2026)] and fitted.endswith(chr(92) + "Meetings")
    assert font.measure(fitted) <= 300
    assert font.measure(_shorten_path("x" * 400, font, 120)) <= 120


def test_unfinished_speakers_point_to_re_transcribe(root, tmp_path):
    folder = make_meeting(tmp_path, "2026-11-25 1030", "2026-11-25T10:30:00", "Call",
                          lines=[("Me", "Hello there.")])
    from meetings import read_meta, write_meta
    meta = read_meta(folder)
    meta["speakers"] = {"status": "interrupted"}
    write_meta(folder, meta)
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=FakeMeetings())
    window.show("Meetings")
    window._meetings_page.show_detail(folder)
    root.update_idletasks()
    texts = page_texts(window._pages["Meetings"])
    assert any(t.startswith("Finding speakers didn't finish") for t in texts)
    buttons = {b.cget("text"): b for b in all_widgets(window._pages["Meetings"]) if isinstance(b, ctk.CTkButton)}
    assert buttons["Re-transcribe"].cget("fg_color") == theme.ACCENT
    assert buttons["Copy as Markdown"].cget("fg_color") != theme.ACCENT
    window._win.destroy()


def test_finding_speakers_shows_a_spinner_over_a_still_bar(root, tmp_path):
    folder = make_meeting(tmp_path, "2026-11-25 1030", "2026-11-25T10:30:00", "Call",
                          lines=[("Me", "Hello there.")])
    stage = ["speakers starting", 75.0]

    class Busy(FakeMeetings):
        def meeting_in_use(self, folder):
            return True

        def meeting_progress(self, folder):
            return tuple(stage)

    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=Busy())
    window.show("Meetings")
    page = window._meetings_page
    page.show_detail(folder)
    root.update_idletasks()
    _, label, _, bar = page._live[0]
    assert label.cget("text").endswith("Finding speakers, step 1 of 2\u2026 01:15")
    assert bar.spinner.widget.winfo_manager() == "pack" and bar.get() == 0 and bar.percent.cget("text") == ""
    stage[:] = ["speakers", 0.4]
    page._tick_progress()
    assert bar.spinner.widget.winfo_manager() == "" and bar.percent.cget("text") == "40%"
    page._stop_progress()
    window._win.destroy()


def test_speaker_menu_adds_moves_and_renames(root, tmp_path):
    import meetings
    from audio_chunks import TimedWord
    folder = tmp_path / "2026-11-25 1030"
    folder.mkdir()
    meetings.write_meta(folder, {"title": "Call", "started": "2026-11-25T10:30:00",
                                 "duration_seconds": 60, "status": "done"})
    mic = [TimedWord("hi", 0.0), TimedWord("there", 0.4)]
    others = [TimedWord("hello", 5.0), TimedWord("yeah", 30.0)]
    meetings.write_words(folder, mic, others, True)
    meetings.rebuild_transcript(folder)

    class Names(FakeMeetings):
        my_name = None

        def set_meeting_my_name(self, name):
            self.my_name = name

    provider = Names()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=provider)
    window.show("Meetings")
    page = window._meetings_page
    page.show_detail(folder)
    root.update()
    assert "Others \u25be" in page_texts(window._pages["Meetings"])
    # The menu opens on the release: opened on the press, it lost focus and closed at once.
    who = next(w for w in all_widgets(window._pages["Meetings"])
               if isinstance(w, ctk.CTkLabel) and w.cget("text") == "Others \u25be")
    assert who._label.bind("<ButtonRelease-1>") and not who._label.bind("<Button-1>")

    def menu_for(text):
        row = next(r for r in page._rows if r["text"] == text)
        page._speaker_menu(page._page, folder, row)
        root.update()
        entry = next(w for w in all_widgets(page._menu) if isinstance(w, ctk.CTkEntry))
        return entry

    # Type a new name: adds the speaker and moves just this line.
    entry = menu_for("yeah")
    entry.insert(0, "Alex")
    entry._entry.event_generate("<Return>")
    root.update()
    lines = meetings.transcript_lines(meetings.read_transcript(folder))
    assert [(s, t) for _, s, t in lines] == [("Me", "hi there"), ("Others", "hello"), ("Alex", "yeah")]
    # The pencil on the current speaker renames it on every line.
    entry = menu_for("hi there")
    pencil = next(w for w in all_widgets(page._menu) if isinstance(w, ctk.CTkButton) and w.cget("text") == "\u270e")
    pencil.invoke()
    root.update()
    assert entry.get() == "Me"
    entry.delete(0, "end")
    entry.insert(0, "Scott")
    entry._entry.event_generate("<Return>")
    root.update()
    lines = meetings.transcript_lines(meetings.read_transcript(folder))
    assert lines[0][1] == "Scott" and provider.my_name == "Scott"
    page._close_menu()
    window._win.destroy()


def test_renaming_a_meeting_moves_its_folder_and_the_page_follows(root, tmp_path):
    import meetings
    folder = make_meeting(tmp_path, "2026-11-24 1300", "2026-11-24T13:00:00", "Untitled",
                          lines=[("Me", "Hello there.")])
    holder = {}

    class Renames(FakeMeetings):
        def rename_meeting_folder(self, folder):
            new = meetings.retitle_folder(folder)
            holder["window"].meeting_moved(folder, new)
            return new

    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=Renames())
    holder["window"] = window
    window.show("Meetings")
    page = window._meetings_page
    page.show_detail(folder)
    root.update()
    page._rename(folder, "Design review", {"title": "Untitled"})
    root.update()
    new = tmp_path / "2026-11-24 1300 Design review"
    assert new.exists() and not folder.exists()
    assert page._detail == new and page._title_field.get() == "Design review"
    window._win.destroy()


def test_meeting_page_plays_from_a_clicked_line_and_saves_the_audio(root, tmp_path, monkeypatch):
    """#63: the player bar, click a line to play from it, the playing line lit, Save audio."""
    import numpy as np
    import sounddevice
    from tkinter import filedialog
    import meetings
    import playback
    folder = make_meeting(tmp_path, "2026-11-24 1300", "2026-11-24T13:00:00", "Design review",
                          lines=[("Me", "First line."), ("Others", "Second line."), ("Me", "Third line.")])
    t = np.arange(4 * meetings.RATE) / meetings.RATE
    for name in ("mic", "others"):
        (folder / f"{name}.wav").unlink(missing_ok=True)
        track = meetings.WavTrack(folder / f"{name}.wav")
        track.append((0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32))
        track.close()

    class Stream:
        def __init__(self, **options):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sounddevice, "OutputStream", Stream)
    released = []

    class Provider(FakeMeetings):
        dictation = False

        def dictating(self):
            return self.dictation

        def playback_released(self):
            released.append(True)

    provider = Provider()
    window = SettingsWindow(root, {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, [], meetings=provider)
    window.show("Meetings")
    page = window._meetings_page
    page.show_detail(folder)
    root.update()
    texts = page_texts(window._pages["Meetings"])
    assert "00:00 / 00:04" in texts
    assert len(page._line_widgets) == 3
    # Clicking the second line's words plays from just before it, and lights it.
    _, _, body = page._line_widgets[1]
    page._play_from(page._line_widgets[1][0])
    assert page._player.playing and playback.holding(folder)
    assert page._player.position == 0.7
    assert page._lit == 1 and body.cget("fg_color") == theme.SELECTED
    # Dictation pauses it, keeping the place; leaving the page also pauses.
    provider.dictation = True
    page._play_tick()
    root.update()  # The release reaches the app when idle
    assert not page._player.playing and not playback.holding(folder) and released
    provider.dictation = False
    page._toggle_play()
    window._select_page("General")
    assert not page._player.playing
    window._select_page("Meetings")
    # Save audio writes one MP3.
    target = tmp_path / "saved.mp3"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **_options: str(target))
    page._save_audio(folder, {"title": "Design review"})
    for _ in range(200):
        root.update()
        if page._export is None:
            break
        import time
        time.sleep(0.02)
    assert page._export is None and target.exists()
    window._close()
    assert page._player is None and not playback.holding(folder)
