"""Windows opened from the tray fit the screen they open on."""
from ui.screen import MARGIN, TITLE_BAR, fit


def test_settings_shrinks_to_a_1080p_screen_at_150_percent():
    area = (0, 0, 1920, 1032)  # 1080p minus the taskbar
    w, h, min_w, min_h, x, y = fit(area, 1.5, 760, 760, 720, 620)
    assert w == 760 and h < 760
    assert y >= 0 and y + (h + TITLE_BAR) * 1.5 <= 1032
    assert min_h <= h  # Windows would otherwise force it taller than the screen.


def test_a_window_that_fits_keeps_its_size_and_is_centered():
    w, h, min_w, min_h, x, y = fit((0, 0, 2560, 1400), 1.0, 600, 580, 480, 380)
    assert (w, h, min_w, min_h) == (600, 580, 480, 380)
    assert x == (2560 - 600) // 2 and y == (1400 - 580 - TITLE_BAR) // 2


def test_second_monitor_offsets_are_kept():
    w, h, min_w, min_h, x, y = fit((1920, -200, 3840, 880), 1.0, 600, 580, 480, 380)
    assert 1920 + MARGIN <= x and x + w <= 3840 and -200 <= y
