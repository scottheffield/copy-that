"""Session-only transcription recovery with shared light and dark colors."""
import customtkinter as ctk
from ui import theme

class HistoryWindow:
    def __init__(self, root, history):
        self._root, self._history, self._win = root, history, None

    @property
    def is_open(self):
        return self._win is not None and self._win.winfo_exists()

    def refresh_theme(self):
        if self.is_open:
            theme.title_bar(self._win)

    def show(self):
        if self.is_open:
            self._win.focus_force()
            return
        self._win = ctk.CTkToplevel(self._root)
        self._win.title('CopyThat · History')
        from ui import screen
        screen.place(self._win, 600, 580, 480, 380)
        self._win.configure(fg_color=theme.BG)
        self._win.update_idletasks()
        self.refresh_theme()
        from ui.icons import render_app_icon
        from PIL import ImageTk
        self._icon_photo = ImageTk.PhotoImage(render_app_icon(32))
        self._win._iconbitmap_method_called = True
        self._win.iconphoto(False, self._icon_photo)
        ctk.CTkLabel(self._win, text='History', text_color=theme.TEXT,
            font=ctk.CTkFont(size=25, weight='bold')).pack(anchor='w', padx=24, pady=(22, 2))
        ctk.CTkLabel(self._win, text='Your recent words, close at hand.',
            text_color=theme.MUTED).pack(anchor='w', padx=24, pady=(0, 16))
        ctk.CTkLabel(self._win, text='This session only. History clears when the app closes or the engine reloads.',
            wraplength=530, justify='left', text_color=theme.MUTED,
            font=ctk.CTkFont(size=12)).pack(side='bottom', fill='x', padx=24, pady=16)
        if not self._history:
            ctk.CTkLabel(self._win, text='Your next thought starts here.\nHold Ctrl + Win to dictate.',
                text_color=theme.MUTED, font=ctk.CTkFont(size=16)).pack(expand=True)
            return
        scroll = ctk.CTkScrollableFrame(self._win, fg_color=theme.BG,
            scrollbar_button_color=theme.BORDER, scrollbar_button_hover_color=theme.MUTED)
        scroll.pack(fill='both', expand=True, padx=18)
        for entry in reversed(self._history):
            frame = ctk.CTkFrame(scroll, fg_color='transparent')
            frame.pack(fill='x', pady=(0, 18))
            ctk.CTkFrame(frame, height=1, fg_color=theme.BORDER).pack(fill='x', pady=(0, 16))
            ts = entry.get('timestamp', '')[:19].replace('T', ' ')
            status = entry.get('status', '').replace('_', ' ').capitalize()
            header = f"{ts} · {entry.get('duration', 0):.1f}s · {status}"
            ctk.CTkLabel(frame, text=header, text_color=theme.MUTED,
                font=ctk.CTkFont(size=12)).pack(anchor='w', padx=6)
            text = entry.get('text', '')
            label = ctk.CTkLabel(frame, text=text, wraplength=500, anchor='w', justify='left',
                text_color=theme.TEXT, font=ctk.CTkFont(size=16))
            label.pack(fill='x', padx=6, pady=(6, 12))
            frame.bind('<Configure>', lambda e, widget=label: widget.configure(wraplength=max(100, e.width - 24)))
            actions = ctk.CTkFrame(frame, fg_color='transparent')
            actions.pack(fill='x', padx=6)
            for caption, value in (('Copy final', text), ('Copy raw', entry.get('raw', text))):
                button = ctk.CTkButton(actions, text=caption, width=100, height=30,
                    fg_color=theme.SURFACE, hover_color=theme.SELECTED,
                    text_color=theme.TEXT, border_color=theme.BORDER, border_width=1)
                button.configure(command=lambda t=value, b=button, c=caption: self._copy(t, b, c))
                button.pack(side='left', padx=(0, 8))

    def _copy(self, text, button, caption):
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            self._root.clipboard_clear()
            self._root.clipboard_append(text)
        button.configure(text='Copied')
        button.after(1200, lambda: button.configure(text=caption) if button.winfo_exists() else None)
