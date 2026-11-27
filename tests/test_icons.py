"""Tests for icon generation."""

import pytest
from PIL import Image

from ui.icons import (
    icon_loading, icon_ready, icon_recording, icon_processing, icon_error,
    STATE_ICONS, ICON_SIZE,
)


class TestIconGeneration:
    """Verify each icon returns a 64x64 RGBA image."""

    @pytest.mark.parametrize("fn", [
        icon_loading, icon_ready, icon_recording, icon_processing, icon_error,
    ])
    def test_icon_size_and_mode(self, fn):
        img = fn()
        assert isinstance(img, Image.Image)
        assert img.size == (ICON_SIZE, ICON_SIZE)
        assert img.mode == "RGBA"

    def test_state_icons_dict_has_all_states(self):
        expected = {"loading", "ready", "recording", "processing", "error"}
        assert set(STATE_ICONS.keys()) == expected

    def test_state_icons_callable(self):
        for name, fn in STATE_ICONS.items():
            img = fn()
            assert isinstance(img, Image.Image), f"STATE_ICONS['{name}'] did not return an Image"

    def test_icons_are_not_fully_transparent(self):
        """Each icon should have some non-transparent pixels."""
        for name, fn in STATE_ICONS.items():
            img = fn()
            alpha = img.split()[3]  # Alpha channel
            assert alpha.getextrema()[1] > 0, f"Icon '{name}' is fully transparent"

    def test_error_icon_differs_from_recording(self):
        """Error icon (X overlay) should differ from plain recording icon."""
        rec = icon_recording()
        err = icon_error()
        assert rec.tobytes() != err.tobytes()


def test_welcome_bitmap_is_a_2x_bmp_for_the_installer(tmp_path):
    from PIL import Image
    from ui.icons import save_welcome_bitmap
    path = tmp_path / "welcome.bmp"
    save_welcome_bitmap(path)
    with Image.open(path) as image:
        assert image.format == "BMP" and image.mode == "RGB"
        assert image.size == (328, 628)


def test_status_dot_is_cut_out_so_it_reads_on_any_taskbar():
    """#8: a clear gap, not a white ring (which vanished on a light taskbar), parts the dot from the bubble."""
    from ui.icons import icon_recording, icon_ready
    assert icon_ready().getpixel((41, 41))[3] > 200        # Bubble there on the plain icon
    assert icon_recording().getpixel((41, 41))[3] < 60     # The gap around the dot
    assert icon_recording().getpixel((56, 56))[:3][0] > 200  # The red dot itself


def test_error_differs_from_recording_at_16px():
    """#8: at 100% scaling the error icon must not look like recording; its mark replaces the check."""
    from ui.icons import icon_error, icon_recording
    error = icon_error().resize((16, 16), Image.LANCZOS).convert("RGBA")
    recording = icon_recording().resize((16, 16), Image.LANCZOS).convert("RGBA")
    differ = sum(1 for x in range(12) for y in range(10)
                 if sum(abs(a - b) for a, b in zip(error.getpixel((x, y)), recording.getpixel((x, y)))) > 120)
    assert differ >= 8
