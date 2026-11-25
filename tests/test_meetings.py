"""Meeting mode: files, resampling, transcript layout, and yielding to dictation."""
import json
import threading
import time
import wave
from datetime import datetime

import numpy as np
import pytest

import meetings
from audio_chunks import Cancelled, TimedWord, WindowedTranscript
from meetings import (RATE, MeetingSession, Resampler, TrackTranscriber, WavTrack, new_folder,
                      render_transcript, repair_wav, transcribe_folder, unfinished, utterances)


def tone(seconds, rate=RATE, freq=440, amp=0.3):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_wav_track_reads_while_writing(tmp_path):
    track = WavTrack(tmp_path / "mic.wav")
    first, second = tone(0.5), tone(0.25, freq=880)
    track.append(first)
    assert len(track) == len(first)
    np.testing.assert_allclose(track[:100], first[:100], atol=1e-4)
    track.append(second)
    np.testing.assert_allclose(track[len(first):len(first) + 50], second[:50], atol=1e-4)
    assert track[10:10].size == 0
    track.close()
    with wave.open(str(tmp_path / "mic.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (RATE, 1, len(first) + len(second))


def test_wav_header_is_current_after_every_write(tmp_path):
    """A crash mid-meeting still leaves a WAV that says how long it is."""
    track = WavTrack(tmp_path / "mic.wav")
    track.append(tone(1.0))
    with wave.open(str(tmp_path / "mic.wav")) as w:
        assert w.getnframes() == RATE
    track.close()


def test_repair_wav_fixes_lengths(tmp_path):
    path = tmp_path / "others.wav"
    track = WavTrack(path)
    track.append(tone(0.5))
    track.close()
    with open(path, "r+b") as f:  # Simulate a header written before the last block
        f.seek(40)
        f.write((0).to_bytes(4, "little"))
    assert repair_wav(path)
    with wave.open(str(path)) as w:
        assert w.getnframes() == RATE // 2


@pytest.mark.parametrize("rate_in", [48000, 44100, 16000])
def test_resampler_keeps_time_and_pitch(rate_in):
    resample = Resampler(rate_in)
    source = tone(2.0, rate=rate_in, freq=500)
    out = np.concatenate([resample(block) for block in np.array_split(source, 37)])
    assert abs(len(out) - 2.0 * RATE) <= 2
    spectrum = np.abs(np.fft.rfft(out[RATE // 2:RATE // 2 + RATE]))
    assert abs(np.argmax(spectrum) - 500) <= 2  # 1 Hz bins over one second


def test_resampler_removes_what_16khz_cannot_hold():
    out = Resampler(48000)(tone(1.0, rate=48000, freq=12000))
    assert np.sqrt(np.mean(np.square(out[200:]))) < 0.01


def words(text, start, step=0.4):
    return [TimedWord(w, start + i * step) for i, w in enumerate(text.split())]


META = {"title": "Untitled", "started": "2026-11-23T16:30:00", "duration_seconds": 75}


def test_transcript_interleaves_tracks_by_time():
    mic = words("hello there", 1.0) + words("sounds good", 20.0)
    others = words("hi how are you", 3.0)
    text = render_transcript(META, mic, others, others_heard=True)
    lines = [line for line in text.splitlines() if line.startswith("**")]
    assert lines == ["**[00:01] Me:** hello there",
                     "**[00:03] Others:** hi how are you",
                     "**[00:20] Me:** sounds good"]
    assert text.startswith("# Untitled\n\n2026-11-23 16:30 · 01:15")


def test_in_person_meeting_is_not_labeled_as_me():
    text = render_transcript(META, words("welcome everyone", 0.5), [], others_heard=False)
    assert "**[00:00] Speaker:** welcome everyone" in text
    assert "Me:" not in text


def test_pause_starts_a_new_line():
    lines = utterances(words("one two", 0) + words("three", 5.0), "Me")
    assert [line["words"] for line in lines] == [["one", "two"], ["three"]]


def test_empty_transcript_says_so():
    assert "No speech was recognized" in render_transcript(META, [], [], True)


def test_new_folder_never_reuses_a_name(tmp_path):
    started = datetime(2026, 11, 23, 16, 30)
    assert new_folder(tmp_path, started).name == "2026-11-23 1630"
    assert new_folder(tmp_path, started).name == "2026-11-23 1630 (2)"


class FakeModel:
    """Returns one word per window, honoring cancellation like Parakeet."""

    def __init__(self):
        self.infer_lock = threading.Lock()
        self.windows = 0

    def windowed_transcript(self, sample_rate, language, cancelled=None):
        def window(samples, offset):
            if cancelled is not None and cancelled():
                raise Cancelled()
            self.windows += 1
            return [TimedWord(f"w{self.windows}", offset)]
        return WindowedTranscript(window, sample_rate)


def filled_track(tmp_path, name, seconds):
    track = WavTrack(tmp_path / name)
    track.append(tone(seconds))
    return track


def test_meeting_waits_while_dictation_is_busy(tmp_path):
    model, busy = FakeModel(), [True]
    transcriber = TrackTranscriber(filled_track(tmp_path, "mic.wav", 40), lambda: busy[0])
    assert not transcriber.step(model, "en", final=True)
    assert model.windows == 0
    busy[0] = False
    assert transcriber.step(model, "en", final=True)
    assert model.windows >= 3


def test_meeting_never_waits_for_the_model_lock(tmp_path):
    model = FakeModel()
    transcriber = TrackTranscriber(filled_track(tmp_path, "mic.wav", 20), lambda: False)
    with model.infer_lock:  # A dictation take holds the model
        assert not transcriber.step(model, "en", final=True)
    assert transcriber.step(model, "en", final=True)


def test_dictation_starting_mid_meeting_cancels_the_window(tmp_path):
    model, busy = FakeModel(), [False]
    transcriber = TrackTranscriber(filled_track(tmp_path, "mic.wav", 40), lambda: busy[0])
    original = model.windowed_transcript

    def wrapped(sample_rate, language, cancelled=None):
        transcript = original(sample_rate, language, cancelled)
        inner = transcript._transcribe

        def window(samples, offset):
            result = inner(samples, offset)
            if model.windows == 1:
                busy[0] = True  # Dictation starts after the first window
            return result
        transcript._transcribe = window
        return transcript

    model.windowed_transcript = wrapped
    assert not transcriber.step(model, "en", final=True)
    assert model.windows == 1 and not model.infer_lock.locked()
    busy[0] = False
    assert transcriber.step(model, "en", final=True)
    assert [w.text for w in transcriber.words][:1] == ["w1"]


def test_model_without_word_times_still_transcribes(tmp_path):
    class Plain:
        def transcribe(self, samples, sample_rate, language):
            return "plain words"
    transcriber = TrackTranscriber(filled_track(tmp_path, "mic.wav", 5), lambda: False)
    assert transcriber.step(Plain(), "en", final=True)
    assert [w.text for w in transcriber.words] == ["plain", "words"]


class FakeCapture:
    def __init__(self, track, *args, **kwargs):
        self.track, self.name, self.devices = track, "Fake device", ["Fake device"]
        self.level = meetings.Level()

    def start(self):
        samples = tone(3.0)
        self.level.add(samples)
        self.track.append(samples)

    def stop(self):
        pass


def test_session_writes_folder_transcript_and_meta(tmp_path):
    model, done = FakeModel(), []
    session = MeetingSession(tmp_path, lambda: model, lambda: False, log=lambda m: None,
                             on_done=lambda folder, ok: done.append(ok),
                             mic_factory=FakeCapture, output_factory=lambda track, log: FakeCapture(track),
                             clock_now=lambda: datetime(2026, 11, 23, 16, 30, 5))
    session.start()
    assert json.loads((session.folder / "meta.json").read_text())["status"] == "recording"
    session.stop()
    assert session.finished.wait(10)
    meta = json.loads((session.folder / "meta.json").read_text())
    assert done == [True] and meta["status"] == "done"
    assert meta["duration_seconds"] == 3.0
    transcript = (session.folder / "transcript.md").read_text(encoding="utf-8")
    assert "Me:" in transcript and "Others:" in transcript


def test_interrupted_meeting_is_finished_on_next_launch(tmp_path):
    folder = new_folder(tmp_path, datetime(2026, 11, 23, 9, 0))
    for name in ("mic.wav", "others.wav"):
        filled_track(folder, name, 2).close()
    meetings.write_meta(folder, {**META, "status": "recording"})
    found = unfinished(tmp_path)
    assert found == [folder]
    assert meetings.read_meta(folder)["status"] == "interrupted"
    model = FakeModel()
    assert transcribe_folder(folder, lambda: model, lambda: False, log=lambda m: None)
    assert meetings.read_meta(folder)["status"] == "done"
    assert (folder / "transcript.md").exists()
    assert unfinished(tmp_path) == []


def test_recording_in_progress_is_left_alone(tmp_path):
    folder = new_folder(tmp_path, datetime(2026, 11, 23, 9, 0))
    meetings.write_meta(folder, {**META, "status": "recording"})
    assert unfinished(tmp_path, skip=folder) == []
    assert meetings.read_meta(folder)["status"] == "recording"


class FakeStream:
    def __init__(self, device, callback):
        self.device, self.callback, self.running = device, callback, True

    def start_stream(self):
        pass

    def stop_stream(self):
        self.running = False

    def close(self):
        pass


class FakePyAudio:
    """PyAudioWPatch's surface as OutputCapture uses it. Its device list is fixed
    when created, like PortAudio's."""
    paFloat32, paContinue = 1, 0
    plugged = ["Speakers (EMEET)", "Speakers (Laptop)"]
    instances = []

    def __init__(self):
        self.devices = [{"name": f"{n} [Loopback]", "index": i, "defaultSampleRate": 48000,
                         "maxInputChannels": 2} for i, n in enumerate(list(self.plugged))]
        self.streams = []
        FakePyAudio.instances.append(self)

    @classmethod
    def PyAudio(cls):
        return cls()

    def get_loopback_device_info_generator(self):
        return iter(self.devices)

    def open(self, input_device_index, stream_callback, **kwargs):
        stream = FakeStream(self.devices[input_device_index]["name"], stream_callback)
        self.streams.append(stream)
        return stream

    def terminate(self):
        pass


class FakeSpeaker:
    current = "Speakers (EMEET)"

    def name(self):
        return FakeSpeaker.current


def test_output_follows_a_switch_portaudio_never_sees(tmp_path):
    from meetings import OutputCapture
    FakePyAudio.instances, FakeSpeaker.current = [], "Speakers (EMEET)"
    track = WavTrack(tmp_path / "others.wav")
    output = OutputCapture(track, log=lambda m: None, pa_module=FakePyAudio, default_speaker=FakeSpeaker)
    output.start()
    assert output.name == "Speakers (EMEET)"
    FakeSpeaker.current = "Speakers (Laptop)"
    output._follow(FakeSpeaker())
    assert output.name == "Speakers (Laptop)" and not output.lost
    assert output.devices == ["Speakers (EMEET)", "Speakers (Laptop)"]
    assert FakePyAudio.instances[0].streams[-1].device == "Speakers (Laptop) [Loopback]"
    output._stop.set()
    output.stop()
    track.close()


def test_output_plugged_in_mid_meeting_restarts_portaudio(tmp_path):
    from meetings import OutputCapture
    FakePyAudio.instances, FakeSpeaker.current = [], "Speakers (EMEET)"
    track = WavTrack(tmp_path / "others.wav")
    output = OutputCapture(track, log=lambda m: None, pa_module=FakePyAudio, default_speaker=FakeSpeaker)
    output.start()
    FakePyAudio.plugged = FakePyAudio.plugged + ["Headphones (USB)"]
    try:
        FakeSpeaker.current = "Headphones (USB)"
        output._follow(FakeSpeaker())
        assert output.name == "Headphones (USB)" and len(FakePyAudio.instances) == 2
    finally:
        FakePyAudio.plugged = FakePyAudio.plugged[:-1]
    output._stop.set()
    output.stop()
    track.close()


def test_output_that_cannot_be_recorded_is_reported(tmp_path):
    from meetings import OutputCapture
    FakePyAudio.instances, FakeSpeaker.current = [], "Speakers (EMEET)"
    track = WavTrack(tmp_path / "others.wav")
    output = OutputCapture(track, log=lambda m: None, pa_module=FakePyAudio, default_speaker=FakeSpeaker)
    output.start()
    FakeSpeaker.current = "Bluetooth (gone)"
    output._follow(FakeSpeaker())
    assert output.lost
    session = type("S", (), {"output": output, "mic": None})()
    assert MeetingSession.problem.fget(session) == "Not recording the other side"
    FakeSpeaker.current = "Speakers (EMEET)"
    output._follow(FakeSpeaker())
    assert not output.lost
    output._stop.set()
    output.stop()
    track.close()


def test_mic_and_speaker_on_one_device_need_no_hint():
    from audio_endpoints import same_device
    assert same_device("Microphone (EMEET OfficeCore M0 Plus)", "Speakers (EMEET OfficeCore M0 Plus)")
    assert same_device("Headset Microphone (2- Jabra Evolve2)", "Headphones (Jabra Evolve2)")
    assert not same_device("Microphone Array (Intel® Smart Sound Technology)", "Speakers (6- Cirrus Logic XU)")
    session = type("S", (), {"mic": type("M", (), {"name": "Microphone (EMEET)"})(),
                             "output": type("O", (), {"name": "Speakers (6- Cirrus Logic XU)"})()})()
    mic, speaker, hint = MeetingSession.devices(session)
    assert (mic, speaker) == ("Microphone (EMEET)", "Speakers (6- Cirrus Logic XU)") and hint


def test_library_lists_groups_and_reads_transcripts(tmp_path):
    import json
    from datetime import date, datetime
    from meetings import day_label, display_title, list_meetings, matches, transcript_lines
    for name, started, title in (("a", "2026-11-21T08:15:00", "Untitled"), ("b", "2026-11-24T13:00:00", "Review")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps(
            {"title": title, "started": started, "duration_seconds": 60, "status": "done"}), encoding="utf-8")
    (tmp_path / "b" / "transcript.md").write_text("# Review\n\n**[00:04] Me:** Ship it Thursday.\n", encoding="utf-8")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "meta.json").write_text("{", encoding="utf-8")
    items = list_meetings(tmp_path)
    assert [item["title"] for item in items] == ["Review", "Untitled"]
    assert display_title(items[1]) == "Untitled · 8:15 AM"
    today = date(2026, 11, 24)
    assert day_label(datetime(2026, 11, 24, 9), today) == "Today, Nov 24"
    assert day_label(datetime(2026, 11, 23, 9), today) == "Yesterday, Nov 23"
    assert day_label(datetime(2026, 11, 21, 9), today) == "Saturday, Nov 21"
    assert day_label(datetime(2025, 11, 3, 9), today) == "Nov 3, 2025"
    assert matches(items[0], "thursday") and not matches(items[1], "thursday")
    assert transcript_lines((tmp_path / "b" / "transcript.md").read_text()) == [("00:04", "Me", "Ship it Thursday.")]


def _recycled(original):
    """The Recycle Bin's ($I info, $R content) paths for an item deleted from original.
    An $I file holds the original path as UTF-16 after a 28-byte header (Windows 10 and later)."""
    import struct
    from pathlib import Path
    found = []
    for info in (Path(original.anchor) / "$Recycle.Bin").glob("*/$I*"):
        try:
            data = info.read_bytes()
            length = struct.unpack_from("<I", data, 24)[0]
            path = data[28:28 + 2 * length].decode("utf-16-le").rstrip("\0")
        except (OSError, struct.error, UnicodeDecodeError):
            continue  # Another user's folder, or an older format
        if Path(path) == original:
            found.append((info, info.with_name("$R" + info.name[2:])))
    return found


def test_trash_folder_moves_a_meeting_to_the_recycle_bin(tmp_path):
    import shutil
    from meetings import trash_folder
    folder = tmp_path / "2026-11-24 1300"
    folder.mkdir()
    (folder / "meta.json").write_text("{}", encoding="utf-8")
    try:
        assert trash_folder(folder)
        assert not folder.exists()
    finally:
        # Empty this test's item from the real Recycle Bin, so runs don't pile up there.
        for info, content in _recycled(folder):
            shutil.rmtree(content, ignore_errors=True)
            info.unlink(missing_ok=True)
    assert not _recycled(folder)


def test_move_meetings_keeps_names_and_skips_folders_in_use(tmp_path):
    from meetings import move_meetings, same_folder
    old, new = tmp_path / "old", tmp_path / "new"
    for name in ("2026-11-24 1300", "2026-11-24 1400", "2026-11-24 1500"):
        (old / name).mkdir(parents=True)
        (old / name / "meta.json").write_text("{}", encoding="utf-8")
    (old / "notes").mkdir()  # Not a meeting: stays put
    (new / "2026-11-24 1300").mkdir(parents=True)  # Name already taken in the new folder
    busy = old / "2026-11-24 1500"
    assert move_meetings(old, new, skip=lambda folder: folder == busy) == (2, 1)
    assert (new / "2026-11-24 1300 (2)" / "meta.json").exists()
    assert (new / "2026-11-24 1400" / "meta.json").exists()
    assert (busy / "meta.json").exists() and (old / "notes").exists()
    assert move_meetings(new, new) == (0, 0)
    assert same_folder(str(new) + "\\", new)


def _mic(tmp_path, dictating=lambda: False):
    track = meetings.WavTrack(tmp_path / "mic.wav")
    mic = meetings.MicCapture(track, mutes=meetings.Mutes(dictating))
    return mic, track


def _feed(mic, seconds, start=100.0, value=0.1):
    """Feed 100 ms blocks of constant sound, captured from start onward."""
    block = np.full((1600, 1), value, dtype=np.float32)
    for i in range(int(round(seconds * 10))):
        mic._on_audio(block, 1600, None, None, now=start + (i + 1) * 0.1)


def test_dictation_is_left_out_of_the_meeting_mic_with_a_margin(tmp_path):
    state = {"on": False}
    mic, track = _mic(tmp_path, lambda: state["on"])
    _feed(mic, 2.0, start=100.0)            # 100.0-102.0 before the take
    state["on"] = True
    _feed(mic, 1.0, start=102.0)            # 102.0-103.0 dictating
    state["on"] = False
    _feed(mic, 2.0, start=103.0)            # 103.0-105.0 after
    mic._flush(now=104.0)                   # Only blocks ending by 103.7 are written yet
    assert abs(track.seconds - 3.7) < 1e-6
    mic._flush(final=True)
    audio = track[0:len(track)]
    assert len(audio) == 5 * RATE           # Same length: left-out audio is silence
    loud = np.abs(audio).reshape(-1, 1600).max(axis=1) > 0
    # Blocks are 100 ms; the take ran 2.0-3.0 s, so 1.7-3.3 s (±0.3 s margin) is silent.
    assert loud[:17].all() and not loud[17:33].any() and loud[33:].all()
    track.close()


def test_manual_mute_is_saved_marked_and_hides_the_silent_mic_problem(tmp_path):
    mic, track = _mic(tmp_path)
    mic.mutes.set_manual(True, now=101.0)
    _feed(mic, 2.0)
    assert mic.mutes.manual
    mic.mutes.set_manual(False, now=101.5)
    mic._flush(final=True)
    loud = np.abs(track[0:len(track)]).reshape(-1, 1600).max(axis=1) > 0
    assert loud[:10].all() and not loud[10:15].any() and loud[15:].all()
    assert mic.mutes.manual_spans(origin=100.0, until=110.0) == [[1.0, 1.5]]
    assert mic._last_sound is not None   # Sound while muted still proves the mic works
    track.close()


def test_transcript_marks_long_manual_mutes_only():
    from datetime import datetime
    words = [TimedWord("hello", 1.0), TimedWord("again", 70.0)]
    meta = {"title": "Sync", "started": datetime(2026, 11, 24, 9).isoformat(), "duration_seconds": 80,
            "muted": [[10.0, 52.0], [60.0, 62.0]]}
    text = meetings.render_transcript(meta, words, [], others_heard=True)
    assert "_[00:10] Me muted for 00:42_" in text
    assert "muted for 00:02" not in text
    assert text.index("hello") < text.index("muted for 00:42") < text.index("again")


def test_session_mute_needs_a_started_meeting_and_survives_in_meta(tmp_path):
    session = meetings.MeetingSession(tmp_path, lambda: None, lambda: False,
                                      mic_factory=FakeCapture, output_factory=lambda track, log: FakeCapture(track))
    session.set_muted(True)
    assert not session.muted                 # Not started yet
    session._t0 = session.mic._started = time.monotonic() - 3
    session.set_muted(True)
    assert session.muted and session.problem is None
    assert meetings.read_meta(session.folder)["muted"][0][1] >= 3
    session.set_muted(False)
    assert not session.muted
    session._close_tracks()


def test_mic_track_keeps_time_when_audio_goes_missing(tmp_path):
    mic, track = _mic(tmp_path)
    block = np.full((1600, 1), 0.1, dtype=np.float32)
    t = 100.0
    for _ in range(20):                       # 2 s delivered on time
        t += 0.1
        mic._on_audio(block, 1600, None, None, now=t)
    t += 1.0                                  # 1 s of audio never arrives
    for _ in range(20):
        t += 0.1
        mic._on_audio(block, 1600, None, None, now=t)
    mic._flush(final=True)
    assert abs(track.seconds - 5.0) <= 0.06   # The clock's 5 s, not the 4 s received
    assert len(mic.filled) == 1 and abs(mic.filled[0][0] - 2.0) < 0.01 and abs(mic.filled[0][1] - 0.95) < 0.01
    audio = track[0:len(track)]
    assert audio[:int(2.0 * RATE)].all()
    assert not audio[int(2.0 * RATE):int(2.94 * RATE)].any()  # The gap sits where audio went missing
    assert audio[int(2.96 * RATE):].all()
    track.close()


def test_mic_track_ignores_late_blocks_that_catch_up(tmp_path):
    mic, track = _mic(tmp_path)
    block = np.full((1600, 1), 0.1, dtype=np.float32)
    t = 100.0
    for i in range(60):
        if i in (20, 40):
            t += 0.4                          # A stall: blocks arrive late...
        elif i in (21, 22, 23, 24, 41, 42, 43, 44):
            t += 0.0                          # ...then the buffered ones arrive at once
        else:
            t += 0.1
        mic._on_audio(block, 1600, None, None, now=t)
    mic._flush(final=True)
    assert mic.filled == [] and abs(track.seconds - 6.0) < 1e-6
    track.close()


def test_snippet_shows_why_a_transcript_matched(tmp_path):
    from meetings import snippet
    folder = tmp_path / "a"
    folder.mkdir()
    (folder / "transcript.md").write_text(
        "# Review\n\n**[00:04] Me:** Hello there.\n\n**[00:09] Others:** "
        "We decided after a long and winding discussion to ship it Thursday if QA passes the final round of checks on the release build.\n",
        encoding="utf-8")
    item = {"folder": folder, "title": "Review", "started": datetime(2026, 11, 24, 9)}
    speaker, before, hit, after = snippet(item, "thursday")
    assert speaker == "Others" and hit == "Thursday"
    assert before.startswith("\u2026") and before.endswith("ship it ")
    assert after.startswith(" if QA") and after.endswith("\u2026")
    assert snippet(item, "review") is None     # The title already explains the match
    assert snippet(item, "nowhere") is None and snippet(item, "  ") is None
    assert snippet(item, "hello") == ("Me", "", "Hello", " there.")


def test_a_rename_during_the_meeting_is_kept(tmp_path):
    """Renaming on the Meetings page while recording: the session's own copy of meta
    must not put the old title back when it saves, nor in the transcript heading."""
    folder = tmp_path / "2026-11-24 2013"
    folder.mkdir()
    session_meta = {"title": "Untitled", "started": "2026-11-24T20:13:00", "status": "recording"}
    meetings.write_meta(folder, session_meta)
    meetings.rename_meeting(folder, "Panel discussion")
    session_meta["status"] = "transcribing"
    meetings.save_meta(folder, session_meta)
    assert meetings.read_meta(folder)["title"] == "Panel discussion"
    meetings.write_meta(folder, {**session_meta, "title": "Untitled"})
    meetings.rename_meeting(folder, "Panel discussion")
    job = meetings.MeetingTranscription.__new__(meetings.MeetingTranscription)
    job.folder, job._log, job._others_heard = folder, print, True

    class Done:
        words, track = [TimedWord("hi", 0.0)], None

        def step(self, *args, **kwargs):
            return True
    job._tracks = {"mic": Done(), "others": Done()}
    job._get_model, job._language = (lambda: None), "en"
    stale = {**session_meta, "title": "Untitled", "duration_seconds": 5}
    assert job.finish(stale)
    assert (folder / "transcript.md").read_text(encoding="utf-8").startswith("# Panel discussion")
    assert stale["title"] == "Panel discussion"
