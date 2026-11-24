"""Render the project's own windows and check microphone/settings round trips."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("COPYTHAT_DATA_DIR", str(Path(__file__).resolve().parents[1] / ".runtime"))
import customtkinter as ctk
from PIL import ImageGrab
from dictation_engine import DEFAULT_CONFIG
from ui.settings import SettingsWindow
from ui.overlay import OverlayWindow


def main():
    root = ctk.CTk()
    root.withdraw()
    output = Path(os.environ["COPYTHAT_DATA_DIR"])
    output.mkdir(exist_ok=True)
    config = {**DEFAULT_CONFIG, "input_device": 7, "beep_on_start": False}
    devices = [{"index": 3, "name": "Sample laptop microphone"}, {"index": 7, "name": "Sample USB microphone"}]
    history = [{"text": "Please check the build.", "raw": "please check the build",
        "status": "retained", "timestamp": "2026-10-10T19:00:00", "duration": 3.2}]
    settings = SettingsWindow(root, config, devices, history=lambda: history)
    settings.show()
    root.update()
    assert settings._mic_var.get() == "Sample USB microphone"
    updated = settings._get_new_config()
    assert updated["input_device"] == 7
    settings._cleanup_choice_var.set("Qwen3 4B (faster)")
    settings._on_cleanup_change("Qwen3 4B (faster)")
    updated = settings._get_new_config()
    assert updated["cleanup_enabled"] and updated["cleanup_model_path"].endswith("Qwen3-4B-int4-ov")
    assert updated["append_space"]
    settings._cleanup_choice_var.set("Rules only (fastest)")
    assert not settings._get_new_config()["cleanup_enabled"]
    settings._cleanup_choice_var.set("Qwen3 4B (faster)")
    settings._mic_var.set("(default)")
    assert settings._get_new_config()["input_device"] is None
    overlay = OverlayWindow(root)
    assert overlay._win.state() == "withdrawn"
    overlay.show_recording()
    overlay.set_field(True, None)  # The bubble opens once it knows where the caret is.
    root.update()
    assert overlay._win.state() == "normal"
    overlay.hide()
    def capture():
        window = settings._win
        x, y, w, h = window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height()
        ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(output / "settings-preview.png")
        settings.show("History")
        def capture_history():
            window = settings._win
            x, y, w, h = window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height()
            ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(output / "history-preview.png")
            window.destroy()
            root.destroy()
            print("UI rendered; microphone selection round trip passed", flush=True)
        root.after(600, capture_history)
    root.after(800, capture)
    root.mainloop()


if __name__ == "__main__":
    main()
