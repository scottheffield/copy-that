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


@pytest.fixture(autouse=True)
def gpu_setting_kept(monkeypatch):
    """Engine tests must not depend on the developer's GPUs. test_ov_devices
    imports resolve_device directly, so it still tests the real lookup."""
    import ov_devices
    monkeypatch.setattr(ov_devices, "resolve_device", lambda device, core=None, log=print: device)
