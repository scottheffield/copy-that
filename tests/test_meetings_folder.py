"""Changing the meetings folder from the app, and falling back when it is unavailable."""
import queue
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def gui(monkeypatch, tmp_path):
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    saved = []
    monkeypatch.setattr(app_module, "save_config", lambda config: saved.append(dict(config)))
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config = {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path / "old")}
    gui._events = queue.Queue()
    gui._retranscribing = set()
    gui._settings_win = MagicMock(_config={"meetings_dir": gui._config["meetings_dir"]})
    gui._saved = saved
    return gui


def drain(gui, timeout=5):
    kind, action = gui._events.get(timeout=timeout)
    action()


def test_changing_the_folder_moves_meetings_and_updates_settings(gui, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "2026-11-24 1300").mkdir(parents=True)
    (old / "2026-11-24 1300" / "meta.json").write_text("{}", encoding="utf-8")
    results = []
    gui.set_meetings_dir(str(new), True, lambda *r: results.append(r))
    assert gui.meetings_busy()
    drain(gui)
    assert results == [(1, 0)] and not gui.meetings_busy()
    assert (new / "2026-11-24 1300" / "meta.json").exists()
    assert gui._config["meetings_dir"] == str(new) == gui._settings_win._config["meetings_dir"]
    assert gui._saved[-1]["meetings_dir"] == str(new)


def test_leaving_meetings_behind_only_saves_the_setting(gui, tmp_path):
    results = []
    gui.set_meetings_dir(str(tmp_path / "new"), False, lambda *r: results.append(r))
    drain(gui)
    assert results == [(0, 0)] and gui._config["meetings_dir"] == str(tmp_path / "new")


def test_unavailable_folder_falls_back_to_the_default(gui, monkeypatch, tmp_path):
    import app as app_module
    import meetings
    default = tmp_path / "default"
    monkeypatch.setitem(app_module.DEFAULT_CONFIG, "meetings_dir", str(default))
    gui._config["meetings_dir"] = "Z:\nowhere"
    roots = []

    class Session:
        def __init__(self, root, *a, **kw):
            roots.append(root)
            if root == "Z:\nowhere":
                raise OSError("drive missing")
            raise RuntimeError("stop here")  # Reached the fallback; the rest isn't under test

    monkeypatch.setattr(meetings, "MeetingSession", Session)
    gui._meeting = None
    gui._meeting_notice = MagicMock()
    gui._meeting_mic = lambda: None
    with pytest.raises(RuntimeError):
        gui._toggle_meeting()
    assert roots == ["Z:\nowhere", str(default)]
    assert gui._config["meetings_dir"] == "Z:\nowhere"  # Kept for when the drive returns
