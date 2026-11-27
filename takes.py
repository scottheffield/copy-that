"""Bounded recording queue. Exactly one worker owns inference and automatic paste."""
import copy
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from context import destination_key, identifier, merge_terms, number_names, relevant, strong_name


# A hold this long that still gives too little audio to transcribe means the microphone stopped sending.
MIC_STOPPED_MIN_HOLD_SECONDS = 1.0


def with_separator(text, config):
    """Trailing space so the next take starts a new word; never before Enter."""
    if (text and config.get("append_space", True) and not config.get("auto_enter", False)
            and not text[-1].isspace()):
        return text + " "
    return text


@dataclass
class Take:
    destination: object
    config: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    status: str = "recording"
    cancelled: threading.Event = field(default_factory=threading.Event)
    audio: object = None
    raw: str = ""
    rules: str = ""
    text: str = ""
    fallback: str | None = None
    timings: dict = field(default_factory=dict)
    started: float = 0
    released: float = 0
    live: object = None  # LiveTranscript while recognition runs during the take
    mic_silent_at: float | None = None  # Seconds in when the mic sent only zeros (#43)
    # Above "field", which shadows dataclasses.field from here on.
    screen_terms: list = field(default_factory=list)  # Names visible in the window at hotkey down
    field: str = ""      # Focused box, from the text field probe


class TakeQueue:
    def __init__(self, recorder, transcribe, clean, paste, foreground, same_destination,
                 modifiers_down, notify, config, take_log=None, live=None, context=None):
        self.recorder, self.transcribe, self.clean, self.paste = recorder, transcribe, clean, paste
        self.live = live  # Optional factory: take -> LiveTranscript or None
        self.context = context  # Optional RecentTerms: names and terms per destination
        self.foreground, self.same_destination = foreground, same_destination
        self.modifiers_down, self.notify, self.config = modifiers_down, notify, config
        self.take_log = take_log
        self._lock = threading.RLock()
        self._takes = []
        self._history = []
        self._recording = None
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self._worker = None

    @property
    def is_recording(self):
        return self._recording is not None

    @property
    def active(self):
        return any(t.status not in ("done", "retained", "failed", "cancelled") for t in self._takes)

    @property
    def cancellable(self):
        return any(t.status not in ("done", "retained", "failed", "cancelled", "pasting")
                   and not t.cancelled.is_set() for t in self._takes)

    @property
    def history(self):
        with self._lock:
            return copy.deepcopy(self._history)

    def state(self):
        if self.is_recording:
            return "recording"
        if self.active:
            return "processing"
        return "ready"

    def _notify(self, **data):
        self.notify(self.state(), data)

    def start_worker(self):
        with self._lock:
            if self._stop.is_set():
                return
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="take-worker", daemon=True)
                self._worker.start()

    def start(self, destination=None):
        with self._lock:
            if self._stop.is_set() or self.is_recording:
                return False
            unfinished = sum(t.status not in ("done", "retained", "failed", "cancelled") for t in self._takes)
            if unfinished >= self.config.get("max_pending_takes", 3):
                self._notify(message="Busy: recording queue is full")
                return False
            take = Take(destination or self.foreground(), copy.deepcopy(self.config))
            self._takes.append(take)
            self._recording = take
            try:
                self.recorder.on_timeout = lambda: self.finish(take.id)
                self.recorder.start()
            except Exception:
                take.status = "failed"
                self._recording = None
                self._notify(message="Microphone could not start", error="microphone")
                self._prune()
                return False
            # Recording state first: per-take helpers report back on other
            # threads and the display ignores them until it shows recording.
            take.started = time.monotonic()
            self._notify(take_id=take.id)
            if self.live:
                try:
                    take.live = self.live(take)
                except Exception:
                    take.live = None  # Recognition after release still works.
            return True

    def finish(self, take_id=None):
        with self._lock:
            take = self._recording
            if take is None or (take_id is not None and take.id != take_id):
                return False
            # Detach first: release and timer can finalize a take only once.
            self._recording = None
            take.released = time.monotonic()
            if take.live:
                take.live.stop()
            try:
                take.audio = self.recorder.stop()
            except Exception:
                take.status = "failed"
                self._notify(message="Microphone could not finish", error="microphone")
                self._prune()
                return False
            if len(take.audio) < take.config["sample_rate"] * 0.3:
                take.status = "done"
                self._notify(message=self._too_short_message(take), finalized=True)
                self._prune()
                return True
            take.status = "queued"
            self.start_worker()
            self._queue.put(take)
            self._notify(take_id=take.id, finalized=True)
            return True

    @staticmethod
    def _too_short_message(take):
        hold = take.released - take.started
        if hold >= MIC_STOPPED_MIN_HOLD_SECONDS:
            return "Microphone stopped sending audio"
        return "Recording too short"

    def cancel(self):
        with self._lock:
            take = self._recording
            if take is None:
                take = next((t for t in reversed(self._takes)
                             if t.status not in ("done", "retained", "failed", "cancelled", "pasting")
                             and not t.cancelled.is_set()), None)
            if take is None:
                return False
            take.cancelled.set()
            if take.live:
                take.live.stop()
            if take is self._recording:
                self._recording = None
                take.status = "cancelled"
                try:
                    self.recorder.stop()
                except Exception:
                    self._notify(message="Recording cancelled; microphone could not close", error="microphone")
            elif take.status == "queued":
                take.status = "cancelled"
            # Processing cancellation keeps its capacity until the worker returns.
            self._notify(message=f"Cancelled take {take.id[:6]}", cancelled_id=take.id, finalized=not self.is_recording)
            return True

    def _run(self):
        while True:
            take = self._queue.get()
            try:
                if take is None:
                    return
                if take.cancelled.is_set() or self._stop.is_set():
                    take.status = "cancelled"
                    continue
                take.status = "processing"
                self._notify(take_id=take.id)
                start = time.monotonic()
                take.timings["queue_seconds"] = start - take.released
                # Reject digital silence before invoking a recognizer.
                import numpy as np
                rms = float(np.sqrt(np.mean(np.square(take.audio))))
                if rms < take.config.get("silence_rms", 0.0001):
                    take.status = "done"
                    self._notify(message="No speech detected")
                    continue
                if take.config.get("vad_enabled", True):
                    from speech_gate import has_speech
                    if not has_speech(take.audio, take.config["sample_rate"]):
                        take.status = "done"
                        self._notify(message="No speech detected")
                        continue
                if take.live:
                    take.raw = self.transcribe(take.audio, take.config, live=take.live)
                else:
                    take.raw = self.transcribe(take.audio, take.config)
                take.timings["transcribe_seconds"] = time.monotonic() - start
                if take.cancelled.is_set() or self._stop.is_set():
                    take.status = "cancelled"
                    continue
                start = time.monotonic()
                terms, spell = self._terms(take, take.raw)
                result = self.clean(take.raw, take.destination.process, take.config,
                                    take.cancelled.is_set, terms, spell)
                take.rules, take.text, take.fallback = result.rules, result.text, result.fallback
                take.timings["cleanup_seconds"] = time.monotonic() - start
                take.status = "awaiting_paste"
                deadline = time.monotonic() + take.config.get("paste_wait_seconds", 15)
                while self.modifiers_down() and not take.cancelled.is_set() and not self._stop.is_set():
                    if time.monotonic() >= deadline:
                        break
                    self._stop.wait(0.02)
                if take.cancelled.is_set() or self._stop.is_set():
                    take.status = "cancelled"
                    continue
                def guard():
                    return (not take.cancelled.is_set() and not self._stop.is_set()
                            and self.same_destination(take.destination))
                def commit():
                    # Serialize cancellation against the irreversible paste.
                    with self._lock:
                        if self.modifiers_down() or not guard():
                            return False
                        take.status = "pasting"
                        return True
                payload = with_separator(take.text, take.config)
                start = time.monotonic()
                will_paste = bool(take.text and not self.modifiers_down() and guard())
                if will_paste:
                    # Clear live text from the destination before the text lands there.
                    self._notify(pasting=take.id)
                pasted = bool(will_paste
                              and self.paste(payload, take.config.get("auto_enter", False), guard, commit))
                take.timings["paste_seconds"] = time.monotonic() - start
                take.timings["release_to_result_seconds"] = time.monotonic() - take.released
                take.status = "done" if pasted or not take.text else "retained"
                message = "" if pasted or not take.text else "Not pasted: press Shift+Alt+Z to paste it"
                self._save_result(take)
                self._notify(text=take.text, message=message, take_id=take.id, retained=take.status == "retained",
                             pasted=pasted, seconds=len(take.audio) / take.config["sample_rate"],
                             timings=take.timings.copy(), fallback=take.fallback)
            except Exception:
                if take is not None:
                    take.status = "failed"
                    take.fallback = "processing_error"
                    # Raw recognition remains recoverable after a paste failure.
                    self._save_result(take)
                    self._notify(error="processing", message="Take failed; available text saved in History")
            finally:
                if take is not None:
                    with self._lock:
                        if take.cancelled.is_set():
                            take.status = "cancelled"
                        self._prune()
                        if take.cancelled.is_set() or not take.text:
                            self._notify()
                self._queue.task_done()

    def _key(self, take):
        return destination_key(take.destination.hwnd, take.field, take.destination.process)

    def _terms(self, take, raw=""):
        """Terms for Polish, and the on-screen identifiers that should make it run."""
        if not (self.context and take.config.get("polish_context", True)):
            return [], []
        found = self.context.terms(self._key(take), getattr(take.destination, "title", ""))
        screen = relevant(take.screen_terms, raw)
        spell = [term for term in screen if identifier(term) or strong_name(term, raw)] + number_names(take.screen_terms, raw)
        return merge_terms(found, screen), spell

    def _save_result(self, take):
        if self.context and take.text and take.status == "done":
            self.context.add(self._key(take), take.text)
        entry = {"id": take.id, "timestamp": take.timestamp, "duration": len(take.audio) / take.config["sample_rate"],
                 "raw": take.raw, "rules": take.rules, "text": take.text or take.raw,
                 "status": take.status, "fallback": take.fallback, "process": take.destination.process,
                 # Where the text went, so replay can group takes by destination.
                 "window": {"hwnd": take.destination.hwnd, "title": getattr(take.destination, "title", "")},
                 "field": take.field, "screen_terms": take.screen_terms, "timings": take.timings,
                 "model": take.config.get("model_size"), "actual_device": take.config.get("actual_device"),
                 "actual_decoder_device": take.config.get("actual_decoder_device"),
                 "literal_mode": take.config.get("literal_mode", False),
                 "mic_silent_at": take.mic_silent_at}
        with self._lock:
            self._history.append(copy.deepcopy(entry))
            self._history = self._history[-20:]
        if take.config.get("take_log_enabled") and self.take_log:
            try:
                self.take_log(take, entry)
            except Exception:
                self._notify(message="Take log could not be saved")

    def _prune(self):
        self._takes = [t for t in self._takes if t.status not in ("done", "retained", "failed", "cancelled")]

    def stop(self):
        with self._lock:
            self._stop.set()
            for take in self._takes:
                take.cancelled.set()
                if take.live:
                    take.live.stop()
            if self._recording:
                self.recorder.stop()
                self._recording = None
            if self._worker:
                self._queue.put(None)
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(2)

    def wait_idle(self, timeout=5):
        end = time.monotonic() + timeout
        while self.active or self._queue.unfinished_tasks:
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True
