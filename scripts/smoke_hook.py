"""Live check that a removed keyboard hook is noticed and reinstalled.

Quit CopyThat first: its own hook would hide Ctrl+Win from this one.
Run: .venv\\Scripts\\python scripts\\smoke_hook.py
"""
import ctypes
import queue
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hotkeys import KeyboardHook  # noqa: E402


def main():
    actions = queue.SimpleQueue()
    hook = KeyboardHook(lambda action, *_: actions.put(action), lambda: False)
    hook.start()

    def wait_for(wanted, prompt, seconds=60):
        print(prompt, flush=True)
        while True:
            try:
                action = actions.get(timeout=seconds)
            except queue.Empty:
                print(f"FAIL: no {wanted} within {seconds}s")
                sys.exit(1)
            print(f"  hook: {action}")
            if action == wanted:
                return

    wait_for("start", "1. Press and release Ctrl+Win once (the hook is live).")
    user = ctypes.WinDLL("user32")
    user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    user.UnhookWindowsHookEx(hook._handle)  # What Windows does to a slow hook.
    while not actions.empty():
        actions.get()
    wait_for("hook_reinstalled",
             "2. Hook removed. Hold Ctrl+Win for about a second, then release. "
             "The Start menu may open; that's expected.")
    wait_for("start", "3. Press and release Ctrl+Win again.")
    print("PASS: the removed hook was reinstalled and the hotkey works again.")
    hook.stop()


if __name__ == "__main__":
    main()
