"""Small, typed Windows boundary. No hooks or input are installed on import."""
import ctypes
import os
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes
from contextlib import contextmanager


@dataclass(frozen=True)
class Destination:
    hwnd: int
    pid: int
    process: str
    title: str = ""  # Take log only, to group takes by window for context


@contextmanager
def single_instance(name="Local\\CopyThatDictation"):
    """Keep one global-hotkey app per Windows login session."""
    _, kernel = _apis()
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        yield error != 183  # ERROR_ALREADY_EXISTS
    finally:
        kernel.CloseHandle(handle)


def _apis():
    if os.name != "nt":
        raise OSError("Dictation input requires Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return user, kernel


def foreground():
    user, kernel = _apis()
    hwnd = user.GetForegroundWindow()
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    name = "unknown"
    if handle:
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                name = os.path.basename(buffer.value).lower()
        finally:
            kernel.CloseHandle(handle)
    title = ctypes.create_unicode_buffer(512)
    user.GetWindowTextW(hwnd, title, len(title))
    return Destination(int(hwnd or 0), pid.value, name, title.value)


def same_destination(destination):
    # Our own windows (pill, Settings, History) never receive dictation.
    if destination.pid == os.getpid():
        return False
    current = foreground()
    return bool(destination.hwnd) and (current.hwnd, current.pid) == (destination.hwnd, destination.pid)


def modifiers_down():
    user, _ = _apis()
    return any(user.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


def send_keys(events):
    """Inject one atomic sequence of (virtual key, key-up) events."""
    user, _ = _apis()
    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user.SendInput.restype = wintypes.UINT
    inputs = (INPUT * len(events))()
    for entry, (vk, up) in zip(inputs, events):
        entry.type = 1
        entry.ki = KEYBDINPUT(vk, 0, 2 if up else 0, 0, 0)
    if user.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError("Windows rejected simulated input")


def mask_start_menu():
    # Unassigned key used by AutoHotkey's menu-mask technique.
    send_keys([(0xE8, False), (0xE8, True)])


PRIORITY_CLASSES = {"below_normal": 0x4000, "normal": 0x20, "above_normal": 0x8000, "high": 0x80}


def set_process_priority(level):
    """Set this process's Windows priority class; False if Windows refused."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    return bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), PRIORITY_CLASSES[level]))


def clipboard_sequence():
    return _apis()[0].GetClipboardSequenceNumber()


_paste_lock = threading.Lock()


def paste_text(text, auto_enter=False, guard=lambda: True, commit=lambda: True):
    """Paste text and restore only the clipboard version owned by this paste.

    Clipboard ownership lasts until restore, so the next take saves the user's
    original clipboard rather than an earlier take's transient transcript.
    The restore waits for the target to read the clipboard; it runs after
    return so the result is reported as soon as the keys are sent.
    """
    if not text:
        return False
    import pyperclip
    _paste_lock.acquire()
    copied = False
    try:
        if modifiers_down() or not guard():
            return False
        old = pyperclip.paste()
        pyperclip.copy(text)
        sequence = clipboard_sequence()
        copied = True
        time.sleep(0.05)
        if modifiers_down() or not guard():
            return False
        if not commit():
            return False
        send_keys([(0x11, False), (0x56, False), (0x56, True), (0x11, True)])
        if auto_enter:
            time.sleep(0.1)
            if not modifiers_down() and guard():
                send_keys([(0x0D, False), (0x0D, True)])
        return True
    finally:
        if copied:
            # Non-daemon: quitting right after a paste still restores the clipboard.
            threading.Thread(target=_restore_clipboard, args=(pyperclip, old, text, sequence),
                             name="clipboard-restore").start()
        else:
            _paste_lock.release()


def _restore_clipboard(pyperclip, old, text, sequence):
    """Restore the user's clipboard, then free the next paste."""
    try:
        time.sleep(0.35)
        if clipboard_sequence() == sequence and pyperclip.paste() == text:
            pyperclip.copy(old)
    finally:
        _paste_lock.release()


def settle_clipboard(timeout=2):
    """Wait until no clipboard restore is pending."""
    if not _paste_lock.acquire(timeout=timeout):
        return False
    _paste_lock.release()
    return True
