import json
import wave

import numpy as np
import pytest

import meetings
import speakers
from audio_chunks import TimedWord

META = {"title": "Panel", "started": "2026-11-24T16:30:00", "duration_seconds": 30, "status": "done"}


@pytest.fixture(autouse=True)
def short_turns_count(monkeypatch):
    """These tests use turns of a few seconds; only the stray tests use the real minimum."""
    monkeypatch.setattr(speakers, "MIN_SPEAKER_SECONDS", 0.0)


def words(text, start, step=0.4):
    return [TimedWord(w, start + i * step) for i, w in enumerate(text.split())]


def make_meeting(tmp_path, mic, others, heard=True, meta=META):
    folder = tmp_path / "2026-11-24 1630"
    folder.mkdir()
    meetings.write_meta(folder, {**meta, "speakers": {"status": "pending"}})
    meetings.write_words(folder, mic, others, heard)
    for name in ("mic.wav", "others.wav"):
        with wave.open(str(folder / name), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            w.writeframes(np.zeros(16000, dtype=np.int16).tobytes())
    (folder / "transcript.md").write_text(meetings.render_transcript(meta, mic, others, heard), encoding="utf-8")
    return folder


def test_assign_picks_the_speaker_talking_at_each_word():
    segments = [(0.0, 2.0, 5), (2.5, 4.0, 3)]
    assert speakers.assign([0.5, 1.9, 3.0], segments) == [5, 5, 3]


def test_assign_keeps_the_current_speaker_through_an_overlap():
    segments = [(0.0, 5.0, 1), (2.0, 2.5, 2)]
    assert speakers.assign([1.0, 2.2], segments) == [1, 1]
    # A new voice over a long turn: the short interjection wins.
    assert speakers.assign([2.2], segments) == [2]


def test_assign_fills_words_no_segment_covers():
    segments = [(1.0, 2.0, 7)]
    assert speakers.assign([0.5, 2.6, 9.0], segments) == [7, 7, 7]
    assert speakers.assign([1.0], []) == [None]


def test_number_counts_by_first_appearance():
    assert speakers.number([4, 4, 1, 4, 9]) == ["Speaker 1", "Speaker 1", "Speaker 2", "Speaker 1", "Speaker 3"]


def test_find_speakers_relabels_others_and_keeps_me(tmp_path):
    mic = words("hi all", 0.0)
    others = words("thanks for having me", 2.0) + words("glad to be here", 6.0)
    folder = make_meeting(tmp_path, mic, others)
    fake = lambda path, stopped, report: [(1.9, 4.0, 0), (5.9, 8.0, 1)]
    assert speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake) == "done"
    lines = meetings.transcript_lines(meetings.read_transcript(folder))
    assert [(s, t) for _, s, t in lines] == [("Me", "hi all"), ("Speaker 1", "thanks for having me"),
                                             ("Speaker 2", "glad to be here")]
    assert meetings.read_meta(folder)["speakers"] == {"status": "done", "count": 2, "track": "others"}


def test_find_speakers_splits_a_line_when_the_speaker_changes(tmp_path):
    others = words("one two three four", 1.0)  # No pause, so one line before labeling
    folder = make_meeting(tmp_path, [], others)
    fake = lambda path, stopped, report: [(0.9, 1.5, 0), (1.6, 3.0, 1)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    lines = meetings.transcript_lines(meetings.read_transcript(folder))
    assert [(s, t) for _, s, t in lines] == [("Speaker 1", "one two"), ("Speaker 2", "three four")]


def test_in_person_meeting_labels_the_mic(tmp_path):
    mic = words("good morning", 0.0) + words("morning", 3.0)
    folder = make_meeting(tmp_path, mic, [], heard=False)
    seen = []
    def fake(path, stopped, report):
        seen.append(path.name)
        return [(0.0, 1.0, 0), (2.9, 3.5, 1)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    assert seen == ["mic.wav"]
    speakers_seen = [s for _, s, _ in meetings.transcript_lines(meetings.read_transcript(folder))]
    assert speakers_seen == ["Speaker 1", "Speaker 2"]
    assert meetings.read_meta(folder)["speakers"]["track"] == "mic"


def test_one_voice_leaves_the_transcript_alone(tmp_path):
    folder = make_meeting(tmp_path, [], words("just me talking", 1.0))
    before = meetings.read_transcript(folder)
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=lambda s, stopped, report: [(0.0, 5.0, 3)])
    assert meetings.read_transcript(folder) == before
    assert meetings.read_meta(folder)["speakers"]["count"] == 1


def test_a_rename_meanwhile_is_kept(tmp_path):
    folder = make_meeting(tmp_path, [], words("a b", 1.0) + words("c d", 5.0))
    def fake(samples, stopped, report):
        meetings.rename_meeting(folder, "Quarterly panel")
        return [(0.0, 2.0, 0), (4.5, 6.0, 1)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    assert meetings.read_transcript(folder).startswith("# Quarterly panel")
    assert meetings.read_meta(folder)["title"] == "Quarterly panel"


def test_quitting_leaves_it_pending_and_failure_is_recorded(tmp_path):
    folder = make_meeting(tmp_path, [], words("a b", 1.0))
    def quit_(samples, stopped, report):
        raise speakers.Stopped()
    assert speakers.find_speakers(folder, log=lambda _: None, diarize_fn=quit_) == "pending"
    assert meetings.speakers_unfinished(tmp_path) == [folder]
    def broken(samples, stopped, report):
        raise RuntimeError("model")
    assert speakers.find_speakers(folder, log=lambda _: None, diarize_fn=broken) == "failed"
    assert meetings.speakers_unfinished(tmp_path) == []


def test_meeting_without_word_times_is_skipped(tmp_path):
    folder = make_meeting(tmp_path, [], words("a", 1.0))
    (folder / "words.json").unlink()
    assert speakers.find_speakers(folder, log=lambda _: None, diarize_fn=None) == "skipped"


def test_finishing_a_meeting_saves_word_times(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    job = meetings.MeetingTranscription.__new__(meetings.MeetingTranscription)
    job.folder, job._log, job._others_heard = folder, print, True
    class Done:
        def __init__(self, w):
            self.words, self.track = w, None
        def step(self, *args, **kwargs):
            return True
    job._tracks = {"mic": Done(words("hello", 0.0)), "others": Done(words("hi there", 1.0))}
    job._get_model, job._language = (lambda: None), "en"
    meta = dict(META)
    assert job.finish(meta)
    data = json.loads((folder / "words.json").read_text(encoding="utf-8"))
    assert data == {"others_heard": True, "mic": [[0.0, "hello"]], "others": [[1.0, "hi"], [1.4, "there"]]}
    assert meta["speakers"] == {"status": "pending"}


def test_read_track_rejects_other_formats(tmp_path):
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(b"\0" * 400)
    try:
        speakers.read_track(path)
    except ValueError:
        pass
    else:
        raise AssertionError("stereo accepted")


def test_app_labels_one_meeting_at_a_time_and_marks_them_in_use(monkeypatch, tmp_path):
    import queue
    import threading
    from types import SimpleNamespace
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config, gui._events, gui._closing = dict(DEFAULT_CONFIG), queue.Queue(), False
    gui._speaker_jobs, gui._speaker_folder, gui._speaker_lock = [], None, threading.Lock()
    gui._engine = SimpleNamespace(active=False)
    gui._refresh_meetings = lambda: None
    release, order, running = threading.Event(), [], []
    def fake(folder, log, stopped):
        running.append(gui.finding_speakers(folder))
        order.append(folder.name)
        release.wait(5)
    monkeypatch.setattr(speakers, "find_speakers", fake)
    a, b = tmp_path / "a", tmp_path / "b"
    gui._find_speakers_later(a)
    gui._find_speakers_later(b)
    gui._find_speakers_later(b)  # Queued once
    assert gui.finding_speakers(a) and gui.finding_speakers(b)
    release.set()
    for _ in range(100):
        if gui._speaker_folder is None:
            break
        threading.Event().wait(0.05)
    assert order == ["a", "b"] and running == [True, True]
    assert not gui.finding_speakers(a) and not gui.finding_speakers(b)


def test_app_skips_labeling_when_the_setting_is_off(tmp_path):
    import queue
    import threading
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    folder = make_meeting(tmp_path, [], words("a b", 1.0))
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config = {**DEFAULT_CONFIG, "meeting_speakers": False}
    gui._events, gui._closing = queue.Queue(), False
    gui._speaker_jobs, gui._speaker_folder, gui._speaker_lock = [], None, threading.Lock()
    gui._speaker_folder = folder
    gui._speaker_worker()
    assert meetings.read_meta(folder)["speakers"] == {"status": "skipped", "reason": "off"}


def test_progress_text():
    from ui.meetings_page import progress_text
    assert progress_text(("transcribing", 0.423)) == "Transcribing\u2026 42%"
    assert progress_text(("speakers", 0.6)) == "Finding speakers\u2026 60%"
    assert progress_text(("speakers waiting", None)) == "Finding speakers\u2026"
    assert progress_text(("speakers starting", 135.4)) == "Finding speakers\u2026 02:15"


def test_elapsed_time_shows_until_separation_reports_a_fraction(tmp_path):
    folder = make_meeting(tmp_path, [], words("a b", 1.0) + words("c d", 5.0))
    seen = []
    def fake(path, stopped, report):
        seen.append((speakers.progress(folder), speakers.elapsed(folder) is not None))
        report(0.25)
        seen.append((speakers.progress(folder), speakers.elapsed(folder) is not None))
        return [(0.0, 2.0, 0), (4.5, 6.0, 1)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    assert seen == [(None, True), (0.25, True)] and speakers.elapsed(folder) is None


def test_speaker_progress_is_reported_while_running(tmp_path):
    folder = make_meeting(tmp_path, [], words("a b", 1.0) + words("c d", 5.0))
    seen = []
    def fake(samples, stopped, report):
        seen.append(speakers.progress(folder))  # None until the first report, not a stuck 0%
        report(0.5)
        seen.append(speakers.progress(folder))
        return [(0.0, 2.0, 0), (4.5, 6.0, 1)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    assert seen == [None, 0.5] and speakers.progress(folder) is None


def test_transcribe_progress_counts_both_tracks(tmp_path):
    job = meetings.MeetingTranscription.__new__(meetings.MeetingTranscription)
    class Part:
        def __init__(self, done, total):
            self.d, self.t = done, total
        def done_and_total(self):
            return self.d, self.t
    job._tracks = {"mic": Part(100, 100), "others": Part(0, 100)}
    assert job.progress() == 0.5


def test_separation_runs_in_its_own_process_and_stops_at_once(tmp_path):
    import time
    import pytest
    import speaker_models
    if speaker_models.problem():
        pytest.skip("speaker models not fetched")
    path = tmp_path / "others.wav"
    rng = np.random.default_rng(0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes((rng.standard_normal(16000 * 3) * 300).astype(np.int16).tobytes())
    seen = []
    segments = speakers.diarize_apart(path, report=seen.append)
    assert isinstance(segments, list)
    started = time.monotonic()
    with pytest.raises(speakers.Stopped):
        speakers.diarize_apart(path, stopped=lambda: True)
    assert time.monotonic() - started < 5


def test_a_speaker_heard_for_seconds_is_a_stray():
    turns = [(0.0, 30.0, 0), (30.0, 31.5, 1), (31.5, 60.0, 0), (60.0, 75.0, 2)]
    assert speakers.drop_strays(turns, 10.0) == [(0.0, 30.0, 0), (31.5, 60.0, 0), (60.0, 75.0, 2)]
    assert speakers.drop_strays([(0.0, 2.0, 0), (2.0, 3.0, 1)], 10.0) == [(0.0, 2.0, 0)]  # Longest kept
    assert speakers.drop_strays([]) == []


def test_stray_reply_joins_the_speaker_around_it(tmp_path, monkeypatch):
    monkeypatch.setattr(speakers, "MIN_SPEAKER_SECONDS", 10.0)
    others = words("so here is the plan", 0.0) + words("yeah", 31.0) + words("and then we ship", 40.0)
    folder = make_meeting(tmp_path, [], others)
    fake = lambda path, stopped, report: [(0.0, 30.5, 0), (30.8, 31.6, 1), (39.0, 70.0, 0)]
    speakers.find_speakers(folder, log=lambda _: None, diarize_fn=fake)
    assert meetings.read_meta(folder)["speakers"]["count"] == 1
    assert "Speaker 2" not in meetings.read_transcript(folder)


def test_launch_restarts_nothing_and_says_what_did_not_finish(tmp_path):
    import queue
    import threading
    import app as app_module
    from dictation_engine import DEFAULT_CONFIG
    labeling = make_meeting(tmp_path, [], words("a b", 1.0))
    meetings.write_meta(labeling, {**META, "speakers": {"status": "running"}})
    recording = tmp_path / "2026-11-24 1700"
    recording.mkdir()
    meetings.write_meta(recording, {**META, "status": "transcribing"})
    gui = app_module.GUIApp.__new__(app_module.GUIApp)
    gui._config = {**DEFAULT_CONFIG, "meetings_dir": str(tmp_path)}
    gui._events, gui._closing, gui._meeting = queue.Queue(), False, None
    gui._speaker_jobs, gui._speaker_folder, gui._speaker_lock = [], None, threading.Lock()
    notes, queued = [], []
    gui._notify = notes.append
    gui._refresh_meetings = lambda: None
    gui._find_speakers_later = queued.append
    gui._note_interrupted_meetings()
    for _ in range(100):
        if gui._events.qsize() >= 2:
            break
        threading.Event().wait(0.02)
    while not gui._events.empty():
        gui._events.get()[1]()
    assert notes == [app_module.interrupted_notice(2)] and queued == []
    assert meetings.read_meta(labeling)["speakers"] == {"status": "interrupted"}
    assert meetings.read_meta(recording)["status"] == "interrupted"
    assert meetings.speakers_unfinished(tmp_path) == []
    assert "A meeting didn't" in app_module.interrupted_notice(1)
