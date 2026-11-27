"""Usage stats for Home (#20): totals, streaks, the heatmap and the friendly wording."""
import json
from datetime import date, timedelta

import usage_stats
from usage_stats import UsageStats, best_streak, heatmap, streak, time_saved, words_like

FRI = date(2026, 11, 27)  # A Friday


def days_of(*pairs):
    return {day.isoformat(): words for day, words in pairs}


def test_record_keeps_numbers_only(tmp_path):
    stats = UsageStats(tmp_path / "stats.json")
    stats.record("hello there general kenobi", 2.5, today=FRI)
    stats.record("", 1.0, today=FRI)  # Nothing said: not a take
    saved = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert saved["words"] == 4 and saved["takes"] == 1 and saved["seconds"] == 2.5
    assert saved["days"] == {"2026-11-27": 4}
    assert "kenobi" not in (tmp_path / "stats.json").read_text(encoding="utf-8")
    # A new instance reads the same totals.
    assert UsageStats(tmp_path / "stats.json").summary(today=FRI)["words"] == 4


def test_speed_and_time_saved(tmp_path):
    stats = UsageStats(tmp_path / "stats.json")
    stats.record(" ".join(["word"] * 150), 60, today=FRI)
    summary = stats.summary(today=FRI)
    assert summary["wpm"] == 150 and summary["times_faster"] == 3.8
    # Typing 150 words at 40 wpm takes 3.75 minutes; speaking took 1.
    assert abs(summary["saved_minutes"] - 2.75) < 1e-9


def test_a_bad_file_starts_fresh_and_reset_deletes_it(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("{not json", encoding="utf-8")
    stats = UsageStats(path)
    assert stats.summary(today=FRI)["words"] == 0
    path.write_text(json.dumps({"words": -3, "days": {"nope": 5, "2026-11-26": "x", "2026-11-25": 7}}),
                    encoding="utf-8")
    stats = UsageStats(path)
    assert stats.summary(today=FRI)["words"] == 0 and stats._data["days"] == {"2026-11-25": 7}
    stats.record("one two", 1, today=FRI)
    stats.reset()
    assert not path.exists() and stats.summary(today=FRI)["words"] == 0


def test_milestone_shows_for_a_week(tmp_path):
    stats = UsageStats(tmp_path / "stats.json")
    stats.record(" ".join(["w"] * 999), 300, today=FRI)
    assert stats.summary(today=FRI)["milestone"] is None
    stats.record("two more", 1, today=FRI)
    assert stats.summary(today=FRI)["milestone"] == 1_000
    assert stats.summary(today=FRI + timedelta(days=6))["milestone"] == 1_000
    assert stats.summary(today=FRI + timedelta(days=7))["milestone"] is None


def test_streak_rules():
    thu, wed, mon = FRI - timedelta(days=1), FRI - timedelta(days=2), FRI - timedelta(days=4)
    assert streak({}, FRI) == 0
    # Today not dictated yet: the streak is still alive from yesterday.
    assert streak(days_of((thu, 5), (wed, 5)), FRI) == 2
    assert streak(days_of((FRI, 1), (thu, 5), (wed, 5)), FRI) == 3
    # A missed weekday breaks it.
    assert streak(days_of((FRI, 1), (wed, 5)), FRI) == 1
    # A weekend without dictation doesn't.
    last_fri = FRI - timedelta(days=7)
    assert streak(days_of((mon, 1), (last_fri, 1)), mon) == 2
    # Dictating on a weekend counts.
    sat = FRI + timedelta(days=1)
    assert streak(days_of((sat, 1), (FRI, 1)), sat) == 2
    # Long ago doesn't count.
    assert streak(days_of((FRI - timedelta(days=30), 9)), FRI) == 0


def test_best_streak():
    assert best_streak({}) == 0
    mon = FRI - timedelta(days=4)
    week = days_of(*((mon + timedelta(days=n), 1) for n in range(5)))
    next_mon = mon + timedelta(days=7)
    assert best_streak({**week, next_mon.isoformat(): 1}) == 6  # Across the weekend
    assert best_streak({**week, (next_mon + timedelta(days=1)).isoformat(): 1}) == 5


def test_heatmap_shape_and_levels():
    weeks = heatmap(days_of((FRI, 100), (FRI - timedelta(days=1), 10)), FRI)
    assert len(weeks) == usage_stats.HEATMAP_WEEKS and all(len(column) == 7 for column in weeks)
    last = weeks[-1]
    assert last[0][0].weekday() == 6  # Sunday first
    assert last[5] == (FRI, 4) and last[4][1] == 1 and last[6][1] is None  # Saturday is still ahead
    assert weeks[0][0][1] == 0


def test_friendly_words():
    assert words_like(100) == "Your first page is on its way"
    assert words_like(1_000) == "About 4 pages"
    assert words_like(48_210) == "About half a novel"
    assert words_like(400_000) == "About 4 novels"
    assert time_saved(40) == ("40", "minutes") and time_saved(1) == ("1", "minute")
    assert time_saved(150) == ("2.5", "hours") and time_saved(900) == ("15", "hours")


def test_save_retries_a_briefly_locked_file(tmp_path, monkeypatch):
    import os
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("held by a scanner")
        real(src, dst)
    monkeypatch.setattr(usage_stats.os, "replace", flaky)
    monkeypatch.setattr(usage_stats, "SAVE_RETRY_SECONDS", 0)
    stats = UsageStats(tmp_path / "stats.json")
    stats.record("one two", 1, today=FRI)
    assert len(calls) == 3 and json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))["words"] == 2
