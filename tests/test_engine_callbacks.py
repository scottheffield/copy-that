"""Tests for DictationApp callback system and state transitions."""

import threading
import time
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from dictation_engine import AppState, DictationApp, DEFAULT_CONFIG


class TestAppState:
    """Verify AppState enum values."""

    def test_all_states_exist(self):
        assert AppState.LOADING.value == "loading"
        assert AppState.READY.value == "ready"
        assert AppState.RECORDING.value == "recording"
        assert AppState.PROCESSING.value == "processing"
        assert AppState.ERROR.value == "error"


class TestCallbackSystem:
    """Verify callback registration and firing."""

    def test_initial_state_is_loading(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        assert app.state == AppState.LOADING

    def test_add_callback_and_fire(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        received = []
        app.add_callback(lambda state, data: received.append((state, data)))

        app._set_state(AppState.READY, {"info": "test"})

        assert len(received) == 1
        assert received[0] == (AppState.READY, {"info": "test"})

    def test_multiple_callbacks(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        cb1, cb2 = [], []
        app.add_callback(lambda s, d: cb1.append(s))
        app.add_callback(lambda s, d: cb2.append(s))

        app._set_state(AppState.RECORDING)

        assert cb1 == [AppState.RECORDING]
        assert cb2 == [AppState.RECORDING]

    def test_broken_callback_does_not_crash(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})

        def bad_callback(s, d):
            raise ValueError("boom")

        app.add_callback(bad_callback)
        # Should not raise
        app._set_state(AppState.READY)
        assert app.state == AppState.READY

    def test_set_state_with_no_data(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        received = []
        app.add_callback(lambda s, d: received.append(d))
        app._set_state(AppState.READY)
        assert received == [{}]


class TestStateTransitions:
    """Verify state transitions during model loading and recording."""

    def test_load_model_transitions_to_ready(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app.whisper = MagicMock()  # Skip real model loading

        states = []
        app.add_callback(lambda s, d: states.append(s))

        app._load_model_background()

        assert AppState.LOADING in states
        assert states[-1] == AppState.READY

    def test_load_model_error_transitions_to_error(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)

        states = []
        app.add_callback(lambda s, d: states.append(s))

        # Force ensure_model to fail
        with patch.object(app, "ensure_model", side_effect=RuntimeError("test fail")):
            app._load_model_background()

        assert AppState.ERROR in states
        assert states[-1] == AppState.ERROR

    def test_recording_state_transitions(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app.whisper = MagicMock()
        app.whisper.transcribe = MagicMock(return_value="hello world")
        app._model_ready.set()

        states = []
        app.add_callback(lambda s, d: states.append(s))

        # Start recording
        with patch("dictation_engine.AudioRecorder.start"):
            app.toggle_recording()
        assert AppState.RECORDING in states

        # Stop recording — should go to PROCESSING then READY
        app.recorder = MagicMock()
        app.recorder.stop = MagicMock(return_value=np.full(16000, 0.05, dtype=np.float32))

        with patch("dictation_engine.type_text"):
            app.toggle_recording()
            assert app._takes.wait_idle()

        assert AppState.PROCESSING in states
        assert states[-1] == AppState.READY
        app.stop()


class TestHistory:
    """Verify transcription history tracking."""

    def test_history_populated_after_transcription(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app.whisper = MagicMock()
        app.whisper.transcribe = MagicMock(return_value="test text")
        app._model_ready.set()

        app.recorder = MagicMock()
        app.recorder.stop = MagicMock(return_value=np.full(16000, 0.05, dtype=np.float32))
        app.start_recording()

        with patch("dictation_engine.type_text"):
            app.toggle_recording()
            assert app._takes.wait_idle()

        assert len(app.history) == 1
        assert app.history[0]["text"] == "Test text"
        assert "timestamp" in app.history[0]
        assert "duration" in app.history[0]
        app.stop()

    def test_history_capped_at_max(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)

        app.whisper = MagicMock()
        app.whisper.transcribe.return_value = "test"
        app._model_ready.set()
        app.recorder = MagicMock()
        app.recorder.stop.return_value = np.full(16000, 0.05, dtype=np.float32)
        with patch("dictation_engine.type_text", return_value=True):
            for i in range(25):
                app.start_recording()
                app.finish_recording()
                assert app._takes.wait_idle()
        assert len(app.history) == app.MAX_HISTORY
        app.stop()

    def test_history_returns_copy(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app._takes._history.append({"text": "foo", "timestamp": "", "duration": 1.0})

        h = app.history
        h.clear()
        # Original should be unchanged
        assert len(app.history) == 1


class TestFallbackDevice:
    """Verify device fallback mechanism."""

    def test_fallback_device_clears_error(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app._load_error = "DEVICE_LOST"
        app.whisper = MagicMock()

        with patch.object(app, "_load_model_background"):
            app.fallback_device("GPU")

        assert app._load_error is None
        assert app.config["device"] == "GPU"
        assert app.whisper is None

    def test_fallback_never_waits_on_the_callers_thread(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        release = threading.Event()
        app._load_thread = threading.Thread(target=release.wait, daemon=True)
        app._load_thread.start()
        with patch.object(app, "_load_model_background") as load:
            started = time.time()
            app.fallback_device("GPU")
            assert time.time() - started < 1
            load.assert_not_called()
            release.set()
            app._load_thread.join(5)
        load.assert_called_once_with(app._load_generation)

    def test_a_load_superseded_while_running_does_not_mark_ready(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False, "probe_text_field": False}
        app = DictationApp(config)
        app._load_generation = 1

        def superseded():
            app._load_generation = 2
        with patch.object(app, "ensure_model", side_effect=superseded), patch.object(app._cleanup, "load"):
            app._load_model_background(generation=1)
        assert not app._model_ready.is_set()

    def test_stale_model_load_generation_is_ignored(self):
        config = {**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False}
        app = DictationApp(config)
        app._load_generation = 2
        with patch.object(app, "_set_state") as state:
            app._load_model_background(generation=1)
        state.assert_not_called()


class TestListInputDevices:
    """Verify audio input device enumeration."""

    def test_list_input_devices_returns_list(self):
        with patch("sounddevice.query_devices", return_value=[
            {"name": "Mic 1", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "Speaker", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Mic 2", "max_input_channels": 2, "max_output_channels": 0},
        ]):
            devices = DictationApp.list_input_devices()

        assert len(devices) == 2
        assert devices[0] == {"index": 0, "name": "Mic 1"}
        assert devices[1] == {"index": 2, "name": "Mic 2"}

    def test_list_input_devices_handles_import_error(self):
        with patch("dictation_engine.DictationApp.list_input_devices", return_value=[]):
            devices = DictationApp.list_input_devices()
        assert devices == []


class TestChimes:
    def test_concurrent_events_play_one_stop_cue(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": True, "vad_enabled": False})
        app._last_recording = True
        played = []
        barrier = threading.Barrier(8)
        def event():
            barrier.wait()
            app._on_take_event("processing", {})
        with patch("dictation_engine.chime_stop", lambda: played.append("stop")):
            threads = [threading.Thread(target=event) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            time.sleep(0.1)
        assert played == ["stop"]

    def test_paste_cue_only_when_text_was_pasted(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": True, "vad_enabled": False})
        played = []
        with patch("dictation_engine.chime_paste", lambda: played.append("paste")):
            app._on_take_event("ready", {"text": "kept", "retained": True, "pasted": False})
            app._on_take_event("ready", {"text": "sent", "pasted": True})
            time.sleep(0.1)
        assert played == ["paste"]


class TestPasteLast:
    def test_pastes_latest_result_with_separator_at_current_destination(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        app._takes._history = [{"text": "older"}, {"text": "latest"}]
        app._paste = MagicMock(return_value=True)
        with patch("dictation_runtime.foreground"), patch("dictation_runtime.same_destination", return_value=True):
            assert app.paste_last()
            text, auto_enter, guard, commit = app._paste.call_args.args
            assert (text, auto_enter, guard()) == ("latest ", False, True)

    def test_empty_history_reports_nothing_to_paste(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        messages = []
        app.add_callback(lambda state, data: messages.append(data.get("message")))
        app._paste = MagicMock()
        assert not app.paste_last()
        assert messages == ["Nothing to paste yet"]
        app._paste.assert_not_called()

    def test_release_cues_stop_once_before_the_mic_stops(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": True, "vad_enabled": False})
        app._last_recording = True
        app._takes._recording = object()
        order = []
        def finish():
            order.append("finish")
            app._takes._recording = None
            app._on_take_event("processing", {})  # The usual post-stop event.
        app.finish_recording = finish
        with patch("dictation_engine.chime_stop", lambda: order.append("cue")):
            thread = threading.Thread(target=app._command_loop, daemon=True)
            app._commands.put(("stop", None, 0))
            thread.start()
            time.sleep(0.2)
            app._stopped.set()
            app._commands.put(("quit", None, 0))
            thread.join(1)
        assert order == ["cue", "finish"]


    def test_bubble_x_frees_stuck_keys_then_cancels(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        order = []
        app._hook = MagicMock()
        app._hook.release_stuck.side_effect = lambda: order.append("release")
        app.cancel = lambda: order.append("cancel")
        thread = threading.Thread(target=app._command_loop, daemon=True)
        app.close_take()
        thread.start()
        time.sleep(0.2)
        app._stopped.set()
        app._commands.put(("quit", None, 0))
        thread.join(1)
        assert order == ["release", "cancel"]


class TestDictationPriority:
    def run_start(self, app):
        app.start_recording = MagicMock(return_value=True)
        thread = threading.Thread(target=app._command_loop, daemon=True)
        app._commands.put(("start", None, 0))
        thread.start()
        time.sleep(0.2)
        app._stopped.set()
        app._commands.put(("quit", None, 0))
        thread.join(1)

    def test_raised_from_start_until_the_take_queue_is_ready(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False,
                            "dictation_priority": "above_normal"})
        with patch("dictation_runtime.set_process_priority", return_value=True) as priority:
            self.run_start(app)
            assert [c.args[0] for c in priority.call_args_list] == ["above_normal"]
            app._on_take_event("processing", {})
            assert priority.call_count == 1  # Cleanup and paste still count as dictating.
            app._on_take_event("ready", {"text": "done", "pasted": True})
            app._on_take_event("ready", {})
        assert [c.args[0] for c in priority.call_args_list] == ["above_normal", "normal"]

    def test_default_is_above_normal(self):
        # Measured 2026-10-13 under load: median recognition 0.50s -> 0.27s, worst decoder pass 2.24s -> 0.38s.
        assert DEFAULT_CONFIG["dictation_priority"] == "above_normal"

    def test_normal_setting_never_touches_priority(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False,
                            "dictation_priority": "normal"})
        with patch("dictation_runtime.set_process_priority") as priority:
            self.run_start(app)
            app._on_take_event("ready", {})
        priority.assert_not_called()

    def test_invalid_priority_is_rejected(self):
        from dictation_engine import validate_config
        with pytest.raises(ValueError):
            validate_config({**DEFAULT_CONFIG, "dictation_priority": "realtime"})


class TestKeepWarm:
    def test_warms_only_while_recording(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False, "keep_warm_seconds": 0.05})
        app.whisper = MagicMock()
        app._takes._recording = object()
        app._start_keep_warm()
        time.sleep(0.2)
        app._takes._recording = None
        time.sleep(0.1)
        calls = app.whisper.keep_warm.call_count
        time.sleep(0.2)
        assert calls >= 2
        assert app.whisper.keep_warm.call_count == calls


class TestStuckModifiers:
    def test_paste_wait_releases_stuck_modifiers_before_checking(self):
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        app._hook = MagicMock()
        with patch("dictation_runtime.modifiers_down", return_value=False):
            assert app._modifiers_down() is False
        app._hook.release_stuck.assert_called_once_with()


class TestSilentMic:
    """#43: the runtime tells the UI and the take log when the mic goes silent mid-take."""

    def test_silence_marks_the_take_and_reaches_the_ui(self):
        from types import SimpleNamespace
        app = DictationApp({**DEFAULT_CONFIG, "beep_on_start": False, "vad_enabled": False})
        assert app.recorder.on_silence == app._mic_silence
        received = []
        app.add_callback(lambda state, data: received.append(data))
        app._mic_silence(True)           # No take recording: nothing to report
        assert received == []
        take = SimpleNamespace(mic_silent_at=None)
        app._takes._recording = take
        app.recorder.silent_at = 19.1
        app._mic_silence(True)
        assert take.mic_silent_at == 19.1 and received[-1] == {"mic_silent": True}
        app._mic_silence(False)
        assert received[-1] == {"mic_silent": False} and take.mic_silent_at == 19.1
        app._takes._recording = None
