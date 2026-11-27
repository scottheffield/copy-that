"""Quiet versions of two CTk controls: a dropdown with a border and a muted chevron, and a
scrolling page whose scrollbar shows only when the page overflows. Also a row of buttons
that wraps onto a second line when the window is too narrow for it."""

import customtkinter as ctk

from ui import theme


class Dropdown(ctk.CTkOptionMenu):
    """A CTkOptionMenu that looks like a field: a thin border, the field's own color behind
    the arrow, and a muted chevron that darkens on hover. Pack it like any widget; the
    border is a frame around it."""

    def __init__(self, master, **kwargs):
        self._box = ctk.CTkFrame(master, fg_color=theme.FIELD, border_color=theme.BORDER,
                                 border_width=1, corner_radius=6)
        self._hovering = False
        kwargs = dict(dict(fg_color=theme.FIELD, button_color=theme.FIELD, button_hover_color=theme.FIELD,
                           text_color=theme.TEXT, dropdown_fg_color=theme.FIELD,
                           dropdown_hover_color=theme.SURFACE, dropdown_text_color=theme.TEXT,
                           corner_radius=4, height=26), **kwargs)
        super().__init__(self._box, **kwargs)
        ctk.CTkOptionMenu.pack(self, fill="x", padx=2, pady=2)  # Inside the 1 px border

    # The frame around the menu is what its parent lays out.
    def pack(self, **kwargs):
        self._box.pack(**kwargs)

    def pack_forget(self):
        self._box.pack_forget()

    def grid(self, **kwargs):
        self._box.grid(**kwargs)

    def destroy(self):
        box = self._box
        super().destroy()
        box.destroy()

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._paint_arrow()

    def _paint_arrow(self):
        if self._state == "disabled":
            return  # CTk greys it out
        pair = theme.TEXT if self._hovering else theme.MUTED
        self._canvas.itemconfig("dropdown_arrow", fill=self._apply_appearance_mode(pair))
        self._box.configure(border_color=theme.MUTED if self._hovering else theme.BORDER)

    def _on_enter(self, event=0):
        super()._on_enter(event)
        self._hovering = self._state == "normal"
        self._paint_arrow()

    def _on_leave(self, event=0):
        super()._on_leave(event)
        self._hovering = False
        self._paint_arrow()


class ScrollPage(ctk.CTkScrollableFrame):
    """A CTkScrollableFrame that hides its scrollbar while everything fits."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._bar_check = None
        self._bar_shown = True
        # CTkScrollbar flushes Tk's pending layout on every redraw, and each flush moves the
        # bar again: filling a page of 20 dictation takes spent 0.9 s in that loop. Tk paints
        # the bar at its next idle moment anyway.
        self._scrollbar._canvas.update_idletasks = lambda: None
        self.bind("<Configure>", self._check_bar, add="+")
        self._parent_canvas.bind("<Configure>", self._check_bar, add="+")

    def _check_bar(self, _event=None):
        if self._bar_check is None:
            self._bar_check = self.after_idle(self._update_bar)

    def _update_bar(self):
        self._bar_check = None
        if not self.winfo_exists():
            return
        visible = self._parent_canvas.winfo_height()
        if visible <= 1:
            return  # Not laid out yet; a Configure follows
        overflows = self.winfo_reqheight() > visible
        if overflows == self._bar_shown:
            return
        self._bar_shown = overflows
        if overflows:
            self._scrollbar.grid()
        else:
            self._scrollbar.grid_remove()
            self._parent_canvas.yview_moveto(0)


class FlowRow(ctk.CTkFrame):
    """A transparent row that lays its children out left to right and wraps them onto the
    next line when they don't fit, like words in a paragraph. Create each child with the
    row as its parent and don't pack it; the row places it. Gaps are in logical pixels."""

    def __init__(self, master, gap=8, line_gap=8, **kwargs):
        super().__init__(master, fg_color="transparent", height=1, **kwargs)
        self._gap, self._line_gap = gap, line_gap
        self._pending = None
        self.bind("<Configure>", self._schedule, add="+")

    def _schedule(self, _event=None):
        if self._pending is None:
            self._pending = self.after_idle(self._layout)

    def layout(self):
        """Lay the row out now, e.g. right after adding its children."""
        self._layout()

    def _layout(self):
        self._pending = None
        if not self.winfo_exists():
            return
        scale = self._get_widget_scaling()
        gap, line_gap = round(self._gap * scale), round(self._line_gap * scale)
        width = self.winfo_width()
        items = [w for w in self.winfo_children() if w is not self._canvas]
        x = y = line_height = 0
        for item in items:
            w, h = item.winfo_reqwidth(), item.winfo_reqheight()
            if x and width > 1 and x + w > width:  # Wrap, unless it's first on its line
                x, y = 0, y + line_height + line_gap
                line_height = 0
            # Tk's own place, in physical pixels: CTk's would scale the numbers again.
            self.tk.call("place", "configure", item._w, "-x", x, "-y", y)
            x += w + gap
            line_height = max(line_height, h)
        height = y + line_height
        if height and abs(self.winfo_reqheight() - height) > 0:
            ctk.CTkFrame.configure(self, height=height / scale)
