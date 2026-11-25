"""Bounded Parakeet windows and conservative overlap stitching."""
import re
import numpy as np
from dataclasses import dataclass
from difflib import SequenceMatcher


class Cancelled(Exception):
    """A provisional recognition pass was abandoned."""


@dataclass(frozen=True)
class TimedWord:
    text: str
    time: float


def _window_end(audio, start, sample_rate, length):
    """End of the window at start. Reads no audio past start + length, so a
    window that ends before the recording does is final while still recording."""
    end = min(start + length, len(audio))
    if end < len(audio):
        frame = int(0.2 * sample_rate)
        candidates = range(max(start + length // 2, end - 2 * sample_rate), end - frame + 1, frame)
        quiet = [(float(np.sqrt(np.mean(np.square(audio[pos:pos + frame])))), pos + frame // 2)
                 for pos in candidates]
        if quiet:
            energy, boundary = min(quiet)
            if energy < 0.003:
                end = boundary
    return end


def _window_sizes(sample_rate, maximum, overlap):
    length, shared = int(maximum * sample_rate), int(overlap * sample_rate)
    if length <= shared or shared < 0:
        raise ValueError("Window length must exceed overlap")
    return length, shared


def window_ranges(audio, sample_rate=16000, maximum=14.5, overlap=2.5):
    length, shared = _window_sizes(sample_rate, maximum, overlap)
    start = 0
    while start < len(audio):
        end = _window_end(audio, start, sample_rate, length)
        yield start, end
        if end == len(audio):
            break
        start = max(start + 1, end - shared)


class WindowedTranscript:
    """Transcribe a growing recording window by window.

    Windows that end before the recording so far are final, so they can be
    recognized while the user is still talking; the result after the last
    window equals recognizing the whole recording at once.

    transcribe(samples, offset_seconds) returns TimedWords with absolute times.
    """

    def __init__(self, transcribe, sample_rate=16000, maximum=14.5, overlap=2.5):
        self._transcribe, self.sample_rate = transcribe, sample_rate
        self._length, self._shared = _window_sizes(sample_rate, maximum, overlap)
        self.words = []
        self._start = 0
        self._previous_end = None
        self.complete = False

    def _join(self, words, start):
        if self._previous_end is None:
            return list(words)
        return stitch_overlap(self.words, words, start / self.sample_rate,
                              self._previous_end / self.sample_rate)

    def advance(self, audio, final=False):
        """Recognize every window that is final for this audio. Returns the count."""
        added = 0
        while not self.complete and self._start < len(audio):
            if not final and self._start + self._length >= len(audio):
                break  # This window can still grow or move its cut.
            start = self._start
            end = _window_end(audio, start, self.sample_rate, self._length)
            words = self._transcribe(audio[start:end], start / self.sample_rate)
            self.words, self._previous_end = self._join(words, start), end
            added += 1
            if end == len(audio):
                self.complete = True
            else:
                self._start = max(start + 1, end - self._shared)
        if final:
            self.complete = True
        return added

    @property
    def position(self):
        """Samples recognized so far (the start of the next window)."""
        return self._start

    def preview(self, audio):
        """Final windows plus a provisional pass over the open tail; commits nothing."""
        if self._start >= len(audio):
            return list(self.words)
        tail = self._transcribe(audio[self._start:], self._start / self.sample_rate)
        return self._join(tail, self._start)

    @property
    def text(self):
        return " ".join(word.text for word in self.words)


def windows(audio, sample_rate=16000, maximum=14.5, overlap=2.5):
    for start, end in window_ranges(audio, sample_rate, maximum, overlap):
        yield audio[start:end]


def stitch_timed(left, right, tolerance=1.0):
    """Remove matching boundary words only when their audio times also agree.

    Decoder emission times are approximate. Uncertain matches retain words;
    a later repetition outside the shared audio must never be removed.
    """
    normalize = lambda word: re.sub(r"[^\w']", "", word.text.casefold())
    for size in range(min(len(left), len(right), 16), 0, -1):
        pairs = list(zip(left[-size:], right[:size]))
        # Longer agreement can tolerate decoder delay near a cut. A lone word
        # needs close timing so a quick "no, no" repetition remains intact.
        allowed_delay = min(tolerance, size * 0.12)
        if all(normalize(a) == normalize(b) and abs(a.time - b.time) <= allowed_delay
               for a, b in pairs):
            return left + right[size:]
    return left + right


def stitch_overlap(left, right, overlap_start, overlap_end, edge=0.5):
    """Join two windows inside their shared audio so each word comes from one.

    Window edges are unreliable in both directions: a word clipped at a cut is
    misheard ("text box" became "Xbox"), and a window's first second can miss
    words it lacks context for. Prefer a junction at words both windows agree
    on, in text and time, nearest the overlap middle. Without agreement, trust
    the left window until shortly before its clipped end.
    """
    normalize = lambda word: re.sub(r"[^\w']", "", word.text.casefold())
    left_ids = [i for i, w in enumerate(left) if w.time >= overlap_start - 0.3]
    right_ids = [j for j, w in enumerate(right) if w.time <= overlap_end + 0.3]
    matcher = SequenceMatcher(None, [normalize(left[i]) for i in left_ids],
                              [normalize(right[j]) for j in right_ids], autojunk=False)
    middle = (overlap_start + overlap_end) / 2
    anchors = [(left_ids[a + k], right_ids[b + k])
               for a, b, size in matcher.get_matching_blocks() if size >= 2
               for k in range(size)
               if abs(left[left_ids[a + k]].time - right[right_ids[b + k]].time) <= 0.5]
    if anchors:
        i, j = min(anchors, key=lambda pair: abs(left[pair[0]].time - middle))
        # The right window heard what follows the anchor, so its punctuation wins.
        return left[:i] + right[j:]
    kept = [word for word in left if word.time < overlap_end - edge]
    cutoff = kept[-1].time if kept else overlap_start
    # Emission times jitter by about one encoder frame between windows.
    tail = [word for word in right if word.time > cutoff + 0.04]
    return stitch_timed(kept, tail)


def stitch(left, right, max_words=6):
    # Only 0.75 seconds of audio is shared. A whole repeated sentence cannot
    # be overlap; prefer a duplicate word over deleting newly spoken content.
    if not left:
        return right.strip()
    if not right:
        return left.strip()
    a, b = left.split(), right.split()
    normalize = lambda word: re.sub(r"[^\w']", "", word.casefold())
    for size in range(min(max_words, len(a), len(b)), 0, -1):
        if [normalize(w) for w in a[-size:]] == [normalize(w) for w in b[:size]]:
            return " ".join(a + b[size:])
    return left.rstrip() + " " + right.lstrip()
