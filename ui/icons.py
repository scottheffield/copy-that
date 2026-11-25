"""Dynamic icon generation for system tray states using Pillow."""

from PIL import Image, ImageDraw

ICON_SIZE = 64
APP_ICON_COLOR = "#2F6FDE"
CHECK_COLOR = "#FFD43B"


def render_app_icon(size, color=APP_ICON_COLOR):
    """A round speech bubble with a check: "copy that", heard and confirmed."""
    ss = 600  # Draw large so the check stays crisp at 16px
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # A wide oval with a short tail dropping from its lower left. The short tail leaves
    # room for a bigger bubble and check, which matters most at 16px in the tray.
    draw.ellipse([10, 30, 590, 470], fill=color)
    draw.polygon([(140, 400), (140, 585), (320, 450)], fill=color)
    draw.line([(175, 250), (285, 350), (470, 160)], fill=CHECK_COLOR, width=86, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


def save_app_icon(path):
    """Write a multi-size Windows .ico for the executable and installer."""
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    render_app_icon(256).save(path, sizes=[(s, s) for s in sizes])


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


def _app_icon_with_status(dot_color, show_x=False):
    """App icon with a colored status dot in the bottom-right corner."""
    base = render_app_icon(ICON_SIZE)
    draw = ImageDraw.Draw(base)

    # Status dot: 20px diameter with 2px white outline
    dot_d = 20
    outline_w = 2
    x2 = ICON_SIZE - 2
    y2 = ICON_SIZE - 2
    x1 = x2 - dot_d
    y1 = y2 - dot_d

    # White outline ring
    draw.ellipse([x1 - outline_w, y1 - outline_w,
                  x2 + outline_w, y2 + outline_w], fill="white")
    # Colored fill
    draw.ellipse([x1, y1, x2, y2], fill=dot_color)

    if show_x:
        m = 5
        draw.line([x1 + m, y1 + m, x2 - m, y2 - m], fill="white", width=2)
        draw.line([x2 - m, y1 + m, x1 + m, y2 - m], fill="white", width=2)

    return base


def icon_loading() -> Image.Image:
    """App icon with gray status dot — model loading."""
    return _app_icon_with_status("#808080")


def icon_ready() -> Image.Image:
    """Plain app icon — ready for recording. A green dot would blur into the check."""
    return render_app_icon(ICON_SIZE)


def icon_recording() -> Image.Image:
    """App icon with red status dot — microphone active."""
    return _app_icon_with_status("#EF4444")


def icon_processing() -> Image.Image:
    """App icon with orange status dot — transcribing (yellow would match the check)."""
    return _app_icon_with_status("#F97316")


def icon_error() -> Image.Image:
    """App icon with red status dot + white X — error state."""
    return _app_icon_with_status("#EF4444", show_x=True)


STATE_ICONS = {
    "loading": icon_loading,
    "ready": icon_ready,
    "recording": icon_recording,
    "processing": icon_processing,
    "error": icon_error,
}
