"""Meeting playback (#63): the two tracks played as one call, and exported as one file.

A meeting keeps its microphone and the other side as separate tracks (mic, others),
WAV while recording and Opus once compressed (#61). Playback mixes them a block at a
time near the playhead, so nothing is decoded ahead or stored twice:

- Levels are matched once per meeting, from a sample of each track's loudest moments,
  so the other side (recorded from the laptop's output) isn't far louder than the mic.
- On laptop speakers the mic also hears the other side, a little late, which sounds
  like an echo in the mix. While the other side is talking and the mic is clearly
  quieter, the mic is turned down (ducked). With a headset that changes nothing.

Files are open only while playing, so a paused meeting can still be renamed or
compressed. Output goes through sounddevice at 16 kHz; Windows' MME layer converts
the rate for the speakers.
"""
import queue
import threading
from pathlib import Path

import numpy as np

from meetings import RATE, TRACKS, track_file

BLOCK = RATE // 10          # Samples mixed at a time: 100 ms
STEP = RATE // 50           # Ducking decides every 20 ms
AHEAD = 8                   # Blocks mixed ahead of the speakers: 800 ms
TARGET_RMS = 0.1            # Matched speech level, about -20 dBFS
GAIN_RANGE = (0.5, 8.0)     # Level matching never cuts more than 6 dB or boosts more than 18 dB
LEVEL_WINDOWS = 48          # One-second windows sampled per track to measure its level
SILENT = 1e-3               # A window quieter than this has no speech in it
DUCK = 0.2                  # The mic's level while ducked: -14 dB
DUCK_OTHERS = 0.02          # The other side counts as talking above this (after matching)
DUCK_RATIO = 0.7            # ...and the mic as only echo below this fraction of it
DUCK_DOWN, DUCK_UP = 0.6, 0.12  # How far the mic's level moves toward its target per 20 ms

_open = {}                  # Folder: how many players or exports hold its tracks open
_open_lock = threading.Lock()


def holding(folder):
    """True while playback or an export has folder's audio files open (Windows can't
    rename or delete them meanwhile)."""
    with _open_lock:
        return _open.get(Path(folder), 0) > 0


def _hold(folder, on):
    with _open_lock:
        count = _open.get(folder, 0) + (1 if on else -1)
        if count > 0:
            _open[folder] = count
        else:
            _open.pop(folder, None)


class Sources:
    """A meeting's tracks open for reading, as float32 blocks that run on in silence past
    a track's end. A missing track reads as silence."""

    def __init__(self, folder):
        import soundfile
        self.folder = Path(folder)
        self._files = {}
        try:
            for name in TRACKS:
                path = track_file(self.folder, name)
                if path is not None:
                    self._files[name] = soundfile.SoundFile(str(path))
        except Exception:
            self.close()
            raise
        for f in self._files.values():
            if (f.samplerate, f.channels) != (RATE, 1):
                self.close()
                raise ValueError("Unexpected meeting audio format")
        self.frames = max((f.frames for f in self._files.values()), default=0)

    def read(self, name, start, count):
        f = self._files.get(name)
        out = np.zeros(count, dtype=np.float32)
        if f is None or start >= f.frames:
            return out
        f.seek(start)
        got = f.read(min(count, f.frames - start), dtype="float32")
        out[:len(got)] = got
        return out

    def level(self, name):
        """A track's speech level: the RMS of its louder sampled seconds; None if silent."""
        f = self._files.get(name)
        if f is None or f.frames < RATE:
            return None
        starts = np.linspace(0, f.frames - RATE, LEVEL_WINDOWS).astype(int)
        levels = [float(np.sqrt(np.mean(self.read(name, int(s), RATE) ** 2))) for s in starts]
        spoken = [x for x in levels if x > SILENT]
        if not spoken:
            return None
        return float(np.percentile(spoken, 75))

    def close(self):
        for f in self._files.values():
            try:
                f.close()
            except Exception:
                pass
        self._files = {}


def gains(sources):
    """{track: gain} that brings each track's speech to TARGET_RMS, within GAIN_RANGE."""
    result = {}
    for name in TRACKS:
        level = sources.level(name)
        result[name] = 1.0 if level is None else float(np.clip(TARGET_RMS / level, *GAIN_RANGE))
    return result


class Mixer:
    """Mixes matched blocks of the two tracks into one, ducking the mic under echo.
    Keeps the ducking level between blocks, so it is reset on a seek."""

    def __init__(self, track_gains):
        self.gains = track_gains
        self.reset()

    def reset(self):
        self._duck = 1.0

    def __call__(self, mic, others):
        mic = mic * self.gains.get("mic", 1.0)
        others = others * self.gains.get("others", 1.0)
        scale = np.empty(len(mic), dtype=np.float32)
        for start in range(0, len(mic), STEP):
            end = min(len(mic), start + STEP)
            o = float(np.sqrt(np.mean(others[start:end] ** 2)))
            m = float(np.sqrt(np.mean(mic[start:end] ** 2)))
            target = DUCK if o > DUCK_OTHERS and m < DUCK_RATIO * o else 1.0
            before = self._duck
            self._duck += (target - self._duck) * (DUCK_DOWN if target < self._duck else DUCK_UP)
            scale[start:end] = np.linspace(before, self._duck, end - start, dtype=np.float32)
        return np.clip(mic * scale + others, -1.0, 1.0)


class Player:
    """Plays one meeting. position and duration are in seconds. Call tick() now and then
    from the UI thread: it reports the end and releases the files.

    on_release(): called (from tick or pause) when the files are closed again, so work
    that waited for them (a folder rename) can go ahead."""

    def __init__(self, folder, on_release=lambda: None):
        self.folder = Path(folder)
        self._on_release = on_release
        self._gains = None
        self._position = 0          # Frames
        self._duration = None
        self._stream = None
        self._sources = None
        self._feeder = None
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=AHEAD)
        self._current = None        # (start frame, samples, offset) being played
        self._seek_to = None
        self._gen = 0               # Seeks so far
        self._lock = threading.Lock()
        self.ended = False
        self.error = None

    # -- What the page shows --------------------------------------------------

    @property
    def playing(self):
        return self._stream is not None

    @property
    def position(self):
        return self._position / RATE

    @property
    def duration(self):
        if self._duration is None:
            try:
                sources = Sources(self.folder)
                self._duration = sources.frames
                sources.close()
            except Exception:
                self._duration = 0
        return self._duration / RATE

    # -- Control ---------------------------------------------------------------

    def play(self):
        """Start from the current position. Returns False (with error set) if it can't."""
        if self.playing:
            return True
        import sounddevice as sd
        self.error = None
        try:
            sources = Sources(self.folder)
        except Exception as error:
            self.error = f"Couldn't open the meeting's audio ({type(error).__name__})"
            return False
        self._duration = sources.frames
        if self._position >= sources.frames:
            self._position = 0
        if self._gains is None:
            self._gains = gains(sources)
        _hold(self.folder, True)
        self._sources = sources
        self.ended = False
        self._stop.clear()
        self._drain()
        self._current = None
        self._feeder = threading.Thread(target=self._feed, args=(self._position,), daemon=True,
                                        name="meeting-playback")
        self._feeder.start()
        try:
            self._stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="float32",
                                           blocksize=BLOCK // 2, callback=self._callback)
            self._stream.start()
        except Exception as error:
            self._stream = None
            self.error = f"Couldn't play to the speakers ({type(error).__name__})"
            self._halt()
            return False
        return True

    def pause(self):
        if self.playing:
            self._halt()

    def toggle(self):
        if self.playing:
            self.pause()
            return True
        return self.play()

    def seek(self, seconds):
        frames = int(max(0.0, seconds) * RATE)
        if self._duration is not None:
            frames = min(frames, self._duration)
        self.ended = False
        with self._lock:
            self._position = frames
            if self.playing:
                self._gen += 1  # Blocks mixed before this are stale
                self._seek_to = frames
                self._current = None
        self._drain()

    def skip(self, seconds):
        self.seek(self.position + seconds)

    def tick(self):
        """From the UI thread: tidy up after the end of the meeting."""
        if self.ended and self._stream is not None:
            self._halt()
            self._position = self._duration or 0

    def close(self):
        self._halt()

    # -- Inside ----------------------------------------------------------------

    def _halt(self):
        stream, self._stream = self._stream, None
        self._stop.set()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._drain()
        feeder, self._feeder = self._feeder, None
        if feeder is not None:
            feeder.join(timeout=2)
        sources, self._sources = self._sources, None
        if sources is not None:
            sources.close()
            _hold(sources.folder, False)
            self._on_release()

    def _drain(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _feed(self, start):
        """Mix blocks from start onward into the queue, following seeks, until stopped.
        Each block carries the seek count it was mixed for, so one mixed just before a
        seek is never played after it."""
        mixer = Mixer(self._gains)
        sources, frame = self._sources, start
        with self._lock:
            gen = self._gen
        while not self._stop.is_set():
            with self._lock:
                if self._seek_to is not None:
                    frame, gen, self._seek_to = self._seek_to, self._gen, None
                    mixer.reset()
            if frame >= sources.frames:
                block = (gen, frame, None)  # The end
            else:
                count = min(BLOCK, sources.frames - frame)
                block = (gen, frame, mixer(sources.read("mic", frame, count), sources.read("others", frame, count)))
            while not self._stop.is_set() and gen == self._gen:
                try:
                    self._queue.put(block, timeout=0.05)
                    break
                except queue.Full:
                    continue
            if gen != self._gen:
                continue  # A seek came in meanwhile
            if self._stop.is_set() or block[2] is None:
                return
            frame += len(block[2])

    def _callback(self, outdata, frames, _time, _status):
        out = outdata[:, 0]
        filled = 0
        while filled < frames:
            with self._lock:
                current, gen = self._current, self._gen
            if current is None:
                try:
                    block = self._queue.get_nowait()
                except queue.Empty:
                    break  # Underrun: silence for the rest
                if block[0] != gen:
                    continue  # Mixed before a seek
                if block[2] is None:
                    self.ended = True
                    break
                current = (block[1], block[2], 0)
            start, samples, offset = current
            take = min(frames - filled, len(samples) - offset)
            out[filled:filled + take] = samples[offset:offset + take]
            filled += take
            offset += take
            with self._lock:
                if self._gen == gen:
                    self._current = None if offset >= len(samples) else (start, samples, offset)
                    self._position = start + offset
        out[filled:] = 0


def export(folder, path, report=lambda fraction: None, stopped=lambda: False):
    """Write the whole meeting, mixed as it plays, to one mono file: .mp3 or .opus.
    Written to path.part first, so a failure leaves nothing half-written. True when done."""
    import soundfile
    path = Path(path)
    if path.suffix.lower() == ".mp3":
        options = dict(format="MP3", subtype="MPEG_LAYER_III", compression_level=0.4)
    elif path.suffix.lower() == ".opus":
        from meetings import OPUS_LEVEL
        options = dict(format="OGG", subtype="OPUS", compression_level=OPUS_LEVEL)
    else:
        raise ValueError("Audio can be saved as .mp3 or .opus")
    part = path.with_name(path.name + ".part")
    sources = Sources(folder)
    _hold(sources.folder, True)
    try:
        mixer = Mixer(gains(sources))
        total = sources.frames
        with soundfile.SoundFile(str(part), "w", samplerate=RATE, channels=1, **options) as out:
            for frame in range(0, total, RATE * 10):
                if stopped():
                    break
                count = min(RATE * 10, total - frame)
                out.write(mixer(sources.read("mic", frame, count), sources.read("others", frame, count)))
                report(min(total, frame + count) / max(1, total))
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        sources.close()
        _hold(sources.folder, False)
    if stopped():
        part.unlink(missing_ok=True)
        return False
    part.replace(path)
    return True
