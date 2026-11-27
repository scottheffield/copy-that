"""How the Dictation page labels each take."""
from datetime import date

from ui.history import length, when


def test_time_alone_for_today_and_date_for_older_takes():
    today = date(2026, 11, 27)
    assert when("2026-11-27T09:05:00", today) == "9:05 AM"
    assert when("2026-11-27T21:40:12.500000", today) == "9:40 PM"
    assert when("2026-11-26T09:05:00", today) == "Nov 26, 9:05 AM"
    assert when("", today) == "" and when(None, today) == ""


def test_length_reads_as_seconds_then_minutes():
    assert length(41.0) == "41 seconds"
    assert length(1.2) == "1 second"
    assert length(65) == "1:05"
    assert length(None) == "0 seconds"
