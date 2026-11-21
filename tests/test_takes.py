import threading
from unittest.mock import Mock, patch
import numpy as np
from takes import TakeQueue
from cleanup import Cleaned
from windows_io import Destination

DEST = Destination(1, 1, "cursor.exe")
SPEECH = np.full(16000, 0.05, dtype=np.float32)


def make_queue(transcribe=None, held=None, same=None):
    recorder = Mock()
    recorder.stop.return_value = SPEECH.copy()
    pastes, notices = [], []
    q = TakeQueue(recorder, transcribe or (lambda audio, config: "hello world"),
        lambda raw, process, config, cancelled, terms=(), spell=(): Cleaned(raw, raw),
        lambda text, auto, guard, commit: guard() and commit() and (pastes.append(text) or True),
        lambda: DEST, same or (lambda dest: True), held or (lambda: False),
        lambda state, data: notices.append((state, data)), {"sample_rate": 16000, "vad_enabled": False})
    return q, pastes, notices


def test_timeout_and_release_finalize_once_and_preserve_audio():
    q, pastes, _ = make_queue()
    try:
        assert q.start()
        q.recorder.on_timeout()
        assert not q.finish()
        assert q.wait_idle()
        assert pastes == ["hello world "]
        assert q.recorder.stop.call_count == 1
    finally:
        q.stop()


def test_cancel_new_recording_leaves_earlier_processing_take_intact():
    entered, release = threading.Event(), threading.Event()
    def transcribe(audio, config):
        entered.set()
        release.wait(2)
        return "first"
    q, pastes, _ = make_queue(transcribe)
    try:
        q.start(); q.finish()
        assert entered.wait(1)
        assert q.start()
        assert q.cancel()
        release.set()
        assert q.wait_idle()
        assert pastes == ["first "]
    finally:
        release.set(); q.stop()


def test_cancel_processing_suppresses_stale_result_and_retains_capacity():
    entered, release = threading.Event(), threading.Event()
    def transcribe(audio, config):
        entered.set(); release.wait(2)
        return "cancelled"
    q, pastes, _ = make_queue(transcribe)
    q.config["max_pending_takes"] = 1
    try:
        q.start(); q.finish()
        assert entered.wait(1)
        q.cancel()
        assert not q.start()
        assert not q.cancellable
        release.set()
        assert q.wait_idle()
        assert not pastes
        assert not q.history
    finally:
        release.set(); q.stop()


def test_order_capacity_and_modifier_wait():
    held = threading.Event(); held.set()
    q, pastes, notices = make_queue(held=held.is_set)
    try:
        for _ in range(3):
            assert q.start(); q.finish()
        assert not q.start()
        assert not pastes
        held.clear()
        assert q.wait_idle()
        assert pastes == ["hello world "] * 3
        assert "".join(pastes) == "hello world hello world hello world "
        assert all(entry["raw"] == entry["text"] == "hello world" for entry in q.history)
        assert len(q.history) == 3
    finally:
        q.stop()


def test_focus_change_retains_text_and_does_not_block_next_take():
    q, pastes, _ = make_queue(same=lambda destination: destination.hwnd == 2)
    try:
        q.start(DEST); q.finish()
        q.start(Destination(2, 2, "code.exe")); q.finish()
        assert q.wait_idle()
        assert pastes == ["hello world "]
        assert q.history[0]["status"] == "retained"
        assert q.history[1]["status"] == "done"
    finally:
        q.stop()


def test_digital_silence_never_calls_recognizer_or_pastes():
    transcribe = Mock()
    q, pastes, _ = make_queue(transcribe)
    q.recorder.stop.return_value = np.zeros(16000, dtype=np.float32)
    try:
        q.start(); q.finish()
        assert q.wait_idle()
        transcribe.assert_not_called()
        assert not pastes
    finally:
        q.stop()


def test_committed_paste_cannot_be_reported_as_cancelled():
    entered, release = threading.Event(), threading.Event()
    q, _, _ = make_queue()
    def paste(text, auto, guard, commit):
        assert guard() and commit()
        entered.set()
        release.wait(2)
        return True
    q.paste = paste
    try:
        q.start(); q.finish()
        assert entered.wait(1)
        assert not q.cancel()
        assert not q.cancellable
        release.set()
        assert q.wait_idle()
        assert q.history[0]["status"] == "done"
    finally:
        release.set(); q.stop()


def test_paste_failure_keeps_raw_recovery_and_next_take_proceeds():
    q, pastes, _ = make_queue()
    successful_paste = q.paste
    attempts = []
    def paste(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("clipboard unavailable")
        return successful_paste(*args)
    q.paste = paste
    try:
        q.start(); q.finish()
        q.start(); q.finish()
        assert q.wait_idle()
        assert q.history[0]["status"] == "failed"
        assert q.history[0]["raw"] == "hello world"
        assert q.history[1]["status"] == "done"
        assert pastes == ["hello world "]
    finally:
        q.stop()


def test_recording_state_is_reported_before_per_take_helpers_start():
    q, _, notices = make_queue()
    seen = []
    q.live = lambda take: seen.append(len(notices)) or None
    try:
        assert q.start()
        assert seen == [1] and notices[0][0] == "recording"
    finally:
        q.stop()


def test_pasting_is_announced_before_the_paste():
    order = []
    q, _, notices = make_queue()
    real_paste = q.paste
    q.paste = lambda *args: order.append(("paste", any("pasting" in d for _, d in notices))) or real_paste(*args)
    try:
        q.start(); q.finish()
        assert q.wait_idle()
        assert order == [("paste", True)]
    finally:
        q.stop()


def test_take_log_records_destination_window_and_field():
    q, _, _ = make_queue()
    q.take_log = Mock()
    q.config["take_log_enabled"] = True
    try:
        q.start(Destination(7, 7, "code.exe", "cleanup.py - copy-that - Visual Studio Code"))
        q._recording.field = "50004|chat-input|TextArea|Message Claude"
        q.finish()
        assert q.wait_idle()
        entry = q.take_log.call_args.args[1]
        assert entry["window"] == {"hwnd": 7, "title": "cleanup.py - copy-that - Visual Studio Code"}
        assert entry["field"] == "50004|chat-input|TextArea|Message Claude"
    finally:
        q.stop()


def test_terms_from_earlier_takes_reach_polish_for_the_same_box_only():
    from context import RecentTerms
    seen = []
    q, _, _ = make_queue()
    q.clean = lambda raw, process, config, cancelled, terms=(), spell=(): seen.append(terms) or Cleaned(raw, raw)
    q.transcribe = lambda audio, config: "merge the OPS-49 branch"
    q.context = RecentTerms()
    try:
        for destination in (Destination(5, 5, "code.exe"), Destination(5, 5, "code.exe"), Destination(6, 6, "grok.exe")):
            q.start(destination); q.finish()
            assert q.wait_idle()
        assert seen == [[], ["OPS-49"], []]
    finally:
        q.stop()


def test_screen_terms_reach_polish_only_when_the_take_seems_to_say_them():
    from context import RecentTerms
    seen = []
    q, _, _ = make_queue()
    q.clean = lambda raw, process, config, cancelled, terms=(), spell=(): seen.append((terms, spell)) or Cleaned(raw, raw)
    q.transcribe = lambda audio, config: "ask Jordin and Jake about the open veeno notes"
    q.context = RecentTerms()
    q.live = lambda take: take.screen_terms.extend(["HANDOFF.md", "OpenVINO", "Jordan", "Jane"])
    try:
        q.start(DEST); q.finish()
        assert q.wait_idle()
        # Identifiers and close names make Polish run.
        # Short names ("Jane" for "Jake") go to Polish but don't make it run.
        assert seen == [(["Jordan", "OpenVINO", "Jane"], ["Jordan", "OpenVINO"])]
    finally:
        q.stop()


def hold_and_finish(q, audio, hold_seconds):
    times = [100.0, 100.0 + hold_seconds]
    q.recorder.stop.return_value = audio
    with patch("takes.time.monotonic", lambda: times.pop(0) if len(times) > 1 else times[0]):
        assert q.start()
        assert q.finish()
    assert q.wait_idle()


def test_long_hold_with_almost_no_audio_says_microphone_stopped():
    q, pastes, notices = make_queue()
    try:
        hold_and_finish(q, np.zeros(3200, dtype=np.float32), 5.0)
        assert not pastes
        assert [d["message"] for s, d in notices if d.get("message")] == ["Microphone stopped sending audio"]
    finally:
        q.stop()


def test_short_tap_still_says_recording_too_short():
    q, pastes, notices = make_queue()
    try:
        hold_and_finish(q, np.zeros(3200, dtype=np.float32), 0.2)
        assert not pastes
        assert [d["message"] for s, d in notices if d.get("message")] == ["Recording too short"]
    finally:
        q.stop()


def test_normal_take_is_unchanged():
    q, pastes, notices = make_queue()
    try:
        hold_and_finish(q, SPEECH.copy(), 1.0)
        assert pastes == ["hello world "]
        assert not [d for s, d in notices if d.get("message")]
    finally:
        q.stop()
