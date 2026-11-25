"""Naming speakers, moving a line, merging and adding speakers (#44 part 2)."""
import meetings
from audio_chunks import TimedWord

META = {"title": "Call", "started": "2026-11-25T10:30:00", "duration_seconds": 60, "status": "done"}


def words(text, start, step=0.4):
    return [TimedWord(w, start + i * step) for i, w in enumerate(text.split())]


def make(tmp_path, labels=None, heard=True):
    folder = tmp_path / "2026-11-25 1030"
    folder.mkdir()
    meetings.write_meta(folder, META)
    mic = words("hi there", 0.0) + words("sounds good", 20.0)
    others = words("hello", 5.0) + words("so the plan is", 10.0) + words("yeah", 30.0)
    meetings.write_words(folder, mic, others, heard)
    meetings.write_speakers(folder, {"labels": labels or {}, "names": {}})
    meetings.rebuild_transcript(folder)
    return folder


def said(folder):
    return [(speaker, text) for _, speaker, text in meetings.transcript_lines(meetings.read_transcript(folder))]


def test_naming_a_speaker_renames_every_line_and_the_transcript(tmp_path):
    labels = {"others": ["Speaker 1", "Speaker 2", "Speaker 2", "Speaker 2", "Speaker 2", "Speaker 1"]}
    folder = make(tmp_path, labels)
    assert meetings.name_speaker(folder, "Speaker 2", "  Paul  ") == "Speaker 2"
    assert said(folder) == [("Me", "hi there"), ("Speaker 1", "hello"), ("Paul", "so the plan is"),
                            ("Me", "sounds good"), ("Speaker 1", "yeah")]
    meetings.name_speaker(folder, "Me", "Scott")
    assert [s for s, _ in said(folder)][0] == "Scott"
    assert not meetings.read_speakers(folder)["edited"]
    assert meetings.has_speaker_edits(folder)  # Paul's name would be lost on Re-transcribe


def test_a_name_another_speaker_has_merges_them(tmp_path):
    labels = {"others": ["Speaker 1", "Speaker 2", "Speaker 2", "Speaker 2", "Speaker 2", "Speaker 1"]}
    folder = make(tmp_path, labels)
    meetings.name_speaker(folder, "Speaker 1", "Paul")
    assert meetings.speaker_named(folder, "paul", besides="Speaker 2") == "Speaker 1"
    assert meetings.name_speaker(folder, "Speaker 2", "Paul") == "Speaker 1"
    assert [s for s, _ in said(folder)] == ["Me", "Paul", "Paul", "Me", "Paul"]
    assert "Speaker 2" not in meetings.read_transcript(folder)


def test_moving_one_line(tmp_path):
    folder = make(tmp_path)
    rows = meetings.speaker_rows(folder)
    yeah = next(r for r in rows if r["text"] == "yeah")
    paul = meetings.add_speaker(folder, "Paul")
    meetings.move_line(folder, yeah["track"], yeah["first"], yeah["last"], paul)
    assert said(folder)[-1] == ("Paul", "yeah")
    assert ("Others", "hello") in said(folder)
    assert meetings.read_speakers(folder)["edited"]


def test_a_mic_line_can_go_to_another_speaker(tmp_path):
    # Echo of the other side on the mic (#62) can be moved off Me.
    folder = make(tmp_path)
    line = next(r for r in meetings.speaker_rows(folder) if r["text"] == "sounds good")
    meetings.move_line(folder, "mic", line["first"], line["last"], "Others")
    assert ("Others", "sounds good") in said(folder)


def test_add_speaker_takes_the_next_free_number(tmp_path):
    folder = make(tmp_path, {"others": ["Speaker 1"] * 6})
    assert meetings.add_speaker(folder, "Dana") == "Speaker 2"
    assert meetings.add_speaker(folder, "Lee") == "Speaker 3"


def test_rows_carry_names_and_word_ranges(tmp_path):
    folder = make(tmp_path)
    meetings.name_speaker(folder, "Others", "Paul")
    rows = meetings.speaker_rows(folder)
    assert [(r["label"], r["name"]) for r in rows][:2] == [("Me", "Me"), ("Others", "Paul")]
    assert meetings.speakers_in(rows) == [("Me", "Me"), ("Others", "Paul")]
    plan = next(r for r in rows if r["text"] == "so the plan is")
    assert (plan["track"], plan["first"], plan["last"]) == ("others", 1, 4)


def test_old_meetings_without_word_times_have_no_rows(tmp_path):
    folder = tmp_path / "old"
    folder.mkdir()
    assert meetings.speaker_rows(folder) is None


def test_a_new_transcript_keeps_only_my_name(tmp_path):
    folder = make(tmp_path, {"others": ["Speaker 1"] * 6})
    meetings.name_speaker(folder, "Speaker 1", "Paul")
    meetings.name_speaker(folder, "Me", "Scott")
    meetings.reset_speakers(folder)
    assert meetings.read_speakers(folder) == {"labels": {}, "names": {"Me": "Scott"}, "edited": False}
    assert not meetings.has_speaker_edits(folder)


def test_set_my_name(tmp_path):
    folder = make(tmp_path)
    meetings.set_my_name(folder, "Scott")
    assert said(folder)[0] == ("Scott", "hi there")


def test_in_person_meeting_names_the_mic_speaker(tmp_path):
    folder = make(tmp_path, heard=False)
    assert said(folder)[0][0] == "Speaker"
    meetings.name_speaker(folder, "Speaker", "Dana")
    assert said(folder)[0] == ("Dana", "hi there")


def test_title_rename_keeps_speaker_names(tmp_path):
    folder = make(tmp_path)
    meetings.name_speaker(folder, "Others", "Paul")
    meetings.rename_meeting(folder, "Catch-up")
    assert meetings.read_transcript(folder).startswith("# Catch-up\n")
    assert ("Paul", "hello") in said(folder)


def test_app_remembers_my_name_and_puts_it_on_new_meetings(tmp_path, monkeypatch):
    import queue
    import threading
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config, gui._events, gui._closing = dict(DEFAULT_CONFIG), queue.Queue(), False
    gui._settings_win, gui._meeting = None, None
    gui._speaker_jobs, gui._speaker_folder, gui._speaker_lock = [], None, threading.Lock()
    saved, queued = [], []
    gui._save_quietly = lambda: saved.append(dict(gui._config))
    gui._find_speakers_later = queued.append
    gui._refresh_meetings = lambda: None
    gui.set_meeting_my_name("  Scott ")
    assert gui._config["meeting_my_name"] == "Scott" and len(saved) == 1
    gui.set_meeting_my_name("Scott")
    assert len(saved) == 1  # Unchanged: not saved again
    folder = make(tmp_path)
    gui._meeting_done(folder, True)
    assert said(folder)[0] == ("Scott", "hi there") and queued == [folder]
    gui.set_meeting_my_name("Me")
    assert gui._config["meeting_my_name"] == ""
