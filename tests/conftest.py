"""Shared fixtures for dictation engine tests."""
import sys
import os
from pathlib import Path

# Add project root to path so we can import dictation_engine
sys.path.insert(0, str(Path(__file__).parent.parent))
# Tests must not write runtime logs or config into the user's app data.
os.environ["COPYTHAT_DATA_DIR"] = str(Path(__file__).parent.parent / ".runtime" / "tests")

import pytest


@pytest.fixture(autouse=True)
def physical_modifiers_released(monkeypatch):
    """Engine tests must not depend on keys the developer is holding."""
    import dictation_runtime
    monkeypatch.setattr(dictation_runtime, "modifiers_down", lambda: False)


@pytest.fixture(autouse=True)
def developer_media_untouched(monkeypatch):
    """Engine tests must not pause music the developer is playing."""
    import media_pause
    monkeypatch.setattr(media_pause, "_sessions", lambda: [])


@pytest.fixture(autouse=True)
def developer_meetings_untouched(monkeypatch, tmp_path):
    """The default meetings folder is the developer's real one; finished meetings there
    get compressed (#61). Tests that build a config from DEFAULT_CONFIG use a temp one."""
    import dictation_engine
    monkeypatch.setitem(dictation_engine.DEFAULT_CONFIG, "meetings_dir", str(tmp_path / "CopyThat Meetings"))


class _NoFocusProbe:
    """Stands in for focus_probe.FocusProbe: reports nothing about the focused field."""

    def __init__(self, log=None):
        pass

    def probe(self, hwnd, callback, retry_after=0.3):
        pass

    def read_screen(self, hwnd, callback, limit=3000):
        pass

    def warm(self):
        pass


@pytest.fixture(autouse=True)
def desktop_left_alone(monkeypatch):
    """Engine tests must not read the developer's desktop through UI Automation. The real
    probe made each test's result depend on the focused window, and started a COM thread
    per engine. test_focus_probe tests its pure parts."""
    import focus_probe
    monkeypatch.setattr(focus_probe, "FocusProbe", _NoFocusProbe)


@pytest.fixture(autouse=True)
def tk_freed_on_the_main_thread():
    """Free each test's garbage here, on the main thread. A Tk interpreter left in a
    reference cycle was otherwise freed by whichever thread garbage collection next ran
    on (a meeting worker, say), and Tcl aborts the process when that isn't the thread
    that created it: "Windows fatal exception: code 0x80000003" (#88)."""
    yield
    import gc
    gc.collect()


@pytest.fixture(autouse=True)
def gpu_setting_kept(monkeypatch):
    """Engine tests must not depend on the developer's GPUs. test_ov_devices
    imports resolve_device directly, so it still tests the real lookup."""
    import ov_devices
    monkeypatch.setattr(ov_devices, "resolve_device", lambda device, core=None, log=print: device)
