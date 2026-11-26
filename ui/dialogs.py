"""Small themed confirmation dialog."""
import tkinter as tk

import customtkinter as ctk

from ui import screen, theme


def _owner(root):
    """The shown CopyThat window the dialog belongs to (the newest), or None."""
    shown = [w for w in root.winfo_children()
             if isinstance(w, tk.Toplevel) and w.winfo_viewable() and w.state() == "normal"]
    return shown[-1] if shown else None


def _center(win, owner):
    """Center win over owner, or on the pointer's monitor when there is no owner."""
    win.update_idletasks()
    try:
        scaling = win._get_window_scaling()
    except Exception:
        scaling = 1.0
    width = win.winfo_reqwidth()
    height = win.winfo_reqheight() + int(screen.TITLE_BAR * scaling)
    if owner is not None:
        cx = owner.winfo_rootx() + owner.winfo_width() // 2
        cy = owner.winfo_rooty() + owner.winfo_height() // 2
    else:
        left, top, right, bottom = (screen.work_area(*screen.cursor())
                                    or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight()))
        cx, cy = (left + right) // 2, (top + bottom) // 2
    win.geometry(f"+{max(0, cx - width // 2)}+{max(0, cy - height // 2)}")


def _set_icon(win):
    """The CopyThat icon, and stop CTkToplevel swapping in its default one at 200 ms."""
    try:
        from PIL import ImageTk
        from ui.icons import render_app_icon
        win._icon_photo = ImageTk.PhotoImage(render_app_icon(32))
        win._iconbitmap_method_called = True
        win.iconphoto(False, win._icon_photo)
    except Exception:
        pass


def confirm(root, title, message, confirm_text, cancel_text, on_confirm, on_cancel=None):
    """Ask a yes/no question without blocking the main loop; on_confirm runs on yes.
    on_cancel, when given, runs on the cancel button only, not when the window is closed."""
    owner = _owner(root)
    win = ctk.CTkToplevel(root)
    win.title(f"CopyThat · {title}")
    win.resizable(False, False)
    win.configure(fg_color=theme.BG)
    win.attributes("-topmost", True)
    win.update_idletasks()
    theme.title_bar(win)
    _set_icon(win)
    ctk.CTkLabel(win, text=title, text_color=theme.TEXT,
                 font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=24, pady=(20, 6))
    ctk.CTkLabel(win, text=message, text_color=theme.MUTED, wraplength=380,
                 justify="left", anchor="w").pack(anchor="w", fill="x", padx=24)
    buttons = ctk.CTkFrame(win, fg_color="transparent")
    buttons.pack(fill="x", padx=24, pady=20)

    def _yes():
        win.destroy()
        on_confirm()

    ctk.CTkButton(buttons, text=confirm_text, command=_yes, width=110,
                  fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                  text_color=theme.ON_ACCENT).pack(side="right", padx=(8, 0))
    def _no():
        win.destroy()
        if on_cancel:
            on_cancel()

    ctk.CTkButton(buttons, text=cancel_text, command=_no, width=110,
                  fg_color=theme.SURFACE, hover_color=theme.SELECTED, text_color=theme.TEXT,
                  border_color=theme.BORDER, border_width=1).pack(side="right")
    _center(win, owner)
    win.after(50, win.focus_force)
    return win
