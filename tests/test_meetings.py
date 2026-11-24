"""Meeting mode: files, resampling, transcript layout, and yielding to dictation."""
import json
import threading
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
    def __init__(self, track, *args):
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
