"""Opt-in local replay files. Never called with logging disabled."""
import json
import wave
from pathlib import Path
import numpy as np

TAKE_FILES = {"audio.wav", "take.json"}
_SYNC_MARKERS = ("onedrive", "dropbox", "icloud", "google drive", "box sync", "sharepoint", "synology drive")


def volume_label(directory):
    """The label of the drive holding directory, or "" when it can't be read.
    Google Drive's virtual drive is labeled "Google Drive", whatever its letter."""
    try:
        import ctypes
        label = ctypes.create_unicode_buffer(261)
        root = Path(directory).anchor
        if root and ctypes.windll.kernel32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0):
            return label.value
    except (AttributeError, OSError):
        pass
    return ""


def private_directory(directory):
    directory = Path(directory).expanduser().resolve()
    repo = Path(__file__).resolve().parent
    # Do not allow personal dictations into tracked project paths or Google Drive.
    if directory == repo or repo in directory.parents:
        raise ValueError("Take logs must live outside the repository")
    if ("google drive" in volume_label(directory).lower()
            or any(part.lower() in ("google drive", "my drive") or "google drive" in part.lower()
                   for part in directory.parts)):
        raise ValueError("Take logs must live outside Google Drive")
    if any(marker in part.lower() for part in directory.parts for marker in _SYNC_MARKERS):
        raise ValueError("Take logs must not live in a cloud-synced folder")
    return directory


def write_take(directory, take, entry):
    root = private_directory(directory)
    folder = root / take.id
    folder.mkdir(parents=True, exist_ok=False)
    audio = (np.clip(take.audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(folder / "audio.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(take.config["sample_rate"])
        wav.writeframes(audio.tobytes())
    (folder / "take.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    prune_takes(root, take.config.get("take_log_keep", 200))


def prune_takes(directory, keep):
    """Delete the oldest takes past keep; 0 keeps them all. A folder holding
    anything more (a checked reference.txt) stays and does not count."""
    if not keep:
        return 0
    takes = [f for f in Path(directory).iterdir()
             if f.is_dir() and {p.name for p in f.iterdir()} == TAKE_FILES]
    takes.sort(key=lambda f: f.stat().st_mtime)
    removed = 0
    for folder in takes[:-keep]:
        try:
            for name in TAKE_FILES:
                (folder / name).unlink()
            folder.rmdir()
            removed += 1
        except OSError:
            continue  # Open elsewhere: the next take tries again.
    return removed
