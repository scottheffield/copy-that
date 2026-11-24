"""Meeting mode: record the microphone and the laptop's audio output, then
transcribe both locally (#32).

A meeting is one folder under the meetings folder:

    2026-11-23 1630/
        mic.wav          what the microphone heard ("Me")
        others.wav       what the laptop played ("Others")
        transcript.md    timestamped, labeled transcript
        meta.json        title, start, duration, status

Both tracks are 16 kHz mono WAV, the rate the speech model uses. The wave
module rewrites the header on every write, so a crash leaves a playable file.

Transcription runs in windows while the meeting is recorded, through the same
model as dictation. Dictation always goes first: meeting work only takes the
model while no dictation take is active, and abandons a window the moment one
starts.
"""
import json
import re
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from audio_chunks import Cancelled, TimedWord, WindowedTranscript

RATE = 16000
STATUSES = ("recording", "transcribing", "done", "failed", "interrupted")
SILENCE_RMS = 0.003      # Below this, a 100 ms block counts as silent
HEARD_FRACTION = 0.01    # Others counts as heard when 1% of its blocks have sound
UTTERANCE_GAP = 1.5      # Seconds of silence that end a transcript line


# -- Audio files --------------------------------------------------------------

class WavTrack:
    """A 16 kHz mono WAV that grows while recording and can be read meanwhile.

    len() and slicing return float32 samples, which is all WindowedTranscript
    needs, so an hour of audio never has to sit in memory.
    """

    HEADER = 44  # The wave module writes a plain 44-byte PCM header.

    def __init__(self, path, writable=True):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._writer = self._file = None
        if writable:
            self._file = open(self.path, "wb")
            self._writer = wave.open(self._file, "wb")
            self._writer.setnchannels(1)
            self._writer.setsampwidth(2)
            self._writer.setframerate(RATE)
            self._writer.writeframes(b"")
            self._file.flush()
            self.frames = 0
        else:
            self.frames = max(0, (self.path.stat().st_size - self.HEADER) // 2)
        # Unbuffered: a buffered reader could hand back bytes cached before the writer's latest block.
        self._reader = open(self.path, "rb", buffering=0)

    @classmethod
    def existing(cls, path):
        return cls(path, writable=False)

    def append(self, samples):
        if not len(samples):
            return
        pcm = np.clip(np.asarray(samples, dtype=np.float32) * 32767, -32768, 32767).astype("<i2")
        with self._lock:
            self._writer.writeframes(pcm.tobytes())
            self._file.flush()
            self.frames += len(pcm)

    def append_silence(self, count):
        self.append(np.zeros(int(count), dtype=np.float32))

    def __len__(self):
        return self.frames

    def __getitem__(self, key):
        if not isinstance(key, slice) or key.step not in (None, 1):
            raise TypeError("WavTrack supports contiguous slices only")
        start, stop, _ = key.indices(self.frames)
        if stop <= start:
            return np.zeros(0, dtype=np.float32)
        with self._lock:
            self._reader.seek(self.HEADER + start * 2)
            raw = self._reader.read((stop - start) * 2)
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768

    @property
    def seconds(self):
        return self.frames / RATE

    def close(self):
        with self._lock:
            if self._writer:
                self._writer.close()
                self._file.close()
                self._writer = self._file = None
            self._reader.close()


def repair_wav(path):
    """Fix the header's lengths from the file size, for a file a crash left behind."""
    path = Path(path)
    size = path.stat().st_size
    if size < WavTrack.HEADER:
        return False
    data = (size - WavTrack.HEADER) // 2 * 2
    with open(path, "r+b") as f:
        f.seek(4)
        f.write((36 + data).to_bytes(4, "little"))
        f.seek(40)
        f.write(data.to_bytes(4, "little"))
    return True


class Resampler:
    """Streaming low-pass and linear-interpolation resampler to 16 kHz."""

    def __init__(self, rate_in, rate_out=RATE, taps=63):
        self.step = rate_in / rate_out
        self._pass = rate_in == rate_out
        cutoff = 0.45 * rate_out / rate_in  # Below the output's Nyquist, as a fraction of rate_in
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        self._h = (h / h.sum()).astype(np.float32)
        self._history = np.zeros(taps - 1, dtype=np.float32)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def __call__(self, samples):
        samples = np.asarray(samples, dtype=np.float32)
        if self._pass:
            return samples
        joined = np.concatenate([self._history, samples])
        filtered = np.convolve(joined, self._h, mode="valid").astype(np.float32)
        self._history = joined[-len(self._history):]
        self._buffer = np.concatenate([self._buffer, filtered])
        last = len(self._buffer) - 1
        if last < self._pos:
            return np.zeros(0, dtype=np.float32)
        count = int((last - self._pos) // self.step) + 1
        positions = self._pos + self.step * np.arange(count)
        out = np.interp(positions, np.arange(len(self._buffer)), self._buffer).astype(np.float32)
        following = self._pos + self.step * count
        used = min(int(following), len(self._buffer))  # The next position may lie past this block
        self._buffer = self._buffer[used:]
        self._pos = following - used
        return out


# -- Capture ------------------------------------------------------------------

class Level:
    """Latest block level and how many blocks had sound, for meters and labels."""

    def __init__(self):
        self.rms = 0.0
        self.blocks = 0
        self.heard = 0

    def add(self, samples):
        if not len(samples):
            return
        self.rms = float(np.sqrt(np.mean(np.square(samples))))
        self.blocks += 1
        if self.rms >= SILENCE_RMS:
            self.heard += 1

    @property
    def heard_fraction(self):
        return self.heard / self.blocks if self.blocks else 0.0


class MicCapture:
    """The microphone dictation uses, through sounddevice at 16 kHz."""

    SILENT_SECONDS = 5  # Exact zeros this long means the mic delivers nothing

    def __init__(self, track, device=None, log=print):
        self.track, self.level, self._log = track, Level(), log
        self._device = device
        self._last_sound = None
        self._blocks = []
        self._lock = threading.Lock()
        self._stream = None
        self._stop = threading.Event()
        self._writer = threading.Thread(target=self._drain, name="meeting-mic", daemon=True)
        self.name = None

    def start(self):
        import sounddevice as sd
        from dictation_engine import _wasapi_convert, sound_mapper
        device = self._device if self._device is not None else sound_mapper("Input")

        def callback(indata, frames, time_info, status):
            with self._lock:
                self._blocks.append(indata[:, 0].copy())

        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=1600,
                                      callback=callback, device=device,
                                      extra_settings=_wasapi_convert(sd, device))
        self._stream.start()
        try:
            if self._device is not None:
                self.name = sd.query_devices(device)["name"]
            else:  # The sound mapper opened Windows' default; ask Windows which one that is.
                from audio_endpoints import DefaultMicrophone
                self.name = DefaultMicrophone().name()
        except Exception:
            self.name = None
        self.name = self.name or "Windows default microphone"
        self._log(f"Meeting: microphone {self.name}")
        self._last_sound = time.monotonic()
        self._writer.start()

    @property
    def silent(self):
        """True when the mic has sent only exact zeros (or nothing) for SILENT_SECONDS.
        A quiet room still has noise; a muted or dead device sends zeros."""
        return self._last_sound is not None and time.monotonic() - self._last_sound > self.SILENT_SECONDS

    def _drain(self):
        while not self._stop.wait(0.2):
            self._flush()
        self._flush()

    def _flush(self):
        with self._lock:
            blocks, self._blocks = self._blocks, []
        for block in blocks:
            self.level.add(block)
            self.track.append(block)
            if block.any():
                self._last_sound = time.monotonic()

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stop.set()
        if self._writer.is_alive():
            self._writer.join(2)


class OutputCapture:
    """What the laptop plays, through PyAudioWPatch's WASAPI loopback.

    The capture spike chose PyAudioWPatch: it kept the exact rate, where
    soundcard ran 0.5-0.7% fast. Loopback delivers nothing while nothing
    plays, so silence is filled from the wall clock to keep this track in
    step with the microphone.

    It follows a change of Windows' default output. PortAudio reads its device
    list once, so the current default comes from Windows (audio_endpoints),
    and PortAudio is restarted on a switch so a newly plugged device is found.
    """

    FOLLOW_SECONDS = 0.5
    STALL_SECONDS = 0.4

    def __init__(self, track, log=print, pa_module=None, default_speaker=None):
        self.track, self.level, self._log = track, Level(), log
        self.devices = []  # Every output recorded from, in order
        self.name = None
        self.lost = False  # True while the default output can't be recorded
        self._pa_module = pa_module
        self._speaker_factory = default_speaker
        self._pa = None
        self._stream = None
        self._resample = None
        self._channels = 2
        self._blocks = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = None
        self._thread = threading.Thread(target=self._run, name="meeting-output", daemon=True)

    def _speaker(self):
        """A reader of Windows' default speaker for the calling thread."""
        if self._speaker_factory is not None:
            return self._speaker_factory()
        from audio_endpoints import DefaultSpeaker
        return DefaultSpeaker()

    def start(self):
        if self._pa_module is None:
            import pyaudiowpatch
            self._pa_module = pyaudiowpatch
        self._pa = self._pa_module.PyAudio()
        self._open(self._speaker().name())
        self._started = time.monotonic()
        self._thread.start()

    def _open(self, speaker_name):
        devices = list(self._pa.get_loopback_device_info_generator())
        device = next((d for d in devices if d["name"].startswith(speaker_name)), None)
        if device is None:
            # Plugged in after PortAudio started: restart it to see the device.
            self._pa.terminate()
            self._pa = self._pa_module.PyAudio()
            device = next(d for d in self._pa.get_loopback_device_info_generator()
                          if d["name"].startswith(speaker_name))
        speaker = {"name": speaker_name}
        rate = int(device["defaultSampleRate"])
        self._channels = max(1, int(device["maxInputChannels"]))
        self._resample = Resampler(rate)
        channels = self._channels

        def callback(data, frames, time_info, status):
            block = np.frombuffer(data, dtype="<f4").reshape(-1, channels).mean(axis=1)
            with self._lock:
                self._blocks.append(block)
            return (None, self._pa_module.paContinue)

        self._stream = self._pa.open(format=self._pa_module.paFloat32, channels=channels, rate=rate,
                                     input=True, input_device_index=device["index"],
                                     frames_per_buffer=rate // 10, stream_callback=callback)
        self._stream.start_stream()
        self.name = speaker["name"]
        self.devices.append(self.name)
        self._log(f"Meeting: recording output from {self.name}")

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _run(self):
        com = None
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            com = comtypes
        except Exception:
            pass
        try:
            speaker = self._speaker()
        except Exception as error:
            speaker = None
            self._log(f"Meeting: can't watch the default output ({type(error).__name__})")
        next_check = time.monotonic() + self.FOLLOW_SECONDS
        while not self._stop.wait(0.1):
            self._flush()
            if speaker is None or time.monotonic() < next_check:
                continue
            next_check = time.monotonic() + self.FOLLOW_SECONDS
            self._follow(speaker)
        self._flush()
        if com is not None:
            try:
                com.CoUninitialize()
            except Exception:
                pass

    def _follow(self, speaker):
        """Switch to Windows' default output if it changed, or retry one that failed."""
        try:
            name = speaker.name()
        except Exception as error:
            self._log(f"Meeting: could not read the default output ({type(error).__name__})")
            return
        if not name or (name == self.name and not self.lost):
            return
        if name != self.name:
            self._log("Meeting: default output changed; following it")
        self._close_stream()
        self._flush()
        try:
            self._open(name)
            self.lost = False
        except Exception as error:
            if not self.lost:
                self._log(f"Meeting: could not record from the new output ({type(error).__name__})")
            self.lost = True
            self.name = name

    def _flush(self):
        with self._lock:
            blocks, self._blocks = self._blocks, []
        for block in blocks:
            samples = self._resample(block)
            self.level.add(samples)
            self.track.append(samples)
        # No packets while nothing plays: pad so this track keeps time with the mic.
        expected = (time.monotonic() - self._started) * RATE
        if not blocks and expected - len(self.track) > self.STALL_SECONDS * RATE:
            self.track.append_silence(expected - len(self.track) - 0.1 * RATE)
            self.level.rms = 0.0  # Nothing playing: the meter drops instead of holding its last value

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2)
        self._close_stream()
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass


# -- Transcription --------------------------------------------------------------

def _plain_window(model, language):
    """A window recognizer for models without word times: spread words across the window."""
    def window(samples, offset):
        words = model.transcribe(samples, sample_rate=RATE, language=language).split()
        span = len(samples) / RATE
        return [TimedWord(w, offset + span * i / max(1, len(words))) for i, w in enumerate(words)]
    return window


_fallback_lock = threading.Lock()


def _infer_lock(model):
    # Parakeet shares one lock with dictation. Whisper models have none; for them,
    # checking that dictation is idle first is the only guard.
    return getattr(model, "infer_lock", None) or _fallback_lock


class TrackTranscriber:
    """Recognize one track window by window, yielding to dictation."""

    def __init__(self, track, busy):
        self.track, self._busy = track, busy
        self._model = None
        self._transcript = None

    def _bind(self, model, language):
        if model is self._model:
            return
        if self._model is not None:
            raise RuntimeError("The speech model changed during a meeting")
        self._model = model
        if hasattr(model, "windowed_transcript"):
            self._transcript = model.windowed_transcript(RATE, language, cancelled=self._busy)
        else:
            self._transcript = WindowedTranscript(_plain_window(model, language), RATE)

    def step(self, model, language, final=False):
        """One pass if the model is free. True when the track is fully transcribed."""
        if model is None or self._busy():
            return False
        self._bind(model, language)
        lock = _infer_lock(model)
        if not lock.acquire(blocking=False):
            return False
        try:
            self._transcript.advance(self.track, final=final)
            return final and self._transcript.complete
        except Cancelled:
            return False  # A dictation take started; try again once it is done.
        finally:
            lock.release()

    @property
    def words(self):
        return list(self._transcript.words) if self._transcript else []


def utterances(words, speaker, gap=UTTERANCE_GAP):
    """Group a track's words into lines, a new line after a pause."""
    lines = []
    for word in words:
        if lines and lines[-1]["speaker"] == speaker and word.time - lines[-1]["end"] <= gap:
            lines[-1]["words"].append(word.text)
            lines[-1]["end"] = word.time
        else:
            lines.append({"speaker": speaker, "start": word.time, "end": word.time, "words": [word.text]})
    return lines


def clock(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


def render_transcript(meta, mic_words, others_words, others_heard):
    """Markdown transcript. Without sound on the output track (an in-person
    meeting), everyone is on the mic, so it isn't labeled as you."""
    me = "Me" if others_heard else "Speaker"
    lines = sorted(utterances(mic_words, me) + utterances(others_words, "Others"),
                   key=lambda line: line["start"])
    started = datetime.fromisoformat(meta["started"])
    out = [f"# {meta['title']}", "",
           f"{started:%Y-%m-%d %H:%M} · {clock(meta.get('duration_seconds', 0))}", ""]
    for line in lines:
        out.append(f"**[{clock(line['start'])}] {line['speaker']}:** {' '.join(line['words'])}")
        out.append("")
    if not lines:
        out.append("_No speech was recognized._")
        out.append("")
    return "\n".join(out)


# -- Folders --------------------------------------------------------------------

def new_folder(root, started):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = f"{started:%Y-%m-%d %H%M}"
    folder, n = root / base, 2
    while folder.exists():
        folder, n = root / f"{base} ({n})", n + 1
    folder.mkdir()
    return folder


def read_meta(folder):
    return json.loads((Path(folder) / "meta.json").read_text(encoding="utf-8"))


def write_meta(folder, meta):
    path = Path(folder) / "meta.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(path)


def unfinished(root, skip=None):
    """Meetings a crash or quit left before their transcript was written.
    Repairs their WAV headers and marks them interrupted. skip: a folder in use."""
    root = Path(root)
    found = []
    if not root.is_dir():
        return found
    for meta_path in sorted(root.glob("*/meta.json")):
        if skip is not None and meta_path.parent == Path(skip):
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("status") not in ("recording", "transcribing", "interrupted"):
            continue
        folder = meta_path.parent
        for name in ("mic.wav", "others.wav"):
            if (folder / name).exists():
                repair_wav(folder / name)
        if meta.get("status") != "interrupted":
            meta["status"] = "interrupted"
            write_meta(folder, meta)
        found.append(folder)
    return found


# -- Orchestration ------------------------------------------------------------

class MeetingTranscription:
    """Transcribe a meeting's two tracks and write transcript.md.

    get_model returns the loaded speech model or None; busy is true while
    dictation needs the model.
    """

    def __init__(self, folder, mic, others, get_model, busy, language="en", log=print,
                 interval=2.0, others_heard=None):
        self.folder, self._log = Path(folder), log
        self._get_model, self._busy, self._language = get_model, busy, language
        self._interval = interval
        self._others_heard = others_heard
        self._tracks = {"mic": TrackTranscriber(mic, busy), "others": TrackTranscriber(others, busy)}
        self._stop_live = threading.Event()

    def live(self):
        """Advance finished windows while recording, until stop_live()."""
        while not self._stop_live.wait(self._interval):
            for transcriber in self._tracks.values():
                try:
                    transcriber.step(self._get_model(), self._language)
                except Exception as error:
                    self._log(f"Meeting: live recognition paused ({type(error).__name__})")

    def stop_live(self):
        self._stop_live.set()

    def finish(self, meta, stopped=lambda: False):
        """Final windows of both tracks, waiting for dictation if needed; writes the transcript."""
        pending = dict(self._tracks)
        while pending and not stopped():
            for name in list(pending):
                if pending[name].step(self._get_model(), self._language, final=True):
                    del pending[name]
            if pending:
                time.sleep(0.25)
        if pending:
            return False
        heard = self._others_heard
        if heard is None:
            heard = _heard(self._tracks["others"].track)
        text = render_transcript(meta, self._tracks["mic"].words, self._tracks["others"].words, heard)
        (self.folder / "transcript.md").write_text(text, encoding="utf-8")
        return True


def _heard(track, block=1600):
    """Whether a finished track has sound in at least HEARD_FRACTION of its blocks."""
    total = heard = 0
    for start in range(0, len(track), block * 50):
        chunk = track[start:start + block * 50]
        usable = len(chunk) // block * block
        if not usable:
            continue
        rms = np.sqrt(np.mean(np.square(chunk[:usable].reshape(-1, block)), axis=1))
        total += len(rms)
        heard += int((rms >= SILENCE_RMS).sum())
    return total > 0 and heard / total >= HEARD_FRACTION


class MeetingSession:
    """One meeting from Start to transcript."""

    def __init__(self, root, get_model, busy, mic_device=None, language="en", log=print,
                 on_done=lambda folder, ok: None, mic_factory=MicCapture, output_factory=OutputCapture,
                 clock_now=datetime.now):
        self.started = clock_now().replace(microsecond=0)
        self.folder = new_folder(root, self.started)
        self._log, self._on_done = log, on_done
        self._get_model, self._busy, self._language = get_model, busy, language
        self.meta = {"title": "Untitled", "started": self.started.isoformat(), "duration_seconds": 0,
                     "status": "recording", "mic_device": None, "output_devices": []}
        write_meta(self.folder, self.meta)
        self.mic_track = WavTrack(self.folder / "mic.wav")
        self.others_track = WavTrack(self.folder / "others.wav")
        self.mic = mic_factory(self.mic_track, mic_device, log)
        self.output = output_factory(self.others_track, log)
        self._t0 = None
        self._closing = threading.Event()
        self.finished = threading.Event()
        self._transcription = None
        self._live_thread = None

    @property
    def elapsed(self):
        return 0 if self._t0 is None else time.monotonic() - self._t0

    def levels(self):
        """(me, others) on a 0-1 scale for the chip's meters; speech is roughly 0.01-0.05 RMS."""
        scale = lambda rms: min(1.0, rms * 25.0)
        return scale(self.mic.level.rms), scale(self.output.level.rms)

    def devices(self):
        """(mic, speaker, hint) for the chip. The hint is set when they are different
        hardware: if the call plays out loud, the mic (and dictation) may hear it."""
        from audio_endpoints import same_device
        mic, speaker = getattr(self.mic, "name", None), getattr(self.output, "name", None)
        hint = None
        if mic and speaker and not same_device(mic, speaker):
            hint = "Separate mic and speaker: if the call plays out loud, dictation may hear it."
        return mic, speaker, hint

    @property
    def problem(self):
        """A short message for the chip, or None."""
        if getattr(self.output, "lost", False):
            return "Not recording the other side"
        return "No sound from your mic" if getattr(self.mic, "silent", False) else None

    def start(self):
        self.mic.start()
        try:
            self.output.start()
        except Exception:
            self.mic.stop()
            raise
        self._t0 = time.monotonic()
        self.meta.update(mic_device=self.mic.name, output_devices=[self.output.name])
        write_meta(self.folder, self.meta)
        self._transcription = MeetingTranscription(self.folder, self.mic_track, self.others_track,
                                                   self._get_model, self._busy, self._language, self._log)
        self._live_thread = threading.Thread(target=self._transcription.live, name="meeting-live", daemon=True)
        self._live_thread.start()

    def _stop_capture(self):
        self.mic.stop()
        self.output.stop()
        self.meta.update(duration_seconds=round(max(self.mic_track.seconds, self.others_track.seconds), 1),
                         output_devices=list(getattr(self.output, "devices", [])))

    def stop(self):
        """Stop recording and transcribe the rest in the background."""
        self._stop_capture()
        self._transcription.stop_live()
        self.meta["status"] = "transcribing"
        write_meta(self.folder, self.meta)
        heard = self.output.level.heard_fraction >= HEARD_FRACTION
        self._transcription._others_heard = heard
        threading.Thread(target=self._finish, name="meeting-finish", daemon=True).start()

    def _finish(self):
        if self._live_thread:
            self._live_thread.join()
        ok = False
        try:
            ok = self._transcription.finish(self.meta, self._closing.is_set)
            if ok:
                self.meta["status"] = "done"
        except Exception as error:
            self._log(f"Meeting: transcription failed ({type(error).__name__})")
            self.meta["status"] = "failed"
        if not ok and self.meta["status"] != "failed":
            self.meta["status"] = "interrupted"  # Quit first; the next launch finishes it.
        self._close_tracks()
        write_meta(self.folder, self.meta)
        self.finished.set()
        self._on_done(self.folder, ok)

    def _close_tracks(self):
        self.mic_track.close()
        self.others_track.close()

    def abandon(self):
        """App is quitting: keep the audio, leave the transcript for the next launch."""
        self._closing.set()
        if self.meta["status"] == "recording":
            self._stop_capture()
            if self._transcription:
                self._transcription.stop_live()
            self.meta["status"] = "interrupted"
            self._close_tracks()
            write_meta(self.folder, self.meta)


def transcribe_folder(folder, get_model, busy, language="en", log=print, stopped=lambda: False):
    """Transcribe a meeting from its WAV files, for a recording a quit or crash interrupted."""
    folder = Path(folder)
    meta = read_meta(folder)
    mic = WavTrack.existing(folder / "mic.wav")
    others = WavTrack.existing(folder / "others.wav")
    try:
        meta["duration_seconds"] = round(max(mic.seconds, others.seconds), 1)
        meta["status"] = "transcribing"
        write_meta(folder, meta)
        job = MeetingTranscription(folder, mic, others, get_model, busy, language, log)
        ok = job.finish(meta, stopped)
        meta["status"] = "done" if ok else "interrupted"
    except Exception as error:
        log(f"Meeting: transcription failed ({type(error).__name__})")
        meta["status"] = "failed"
        ok = False
    finally:
        mic.close()
        others.close()
    write_meta(folder, meta)
    return ok


# -- Library: the Meetings page ---------------------------------------------------

LINE = re.compile(r"^\*\*\[(?P<time>[\d:]+)\] (?P<speaker>[^:*]+):\*\* (?P<text>.*)$")
BUSY = ("recording", "transcribing")


def list_meetings(root):
    """Every meeting folder under root with a readable meta.json, newest first.
    Each item: folder, title, started (datetime), duration (seconds), status."""
    root = Path(root)
    found = []
    if not root.is_dir():
        return found
    for meta_path in root.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(meta["started"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        found.append({"folder": meta_path.parent, "title": meta.get("title") or "Untitled",
                      "started": started, "duration": meta.get("duration_seconds") or 0,
                      "status": meta.get("status", "done")})
    found.sort(key=lambda item: item["started"], reverse=True)
    return found


def display_title(item):
    """An untitled meeting is named by its start time, so a list of them stays readable."""
    if item["title"] in ("", "Untitled"):
        return f"Untitled · {item['started']:%I:%M %p}".replace("· 0", "· ")
    return item["title"]


def day_label(started, today):
    """Group heading: Today, Nov 24 / Yesterday, Nov 23 / Friday, Nov 21 / Nov 3, 2025."""
    day = started.date()
    month_day = f"{started:%b} {day.day}"
    if day == today:
        return f"Today, {month_day}"
    if (today - day).days == 1:
        return f"Yesterday, {month_day}"
    if day.year == today.year:
        return f"{started:%A}, {month_day}"
    return f"{month_day}, {day.year}"


def read_transcript(folder):
    try:
        return (Path(folder) / "transcript.md").read_text(encoding="utf-8")
    except OSError:
        return None


def transcript_lines(text, limit=None):
    """(time, speaker, text) for each spoken line of a transcript.md."""
    lines = []
    for raw in (text or "").splitlines():
        match = LINE.match(raw.strip())
        if match:
            lines.append((match["time"], match["speaker"], match["text"]))
            if limit and len(lines) >= limit:
                break
    return lines


def matches(item, query):
    """Case-insensitive match on the title or the transcript text."""
    query = query.strip().lower()
    if not query:
        return True
    if query in display_title(item).lower():
        return True
    text = read_transcript(item["folder"])
    return bool(text) and query in text.lower()


def rename_meeting(folder, title):
    """Set the title in meta.json and the transcript's heading. Folder names stay date-based."""
    title = " ".join(title.split())[:120]
    if not title:
        raise ValueError("A meeting needs a title")
    folder = Path(folder)
    meta = read_meta(folder)
    meta["title"] = title
    write_meta(folder, meta)
    text = read_transcript(folder)
    if text is not None and text.startswith("# "):
        rest = text.split("\n", 1)[1] if "\n" in text else ""
        (folder / "transcript.md").write_text(f"# {title}\n{rest}", encoding="utf-8")
    return meta


def trash_folder(folder):
    """Move a meeting folder to the Recycle Bin. Returns True when it is gone."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    folder = Path(folder).resolve()
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=str(folder) + "\0",
                         fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted and not folder.exists()
