"""Small themed confirmation dialog."""
import customtkinter as ctk

from ui import theme


def confirm(root, title, message, confirm_text, cancel_text, on_confirm, on_cancel=None):
    """Ask a yes/no question without blocking the main loop; on_confirm runs on yes.
    on_cancel, when given, runs on the cancel button only, not when the window is closed."""
    win = ctk.CTkToplevel(root)
    win.title(f"CopyThat · {title}")
    win.resizable(False, False)
    win.configure(fg_color=theme.BG)
    win.attributes("-topmost", True)
    win.update_idletasks()
    theme.title_bar(win)
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
    win.after(50, win.focus_force)
    return win
