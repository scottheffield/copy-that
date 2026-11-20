import json
import os
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import take_log


def test_sync_folder_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(take_log, "__file__", str(tmp_path / "repo" / "take_log.py"))
    take = SimpleNamespace(id="sample", audio=np.zeros(160, dtype=np.float32), config={"sample_rate": 16000})
    with pytest.raises(ValueError, match="cloud-synced"):
        take_log.write_take(tmp_path / "OneDrive" / "takes", take, {"raw": "hello"})


def test_log_round_trip_and_repository_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(take_log, "__file__", str(tmp_path / "repo" / "take_log.py"))
    take = SimpleNamespace(id="sample", audio=np.full(16000, 0.05, dtype=np.float32), config={"sample_rate": 16000})
    entry = {"raw": "hello", "text": "Hello.", "timings": {"transcribe_seconds": 0.1}}
    take_log.write_take(tmp_path / "data", take, entry)
    assert json.loads((tmp_path / "data" / "sample" / "take.json").read_text()) == entry
    from scripts.benchmark import read_audio
    assert len(read_audio(tmp_path / "data" / "sample" / "audio.wav")) == 16000
    with pytest.raises(ValueError, match="outside the repository"):
        take_log.write_take(tmp_path / "repo" / "data", take, entry)


def make_take(root, name, age, *extra):
    folder = root / name
    folder.mkdir()
    for file in (*take_log.TAKE_FILES, *extra):
        (folder / file).write_text("x")
    os.utime(folder, (1000 + age, 1000 + age))


def test_only_the_newest_takes_stay(tmp_path):
    for age in range(5):
        make_take(tmp_path, f"take{age}", age)
    assert take_log.prune_takes(tmp_path, 2) == 3
    assert sorted(f.name for f in tmp_path.iterdir()) == ["take3", "take4"]


def test_zero_keeps_every_take(tmp_path):
    make_take(tmp_path, "take", 0)
    assert take_log.prune_takes(tmp_path, 0) == 0
    assert (tmp_path / "take").is_dir()


def test_checked_takes_and_other_folders_are_left_alone(tmp_path):
    make_take(tmp_path, "checked", 0, "reference.txt")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "notes.txt").write_text("x")
    (tmp_path / "loose.txt").write_text("x")
    make_take(tmp_path, "old", 1)
    make_take(tmp_path, "new", 2)
    assert take_log.prune_takes(tmp_path, 1) == 1
    assert sorted(f.name for f in tmp_path.iterdir()) == ["checked", "loose.txt", "new", "other"]


def test_writing_a_take_applies_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(take_log, "__file__", str(tmp_path / "repo" / "take_log.py"))
    root = tmp_path / "data"
    root.mkdir()
    make_take(root, "old", 0)
    take = SimpleNamespace(id="new", audio=np.zeros(160, dtype=np.float32),
                           config={"sample_rate": 16000, "take_log_keep": 1})
    take_log.write_take(root, take, {"raw": "hello"})
    assert [f.name for f in root.iterdir()] == ["new"]


def test_logging_off_does_not_call_disk_writer():
    from tests.test_takes import make_queue
    from unittest.mock import Mock
    q, _, _ = make_queue()
    q.take_log = Mock()
    try:
        q.start(); q.finish()
        assert q.wait_idle()
        q.take_log.assert_not_called()
    finally:
        q.stop()


@pytest.mark.parametrize("label, path, refused", [
    ("Google Drive", "G:/takes", True),       # Google Drive on its usual letter
    ("Google Drive", "H:/takes", True),       # on another letter
    ("Data", "G:/takes", False),              # an ordinary G: drive
    ("", "G:/takes", False),                  # label can't be read
    ("Windows-SSD", "C:/Users/me/My Drive/takes", True),  # mirror mode
])
def test_google_drive_is_found_by_label_not_letter(monkeypatch, label, path, refused):
    monkeypatch.setattr(take_log, "volume_label", lambda directory: label)
    if refused:
        with pytest.raises(ValueError, match="Google Drive"):
            take_log.private_directory(path)
    else:
        assert take_log.private_directory(path).parts[-1] == "takes"


def test_volume_label_of_a_local_folder_is_readable(tmp_path):
    assert isinstance(take_log.volume_label(tmp_path), str)
