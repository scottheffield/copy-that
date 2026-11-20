"""Deterministic Ctrl+Win gestures and a dedicated WH_KEYBOARD_LL thread."""
import ctypes
import queue
import threading
from ctypes import wintypes

CTRL = {0x11, 0xA2, 0xA3}
WIN = {0x5B, 0x5C}
SHIFT = {0x10, 0xA0, 0xA1}
ALT = {0x12, 0xA4, 0xA5}
SPACE, ESCAPE, Z = 0x20, 0x1B, 0x5A
MODIFIERS = CTRL | WIN | SHIFT | ALT
# Windows stops waiting for a slow hook (LowLevelHooksTimeout) and passes the
# key on. A decision made later than this may not have been honored.
LATE_MS = 200
KEY_NAMES = {**{vk: "Ctrl" for vk in CTRL}, **{vk: "Win" for vk in WIN},
             **{vk: "Shift" for vk in SHIFT}, **{vk: "Alt" for vk in ALT}}


class Gesture:
    """Returns actions without doing microphone or model work in the hook."""
    def __init__(self):
        self.down = set()
        self.swallowed = set()
        self.chord = False
        self.locked = False
        self.holding = False
        self.escape_consumed = False
        self.epoch = 0
        self.hidden_releases = set()  # modifier releases swallowed; Windows may still hold them down

    def finish(self):
        self.locked = self.holding = False
        # chord stays latched until both modifiers are up after timeout/cancel.

    def stuck(self, is_down):
        """Modifiers whose release was swallowed although Windows still holds them down.

        A late decision can swallow a release whose press Windows already
        passed on; the key then stays down for every app (typing does nothing).
        """
        stuck = {vk for vk in self.hidden_releases if vk not in self.down and is_down(vk)}
        self.hidden_releases &= self.down  # Pressed again: decide once it is released.
        return stuck

    def forget_released(self, is_down, vk=None):
        """Drop modifiers this hook missed the release of; returns them.

        The lock screen and sign-in screen hide key releases from the hook, so
        Win from Win+L can look held forever: the next Ctrl would start a take
        and the latched chord would swallow all typing. Only presses that
        reached Windows can be checked, so swallowed ones are left alone.
        """
        stale = {k for k in self.down & MODIFIERS
                 if k != vk and k not in self.swallowed and not is_down(k)}
        self.down -= stale
        return stale

    def event(self, vk, pressed, active=False, late=False):
        """late: Windows already passed this key on, so a swallow was not honored."""
        actions = []
        repeat = pressed and vk in self.down
        if pressed:
            self.down.add(vk)
        else:
            self.down.discard(vk)
        ctrl = bool(self.down & CTRL)
        win = bool(self.down & WIN)
        both = ctrl and win
        swallow = vk in self.swallowed

        if (vk == Z and pressed and not repeat and not self.chord and not ctrl and not win
                and self.down & SHIFT and self.down & ALT):
            # Shift+Alt+Z pastes the latest result. Mask so the Alt release
            # does not activate the destination's menu bar.
            swallow = True
            actions += ["mask", "paste_last"]
        elif vk == ESCAPE:
            if pressed and not repeat and active:
                self.escape_consumed = True
                actions.append("cancel")
                self.finish()
            swallow = self.escape_consumed
            if not pressed:
                self.escape_consumed = False
        elif both and not self.chord and pressed and not repeat and vk in CTRL | WIN:
            self.chord = True
            swallow = True
            actions.append("mask")
            if self.locked:
                self.finish()
                actions.append("stop")
            else:
                self.epoch += 1
                self.holding = True
                actions.append("start")
        elif self.chord and pressed:
            # Suppress OS shortcuts and normal characters until chord is up.
            swallow = True
            if vk == SPACE and not repeat and self.holding:
                self.locked, self.holding = True, False
                actions.append("lock")

        if self.chord and not both and self.holding:
            self.holding = False
            actions.append("stop")
        if self.chord and not ctrl and not win:
            self.chord = False
        if pressed and swallow and not repeat and not late:
            self.swallowed.add(vk)
        elif not pressed:
            if swallow and vk in MODIFIERS:
                self.hidden_releases.add(vk)
            self.swallowed.discard(vk)
        # Releases whose downs reached Windows also reach it, preventing stuck keys.
        return swallow, actions


class KeyboardHook:
    def __init__(self, dispatch, active):
        self.dispatch, self.active = dispatch, active
        self.gesture = Gesture()
        self._commands = queue.SimpleQueue()
        self._ready = threading.Event()
        self._error = None
        self._thread = None
        self._thread_id = None
        self._lock = threading.Lock()  # Gesture state: hook thread and release_stuck callers
        self._key_down = None

    def finish(self, epoch):
        self._commands.put(epoch)

    def release_stuck(self):
        """Release modifiers Windows still holds after their release was swallowed; any thread."""
        if self._key_down is None:
            return set()
        with self._lock:
            return self._release_stuck_locked()

    def _release_stuck_locked(self):
        if not self.gesture.hidden_releases:
            return set()
        from windows_io import send_keys
        stuck = self.gesture.stuck(self._key_down)
        if stuck:
            # The unassigned mask key first, so a lone Win or Alt release opens no menu.
            send_keys([(0xE8, False), (0xE8, True)] + [(vk, True) for vk in sorted(stuck)])
            names = ", ".join(sorted({KEY_NAMES.get(vk, hex(vk)) for vk in stuck}))
            self.dispatch("stuck_released", names, self.gesture.epoch)
        return stuck

    def start(self):
        self._thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Keyboard hook did not start")
        if self._error:
            raise self._error

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(2)

    def _run(self):
        from windows_io import foreground, mask_start_menu, send_keys
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel.GetModuleHandleW.restype = wintypes.HMODULE
            self._thread_id = kernel.GetCurrentThreadId()
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
            user.SetWindowsHookExW.restype = wintypes.HANDLE
            user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user.CallNextHookEx.restype = ctypes.c_ssize_t
            user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
            user.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user.GetAsyncKeyState.restype = ctypes.c_short
            kernel.GetTickCount.restype = wintypes.DWORD

            class Key(ctypes.Structure):
                _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD),
                            ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("extra", ctypes.c_size_t)]

            def handle_key(code, message, address):
                if code < 0:
                    return user.CallNextHookEx(None, code, message, address)
                key = ctypes.cast(address, ctypes.POINTER(Key)).contents
                if key.flags & 0x10:  # Ignore injected menu-mask and paste keys.
                    return user.CallNextHookEx(None, code, message, address)
                with self._lock:
                    while not self._commands.empty():
                        epoch = self._commands.get()
                        if epoch == self.gesture.epoch:
                            self.gesture.finish()
                    pressed = message in (0x100, 0x104)
                    stale = self.gesture.forget_released(self._key_down, key.vk)
                    if stale:
                        names = ", ".join(sorted({KEY_NAMES.get(vk, hex(vk)) for vk in stale}))
                        self.dispatch("stale_forgotten", names, self.gesture.epoch)
                    stuck = self._release_stuck_locked()
                    if stuck and pressed and key.vk not in MODIFIERS and not self.gesture.chord:
                        # Replay the key after the releases so it types instead of firing a shortcut.
                        send_keys([(key.vk, False)])
                        return 1
                    # Windows already passed a key this old on; swallowing it now has no effect.
                    late = ((kernel.GetTickCount() - key.time) & 0xFFFFFFFF) > LATE_MS
                    swallow, actions = self.gesture.event(key.vk, pressed, self.active(), late)
                    for action in actions:
                        if action == "mask":
                            mask_start_menu()
                        else:
                            destination = foreground() if action == "start" else None
                            self.dispatch(action, destination, self.gesture.epoch)
                return 1 if swallow else user.CallNextHookEx(None, code, message, address)

            def callback(code, message, address):
                try:
                    return handle_key(code, message, address)
                except Exception:
                    self.dispatch("hook_error", None, self.gesture.epoch)
                    return user.CallNextHookEx(None, code, message, address)

            self._callback = callback_type(callback)
            self._key_down = lambda vk: bool(user.GetAsyncKeyState(vk) & 0x8000)
            handle = user.SetWindowsHookExW(13, self._callback, kernel.GetModuleHandleW(None), 0)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            try:
                msg = wintypes.MSG()
                while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    user.TranslateMessage(ctypes.byref(msg))
                    user.DispatchMessageW(ctypes.byref(msg))
            finally:
                user.UnhookWindowsHookEx(handle)
        except Exception as error:
            self._error = error
            self._ready.set()
