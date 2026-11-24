"""App lifecycle, command dispatch, and model integration for the tray and CLI."""
import queue
import time
import threading
from app_paths import data_directory
from cleanup import Cleanup
from context import RecentTerms
from hotkeys import KeyboardHook
from takes import TakeQueue, with_separator
from take_log import write_take
from windows_io import foreground, same_destination, modifiers_down, set_process_priority

WASAPI = "Windows WASAPI"


def find_input_device(devices, key):
    """The listed device for a saved key, including keys saved from another
    audio API before the list showed WASAPI only (MME names are truncated)."""
    exact = next((d for d in devices if d.get("key") == key), None)
    if exact or not key or ":" not in key:
        return exact
    name = key.split(":", 1)[1]
    return next((d for d in devices if d["name"] == name), None) or         next((d for d in devices if len(name) >= 31 and d["name"].startswith(name)), None)


class DictationRuntime:
    MAX_HISTORY = 20

    def __init__(self, config, state_enum, recorder_factory, log, paste):
        self.config = config
        self.State, self.log = state_enum, log
        self.whisper = None
        self._model_ready = threading.Event()
        self._load_error = None
        self._state = self.State.LOADING
        self._callbacks = []
        self._stopped = threading.Event()
        self._commands = queue.SimpleQueue()
        self._hook = None
        self._gesture_epoch = 0
        self._cleanup = Cleanup(config)
        self._last_recording = False
        self._chime_lock = threading.Lock()
        self._recorder = recorder_factory(sample_rate=config["sample_rate"],
            max_record_seconds=config.get("max_record_seconds"), input_device=config.get("input_device"))
        self._recorder.on_warning = self._limit_warning
        self._recorder.warning_seconds = config.get("limit_warning_seconds", 15)
        self._paste = paste
        self._takes = TakeQueue(self._recorder, self._transcribe, self._clean, paste,
            foreground, same_destination, self._modifiers_down, self._on_take_event, config,
            lambda take, entry: write_take(take.config["take_log_dir"], take, entry),
            live=self._begin_take, context=RecentTerms())
        self._focus_probe = None
        self._media = None
        self._priority_raised = False
        self._load_thread = None
        self._command_thread = None
        self._load_generation = 0
        self.meeting_active = False  # Set by the app while a meeting is being recorded

    @property
    def recorder(self):
        return self._recorder

    @recorder.setter
    def recorder(self, recorder):
        self._recorder = recorder
        self._takes.recorder = recorder

    @property
    def is_recording(self):
        return self._takes.is_recording

    @property
    def active(self):
        return self._takes.active or bool(self._cleanup.model and self._cleanup.model.busy)

    @property
    def state(self):
        return self._state

    @property
    def speech_model(self):
        """The loaded speech model, or None while loading or after a failed load."""
        if self._model_ready.is_set() and not self._load_error and not self._stopped.is_set():
            return self.whisper
        return None

    @property
    def history(self):
        return self._takes.history

    def add_callback(self, fn):
        self._callbacks.append(fn)

    def _set_state(self, state, data=None):
        if self._stopped.is_set():
            return
        self._state = state
        for callback in self._callbacks:
            try:
                callback(state, data or {})
            except Exception:
                self.log("UI callback failed")

    def _on_take_event(self, state, data):
        timings = data.get("timings")
        if timings:
            self.log("Take finished: "
                     f"recognition={timings.get('transcribe_seconds', 0):.2f}s, "
                     f"cleanup={timings.get('cleanup_seconds', 0):.2f}s, "
                     f"release-to-result={timings.get('release_to_result_seconds', 0):.2f}s, "
                     f"fallback={data.get('fallback') or 'none'}")
        self._cue_transition(self.is_recording)
        if state == "ready":
            self._restore_priority()
        if data.get("pasted") and self.config.get("beep_on_start", True):
            from dictation_engine import chime_paste
            threading.Thread(target=chime_paste, daemon=True).start()
        if data.get("finalized") and self._hook:
            self._hook.finish(self._gesture_epoch)
        self._set_state(self.State(state), data)

    def _cue_transition(self, recording):
        # Hook, recording and worker threads all report; one transition, one cue.
        with self._chime_lock:
            changed = recording != self._last_recording
            self._last_recording = recording
        if not changed:
            return
        self._pause_media(recording)
        if not self.config.get("beep_on_start", True):
            return
        from dictation_engine import chime_start, chime_stop
        if recording:
            live = getattr(self._recorder, "live", None)
            def cue_when_heard():
                # The cue means "you are being heard": wait for real audio, not
                # just an open stream, but never longer than a short fallback.
                if live is not None:
                    heard = live.wait(self.config.get("start_cue_wait_seconds", 0.4))
                    self.log("Mic delivering audio" if heard else "Mic still silent; start cue anyway")
                chime_start()
            threading.Thread(target=cue_when_heard, name="start-cue", daemon=True).start()
        else:
            chime_stop()  # Non-blocking; plays through the already-awake output.

    def _pause_media(self, recording):
        """Pause what is playing while the mic is open; resume only that."""
        if recording and (not self.config.get("pause_media", True) or self.meeting_active):
            return  # During a meeting recording, the call's audio must keep playing.
        if self._media is None:
            from media_pause import MediaPause
            self._media = MediaPause(self.log)
        self._media.pause() if recording else self._media.resume()

    def _modifiers_down(self):
        """Held modifiers delay the paste; a stuck one is released first, not waited out."""
        if self._hook:
            self._hook.release_stuck()
        return modifiers_down()

    def _streams(self):
        # Class attribute, so test doubles never opt in by accident.
        return (self.config.get("live_recognition", True)
                and getattr(type(self.whisper), "supports_streaming", False) is True
                and hasattr(self._recorder, "snapshot"))

    def _begin_take(self, take):
        """Per-take helpers at recording start: where the text will land, and live recognition."""
        self._probe_field(take)
        self._read_screen(take)
        return self._begin_live(take)

    def _probe_field(self, take):
        if not take.config.get("probe_text_field", True) or not getattr(take.destination, "hwnd", 0):
            return
        def report(info):
            take.field = info.field
            self.log(f"Text field: control={info.control} editable={info.editable} "
                     f"caret={'yes' if info.caret else 'no'} probe={info.ms:.0f}ms")
            if self._takes._recording is take:
                self._set_state(self.state, {"field": info})
        self._probe().probe(take.destination.hwnd, report)

    def _read_screen(self, take):
        """Names visible in the window, read while the user talks."""
        if not (take.config.get("screen_context", True) and take.config.get("polish_context", True)
                and take.config.get("cleanup_enabled") and getattr(take.destination, "hwnd", 0)):
            return
        from context import screen_terms
        def report(items, selection, ms):
            take.screen_terms = screen_terms(items, selection)
            # Counts only: screen text, like transcripts, stays out of the log.
            self.log(f"Screen context: {len(items)} elements, {len(take.screen_terms)} terms, "
                     f"selection={'yes' if selection else 'no'}, read={ms:.0f}ms")
        self._probe().read_screen(take.destination.hwnd, report)

    def _probe(self):
        if self._focus_probe is None:
            from focus_probe import FocusProbe
            self._focus_probe = FocusProbe(self.log)
        return self._focus_probe

    def _begin_live(self, take):
        if not self._streams():
            return None
        from live_transcript import LiveTranscript
        def on_text(text):
            # Same state: live text never changes recording/processing status.
            if take.config.get("show_live_text", True) and self._takes._recording is take:
                self._set_state(self.state, {"live_text": text})
        return LiveTranscript(self.whisper, self._recorder.snapshot, take.config["sample_rate"],
                              take.config["language"], on_text,
                              take.config.get("live_interval_seconds", 0.3), self.log)

    def _raise_priority(self):
        """Win CPU contention from hotkey to paste only; idle CopyThat stays normal."""
        level = self.config.get("dictation_priority", "above_normal")
        if level == "normal" or self._priority_raised:
            return
        try:
            self._priority_raised = set_process_priority(level)
        except Exception:
            self._priority_raised = False
        self.log(f"Priority: {level}" if self._priority_raised else f"Priority: {level} refused by Windows")

    def _restore_priority(self):
        if not self._priority_raised:
            return
        self._priority_raised = False
        try:
            set_process_priority("normal")
        except Exception:
            self.log("Priority: could not return to normal")

    def _start_keep_warm(self):
        interval = self.config.get("keep_warm_seconds", 2)
        if not interval or not hasattr(self.whisper, "keep_warm") or self._streams():
            return  # Live recognition keeps the accelerators busy itself.
        def loop():
            # Warm immediately, then periodically, only while this take records.
            while self.is_recording and not self._stopped.is_set():
                try:
                    self.whisper.keep_warm()
                except Exception:
                    return
                self._stopped.wait(interval)
        threading.Thread(target=loop, name="keep-warm", daemon=True).start()

    def _limit_warning(self):
        if not self.is_recording:
            return
        seconds = self.config.get("limit_warning_seconds", 15)
        if self.config.get("beep_on_start", True):
            from dictation_engine import chime_warning
            threading.Thread(target=chime_warning, daemon=True).start()
        self._set_state(self.State.RECORDING, {"message": f"Recording ends in {seconds} seconds"})

    def _load_model_background(self, generation=None):
        def superseded():
            return generation is not None and generation != self._load_generation
        if superseded():
            return
        self._set_state(self.State.LOADING)
        try:
            if self.config.get("probe_text_field", True):
                self._probe().warm()  # While loading, not on the first take.
            self.ensure_model()
            self._cleanup.load(data_directory() / "models")
            if self._cleanup.model:
                from pathlib import Path
                for note in getattr(self._cleanup.model, "device_notes", ()):
                    self.log(f"Cleanup device:{note}")
                self.log(f"Cleanup loaded: {Path(self.config['cleanup_model_path']).name} on {self._cleanup.model.device}; "
                         f"prompt_lookup={self._cleanup.model.prompt_lookup}")
            if self._stopped.is_set() or superseded():
                return
            self._model_ready.set()
            notice = "Cleanup unavailable; using rules" if self._cleanup.load_error else ""
            self._set_state(self.State.READY, {"message": notice})
            self.log("Models ready; hold Ctrl+Win to dictate")
        except Exception as error:
            if superseded():
                return
            self._load_error = type(error).__name__
            self._model_ready.set()
            self._set_state(self.State.ERROR, {"error": "Speech model unavailable. Run setup."})
            self.log(f"Speech model loading failed ({type(error).__name__})")

    def _transcribe(self, audio, config, live=None):
        config["actual_device"] = self.whisper.device
        config["actual_decoder_device"] = getattr(self.whisper, "decoder_device", None)
        if live is not None:
            try:
                text = live.finish(audio)
                self.log(f"Live recognition: {live.windows_while_recording} window(s) done before release")
                return text
            except Exception as error:
                self.log(f"Live recognition failed at release ({type(error).__name__}); recognizing again")
        return self.whisper.transcribe(audio, sample_rate=config["sample_rate"], language=config["language"])

    def _clean(self, raw, process, config, cancelled, terms=(), spell=()):
        # Each take owns its settings even if the user changes literal mode later.
        cleaner = Cleanup(config, self._cleanup.model)
        return cleaner.run(raw, process, config.get("literal_mode", False), cancelled, terms, spell)

    def start_recording(self, destination=None):
        if self._stopped.is_set() or not self._model_ready.is_set() or self._load_error:
            if self.state == self.State.LOADING:
                # Shown in the bubble: the hotkey worked, the models aren't up yet.
                self._set_state(self.state, {"message": "Still loading, ready in a moment", "loading": True})
            else:
                self._set_state(self.state, {"message": "Speech model is not ready"})
            return False
        key = self.config.get("input_device_key")
        if key:
            device = find_input_device(self.list_input_devices(), key)
            if device is None:
                self._set_state(self.state, {"message": "Selected microphone is unavailable; choose a microphone in Settings"})
                return False
            self.recorder.input_device = device["index"]
        return self._takes.start(destination)

    def finish_recording(self):
        return self._takes.finish()

    def toggle_recording(self):
        if self.is_recording:
            return self.finish_recording()
        started = self.start_recording()
        if started:
            # Started by a click, not the held chord; Space does not apply.
            self._set_state(self.State.RECORDING, {"mode": "toggle"})
        return started

    def cancel(self):
        return self._takes.cancel()

    def close_take(self):
        """The bubble's X: cancel like Escape, and free modifiers Windows still holds; any thread."""
        self.dispatch("close")

    def paste_last(self):
        """Explicit recovery: paste the latest result where the user is now."""
        entry = next((e for e in reversed(self.history) if e.get("text")), None)
        if entry is None:
            self._set_state(self.state, {"message": "Nothing to paste yet"})
            return False
        deadline = time.monotonic() + 5
        while modifiers_down() and time.monotonic() < deadline:
            time.sleep(0.02)
        destination = foreground()
        # The user chose this target; only refuse our own windows.
        allowed = lambda: same_destination(destination)
        pasted = bool(self._paste(with_separator(entry["text"], self.config), False, allowed, allowed))
        if pasted and self.config.get("beep_on_start", True):
            from dictation_engine import chime_paste
            threading.Thread(target=chime_paste, daemon=True).start()
        if not pasted:
            self._set_state(self.state, {"message": "Click into a text field, then press Shift+Alt+Z"})
        return pasted

    def dispatch(self, action, destination=None, epoch=0):
        # Hook callback never waits for an audio stream or model.
        self._commands.put((action, destination, epoch))

    def _command_loop(self):
        while not self._stopped.is_set():
            action, destination, epoch = self._commands.get()
            try:
                if action == "start":
                    self.log("Hotkey: start")  # Timestamps show press-to-mic latency.
                    self._gesture_epoch = epoch
                    cues = self.config.get("beep_on_start", True)
                    if cues:
                        from dictation_engine import prepare_cues
                        prepare_cues()
                    if self.start_recording(destination):
                        self._raise_priority()
                        self._start_keep_warm()
                    else:
                        if cues:
                            from dictation_engine import release_cues
                            release_cues()
                        if self._hook:
                            self._hook.finish(epoch)
                elif action == "stop":
                    if self.is_recording:
                        self._cue_transition(False)  # Cue at release, before the mic stops.
                    self.finish_recording()
                elif action == "cancel":
                    self.cancel()
                elif action == "close":
                    # Through this queue, so it never races a hotkey action.
                    if self._hook:
                        self._hook.release_stuck()
                    self.cancel()
                elif action == "paste_last":
                    # Waits for Shift/Alt release; keep recording commands responsive.
                    threading.Thread(target=self.paste_last, name="paste-last", daemon=True).start()
                elif action == "lock":
                    if self.is_recording:
                        self.log("Recording locked hands-free")
                        self._set_state(self.State.RECORDING, {"mode": "locked"})
                elif action == "stale_forgotten":
                    self.log(f"Hotkey: forgot a {destination} key released out of sight")
                elif action == "stuck_released":
                    self.log(f"Hotkey: released a stuck {destination} key")
                elif action == "hook_reinstalled":
                    # Ctrl+Win was pressed while the hook was gone: in a take, that meant stop.
                    recording = self.is_recording
                    self.log("Hotkey: Windows removed the keyboard hook; reinstalled"
                             + (" and stopped the take" if recording else ""))
                    if recording:
                        self._cue_transition(False)
                        self.finish_recording()
                    else:
                        self._set_state(self.state, {"message": "Hotkey restarted; press Ctrl+Win again"})
                elif action == "hook_error":
                    self._set_state(self.state, {"message": "Keyboard input could not be handled; restart dictation"})
                elif action == "quit":
                    break
            except Exception as error:
                self.log(f"Recording action failed ({type(error).__name__})")
                self._set_state(self.state, {"message": "Recording action failed"})

    def start_background(self):
        if self._command_thread:
            return
        self._hook = KeyboardHook(self.dispatch, lambda: self._takes.cancellable)
        self._hook.start()
        self._command_thread = threading.Thread(target=self._command_loop, name="recording-commands", daemon=True)
        self._command_thread.start()
        self._load_generation = 1
        self._spawn_load_thread()

    def _spawn_load_thread(self, previous=None):
        generation = self._load_generation

        def load():
            if previous and previous.is_alive():
                previous.join(timeout=180)  # Here, never on the caller's UI thread.
                self.whisper = None  # It may have finished on the old device.
            self._load_model_background(generation)

        self._load_thread = threading.Thread(target=load, name="model-load", daemon=True)
        self._load_thread.start()

    def fallback_device(self, device):
        if self.active:
            raise RuntimeError("Finish or cancel pending takes before reloading a model")
        self.config["device"] = device
        self._load_error = None
        self._model_ready.clear()
        self.whisper = None
        self._load_generation += 1
        self._spawn_load_thread(previous=self._load_thread)

    @staticmethod
    def list_input_devices():
        """One entry per microphone, with its full name.

        PortAudio lists each mic once per Windows audio API (MME, DirectSound,
        WASAPI, WDM-KS), and MME cuts names at 31 characters. Show only WASAPI
        devices when there are any; the recorder converts their rate to 16 kHz.
        """
        try:
            import sounddevice as sd
            result = []
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] <= 0:
                    continue
                entry = {"index": index, "name": device["name"]}
                if "hostapi" in device:
                    host = sd.query_hostapis(device["hostapi"])["name"]
                    entry.update(host=host, key=f"{host}:{device['name']}", label=f"{device['name']} ({host})")
                result.append(entry)
            wasapi = [dict(d, label=d["name"]) for d in result if d.get("host") == WASAPI]
            return wasapi or result
        except Exception:
            return []

    def stop(self):
        self._stopped.set()
        if self._hook:
            self._hook.stop()
        self._commands.put(("quit", None, 0))
        self._takes.stop()
        if self._command_thread:
            self._command_thread.join(2)
        self.log("Dictation stopped")

    def run(self):
        self.start_background()
        try:
            while not self._stopped.wait(0.5):
                pass
        except KeyboardInterrupt:
            self.stop()
