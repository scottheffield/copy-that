"""Label who spoke in a meeting transcript (#44).

After a meeting's transcript is saved, speaker separation runs in the
background on one track: Others when the laptop played the call, or the mic
for an in-person meeting. Each recognized word gets the speaker who was
talking at its time, and transcript.md is rewritten with Speaker 1..N,
numbered by who speaks first. The words come from words.json, so nothing is
recognized twice.

meta.json records {"speakers": {"status": ..., "count": N}}, where status is
pending, running, done, failed or skipped.
"""
import time
import wave
from pathlib import Path

import numpy as np

import speaker_models
from meetings import RATE, read_meta, read_words, render_transcript, write_meta

# Settings from the #44 spike: 9 of 9 voices on two panel recordings.
CLUSTER_THRESHOLD = 1.0
MIN_DURATION_ON = 0.3
MIN_DURATION_OFF = 0.5
NUM_THREADS = 2
NEAREST_SECONDS = 1.0  # A word outside every segment takes a segment this close


class Stopped(Exception):
    """The app is quitting; speaker separation was abandoned."""


_diarizer = None
_progress = {}  # folder -> fraction done, while its speakers are being found


def progress(folder):
    """Fraction done while a meeting's speakers are being found, else None."""
    return _progress.get(Path(folder))


def _load():
    global _diarizer
    if _diarizer is None:
        import sherpa_onnx
        reason = speaker_models.problem()
        if reason:
            raise FileNotFoundError(f"Speaker models unavailable: {reason}")
        seg, emb = speaker_models.model_paths()
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg)),
                num_threads=NUM_THREADS),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb), num_threads=NUM_THREADS),
            clustering=sherpa_onnx.FastClusteringConfig(threshold=CLUSTER_THRESHOLD),
            min_duration_on=MIN_DURATION_ON, min_duration_off=MIN_DURATION_OFF)
        if not config.validate():
            raise ValueError("Speaker separation settings were rejected")
        _diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    return _diarizer


def read_track(path):
    """A meeting WAV (16 kHz mono 16-bit) as float32 samples."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("Unexpected meeting audio format")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def diarize(samples, stopped=lambda: False, report=lambda fraction: None):
    """[(start, end, speaker id)] sorted by start, for 16 kHz samples."""
    diarizer = _load()
    if diarizer.sample_rate != RATE:
        raise ValueError("Speaker models expect a different sample rate")

    def callback(done, total):
        report(done / total if total else 0.0)
        return 1 if stopped() else 0
    result = diarizer.process(samples, callback=callback)
    if stopped():
        raise Stopped()
    return [(s.start, s.end, s.speaker) for s in result.sort_by_start_time()]


def assign(times, segments, nearest=NEAREST_SECONDS):
    """The speaker id for each word time, or None when no one was found near it.

    Where segments overlap, the previous word's speaker is kept if still talking,
    else the shortest segment wins (a short interjection over a long turn)."""
    out, previous = [], None
    for t in times:
        covering = [s for s in segments if s[0] <= t <= s[1]]
        if covering:
            ids = [s[2] for s in covering]
            speaker = previous if previous in ids else min(covering, key=lambda s: s[1] - s[0])[2]
        else:
            close = [(min(abs(t - s[0]), abs(t - s[1])), s[2]) for s in segments]
            close = [c for c in close if c[0] <= nearest]
            speaker = min(close)[1] if close else None
        out.append(speaker)
        if speaker is not None:
            previous = speaker
    # A word no one was near joins the speaker before it (or after it, at the start).
    for i in range(len(out)):
        if out[i] is None and i > 0:
            out[i] = out[i - 1]
    for i in range(len(out) - 2, -1, -1):
        if out[i] is None:
            out[i] = out[i + 1]
    return out


def number(ids):
    """Speaker 1..N names, in the order each speaker first talks."""
    names = {}
    for speaker in ids:
        if speaker is not None and speaker not in names:
            names[speaker] = f"Speaker {len(names) + 1}"
    return [names.get(speaker, "Speaker") for speaker in ids]


def _set_status(folder, status, **extra):
    meta = read_meta(folder)  # Fresh: a rename may have happened meanwhile
    meta["speakers"] = {"status": status, **extra}
    write_meta(folder, meta)
    return meta


def find_speakers(folder, log=print, stopped=lambda: False, diarize_fn=diarize):
    """Label the speakers in one meeting's transcript. Returns the final status."""
    folder = Path(folder)
    try:
        mic_words, others_words, heard = read_words(folder)
    except (OSError, ValueError, KeyError, TypeError):
        _set_status(folder, "skipped", reason="no word times")
        return "skipped"
    track, words = ("others", others_words) if heard else ("mic", mic_words)
    if not words:
        _set_status(folder, "done", count=0)
        return "done"
    _set_status(folder, "running")
    started = time.monotonic()
    _progress[folder] = 0.0
    try:
        samples = read_track(folder / f"{track}.wav")
        segments = diarize_fn(samples, stopped, lambda f: _progress.__setitem__(folder, f))
        del samples
        ids = assign([w.time for w in words], segments)
    except Stopped:
        _set_status(folder, "pending")  # Runs again on the next launch
        return "pending"
    except Exception as error:
        log(f"Meeting: finding speakers failed ({type(error).__name__}: {error})")
        _set_status(folder, "failed")
        return "failed"
    finally:
        _progress.pop(folder, None)
    count = len({i for i in ids if i is not None})
    meta = read_meta(folder)
    if count > 1:
        text = render_transcript(meta, mic_words, others_words, heard, labels={track: number(ids)})
        (folder / "transcript.md").write_text(text, encoding="utf-8")
    # One voice: the transcript's Others / Speaker label already says it.
    meta["speakers"] = {"status": "done", "count": count, "track": track}
    write_meta(folder, meta)
    log(f"Meeting: found {count} speaker(s) in {folder.name} "
        f"({len(segments)} segments, {time.monotonic() - started:.1f}s)")
    return "done"
