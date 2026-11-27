"""Dynamic icon generation for system tray states using Pillow."""

from PIL import Image, ImageDraw

ICON_SIZE = 64
APP_ICON_COLOR = "#2F6FDE"
CHECK_COLOR = "#FFD43B"
DOT_SIZE, DOT_GAP = 230, 36          # Status dot and the gap around it, out of 600
LOADING_DOT = "#8A8F98"              # Mid gray: reads on light and dark taskbars
RECORDING_DOT = "#EF4444"
PROCESSING_DOT = "#F97316"


def render_app_icon(size, color=APP_ICON_COLOR, mark="check", dot=None):
    """A round speech bubble with a check: "copy that", heard and confirmed.

    mark="alert" draws an exclamation mark in place of the check. dot is a status color
    for a dot in the lower right, cut out of the bubble with a clear gap so it stands
    apart on light and dark taskbars alike (#8)."""
    ss = 600  # Draw large so the check stays crisp at 16px
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # A wide oval with a short tail dropping from its lower left. The short tail leaves
    # room for a bigger bubble and check, which matters most at 16px in the tray.
    draw.ellipse([10, 30, 590, 470], fill=color)
    draw.polygon([(140, 400), (140, 585), (320, 450)], fill=color)
    if mark == "alert":
        draw.line([(280, 105), (280, 270)], fill=CHECK_COLOR, width=86)
        draw.ellipse([238, 318, 322, 402], fill=CHECK_COLOR)
    else:
        draw.line([(175, 250), (285, 350), (470, 160)], fill=CHECK_COLOR, width=86, joint="curve")
    if dot:
        # About 6px across at 16px, with a 1px gap: big enough to read at 100% scaling.
        right = ss - 6
        left = right - DOT_SIZE
        draw.ellipse([left - DOT_GAP, left - DOT_GAP, right + DOT_GAP, right + DOT_GAP], fill=(0, 0, 0, 0))
        draw.ellipse([left, left, right, right], fill=dot)
    return img.resize((size, size), Image.LANCZOS)


def save_app_icon(path):
    """Write a multi-size Windows .ico for the executable and installer."""
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    render_app_icon(256).save(path, sizes=[(s, s) for s in sizes])


GLYPH_FONTS = (r"C:\Windows\Fonts\SegoeIcons.ttf", r"C:\Windows\Fonts\segmdl2.ttf")
GLYPHS = {"Home": "\uE80F", "Meetings": "\uE716", "Dictation": "\uE720", "Dictionary": "\uE82D", "Settings": "\uE713"}


def render_glyph(char, color, size):
    """A Windows icon font glyph (Fluent on 11, MDL2 on 10) on a transparent square,
    or None when neither font is installed."""
    from PIL import ImageFont
    for path in GLYPH_FONTS:
        try:
            font = ImageFont.truetype(path, int(size * 0.8))
            break
        except OSError:
            continue
    else:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((size / 2, size / 2), char, font=font, fill=color, anchor="mm")
    return img


WELCOME_BITMAP_SIZE = (164, 314)  # The installer's welcome and finish page panel.
WELCOME_BACKGROUND = "#EAF1FD"


def save_welcome_bitmap(path, scale=2):
    """Write the installer's side panel: the app icon on a pale blue panel.
    Drawn at 2x so a DPI-aware installer scales it down, not up."""
    width, height = (n * scale for n in WELCOME_BITMAP_SIZE)
    panel = Image.new("RGB", (width, height), WELCOME_BACKGROUND)
    icon = render_app_icon(96 * scale)
    panel.paste(icon, ((width - icon.width) // 2, height // 3 - icon.height // 2), icon)
    panel.save(path, format="BMP")


def icon_loading() -> Image.Image:
    """App icon with a gray status dot: the model is loading."""
    return render_app_icon(ICON_SIZE, dot=LOADING_DOT)


def icon_ready() -> Image.Image:
    """Plain app icon: ready for recording. A green dot would blur into the check."""
    return render_app_icon(ICON_SIZE)


def icon_recording() -> Image.Image:
    """App icon with a red status dot: the microphone is on."""
    return render_app_icon(ICON_SIZE, dot=RECORDING_DOT)


def icon_processing() -> Image.Image:
    """App icon with an orange status dot: transcribing (yellow would match the check)."""
    return render_app_icon(ICON_SIZE, dot=PROCESSING_DOT)


def icon_error() -> Image.Image:
    """An exclamation mark in place of the check, with a red dot. The mark is what tells it
    from recording at 16px, where an X inside the dot was too small to see."""
    return render_app_icon(ICON_SIZE, mark="alert", dot=RECORDING_DOT)


STATE_ICONS = {
    "loading": icon_loading,
    "ready": icon_ready,
    "recording": icon_recording,
    "processing": icon_processing,
    "error": icon_error,
}
