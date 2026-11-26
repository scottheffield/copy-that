"""Where the confirmation dialog opens."""
import tkinter as tk

from ui import dialogs


class FakeWindow:
    def __init__(self, x=0, y=0, width=0, height=0, shown=True):
        self.x, self.y, self.width, self.height, self.shown = x, y, width, height, shown
        self.placed = None

    def winfo_viewable(self):
        return self.shown

    def state(self):
        return "normal" if self.shown else "withdrawn"

    def winfo_rootx(self):
        return self.x

    def winfo_rooty(self):
        return self.y

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def winfo_reqwidth(self):
        return self.width

    def winfo_reqheight(self):
        return self.height

    def update_idletasks(self):
        pass

    def _get_window_scaling(self):
        return 1.0

    def geometry(self, text):
        self.placed = text


class FakeToplevel(FakeWindow, tk.Toplevel):
    pass


class FakeRoot:
    def __init__(self, children):
        self.children_ = children

    def winfo_children(self):
        return self.children_


def test_the_dialog_belongs_to_the_newest_shown_window():
    hidden, settings, welcome = FakeToplevel(shown=False), FakeToplevel(), FakeToplevel()
    assert dialogs._owner(FakeRoot([hidden, settings, welcome])) is welcome
    assert dialogs._owner(FakeRoot([hidden, object()])) is None


def test_the_dialog_is_centered_over_its_window():
    owner = FakeWindow(x=300, y=150, width=900, height=700)
    win = FakeWindow(width=460, height=180)
    dialogs._center(win, owner)
    height = 180 + dialogs.screen.TITLE_BAR
    assert win.placed == f"+{750 - 230}+{500 - height // 2}"


def test_without_a_window_the_dialog_is_centered_on_the_screen(monkeypatch):
    monkeypatch.setattr(dialogs.screen, "work_area", lambda x, y: (0, 0, 1920, 1080))
    win = FakeWindow(width=460, height=180)
    dialogs._center(win, None)
    height = 180 + dialogs.screen.TITLE_BAR
    assert win.placed == f"+{960 - 230}+{540 - height // 2}"
