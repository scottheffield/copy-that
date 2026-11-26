"""The meeting chip: what it shows in each state and what its controls do."""
import tkinter as tk
from types import SimpleNamespace

import pytest

from ui.meeting_chip import MeetingChip, clock


@pytest.fixture(scope="module")
def root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()
    del root
    import gc
    gc.collect()  # Free the interpreter on this thread, not a worker's (#88)


@pytest.fixture
def chip(root):
    calls = []
    c = MeetingChip(root, on_stop=lambda: calls.append("stop"), on_open=lambda: calls.append("open"),
                    on_consent_seen=lambda: calls.append("consent"),
                    on_moved=lambda pos: calls.append(("moved", pos)), position=(1900, 20))
    c.calls = calls
    yield c
    c.hide()
    c._win.destroy()


def texts(chip):
    canvas = chip._canvas
    return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]


def click(chip, callback_name):
    """Click the middle of the hit box whose callback has this name."""
    for x1, y1, x2, y2, cb in chip._hits:
        if getattr(cb, "__name__", "") == callback_name:
            chip._on_press(SimpleNamespace(x=(x1 + x2) // 2, y=(y1 + y2) // 2, x_root=0, y_root=0))
            return
    raise AssertionError(f"no {callback_name} control; hits={chip._hits}")


def test_clock():
    assert clock(5) == "0:05" and clock(724) == "12:04" and clock(3725) == "1:02:05"


def test_recording_shows_timer_and_stop_stays_on_screen(chip, root):
    chip.show_recording()
    chip.update(724, 0.4, 0.1)
    root.update_idletasks()
    assert chip.state == "recording"
    assert "12:04" in texts(chip)
    assert chip._win.state() != "withdrawn"
    click(chip, "_stop")
    assert chip.calls == ["stop"]


def test_both_sides_always_have_a_labeled_waveform(chip, root):
    chip.show_recording()
    for _ in range(12):
        chip.update(10, 0.5, 0.0)
    assert {"Me", "Others"} <= set(texts(chip))
    canvas = chip._canvas
    bars = [canvas.coords(i) for i in canvas.find_all() if canvas.type(i) == "rectangle"]
    assert len(bars) == 2 * chip.BARS
    heights = [y2 - y1 for _, y1, _, y2 in bars]
    assert max(heights[:chip.BARS]) > max(heights[chip.BARS:])  # Me is talking, Others is quiet


def test_grows_leftward_from_its_right_edge(chip, root):
    chip.show_recording()
    chip.update(10, 0, 0)
    root.update_idletasks()
    narrow = chip._canvas.winfo_reqwidth()
    chip.update(10, 0, 0, problem="No sound from your mic and a long message")
    root.update_idletasks()
    assert chip._canvas.winfo_reqwidth() != narrow
    assert chip._right == 1900


def test_dead_mic_replaces_the_timer_label(chip):
    chip.show_recording()
    chip.update(65, 0.0, 0.3, problem="No sound from your mic")
    assert "No sound from your mic" in texts(chip)
    assert any(getattr(cb, "__name__", "") == "_stop" for *_, cb in chip._hits)


def test_finishing_then_ready_opens_the_transcript(chip):
    chip.show_recording()
    chip.show_finishing(2832)
    assert "Finishing…" in texts(chip) and "47:12" in texts(chip)
    assert not chip._hits  # Nothing to click while finishing
    chip.show_ready()
    assert "Transcript ready" in texts(chip)
    click(chip, "_open")
    assert chip.calls == ["open"] and chip.state == "hidden"


def test_ready_can_be_dismissed(chip):
    chip.show_ready()
    click(chip, "hide")
    assert chip.state == "hidden"


def test_failed_says_so(chip):
    chip.show_failed()
    assert "Transcript failed" in texts(chip)


def test_consent_note_shows_once_until_got_it(chip):
    chip.show_recording(consent=True)
    assert "Recording others may need their consent." in texts(chip)
    click(chip, "_consent_seen")
    assert chip.calls == ["consent"]
    assert "Recording others may need their consent." not in texts(chip)


def test_drag_moves_and_reports_the_new_spot(chip):
    chip.show_recording()
    chip._on_press(SimpleNamespace(x=150, y=26, x_root=500, y_root=500))  # Between the waveforms
    chip._on_drag(SimpleNamespace(x_root=400, y_root=560))
    chip._on_release(SimpleNamespace(x_root=400, y_root=560))
    assert chip.calls == [("moved", (1800, 80))]


def test_theme_change_keeps_state(chip):
    chip.show_recording()
    chip.update(30, 0.1, 0.1)
    chip.set_theme("dark")
    assert chip.state == "recording" and "0:30" in texts(chip)
    chip.set_theme("light")


def test_hover_names_the_devices_and_warns_when_they_differ(chip):
    chip.show_recording()
    devices = ("Microphone (EMEET)", "Speakers (Laptop)", "Separate mic and speaker")
    chip.update(10, 0.1, 0.1, devices=devices)
    assert not any(t.startswith("Me: ") for t in texts(chip))  # Nothing extra until hovered
    chip._set_hover(True)
    assert {"Me: Microphone (EMEET)", "Others: Speakers (Laptop)", "Separate mic and speaker"} <= set(texts(chip))
    chip._set_hover(False)
    assert not any(t.startswith("Me: ") for t in texts(chip))


def test_mic_button_mutes_and_shows_it(root):
    muted = []
    chip = MeetingChip(root, on_mute=muted.append, position=(1900, 20))
    try:
        chip.show_recording()
        chip.update(10, 0.4, 0.2)
        assert "Me" in texts(chip) and "Muted" not in texts(chip)
        click(chip, "_toggle_mute")
        assert muted == [True] and chip.muted
        assert "Muted" in texts(chip) and "Me" not in texts(chip) and "Others" in texts(chip)
        chip.update(11, 0.0, 0.2, muted=True)   # The app's poll keeps it muted
        assert "Muted" in texts(chip)
        click(chip, "_toggle_mute")
        assert muted == [True, False] and not chip.muted
    finally:
        chip._win.destroy()


def test_mic_button_tooltip_says_what_it_does(chip):
    chip.show_recording()
    chip.update(10, 0.1, 0.1, devices=("Mic", "Speakers", None))
    x1, y1, x2, y2 = chip._mic_box
    chip._set_hover(True)
    chip._on_motion(SimpleNamespace(x=(x1 + x2) // 2, y=26))
    assert "Mute your mic in this meeting" in texts(chip)
    assert not any(t.startswith("Me: ") for t in texts(chip))
    chip.update(11, 0.1, 0.1, muted=True)
    assert "Unmute your mic" in texts(chip)
    chip._on_motion(SimpleNamespace(x=x2 + 40, y=26))
    assert "Unmute your mic" not in texts(chip)


def test_a_new_meeting_starts_unmuted(chip):
    chip.show_recording()
    chip.update(10, 0, 0, muted=True)
    chip.show_recording()
    assert not chip.muted and "Me" in texts(chip)
