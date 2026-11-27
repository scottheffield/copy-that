"""Local usage stats for the Home page (#20): numbers only, kept on this PC.

stats.json holds running totals and a word count per day. It never holds text,
app names or times of day:
    {"version": 1, "words": 48210, "seconds": 19800.5, "takes": 1630,
     "days": {"2026-11-27": 812, ...}, "milestone": {"words": 25000, "day": "2026-11-20"}}
"""
import json
import math
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

TYPING_WPM = 40          # A typical typing speed, for "faster than typing" and time saved
MILESTONES = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)
MILESTONE_DAYS = 7       # How long Home shows a milestone after passing it
HEATMAP_WEEKS = 26
SAVE_TRIES, SAVE_RETRY_SECONDS = 5, 0.05


def count_words(text):
    return len((text or "").split())


class UsageStats:
    """Thread-safe running totals, saved after each take. A file that can't be read starts fresh."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    def _empty(self):
        return {"version": 1, "words": 0, "seconds": 0.0, "takes": 0, "days": {}, "milestone": None}

    def _load(self):
        data = self._empty()
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if not isinstance(saved, dict):
            return data
        for key in ("words", "takes"):
            if isinstance(saved.get(key), int) and saved[key] >= 0:
                data[key] = saved[key]
        if isinstance(saved.get("seconds"), (int, float)) and saved["seconds"] >= 0:
            data["seconds"] = float(saved["seconds"])
        if isinstance(saved.get("days"), dict):
            data["days"] = {day: words for day, words in saved["days"].items()
                            if isinstance(words, int) and words > 0 and _valid_day(day)}
        milestone = saved.get("milestone")
        if isinstance(milestone, dict) and isinstance(milestone.get("words"), int) and _valid_day(milestone.get("day")):
            data["milestone"] = {"words": milestone["words"], "day": milestone["day"]}
        return data

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._data), encoding="utf-8")
        # A virus scanner or the indexer can hold the file for a moment after a write.
        for attempt in range(SAVE_TRIES):
            try:
                os.replace(temp, self.path)
                return
            except PermissionError:
                if attempt == SAVE_TRIES - 1:
                    raise  # The totals stay in memory; the next take saves them
                time.sleep(SAVE_RETRY_SECONDS)

    def record(self, text, seconds, today=None):
        """Count one finished take: its words and how long it was held."""
        words = count_words(text)
        if words <= 0:
            return
        day = (today or date.today()).isoformat()
        with self._lock:
            before = self._data["words"]
            self._data["words"] += words
            self._data["seconds"] += max(0.0, float(seconds or 0))
            self._data["takes"] += 1
            self._data["days"][day] = self._data["days"].get(day, 0) + words
            passed = [m for m in MILESTONES if before < m <= self._data["words"]]
            if passed:
                self._data["milestone"] = {"words": passed[-1], "day": day}
            self._save()

    def reset(self):
        with self._lock:
            self._data = self._empty()
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def summary(self, today=None):
        """What Home shows. Every number is derived here, so the page only formats."""
        today = today or date.today()
        with self._lock:
            data = json.loads(json.dumps(self._data))
        words, seconds = data["words"], data["seconds"]
        minutes = seconds / 60
        wpm = round(words / minutes) if minutes >= 0.5 else 0
        saved_minutes = max(0.0, words / TYPING_WPM - minutes)
        milestone = data["milestone"]
        if milestone and (today - date.fromisoformat(milestone["day"])).days >= MILESTONE_DAYS:
            milestone = None
        days = data["days"]
        return {
            "words": words, "takes": data["takes"], "wpm": wpm,
            "times_faster": round(wpm / TYPING_WPM, 1) if wpm else 0,
            "saved_minutes": saved_minutes,
            "streak": streak(days, today), "best_streak": best_streak(days),
            "dictated_today": days.get(today.isoformat(), 0) > 0,
            "milestone": milestone["words"] if milestone else None,
            "weeks": heatmap(days, today),
        }


def _valid_day(day):
    try:
        date.fromisoformat(day)
        return True
    except (TypeError, ValueError):
        return False


def _rest_day(day):
    return day.weekday() >= 5  # A weekend without dictation doesn't break a streak


def streak(days, today):
    """Days in a row with dictation, up to today. Today doesn't count against it until it's
    over, and a Saturday or Sunday without dictation is skipped rather than breaking it."""
    count, day = 0, today
    if not days.get(day.isoformat()):
        day -= timedelta(days=1)
    while True:
        if days.get(day.isoformat()):
            count += 1
        elif not _rest_day(day):
            return count
        day -= timedelta(days=1)
        if count == 0 and (today - day).days > 3:
            return 0  # Nothing recent: no need to walk back further


def best_streak(days):
    """The longest run, by the same rules as streak()."""
    if not days:
        return 0
    dated = sorted(date.fromisoformat(day) for day in days)
    best = run = 0
    previous = None
    for day in dated:
        if previous is not None:
            between = [previous + timedelta(days=n) for n in range(1, (day - previous).days)]
            run = run + 1 if all(_rest_day(gap) for gap in between) else 1
        else:
            run = 1
        best = max(best, run)
        previous = day
    return best


def heatmap(days, today, weeks=HEATMAP_WEEKS):
    """weeks columns of 7 cells, Sunday first, ending with this week. Each cell:
    (date, level 0-4, or None after today). Levels are relative to the busiest day shown."""
    start = today - timedelta(days=(today.weekday() + 1) % 7) - timedelta(weeks=weeks - 1)
    cells = [start + timedelta(days=n) for n in range(weeks * 7)]
    counts = [days.get(day.isoformat(), 0) for day in cells]
    busiest = max(counts) or 1
    columns = []
    for week in range(weeks):
        column = []
        for n in range(7):
            day, words = cells[week * 7 + n], counts[week * 7 + n]
            level = None if day > today else (0 if not words else max(1, math.ceil(4 * words / busiest)))
            column.append((day, level))
        columns.append(column)
    return columns


# -- Words a person enjoys reading ----------------------------------------------------

def words_like(words):
    """A fun equivalent for a word count."""
    if words < 250:
        return "Your first page is on its way"
    if words < 7_500:
        return f"About {round(words / 250):,} pages"
    if words < 20_000:
        return "About a short story"
    if words < 45_000:
        return "About a novella"
    if words < 90_000:
        return "About half a novel"
    if words < 180_000:
        return "About a whole novel"
    return f"About {round(words / 90_000):,} novels"


def time_saved(minutes):
    """(number, unit) such as ("15", "hours") or ("40", "minutes")."""
    if minutes >= 90:
        hours = minutes / 60
        return (f"{hours:.1f}".rstrip("0").rstrip(".") if hours < 10 else f"{round(hours):,}"), "hours"
    whole = round(minutes)
    return str(whole), "minute" if whole == 1 else "minutes"
