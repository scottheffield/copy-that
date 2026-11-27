"""CopyThat colors: CustomTkinter pairs are ordered light, dark."""

import customtkinter as ctk

BG = ("#F8FAFD", "#161C27")
SURFACE = ("#EEF2F8", "#1E2633")
FIELD = ("#FFFFFF", "#263040")
TEXT = ("#1C2533", "#EEF2F8")
MUTED = ("#5D6B80", "#A9B4C6")
BORDER = ("#D3DBE7", "#3A4659")
ACCENT = ("#2558B8", "#8DB4FF")
ACCENT_HOVER = ("#1D4797", "#A6C4FF")
ON_ACCENT = ("#FFFFFF", "#10213F")
SELECTED = ("#DCE7FA", "#24395C")
SUCCESS = ("#287044", "#85C69A")
WARNING = ("#865300", "#E8BC70")
ERROR = ("#AE3554", "#F191A0")
PILL_BORDER = ("#8B98AD", "#6F7D93")
STOP_BG = ("#F8E4E9", "#492B37")
READY_BG = ("#E0EFE4", "#203B2B")


# Highlight colors, chosen in Settings. Each sets ACCENT, ACCENT_HOVER, ON_ACCENT and SELECTED.
# Text on each accent, and each accent on its SELECTED tint, keeps at least 4.5:1 contrast.
HIGHLIGHTS = {
    "blue": {"name": "Blue", "ACCENT": ACCENT, "ACCENT_HOVER": ACCENT_HOVER,
             "ON_ACCENT": ON_ACCENT, "SELECTED": SELECTED},
    "violet": {"name": "Violet", "ACCENT": ("#6741C7", "#B9A2FF"), "ACCENT_HOVER": ("#5533AA", "#CBB9FF"),
               "ON_ACCENT": ("#FFFFFF", "#22164A"), "SELECTED": ("#E8E1FB", "#352A5C")},
    "teal": {"name": "Teal", "ACCENT": ("#0C6F69", "#6FD3C8"), "ACCENT_HOVER": ("#095A55", "#8FE0D7"),
             "ON_ACCENT": ("#FFFFFF", "#0B2E2B"), "SELECTED": ("#D7EFEC", "#1D3F3E")},
    "amber": {"name": "Amber", "ACCENT": ("#8F5B12", "#E3B26A"), "ACCENT_HOVER": ("#764A0E", "#EDC488"),
              "ON_ACCENT": ("#FFFFFF", "#3A2606"), "SELECTED": ("#F5E8D3", "#3E3221")},
}
HIGHLIGHT_KEYS = ("ACCENT", "ACCENT_HOVER", "ON_ACCENT", "SELECTED")
_COLOR_OPTIONS = ("fg_color", "hover_color", "text_color", "border_color", "button_color",
                  "button_hover_color", "progress_color", "checkmark_color")


def set_highlight(name):
    """Switch the highlight colors; returns {old pair: new pair} for recolor().
    Widgets made afterwards read the new colors from this module."""
    chosen = HIGHLIGHTS.get(name, HIGHLIGHTS["blue"])
    changes = {}
    for key in HIGHLIGHT_KEYS:
        old, new = globals()[key], chosen[key]
        if old != new:
            changes[old] = new
        globals()[key] = new
    return changes


def recolor(widget, changes):
    """Give open CTk widgets under widget the new highlight, in place. A widget with
    keep_colors set (a color swatch) keeps its own."""
    if not changes or getattr(widget, "keep_colors", False):
        return
    for option in _COLOR_OPTIONS:
        try:
            value = widget.cget(option)
        except Exception:
            continue  # Plain Tk widgets, and CTk ones without this option
        if isinstance(value, list):
            value = tuple(value)
        if value in changes:
            try:
                widget.configure(**{option: changes[value]})
            except Exception:
                pass
    for child in widget.winfo_children():
        recolor(child, changes)


def color(pair, mode=None):
    return pair[1 if (mode or ctk.get_appearance_mode()).lower() == "dark" else 0]


def title_bar(window):
    from ui.glass import set_title_bar_color
    set_title_bar_color(window, color(BG), color(TEXT))


class ThemeController:
    """Bridge CTk's live Windows system detection to non-CTk surfaces.

    CTk updates its color-pair widgets itself. This main-thread observer also
    repaints the Pillow/Tk pill and native title bars when the resolved mode
    changes, without recreating windows or interrupting recording.
    """

    def __init__(self, root, mode, on_change):
        self.root, self.on_change = root, on_change
        self._resolved = None
        self._timer = None
        self.set_mode(mode)
        self._poll()

    def set_mode(self, mode):
        if mode not in ("system", "light", "dark"):
            raise ValueError("theme must be system, light or dark")
        ctk.set_appearance_mode(mode)
        # CTk otherwise waits for its next timer when returning to System.
        if mode == "system":
            ctk.AppearanceModeTracker.init_appearance_mode()
        self._refresh()

    def _refresh(self):
        resolved = ctk.get_appearance_mode().lower()
        if resolved != self._resolved:
            self._resolved = resolved
            self.on_change(resolved)

    def _poll(self):
        self._refresh()
        self._timer = self.root.after(250, self._poll)

    def close(self):
        if self._timer is not None:
            self.root.after_cancel(self._timer)
            self._timer = None
