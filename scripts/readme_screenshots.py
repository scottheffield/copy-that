"""Take the README screenshots from the real windows, filled with made-up data.

    python scripts/readme_screenshots.py            # light and dark
    python scripts/readme_screenshots.py --theme dark

Each theme runs in its own process with a throwaway data folder (COPYTHAT_DATA_DIR),
so your own history, meetings and stats are never read or shown. The people, company
and meetings below are invented. Windows open briefly on screen while they are
captured; the pointer moves to a corner of the screen and back so nothing shows hover.

Writes docs/screenshots/{home,meetings,meeting,pill,tray}-{light,dark}.png.
Needs a desktop session, so it isn't part of the tests.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
THEMES = ("light", "dark")
SHOTS = ("home", "meetings", "meeting", "pill", "tray")

MY_NAME = "Jordan"
BACKDROP = {"light": "#DDE3EB", "dark": "#0D1118"}  # Behind each window, so no desktop shows
PAD = 28            # Backdrop around a window, unscaled px
SETTLE = 0.7        # Seconds for a window to draw and DWM to finish its fade-in
WORDS_PER_SECOND = 2.6

# -- Made-up meetings: a small outdoor gear company, Fernhill ------------------------------
# (days ago, hour, minute, minutes long, title, [(speaker, words)]). Me is the mic track.

KICKOFF = [
    ("Me", "Morning, everyone. Thanks for jumping on early. The goal today is to lock the spring "
           "catalog timeline and figure out who owns what."),
    ("Priya", "Sounds good. I pulled last year's schedule so we have something to compare against. "
              "We were about two weeks late on photography."),
    ("Marcus", "That was mostly the weather. We had three outdoor shoots rained out in March."),
    ("Me", "Right. So can we move the outdoor shoots up to late February and keep a studio day as the backup?"),
    ("Elena", "February works for the trail packs. The new tents won't have final samples until the "
              "second week of March, though."),
    ("Priya", "Then let's shoot the tents in the studio first and save the outdoor setup for the packs "
              "and apparel."),
    ("Marcus", "I can book the studio for March tenth through twelfth. I'll check the lake location for "
               "the twenty-fourth as well."),
    ("Me", "Great. Elena, can you confirm the sample dates with the factory by Friday?"),
    ("Elena", "Yes. I'll send them a note this afternoon and copy everyone."),
    ("Priya", "One more thing. The copy deck was the other bottleneck last year. I'd like product "
              "descriptions locked before the shoot, not after."),
    ("Me", "Agreed. Let's set February sixteenth as the copy freeze. I'll draft the pack descriptions this week."),
    ("Marcus", "Do we still want the field notes section? It tested well, but it took a lot of layout time."),
    ("Priya", "Keep it, but trim it to four pages. That alone saves us a few days."),
    ("Me", "Okay, to recap: copy freeze on the sixteenth, studio March tenth to twelfth, the lake shoot "
           "on the twenty-fourth, and Elena confirms samples by Friday."),
    ("Elena", "And I'll put the shared timeline in the project folder so we're all looking at the same dates."),
    ("Me", "Perfect. Thanks, everyone. Same time next week."),
]
HIGHLIGHT_LINE = 4  # The transcript line shown as playing

MEETINGS = [
    (0, 9, 2, None, "Spring catalog check-in", KICKOFF),
    (1, 14, 30, 28, "Design review: trail pack page", [
        ("Sam", "I moved the size chart above the fold. Shoppers kept scrolling past it."),
        ("Me", "That reads much better. Can we try the photo carousel at full width too?"),
        ("Priya", "Full width works on desktop. On phones I'd keep the thumbnails."),
    ]),
    (2, 11, 0, 15, "Vendor call: recycled packaging", [
        ("Dana", "The recycled mailers come in three sizes, and we can print one color on them."),
        ("Me", "One color is fine. What's the lead time on the first order?"),
        ("Dana", "About four weeks from artwork approval."),
    ]),
    (3, 16, 0, 47, "Hiring sync: support lead", [
        ("Marcus", "We have four strong candidates. Two of them have run a support team before."),
        ("Me", "Let's do second interviews with those two next week."),
        ("Elena", "I can sit in on Tuesday's."),
    ]),
    (6, 10, 30, 25, "1:1 with Priya", [
        ("Priya", "The catalog plan is in good shape. I'd like to take on the wholesale lookbook next."),
        ("Me", "That makes sense. Let's map out what you'd hand off first."),
    ]),
    (9, 13, 0, 65, "Q1 budget walkthrough", [
        ("Elena", "Photography is the biggest line. Moving the shoots earlier doesn't change the total."),
        ("Me", "Good. Then the open question is the trade show booth."),
        ("Marcus", "I'll get two quotes by the end of the month."),
    ]),
]

PILL_DRAFT = "Hi Elena,\n\nThanks for confirming the sample dates. "
PILL_LIVE = "I'll book the studio for March tenth and send the updated timeline to everyone by Friday"


# -- Seeding the data folder -------------------------------------------------------------------

def timed_words(lines, start=4.0):
    """(mic words, others words, others labels, names, end seconds) for (speaker, words) lines.
    Others get Speaker 1, 2... labels in order of first appearance, named as given."""
    from audio_chunks import TimedWord
    mic, others, labels, names = [], [], [], {"Me": MY_NAME}
    by_name = {}
    t = start
    for n, (speaker, text) in enumerate(lines):
        words = text.split()
        times = [t + i / WORDS_PER_SECOND for i in range(len(words))]
        if speaker == "Me":
            mic += [TimedWord(w, round(s, 3)) for w, s in zip(words, times)]
        else:
            label = by_name.setdefault(speaker, f"Speaker {len(by_name) + 1}")
            names[label] = speaker
            others += [TimedWord(w, round(s, 3)) for w, s in zip(words, times)]
            labels += [label] * len(words)
        t = times[-1] + 2.2 + (n % 3) * 0.9  # A pause between speakers, so lines don't merge
    return mic, others, labels, names, t


def seed_meetings(root, today):
    """Meeting folders as a finished recording leaves them. The first gets silent audio,
    so its page shows the player."""
    import meetings
    import numpy as np
    import soundfile
    folders = []
    for days_ago, hour, minute, length, title, lines in MEETINGS:
        started = datetime.combine(today - timedelta(days=days_ago), datetime.min.time()).replace(
            hour=hour, minute=minute)
        mic, others, labels, names, end = timed_words(lines)
        duration = round(end + 20) if length is None else length * 60
        meta = {"title": title, "started": started.isoformat(), "duration_seconds": duration,
                "status": "done", "mic_device": None, "output_devices": [], "speakers": {"status": "done"}}
        folder = Path(root) / f"{started:%Y-%m-%d %H%M} {meetings.folder_title(title)}"
        folder.mkdir(parents=True)
        meetings.write_meta(folder, meta)
        meetings.write_words(folder, mic, others, True)
        meetings.write_speakers(folder, {"labels": {"others": labels}, "names": names})
        meetings.rebuild_transcript(folder)
        if length is None:
            silence = np.zeros(int(duration * meetings.RATE), dtype=np.int16)
            soundfile.write(str(folder / "mic.wav"), silence, meetings.RATE, subtype="PCM_16")
        folders.append(folder)
    return folders


def seed_stats(path, today):
    """Six months of weekday dictation, a few weekends, and today, at about 150 wpm."""
    rng = random.Random(7)
    days = {}
    for back in range(7 * 26):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        recent = back <= 30  # Every weekday lately, for a streak worth showing
        if (weekend and rng.random() < 0.15) or (not weekend and (recent or rng.random() < 0.85)):
            days[day.isoformat()] = rng.randint(120, 600) if weekend else rng.randint(350, 2600)
    words = sum(days.values())
    data = {"version": 1, "words": words, "seconds": round(words / 152 * 60, 1),
            "takes": words // 38, "days": days, "milestone": None}
    Path(path).write_text(json.dumps(data), encoding="utf-8")


# -- Capturing -----------------------------------------------------------------------------------

class Provider:
    """Stands in for the app: idle, nothing recording, nothing busy."""

    def meeting_status(self):
        return "idle", 0

    def dictation_status(self):
        return "Ready"

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def frame_hwnd(win):
    import ctypes
    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


def bounds(win):
    """(left, top, right, bottom) of a window as drawn, without its shadow, in physical px."""
    import ctypes
    import ctypes.wintypes as wintypes
    rect = wintypes.RECT()
    hwnd = frame_hwnd(win)
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)):
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))  # DWMWA_EXTENDED_FRAME_BOUNDS failed
    return rect.left, rect.top, rect.right, rect.bottom


class Studio:
    """A plain backdrop behind the window being captured."""

    def __init__(self, root, color):
        import tkinter as tk
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg=color)
        self.win.attributes("-topmost", True)
        self.win.withdraw()

    def settle(self, seconds=SETTLE):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.02)

    def shoot(self, path, *wins):
        """Put the backdrop behind wins (front last), then grab them with the padding."""
        import ctypes
        from PIL import ImageGrab
        self.settle(0.2)
        pad = round(PAD * (ctypes.windll.user32.GetDpiForWindow(frame_hwnd(wins[0])) or 96) / 96)
        rects = [bounds(w) for w in wins]
        left = min(r[0] for r in rects) - pad
        top = min(r[1] for r in rects) - pad
        right = max(r[2] for r in rects) + pad
        bottom = max(r[3] for r in rects) + pad
        self.win.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
        self.win.deiconify()
        self.win.lift()
        for w in wins:
            w.attributes("-topmost", True)
            w.lift()
        self.settle()
        ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True).save(path, optimize=True)
        self.win.withdraw()
        for w in wins:
            try:
                w.attributes("-topmost", False)
            except Exception:
                pass
        print(f"  {path}")


def park_pointer():
    """Move the pointer to the bottom-right corner of its screen; returns where it was."""
    import ctypes
    from ui.screen import cursor, work_area
    was = cursor()
    area = work_area(*was)
    if area:
        ctypes.windll.user32.SetCursorPos(area[2] - 2, area[3] - 2)
    return was


def capture(mode, out):
    data = Path(tempfile.mkdtemp(prefix="copythat-shots-"))
    os.environ["COPYTHAT_DATA_DIR"] = str(data)
    sys.path.insert(0, str(ROOT))
    import ctypes
    import customtkinter as ctk
    from dictation_engine import DEFAULT_CONFIG
    from ui import theme
    import ui.home_page as home_page
    from ui.overlay import OverlayWindow
    from ui.settings import SettingsWindow
    from ui.tray_panel import TrayPanel
    from usage_stats import UsageStats

    today = date.today()
    meetings_dir = data / "Meetings"
    folders = seed_meetings(meetings_dir, today)
    seed_stats(data / "stats.json", today)
    config = {**DEFAULT_CONFIG, "theme": mode, "highlight": "blue", "meetings_dir": str(meetings_dir),
              "meeting_my_name": MY_NAME, "usage_stats": True, "main_window_page": "Home"}

    # The greeting follows the clock; keep it "Good morning" whenever this runs.
    real_greeting = home_page.greeting
    home_page.greeting = lambda name, now=None: real_greeting(name, datetime.now().replace(hour=10))

    ctk.set_appearance_mode(mode)
    theme.set_highlight("blue")
    root = ctk.CTk()
    root.withdraw()
    studio = Studio(root, BACKDROP[mode])
    stats = UsageStats(data / "stats.json")
    provider = Provider()
    was = park_pointer()
    out.mkdir(parents=True, exist_ok=True)
    try:
        main = SettingsWindow(root, config, [], meetings=provider, stats=stats)
        main.show("Home")
        studio.settle()
        from ui import screen
        screen.place(main._win, 840, 860, 720, 620)  # Roomier than usual, so more of each page shows
        studio.settle()
        studio.shoot(out / f"home-{mode}.png", main._win)

        main.show("Meetings")
        studio.settle()
        studio.shoot(out / f"meetings-{mode}.png", main._win)

        main.show_meeting(folders[0])
        studio.settle()
        page = main._meetings_page
        if page._player is not None and page._transcript is not None:
            start = page._transcript.starts[HIGHLIGHT_LINE][0]
            page._player._position = int((start + 2) * 16000)  # As if paused partway through that line
            page._update_controls()
        studio.shoot(out / f"meeting-{mode}.png", main._win)
        main._win.withdraw()

        # The tray panel, opened above the middle of the taskbar, then lifted clear of it so
        # the backdrop's padding doesn't run onto the taskbar.
        from ui.screen import work_area
        area = work_area(*was) or (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        panel = TrayPanel(root, provider, stats, lambda: config, lambda: str(meetings_dir))
        panel._close_if_unfocused = lambda: None  # The backdrop mustn't close it
        panel.show(anchor=((area[0] + area[2]) // 2, area[3] + 10))
        left, top, _right, _bottom = bounds(panel._win)
        panel._win.geometry(f"+{left}+{top - 200}")
        studio.settle()
        studio.shoot(out / f"tray-{mode}.png", panel._win)
        panel.close()

        studio.shoot(out / f"pill-{mode}.png", *pill_scene(root, mode, OverlayWindow, theme))
    finally:
        ctypes.windll.user32.SetCursorPos(*was)
        try:
            root.destroy()
        except Exception:
            pass
        shutil.rmtree(data, ignore_errors=True)


def pill_scene(root, mode, OverlayWindow, theme):
    """A plain draft window with the caret after a sentence, and the bubble recording below it."""
    import math
    import tkinter as tk
    import customtkinter as ctk
    from ui import screen

    draft = ctk.CTkToplevel(root)
    draft.title("Draft")
    screen.place(draft, 760, 250, 600, 200)
    draft.configure(fg_color=theme.BG)
    theme.title_bar(draft)
    scale = ctk.ScalingTracker.get_widget_scaling(draft)
    text = tk.Text(draft, bd=0, highlightthickness=0, wrap="word", font=("Segoe UI", round(11 * scale)),
                   bg=theme.color(theme.BG, mode), fg=theme.color(theme.TEXT, mode),
                   insertbackground=theme.color(theme.TEXT, mode), padx=round(28 * scale), pady=round(24 * scale))
    text.pack(fill="both", expand=True)
    text.insert("1.0", PILL_DRAFT)
    text.mark_set("insert", "end-1c")
    draft.update()
    text.focus_force()
    draft.update()

    overlay = OverlayWindow(root)
    overlay.set_theme(mode)
    overlay.show_recording()
    for n in range(26):
        overlay.update_audio_level(0.35 + 0.3 * math.sin(n * 0.9) + 0.2 * math.sin(n * 2.3))
    overlay._rec_start = time.time() - 6.4
    x, y, _w, h = text.bbox("insert")
    left, top = text.winfo_rootx() + x, text.winfo_rooty() + y
    overlay.set_field(True, (left, top, left + 2, top + h))
    overlay.set_live_text(PILL_LIVE)
    return draft, overlay._win


# -- Running ---------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--theme", choices=("both",) + THEMES, default="both")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--capture", choices=THEMES, help=argparse.SUPPRESS)  # One theme, in this process
    args = parser.parse_args()
    if args.capture:
        capture(args.capture, args.out.resolve())
        return
    for mode in THEMES if args.theme == "both" else (args.theme,):
        print(f"{mode}:")
        subprocess.run([sys.executable, __file__, "--capture", mode, "--out", str(args.out)], check=True)


if __name__ == "__main__":
    main()
