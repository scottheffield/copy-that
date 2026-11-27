"""The Dictation page of the main window: this session's takes, ready to copy again."""
import customtkinter as ctk
from ui import theme

DESCRIPTION = "Your recent takes, close at hand."
STATUS_LABELS = {"retained": "Not pasted"}  # Kept here because the paste didn't happen


def build_history_page(page, root, entries):
    """Fill page (a CTkScrollableFrame) with the newest takes first. Rebuilt on each visit."""
    for child in page.winfo_children():
        child.destroy()
    ctk.CTkLabel(page, text='Dictation', text_color=theme.TEXT,
        font=ctk.CTkFont(size=25, weight='bold')).pack(anchor='w', padx=20, pady=(20, 2))
    ctk.CTkLabel(page, text=DESCRIPTION, text_color=theme.MUTED,
        wraplength=460, justify='left').pack(anchor='w', padx=20, pady=(0, 4))
    ctk.CTkLabel(page, text='This session only. The list clears when the app closes or the engine reloads.',
        wraplength=460, justify='left', text_color=theme.MUTED,
        font=ctk.CTkFont(size=12)).pack(anchor='w', padx=20, pady=(0, 12))
    if not entries:
        ctk.CTkLabel(page, text='Your next thought starts here.\nHold Ctrl + Win to dictate.',
            text_color=theme.MUTED, font=ctk.CTkFont(size=16)).pack(pady=60)
        return
    for entry in reversed(entries):
        frame = ctk.CTkFrame(page, fg_color='transparent')
        frame.pack(fill='x', padx=14, pady=(0, 18))
        ctk.CTkFrame(frame, height=1, fg_color=theme.BORDER).pack(fill='x', pady=(0, 16))
        status = entry.get('status', '')
        status = STATUS_LABELS.get(status, status.replace('_', ' ').capitalize())
        header = ' · '.join(part for part in (
            when(entry.get('timestamp', '')), length(entry.get('duration', 0)), status) if part)
        ctk.CTkLabel(frame, text=header, text_color=theme.MUTED,
            font=ctk.CTkFont(size=12)).pack(anchor='w', padx=6)
        text = entry.get('text', '')
        label = ctk.CTkLabel(frame, text=text, wraplength=500, anchor='w', justify='left',
            text_color=theme.TEXT, font=ctk.CTkFont(size=16))
        label.pack(fill='x', padx=6, pady=(6, 12))
        # Event widths are physical pixels; CTk scales wraplength by the display scaling.
        frame.bind('<Configure>', lambda e, widget=label: widget.configure(
            wraplength=max(100, e.width / widget._get_widget_scaling() - 24)))
        actions = ctk.CTkFrame(frame, fg_color='transparent')
        actions.pack(fill='x', padx=6)
        for caption, value in (('Copy final', text), ('Copy raw', entry.get('raw', text))):
            button = ctk.CTkButton(actions, text=caption, width=100, height=30,
                fg_color=theme.SURFACE, hover_color=theme.SELECTED,
                text_color=theme.TEXT, border_color=theme.BORDER, border_width=1)
            button.configure(command=lambda t=value, b=button, c=caption: _copy(root, t, b, c))
            button.pack(side='left', padx=(0, 8))


def when(timestamp, today=None):
    """9:05 AM for a take from today; Nov 26, 9:05 AM for an older one; "" if unreadable."""
    from datetime import date, datetime
    try:
        moment = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return ''
    clock = moment.strftime('%I:%M %p').lstrip('0')
    if moment.date() == (today or date.today()):
        return clock
    return f"{moment.strftime('%b')} {moment.day}, {clock}"


def length(seconds):
    """41 seconds, 1 second, or 1:05 from a minute up."""
    seconds = round(seconds or 0)
    if seconds < 60:
        return f"{seconds} second{'' if seconds == 1 else 's'}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _copy(root, text, button, caption):
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception:
        root.clipboard_clear()
        root.clipboard_append(text)
    button.configure(text='Copied')
    button.after(1200, lambda: button.configure(text=caption) if button.winfo_exists() else None)
