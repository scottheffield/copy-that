"""Meeting folders carry their titles (#57)."""
import queue
import threading

import meetings

META = {"title": "Untitled", "started": "2026-11-24T20:13:00", "duration_seconds": 60, "status": "done"}


def make(tmp_path, name="2026-11-24 2013", title="Untitled"):
    folder = tmp_path / name
    folder.mkdir()
    meetings.write_meta(folder, {**META, "title": title})
    return folder


def test_folder_title_is_safe_for_windows():
    assert meetings.folder_title('Q4: "Plan" / review?') == "Q4 Plan review"
    assert meetings.folder_title("Notes...  ") == "Notes"
    assert meetings.folder_title("a\tb\nc") == "a b c"
    assert len(meetings.folder_title("x" * 200)) == meetings.FOLDER_TITLE_LENGTH


def test_name_keeps_the_date_first_and_untitled_stays_bare():
    assert meetings.titled_name({**META, "title": "Panel discussion"}) == "2026-11-24 2013 Panel discussion"
    assert meetings.titled_name(META) == "2026-11-24 2013"
    assert meetings.titled_name({**META, "title": "???"}) == "2026-11-24 2013"


def test_retitle_renames_and_a_second_rename_replaces_the_title(tmp_path):
    folder = make(tmp_path)
    meetings.rename_meeting(folder, "Panel discussion")
    folder = meetings.retitle_folder(folder)
    assert folder.name == "2026-11-24 2013 Panel discussion" and (folder / "meta.json").exists()
    meetings.rename_meeting(folder, "Panel recap")
    folder = meetings.retitle_folder(folder)
    assert folder.name == "2026-11-24 2013 Panel recap"
    assert [p.name for p in tmp_path.iterdir()] == ["2026-11-24 2013 Panel recap"]


def test_two_meetings_with_one_title_do_not_collide(tmp_path):
    first = meetings.retitle_folder(make(tmp_path, title="Standup"))
    second = meetings.retitle_folder(make(tmp_path, "2026-11-24 2013 (2)", title="Standup"))
    assert first.name == "2026-11-24 2013 Standup" and second.name == "2026-11-24 2013 Standup (2)"
    assert meetings.retitle_folder(second) == second  # Already right: left alone


def bare_app(tmp_path):
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config, gui._events, gui._closing = {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}, queue.Queue(), False
    gui._settings_win, gui._meeting = None, None
    gui._retranscribing, gui._folder_renames = set(), set()
    gui._speaker_jobs, gui._speaker_folder, gui._speaker_lock = [], None, threading.Lock()
    gui.statuses = []
    gui._settings_status = lambda text, color: gui.statuses.append(text)
    return gui


def test_app_waits_until_the_meeting_is_idle(tmp_path):
    gui = bare_app(tmp_path)
    folder = make(tmp_path, title="Panel")
    gui._speaker_folder = folder  # Speakers are being found: its files are open
    assert gui.rename_meeting_folder(folder) == folder and folder.exists()
    gui._refresh_meetings()
    assert folder.exists()
    gui._speaker_folder = None
    gui._refresh_meetings()
    assert not folder.exists() and (tmp_path / "2026-11-24 2013 Panel").exists()
    assert gui._folder_renames == set()


def test_app_moves_the_current_meeting_and_the_page_along(tmp_path):
    from types import SimpleNamespace
    gui = bare_app(tmp_path)
    folder = make(tmp_path, title="Panel")
    done = threading.Event()
    done.set()
    gui._meeting = SimpleNamespace(folder=folder, finished=done)
    moved = []
    gui._settings_win = SimpleNamespace(meeting_moved=lambda a, b: moved.append((a.name, b.name)),
                                        refresh_meetings=lambda: None)
    new = gui.rename_meeting_folder(folder)
    assert gui._meeting.folder == new and moved == [("2026-11-24 2013", "2026-11-24 2013 Panel")]


def test_a_refused_rename_keeps_the_folder_and_says_so(tmp_path, monkeypatch):
    gui = bare_app(tmp_path)
    folder = make(tmp_path, title="Panel")
    def refuse(_folder):
        raise PermissionError("in use")
    monkeypatch.setattr(meetings, "retitle_folder", refuse)
    assert gui.rename_meeting_folder(folder) == folder and folder.exists()
    assert gui.statuses and "not its folder" in gui.statuses[0]
