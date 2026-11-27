"""Tests for ui.glass: PIL rendering helpers."""

from PIL import Image

import pytest

from ui.glass import (
    TRANSPARENT_RGB, PillCache, composite_on_transparent,
    render_dot, render_icon_mic, render_icon_stop, render_pill,
    render_button, _hex_to_rgba,
)


# --- render_pill --------------------------------------------------------------

class TestRenderPill:
    def test_returns_correct_size_rgba(self):
        img = render_pill(150, 38, radius=19)
        assert img.size == (150, 38)
        assert img.mode == "RGBA"

    def test_center_is_opaque(self):
        img = render_pill(150, 38, radius=19)
        cx, cy = 75, 19
        _, _, _, a = img.getpixel((cx, cy))
        assert a > 200, "Center pixel should be nearly opaque"

    def test_corners_are_transparent(self):
        img = render_pill(150, 38, radius=19)
        _, _, _, a = img.getpixel((0, 0))
        assert a < 50, "Top-left corner should be mostly transparent"

    def test_different_sizes(self):
        for w, h in [(300, 70), (100, 30), (200, 54)]:
            img = render_pill(w, h, radius=min(h // 2, 19))
            assert img.size == (w, h)


# --- render_dot ---------------------------------------------------------------

class TestRenderDot:
    def test_returns_correct_size(self):
        img = render_dot(10, (48, 255, 88, 255))
        assert img.size == (10, 10)
        assert img.mode == "RGBA"

    def test_center_matches_color(self):
        color = (48, 209, 88, 255)
        img = render_dot(20, color)
        r, g, b, a = img.getpixel((10, 10))
        assert a > 200
        # Color should be close (LANCZOS can shift slightly)
        assert abs(r - color[0]) < 20
        assert abs(g - color[1]) < 20


# --- render_icon_mic ----------------------------------------------------------

class TestRenderIconMic:
    def test_returns_correct_size(self):
        img = render_icon_mic(24, "#30D158")
        assert img.size == (24, 24)
        assert img.mode == "RGBA"

    def test_not_empty(self):
        img = render_icon_mic(24, "#30D158")
        # Should have some non-transparent pixels
        pixels = list(img.tobytes())
        # RGBA: every 4th byte is alpha
        visible = sum(1 for i in range(3, len(pixels), 4) if pixels[i] > 10)
        assert visible > 10, "Mic icon should have visible content"


# --- render_icon_stop ---------------------------------------------------------

class TestRenderIconStop:
    def test_returns_correct_size(self):
        img = render_icon_stop(24, "#FF453A")
        assert img.size == (24, 24)
        assert img.mode == "RGBA"

    def test_not_empty(self):
        img = render_icon_stop(24, "#FF453A")
        pixels = list(img.tobytes())
        visible = sum(1 for i in range(3, len(pixels), 4) if pixels[i] > 10)
        assert visible > 10, "Stop icon should have visible content"


# --- render_button ------------------------------------------------------------

class TestRenderButton:
    def test_returns_correct_size(self):
        icon = render_icon_mic(12, "#30D158")
        img = render_button(24, "#30D158", "#1B3A20", icon)
        assert img.size == (24, 24)
        assert img.mode == "RGBA"

    def test_center_has_content(self):
        icon = render_icon_mic(12, "#30D158")
        img = render_button(24, "#30D158", "#1B3A20", icon)
        _, _, _, a = img.getpixel((12, 12))
        assert a > 100


# --- composite_on_transparent -------------------------------------------------

class TestCompositeOnTransparent:
    def test_returns_rgb(self):
        rgba = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        result = composite_on_transparent(rgba)
        assert result.mode == "RGB"

    def test_transparent_areas_become_bg(self):
        rgba = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        result = composite_on_transparent(rgba)
        assert result.getpixel((5, 5)) == TRANSPARENT_RGB

    def test_opaque_areas_preserved(self):
        rgba = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        result = composite_on_transparent(rgba)
        assert result.getpixel((5, 5)) == (255, 0, 0)

    def test_alpha_threshold_opaque(self):
        # Alpha >= 128 is treated as fully opaque (no fringe artifacts)
        rgba = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
        result = composite_on_transparent(rgba)
        r, g, b = result.getpixel((5, 5))
        assert (r, g, b) == (255, 0, 0), "alpha>=128 should be fully opaque"

    def test_alpha_threshold_transparent(self):
        # Alpha < 128 is treated as fully transparent (becomes bg color)
        rgba = Image.new("RGBA", (10, 10), (255, 0, 0, 127))
        result = composite_on_transparent(rgba)
        assert result.getpixel((5, 5)) == TRANSPARENT_RGB


# --- PillCache ----------------------------------------------------------------

class TestPillCache:
    def test_same_key_returns_same_object(self):
        cache = PillCache()
        img1 = cache.get(150, 38, radius=19)
        img2 = cache.get(150, 38, radius=19)
        assert img1 is img2

    def test_different_key_returns_different_object(self):
        cache = PillCache()
        img1 = cache.get(150, 38, radius=19)
        img2 = cache.get(300, 70, radius=19)
        assert img1 is not img2

    def test_invalidate_clears(self):
        cache = PillCache()
        img1 = cache.get(150, 38, radius=19)
        cache.invalidate()
        img2 = cache.get(150, 38, radius=19)
        assert img1 is not img2

    def test_evicts_oldest_at_max_size(self):
        cache = PillCache(max_size=2)
        cache.get(10, 10, radius=5)
        cache.get(20, 20, radius=10)
        cache.get(30, 30, radius=15)  # should evict (10, 10)
        assert (10, 10) not in cache._cache
        assert (20, 20) in cache._cache
        assert (30, 30) in cache._cache


# --- _hex_to_rgba helper -----------------------------------------------------

class TestHexToRgba:
    def test_basic(self):
        assert _hex_to_rgba("#FF0000") == (255, 0, 0, 255)
        assert _hex_to_rgba("#00FF00") == (0, 255, 0, 255)
        assert _hex_to_rgba("#0000FF") == (0, 0, 255, 255)

    def test_custom_alpha(self):
        assert _hex_to_rgba("#FF0000", alpha=128) == (255, 0, 0, 128)
