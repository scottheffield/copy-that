"""Load the tray app offline, assert readiness, then shut down its hook and GUI."""
import os
import socket
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("COPYTHAT_DATA_DIR", str(Path(__file__).resolve().parents[1] / ".runtime"))


def reject_network(*args, **kwargs):
    raise AssertionError("Runtime attempted network access")


socket.create_connection = reject_network
socket.socket.connect = reject_network
from app import GUIApp
from dictation_engine import load_config, AppState


def main():
    config = load_config()
    config["cleanup_enabled"] = True
    app = GUIApp(config)
    failed = []
    def check():
        if app._engine.state == AppState.ERROR:
            failed.append("Speech model failed to load")
            app._quit()
        elif app._engine._model_ready.is_set():
            if app._engine._cleanup.load_error:
                failed.append("Cleanup model failed to load")
            if app._overlay._win.state() != "withdrawn":
                failed.append("Idle pill was visible")
            print("Tray loaded with resident speech and cleanup models; Python network calls blocked", flush=True)
            app._quit()
        else:
            app._root.after(100, check)
    def timeout():
        failed.append("App did not become ready within 120 seconds")
        app._quit()
    app._root.after(100, check)
    app._root.after(120000, timeout)
    app.run()
    if failed:
        raise AssertionError("; ".join(failed))


if __name__ == "__main__":
    main()
