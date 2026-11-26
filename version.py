"""The app's one version number. Bump per merged batch: minor for
features, patch for fixes. The installer build reads it and tags v<VERSION>."""
import subprocess
import sys
from pathlib import Path

VERSION = "0.4.1"
# Written by scripts/build_windows.py into the packaged app.
COMMIT_FILE = "build_commit.txt"


def commit():
    """Short commit the build came from: baked into the installed app, read
    from git in a dev checkout, empty when neither is available."""
    if getattr(sys, "frozen", False):
        try:
            return (Path(sys._MEIPASS) / COMMIT_FILE).read_text(encoding="utf-8").strip()
        except (AttributeError, OSError):
            return ""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                              capture_output=True, text=True, timeout=2,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def label():
    """"0.2.0 (abc1234)" installed, "0.2.0 dev (abc1234)" from a checkout."""
    dev = "" if getattr(sys, "frozen", False) else " dev"
    sha = commit()
    return f"{VERSION}{dev}" + (f" ({sha})" if sha else "")
