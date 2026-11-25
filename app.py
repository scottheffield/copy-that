"""GUI orchestrator for CopyThat Engine.

Entry point for tray-icon mode. Wires the engine, system tray, overlay,
settings dialog, onboarding wizard, and transcription history together.

Usage:
    python app.py [--device NPU|GPU|CPU] [--model base|small|medium|turbo|parakeet]
"""

import sys
import argparse
import queue
import threading
import time
import customtkinter as ctk

from pathlib import Path

from dictation_engine import (
    AppState, CLEANUP_REGISTRY, DEFAULT_CONFIG, DictationApp, MODEL_REGISTRY, MODEL_DIR,
    load_config, save_config, validate_config, validate_settings, log, create_model,
    is_cleanup_downloaded, is_model_downloaded, launch_device,
)
from model_store import Downloads, installed, remove
from version import VERSION, label as version_label
from ui.tray import TrayManager
from ui.overlay import OverlayWindow
from ui.settings import SettingsWindow


def interrupted_notice(count):
    """The launch notification for meetings a quit or crash left unfinished."""
    if count == 1:
        return "A meeting didn't finish processing. Open it in Meetings and click Re-transcribe."
    return f"{count} meetings didn't finish processing. Open them in Meetings and click Re-transcribe."


class GUIApp:
    """Main GUI application wiring all components."""

    _meeting = None  # The current or last MeetingSession
    _retranscribing = frozenset()  # Replaced by a set in __init__; folders being re-transcribed
    _full_speed = False            # Opted out of Windows power throttling right now (#14)
    _folder_renames = frozenset()  # Replaced by a set in __init__; folders to retitle once idle (#57)
    _speaker_jobs, _speaker_folder = (), None  # Replaced in __init__; speaker labeling queue
    _speaker_lock = threading.Lock()
    _moving_meetings = False  # True while meetings move to a newly chosen folder
    _recovery_started = False

    def __init__(self, config: dict):
        self._config = config
        self._events = queue.SimpleQueue()
        self._closing = False
        self._engine_started = False
        self._downloads = Downloads(on_done=lambda key, error: self._events.put(
            ("action", lambda: self._download_finished(key, error))))
        self._welcome = None
        self._first_run = False
        self._offer_delete = []  # Models switched away from; offered for deletion once the new one loads

        # Hidden root drives the customtkinter mainloop
        ctk.set_appearance_mode(config.get("theme", "system"))
        self._root = ctk.CTk()
        self._root.withdraw()  # No visible root window
        self._root.protocol("WM_DELETE_WINDOW", self._quit)

        # Set app icon on root — inherited by all toplevel windows
        from ui.icons import render_app_icon
        from PIL import ImageTk
        self._icon_photo = ImageTk.PhotoImage(render_app_icon(32))
        self._root.iconphoto(True, self._icon_photo)

        # Engine
        self._engine = DictationApp(config)
        self._engine.add_callback(self._on_state_change)

        # Overlay
        self._overlay = OverlayWindow(self._root, on_toggle=lambda: self._engine.toggle_recording(),
                                      on_close=lambda: self._engine.close_take())
        self._overlay.set_show_balloon(config.get("show_balloon", True))
        self._overlay.set_balloon_font_size(config.get("balloon_font_size", 16))
        self._overlay.set_bubble_position(config.get("bubble_position", "cursor"))

        # Meeting chip: its own window, so the pill stays free for dictation mid-meeting.
        from ui.meeting_chip import MeetingChip
        position = config.get("meeting_chip_position")
        self._chip = MeetingChip(
            self._root, on_stop=self._toggle_meeting, on_open=self._open_last_transcript,
            on_consent_seen=self._consent_seen, on_moved=self._chip_moved,
            position=tuple(position) if position else None, on_mute=self._set_meeting_muted)
        self._chip_poll_id = None

        # Tray
        import startup
        self._tray = TrayManager(
            on_toggle=lambda: self._events.put(("action", self._engine.toggle_recording)),
            on_quit=lambda: self._events.put(("action", self._quit)),
            on_settings=lambda: self._events.put(("action", self._show_settings)),
            on_history=lambda: self._events.put(("action", self._show_history)),
            on_open=lambda: self._events.put(("action", self._show_main)),
            device=config["device"],
            model=config["model_size"],
            hotkey=config["hotkey"],
            on_startup=(lambda: self._events.put(("action", self._toggle_startup))) if getattr(sys, "frozen", False) else None,
            startup_enabled=startup.enabled,
            on_meeting=lambda: self._events.put(("action", self._toggle_meeting)),
            meeting_recording=self._meeting_recording,
            on_meetings_folder=lambda: self._events.put(("action", self._open_meetings_folder)),
            on_meeting_mute=lambda: self._events.put(("action", lambda: self._set_meeting_muted(None))),
            meeting_muted=lambda: bool(self._meeting_recording() and self._meeting.muted),
        )

        # Lazy-created windows
        self._settings_win: SettingsWindow | None = None
        self._retranscribing = set()
        self._folder_renames = set()
        self._speaker_jobs = []          # Meeting folders waiting for speaker labels, oldest first
        self._speaker_folder = None      # The folder being labeled now
        self._speaker_lock = threading.Lock()
        self._audio_poll_id = None
        from ui.theme import ThemeController
        self._theme_controller = ThemeController(self._root, config.get("theme", "system"), self._refresh_theme)
        self._root.after(30, self._drain_events)

    def _refresh_theme(self, mode):
        self._overlay.set_theme(mode)
        self._chip.set_theme(mode)
        for window in (self._settings_win, self._welcome):
            if window:
                window.refresh_theme()

    def run(self):
        """Start the application."""
        # Runtime loads local files only. Downloads start from an explicit click.
        if is_model_downloaded(self._config["model_size"]):
            self._start_engine()
        else:
            self._tray.update_state("loading", f"CopyThat {VERSION} — Download a speech model to start")
            self._show_welcome()

        # Start tray
        self._tray.start()

        # Run mainloop on main thread
        self._root.mainloop()

    def _start_engine(self):
        """Start the engine in non-blocking mode."""
        self._engine.start_background()
        self._engine_started = True

    def _restart_engine(self):
        self._engine.stop()
        self._settings_status("Loading model...", "#FF9F0A")
        self._settings_set_apply(False)
        self._engine = DictationApp(self._config)
        self._engine.add_callback(self._on_state_change)
        self._engine.meeting_active = self._meeting_recording()
        self._start_engine()

    # -- Models ------------------------------------------------------------

    @staticmethod
    def _model_info(kind, key):
        return (MODEL_REGISTRY if kind == "speech" else CLEANUP_REGISTRY)[key]

    def _display_name(self, kind, key):
        info = self._model_info(kind, key)
        return info["name"] if kind == "speech" else f"{info['name']} polish ({info['model']})"

    def _model_dir(self, kind, key):
        return MODEL_DIR / self._model_info(kind, key)["local_dir"]

    def _cleanup_key(self, config):
        """The catalog cleanup model a config uses, or None for rules only or a custom folder."""
        if not config.get("cleanup_enabled"):
            return None
        path = Path(config.get("cleanup_model_path", "")).resolve()
        return next((key for key in CLEANUP_REGISTRY if self._model_dir("cleanup", key).resolve() == path), None)

    def _in_use(self, kind, key):
        if kind == "speech":
            return key == self._config["model_size"]
        return key == self._cleanup_key(self._config)

    def model_state(self, kind, key):
        """(state, fraction) for a model card: downloading, available, installed or removable."""
        fraction = self._downloads.fraction(f"{kind}:{key}")
        if fraction is not None:
            return "downloading", fraction
        if not installed(self._model_dir(kind, key)):
            return "available", 0.0
        return ("installed" if self._in_use(kind, key) else "removable"), 1.0

    def download_model(self, kind, key):
        info = self._model_info(kind, key)
        repo = info["ov_repo"] if kind == "speech" else info["repo"]
        self._downloads.start(f"{kind}:{key}", repo, info["revision"], self._model_dir(kind, key))

    def cancel_download(self, kind, key):
        self._downloads.cancel(f"{kind}:{key}")

    def remove_model(self, kind, key):
        info = self._model_info(kind, key)
        from ui.dialogs import confirm
        from ui.model_cards import size_text
        confirm(self._root, f"Remove {self._display_name(kind, key)}?",
                f"This frees {size_text(info['size_gb'])}. You can download it again from Settings.",
                "Remove", "Keep", lambda: self._delete_models([(kind, key)]))

    def _delete_models(self, models):
        for kind, key in models:
            if self._in_use(kind, key) or self._downloads.fraction(f"{kind}:{key}") is not None:
                continue
            try:
                remove(self._model_dir(kind, key))
                log(f"Removed {kind} model {key}")
            except OSError as error:
                log(f"Could not remove {key}: {error}")
                self._settings_status("Couldn't delete the model; it may still be in use. Try again later.", "#FF453A")

    def _offer_deletes(self):
        models = [(kind, key) for kind, key in self._offer_delete
                  if not self._in_use(kind, key) and installed(self._model_dir(kind, key))]
        self._offer_delete = []
        if not models:
            return
        from ui.dialogs import confirm
        from ui.model_cards import size_text
        infos = [self._model_info(kind, key) for kind, key in models]
        names = " and ".join(self._display_name(kind, key) for kind, key in models)
        size = size_text(sum(info["size_gb"] for info in infos))
        confirm(self._root, "Delete unused models?",
                f"You're no longer using {names}. Deleting frees {size}. "
                f"You can download {'it' if len(models) == 1 else 'them'} again from Settings.",
                "Delete", "Keep", lambda: self._delete_models(models))

    def _show_welcome(self):
        from ui.onboarding import WelcomeWindow
        if not (self._welcome and self._welcome.is_open):
            self._welcome = WelcomeWindow(self._root, self, self._start_first_run)
        self._root.after(0, self._welcome.show)

    def _start_first_run(self):
        """Parakeet first so dictation starts sooner, then Qwen3 4B for cleanup."""
        self._first_run = True
        if is_model_downloaded("parakeet"):
            self._download_finished("speech:parakeet", None)
        else:
            self.download_model("speech", "parakeet")

    def _download_finished(self, key, error):
        if error == "cancelled":
            self._first_run = False
            return
        if error:
            log(f"Download failed for {key}: {error}")
            message = "Download failed. Check your internet connection and try again."
            self._settings_status(message, "#FF453A")
            if self._welcome:
                self._welcome.set_error(message)
            self._first_run = False
            return
        log(f"Downloaded {key}")
        if not self._first_run:
            return
        if key == "speech:parakeet":
            self._config.update(model_size="parakeet", device=launch_device({**self._config, "model_size": "parakeet"}))
            save_config(self._config)
            self._tray.update_info(device=self._config["device"], model="parakeet", hotkey=self._config["hotkey"])
            self._restart_engine()
            if is_cleanup_downloaded("4b"):
                self._download_finished("cleanup:4b", None)
            else:
                self.download_model("cleanup", "4b")
        elif key == "cleanup:4b":
            self._first_run = False
            self._enable_first_run_cleanup()

    def _enable_first_run_cleanup(self):
        if self._closing:
            return
        if self._engine.active or self._engine.state == AppState.LOADING:
            self._root.after(1000, self._enable_first_run_cleanup)  # Never interrupt a take
            return
        self._on_settings_apply({**self._config, "cleanup_enabled": True,
                                 "cleanup_model_path": str(self._model_dir("cleanup", "4b"))})

    # -- State callback (fires from bg threads) ----------------------------

    def _on_state_change(self, state: AppState, data: dict):
        """Engine state changed — schedule UI update on main thread."""
        self._events.put(("state", (state, data)))

    def _drain_events(self):
        if self._closing:
            return
        for _ in range(100):
            if self._events.empty():
                break
            kind, value = self._events.get()
            if kind == "state":
                self._update_ui(*value)
            else:
                value()
        self._update_speed()
        if not self._closing:
            self._root.after(30, self._drain_events)

    def _busy_for_speed(self):
        """True while a dictation take or meeting transcription is running."""
        current = self._meeting
        return bool(getattr(self._engine, "active", False) or self._retranscribing
                    or (current and not current.finished.is_set()))

    def _update_speed(self):
        """Run at full speed while there is work, even with CopyThat's window behind others:
        Windows power throttling (EcoQoS) slowed a meeting's transcription about 4x when it
        wasn't in front. Idle, Windows decides again, which saves battery (#14)."""
        busy = self._busy_for_speed()
        if busy == self._full_speed:
            return
        self._full_speed = busy
        try:
            from windows_io import keep_full_speed
            if not keep_full_speed(busy):
                log("Power throttling: Windows refused the change")
        except Exception as error:
            log(f"Power throttling: could not change it ({type(error).__name__})")

    def _update_ui(self, state: AppState, data: dict):
        """Update tray icon and overlay from the main thread."""
        state_name = state.value
        if "live_text" in data:
            self._overlay.set_live_text(data["live_text"])
            return
        if "pasting" in data:
            self._overlay.clear_balloon()
            return
        if "mic_silent" in data:
            self._overlay.set_mic_silent(data["mic_silent"])
            return
        if "field" in data:
            field = data["field"]
            self._overlay.set_field(field.editable, field.caret)
            return
        message = data.get("message", "")
        if message:
            self._settings_status(message, "#FF9F0A")
        if data.get("loading"):
            self._overlay.note_waiting(True)
            self._overlay.show_notice(message, "Loading")
            return

        if state == AppState.LOADING:
            self._tray.update_state(state_name, f"CopyThat {VERSION} — Loading model...")
            self._overlay.show_loading()
            self._settings_status("Loading model...", "#FF9F0A")

        elif state == AppState.READY:
            self._stop_audio_polling()
            text = data.get("text")
            self._tray.update_state(state_name, f"CopyThat {VERSION} — Ready")
            if text:
                # Pasted text is visible where it landed; only unpasted text needs the balloon.
                self._overlay.show_result(text, linger=not data.get("pasted"))
            elif self._overlay.waiting:
                # Someone tried during loading; tell them once it's ready, then stay quiet.
                self._overlay.note_waiting(False)
                self._overlay.show_notice("Hold Ctrl+Win to dictate", "Ready", seconds=2.0)
            else:
                self._overlay.show_ready()
            self._settings_status("Model ready.", "#30D158")
            self._settings_set_apply(True)
            if self._offer_delete:
                self._offer_deletes()
            if not self._recovery_started:
                self._recovery_started = True
                self._note_interrupted_meetings()

        elif state == AppState.RECORDING:
            self._tray.update_state(state_name, f"CopyThat {VERSION} — Recording...")
            if self._overlay._state != "recording":
                self._overlay.show_recording()
                self._start_audio_polling()
            if data.get("mode"):
                self._overlay.set_recording_mode(data["mode"])

        elif state == AppState.PROCESSING:
            self._stop_audio_polling()
            self._tray.update_state(state_name, f"CopyThat {VERSION} — Transcribing...")
            self._overlay.show_processing()

        elif state == AppState.ERROR:
            self._stop_audio_polling()
            error_msg = data.get("error", "Unknown error")
            self._tray.update_state(state_name, f"CopyThat {VERSION} — Error: {error_msg[:60]}")
            self._overlay.show_error()
            self._settings_status(f"Error: {error_msg[:40]}", "#FF453A")
            self._settings_set_apply(True)

            # Auto-fallback on DEVICE_LOST
            if data.get("device_lost") and self._config["device"] == "NPU":
                log("DEVICE_LOST detected — auto-falling back to GPU")
                self._config["device"] = "GPU"
                self._tray.update_info(
                    device="GPU",
                    model=self._config["model_size"],
                    hotkey=self._config["hotkey"],
                )
                self._engine.fallback_device("GPU")

    # -- Audio level polling -----------------------------------------------

    def _start_audio_polling(self):
        """Start polling audio levels for waveform display."""
        self._poll_audio()

    def _poll_audio(self):
        if self._engine.is_recording:
            level = self._engine.recorder.audio_level
            self._overlay.update_audio_level(level)
            self._audio_poll_id = self._root.after(50, self._poll_audio)

    def _stop_audio_polling(self):
        if self._audio_poll_id:
            self._root.after_cancel(self._audio_poll_id)
            self._audio_poll_id = None

    # -- Main window (Dictation, Meetings and Settings) ----------------------

    def _show_main(self, page=None):
        """Open the main window at page, or at the last page used."""
        if self._settings_win and self._settings_win.is_open:
            self._settings_win.show(page)
            return
        devices = DictationApp.list_input_devices()
        self._settings_win = SettingsWindow(
            self._root, self._config, devices, on_apply=self._on_settings_apply, models=self,
            history=lambda: self._engine.history, on_page_change=self._on_page_change, meetings=self,
        )
        self._root.after(0, lambda: self._settings_win.show(page))

    def _show_settings(self):
        self._show_main("General")

    def _show_history(self):
        self._show_main("Dictation")

    def _on_page_change(self, page):
        if self._config.get("main_window_page") == page:
            return
        self._config["main_window_page"] = page
        try:
            save_config(self._config)
        except Exception as error:
            log(f"Couldn't save the last page: {error}")

    def _on_settings_apply(self, new_config: dict):
        try:
            validate_settings(new_config)
        except ValueError as error:
            self._settings_status(str(error), "#FF453A")
            return
        if self._engine.active or self._engine.state == AppState.LOADING:
            self._settings_status("Finish pending takes before applying settings.", "#FF9F0A")
            return
        meeting = getattr(self, "_meeting", None)
        if meeting and not meeting.finished.is_set():
            self._settings_status("Stop the meeting recording and let its transcript finish first.", "#FF9F0A")
            return
        model_changed = new_config["model_size"] != self._config["model_size"]
        if model_changed and not is_model_downloaded(new_config["model_size"]):
            self._settings_status("Download this speech model first.", "#FF9F0A")
            return
        if new_config.get("cleanup_enabled"):
            from pathlib import Path
            if not (Path(new_config["cleanup_model_path"]) / "model-manifest.json").is_file():
                self._settings_status("The polish model isn't downloaded. Download it or choose Off.", "#FF9F0A")
                return
        device_changed = new_config["device"] != self._config["device"]
        hotkey_changed = new_config["hotkey"] != self._config["hotkey"]
        runtime_changed = any(new_config.get(key) != self._config.get(key) for key in (
            "input_device", "input_device_key", "cleanup_enabled", "cleanup_model_path"))
        if model_changed:
            self._offer_delete.append(("speech", self._config["model_size"]))
        old_cleanup = self._cleanup_key(self._config)
        if old_cleanup and old_cleanup != self._cleanup_key(new_config):
            self._offer_delete.append(("cleanup", old_cleanup))

        self._config.update(new_config)
        save_config(self._config)
        if self._settings_win:
            self._settings_win._config = dict(self._config)
        self._theme_controller.set_mode(self._config.get("theme", "system"))

        # Update balloon settings immediately (no engine restart needed)
        self._overlay.set_show_balloon(self._config.get("show_balloon", True))
        self._overlay.set_balloon_font_size(self._config.get("balloon_font_size", 16))
        self._overlay.set_bubble_position(self._config.get("bubble_position", "cursor"))

        self._tray.update_info(
            device=self._config["device"],
            model=self._config["model_size"],
            hotkey=self._config["hotkey"],
        )

        if model_changed or device_changed or hotkey_changed or runtime_changed or not self._engine_started:
            log("Settings changed — reloading engine...")
            self._restart_engine()
        else:
            self._settings_status("Settings saved.", "#30D158")

    def _settings_status(self, text: str, color: str = "gray60"):
        """Push a status message to the settings window if it's open."""
        if self._settings_win and self._settings_win.is_open:
            self._settings_win.update_status(text, color)

    def _settings_set_apply(self, enabled: bool):
        """Enable/disable Apply button in settings window."""
        if self._settings_win and self._settings_win.is_open:
            self._settings_win.set_apply_enabled(enabled)

    # -- Meetings ----------------------------------------------------------

    def _meeting_recording(self):
        return bool(self._meeting and self._meeting.meta["status"] == "recording")

    def _meeting_notice(self, text):
        # The pill belongs to dictation while a take is in progress.
        if not self._engine.active:
            self._overlay.show_notice(text, "Meeting", seconds=3.0)

    def _meeting_mic(self):
        """The microphone dictation uses, so both record the same device."""
        key = self._config.get("input_device_key")
        if not key:
            return None
        from dictation_runtime import find_input_device
        device = find_input_device(DictationApp.list_input_devices(), key)
        return device["index"] if device else None

    def _toggle_meeting(self):
        if self._meeting_recording():
            self._stop_meeting()
            return
        if self._meeting and not self._meeting.finished.is_set():
            self._meeting_notice("Still finishing the last meeting")
            return
        from meetings import MeetingSession, same_folder, write_meta
        if self._moving_meetings:
            self._meeting_notice("Still moving meetings to the new folder")
            return

        def create(root):
            return MeetingSession(
                root, lambda: self._engine.speech_model, lambda: self._engine.active,
                mic_device=self._meeting_mic(), language=self._config.get("language", "en"), log=log,
                on_done=lambda folder, ok: self._events.put(("action", lambda: self._meeting_done(folder, ok))),
                dictating=self._dictation_left_out)

        root, fallback = self._config["meetings_dir"], DEFAULT_CONFIG["meetings_dir"]
        try:
            session = create(root)
        except OSError as error:
            log(f"Meeting: could not create the meeting folder ({type(error).__name__})")
            session = None
            if not same_folder(root, fallback):
                try:
                    session = create(fallback)  # E.g. an unplugged drive; keep the setting for when it returns
                    log("Meeting: saving to the default meetings folder instead")
                    self._meeting_notice("Meetings folder unavailable. Saving to CopyThat Meetings")
                except OSError:
                    session = None
            if session is None:
                self._meeting_notice("Couldn't create the meetings folder")
                return
        try:
            session.start()
        except Exception as error:
            log(f"Meeting: could not start recording ({type(error).__name__}: {error})")
            session.meta["status"] = "failed"
            session._close_tracks()
            write_meta(session.folder, session.meta)
            self._meeting_notice("Couldn't start the meeting recording")
            return
        self._meeting = session
        self._engine.meeting_active = True
        log(f"Meeting: recording started in {session.folder.name}")
        if self._config.get("beep_on_start", True):
            from dictation_engine import chime_start
            chime_start()
        self._chip.show_recording(consent=not self._config.get("meeting_consent_seen", False))
        self._poll_chip()
        self._tray.update_state(self._tray._state)  # Menu text: Stop meeting recording
        self._refresh_meetings()

    def _poll_chip(self):
        session = self._meeting
        if not self._meeting_recording():
            self._chip_poll_id = None
            return
        self._chip.update(session.elapsed, *session.levels(), problem=session.problem,
                          devices=session.devices(), muted=session.muted)
        self._chip_poll_id = self._root.after(150, self._poll_chip)

    def _dictation_left_out(self):
        """Called from the mic's audio thread: true while a dictation take records and
        the setting keeps dictation out of meeting transcripts."""
        return self._engine.is_recording and self._config.get("meeting_skip_dictation", True)

    def _set_meeting_muted(self, on):
        """Mute or unmute the meeting's mic; on=None toggles (the tray item)."""
        if not self._meeting_recording():
            return
        session = self._meeting
        session.set_muted(not session.muted if on is None else on)
        self._chip.muted = session.muted
        self._tray.update_state(self._tray._state)

    def _stop_meeting(self):
        session = self._meeting
        session.stop()
        self._engine.meeting_active = False
        log(f"Meeting: stopped after {session.meta['duration_seconds']:.0f}s; finishing the transcript")
        if self._config.get("beep_on_start", True):
            from dictation_engine import chime_stop
            chime_stop()
        self._chip.show_finishing(session.meta["duration_seconds"])
        self._poll_finishing()
        self._tray.update_state(self._tray._state)
        self._refresh_meetings()

    def _poll_finishing(self):
        """The chip's Finishing shows how much of the transcript is done."""
        from meetings import transcribe_progress
        session = self._meeting
        if not session or self._chip.state != "finishing":
            return
        self._chip.set_progress(transcribe_progress(session.folder))
        self._root.after(500, self._poll_finishing)

    def _meeting_done(self, folder, ok):
        log(f"Meeting: transcript {'saved' if ok else 'not finished'} in {folder.name}")
        if self._retranscribing:
            self._retranscribing.discard(Path(folder))
        if ok:
            my_name = self._config.get("meeting_my_name", "")
            if my_name:
                from meetings import set_my_name
                try:
                    set_my_name(folder, my_name)
                except (OSError, ValueError, KeyError) as error:
                    log(f"Meeting: could not put your name on the transcript ({type(error).__name__})")
            self._find_speakers_later(folder)
        self._refresh_meetings()
        if self._meeting and self._meeting.folder == folder and self._chip.state == "finishing":
            self._chip.show_ready() if ok else self._chip.show_failed()

    def _open_last_transcript(self):
        """The chip's Open: the meeting's page in the main window."""
        if not self._meeting:
            return
        folder = self._meeting.folder
        if not (self._settings_win and self._settings_win.is_open):
            self._show_main("Meetings")
        self._root.after(50, lambda: self._settings_win and self._settings_win.show_meeting(folder))

    def _consent_seen(self):
        self._config["meeting_consent_seen"] = True
        self._save_quietly()

    def _chip_moved(self, position):
        self._config["meeting_chip_position"] = list(position)
        self._save_quietly()

    def _save_quietly(self):
        try:
            save_config(self._config)
        except (OSError, ValueError) as error:
            log(f"Could not save settings ({type(error).__name__})")

    def _refresh_meetings(self):
        self._rename_waiting_folders()
        if self._settings_win:
            self._settings_win.refresh_meetings()

    # -- Folder names follow titles (#57) ---------------------------------------

    def rename_meeting_folder(self, folder):
        """Give a meeting's folder its title: now, or once the meeting is idle (Windows can't
        rename a folder while its files are open). Returns the folder's path now."""
        folder = Path(folder)
        if self.meeting_in_use(folder):
            self._folder_renames.add(folder)
            return folder
        return self._rename_folder_now(folder)

    def _rename_folder_now(self, folder):
        from meetings import retitle_folder
        try:
            new = retitle_folder(folder)
        except (OSError, ValueError) as error:
            log(f"Meeting: could not rename the folder {folder.name} ({type(error).__name__})")
            self._settings_status("The meeting was renamed, but not its folder. A file in it may be open.",
                                  "#FF9F0A")
            return folder
        if new != folder:
            log(f"Meeting: folder renamed to {new.name}")
            current = self._meeting
            if current and current.folder == folder:
                current.folder = new  # The chip's Open goes to the right place
            if self._settings_win:
                self._settings_win.meeting_moved(folder, new)
        return new

    def _rename_waiting_folders(self):
        for folder in list(self._folder_renames):
            if not self.meeting_in_use(folder):
                self._folder_renames.discard(folder)
                if folder.exists():
                    self._rename_folder_now(folder)

    def set_meeting_my_name(self, name):
        """Remember the name given to Me, for the meetings that follow (#44)."""
        name = " ".join(str(name).split())[:60]
        if name == "Me":
            name = ""
        if self._config.get("meeting_my_name", "") == name:
            return
        self._config["meeting_my_name"] = name
        if self._settings_win:  # It keeps its own copy; Apply would otherwise put the old one back
            self._settings_win._config["meeting_my_name"] = name
        self._save_quietly()

    def meeting_in_use(self, folder):
        """True while a meeting is being recorded or transcribed into folder."""
        folder = Path(folder)
        current = self._meeting
        if current and current.folder == folder and not current.finished.is_set():
            return True
        return folder in self._retranscribing or self.finding_speakers(folder)

    def meeting_progress(self, folder):
        """(stage, fraction) while a meeting's transcript is being finished or its speakers
        found: ("transcribing", 0.4), ("speakers", 0.6), ("speakers starting", seconds so far)
        before separation reports a fraction, ("speakers waiting", None) in the queue; else None."""
        from meetings import transcribe_progress
        from speakers import elapsed, progress
        folder = Path(folder)
        fraction = transcribe_progress(folder)
        if fraction is not None:
            return "transcribing", fraction
        if self.finding_speakers(folder):
            fraction = progress(folder)
            if fraction is not None:
                return "speakers", fraction
            seconds = elapsed(folder)
            return ("speakers starting", seconds) if seconds is not None else ("speakers waiting", None)
        return None

    def finding_speakers(self, folder):
        """True while speakers are being labeled in folder, or it is waiting its turn."""
        folder = Path(folder)
        with self._speaker_lock:
            return folder == self._speaker_folder or folder in self._speaker_jobs

    def _find_speakers_later(self, folder):
        """Queue speaker labeling; one meeting at a time, in the background."""
        folder = Path(folder)
        with self._speaker_lock:
            if folder == self._speaker_folder or folder in self._speaker_jobs:
                return
            self._speaker_jobs.append(folder)
            if self._speaker_folder is not None:
                return  # The worker picks it up next
            self._speaker_folder = self._speaker_jobs.pop(0)
        threading.Thread(target=self._speaker_worker, name="meeting-speakers", daemon=True).start()

    def _speaker_worker(self):
        from meetings import read_meta, write_meta
        from speakers import find_speakers
        while True:
            with self._speaker_lock:
                folder = self._speaker_folder
            try:
                if not self._config.get("meeting_speakers", True):
                    meta = read_meta(folder)
                    meta["speakers"] = {"status": "skipped", "reason": "off"}
                    write_meta(folder, meta)
                else:
                    while self._engine.active and not self._closing:
                        time.sleep(0.5)  # Dictation first; the take gets the CPU
                    if not self._closing:
                        self._events.put(("action", self._refresh_meetings))
                        find_speakers(folder, log, lambda: self._closing)
            except Exception as error:
                log(f"Meeting: finding speakers failed ({type(error).__name__})")
            with self._speaker_lock:
                self._speaker_folder = self._speaker_jobs.pop(0) if self._speaker_jobs and not self._closing else None
                done = self._speaker_folder is None
            self._events.put(("action", self._refresh_meetings))
            if done:
                return

    def meeting_status(self):
        """(state, elapsed seconds) for the Meetings page's Record button:
        recording, finishing (the last transcript is still being written), or idle."""
        if self._meeting_recording():
            return "recording", self._meeting.elapsed
        if self._meeting and not self._meeting.finished.is_set():
            return "finishing", 0
        return "idle", 0

    def toggle_meeting(self):
        """Start or stop a meeting from the main window, like the tray item."""
        self._toggle_meeting()

    def meetings_busy(self):
        """True while any meeting is recording, transcribing, or moving, so the folder can't change."""
        current = self._meeting
        return bool((current and not current.finished.is_set()) or self._retranscribing or self._moving_meetings)

    def set_meetings_dir(self, folder, move, on_done):
        """Save folder as the meetings folder. With move, existing meetings follow it in the
        background; on_done(moved, failed) then runs on the UI thread."""
        import threading
        from meetings import move_meetings
        old = self._config["meetings_dir"]
        self._config["meetings_dir"] = str(Path(folder))
        if self._settings_win:  # It keeps its own copy; Apply would otherwise put the old folder back
            self._settings_win._config["meetings_dir"] = self._config["meetings_dir"]
        self._save_quietly()
        log("Meeting: meetings folder changed" + (", moving meetings" if move else ""))
        if not move:
            self._events.put(("action", lambda: on_done(0, 0)))
            return
        self._moving_meetings = True

        def run():
            try:
                moved, failed = move_meetings(old, folder, skip=self.meeting_in_use)
            except OSError as error:
                log(f"Meeting: could not move meetings ({type(error).__name__})")
                moved, failed = 0, -1
            log(f"Meeting: moved {moved} meeting(s), {max(failed, 0)} left behind")

            def finish():
                self._moving_meetings = False
                on_done(moved, failed)
            self._events.put(("action", finish))

        threading.Thread(target=run, name="meeting-move", daemon=True).start()

    def retranscribe_meeting(self, folder):
        """Transcribe a meeting again from its WAV files, giving way to dictation as usual."""
        import threading
        from meetings import read_meta, transcribe_folder, write_meta
        folder = Path(folder)
        if self.meeting_in_use(folder):
            return
        try:
            meta = read_meta(folder)
            meta["status"] = "transcribing"
            write_meta(folder, meta)
        except (OSError, ValueError) as error:
            log(f"Meeting: could not re-transcribe {folder.name} ({type(error).__name__})")
            self._settings_status("Couldn't re-transcribe this meeting.", "#FF453A")
            return
        self._retranscribing.add(folder)
        log(f"Meeting: re-transcribing {folder.name}")

        def run():
            ok = transcribe_folder(folder, lambda: self._engine.speech_model, lambda: self._engine.active,
                                   self._config.get("language", "en"), log, lambda: self._closing)
            self._events.put(("action", lambda: self._meeting_done(folder, ok)))

        threading.Thread(target=run, name="meeting-retranscribe", daemon=True).start()

    def _open_meetings_folder(self):
        import os
        folder = Path(self._config["meetings_dir"])
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as error:
            log(f"Meeting: could not open the meetings folder ({type(error).__name__})")

    def _note_interrupted_meetings(self):
        """Mark meetings a quit or crash left unfinished, and say so once (#66). Also give
        every folder its meeting's title again: one titled before folders followed titles,
        one Windows wouldn't rename before a quit, or one renamed by hand.

        Nothing is restarted: work that starts by itself at launch, with no word
        of why the PC is busy, is a mystery. The meeting says it didn't finish,
        and Re-transcribe finishes it."""
        import threading
        from meetings import misnamed_folders, speakers_unfinished, stop_speakers, unfinished

        def run():
            try:
                folders = unfinished(self._config["meetings_dir"],
                                     skip=self._meeting.folder if self._meeting else None)
                for folder in speakers_unfinished(self._config["meetings_dir"]):
                    stop_speakers(folder)
                    folders.append(folder)
            except OSError:
                folders = []
            try:
                stale = misnamed_folders(self._config["meetings_dir"])
            except OSError:
                stale = []
            if stale:  # The title is the meeting's name: its folder follows it, quietly (#57)
                self._events.put(("action", lambda: (self._folder_renames.update(stale),
                                                     self._refresh_meetings())))
            if not folders:
                return
            for folder in folders:
                log(f"Meeting: {folder.name} didn't finish; Re-transcribe finishes it")
            message = interrupted_notice(len(folders))
            self._events.put(("action", lambda: self._notify(message)))
            self._events.put(("action", self._refresh_meetings))

        threading.Thread(target=run, name="meeting-recovery", daemon=True).start()

    def _notify(self, message):
        try:
            self._tray._icon.notify(message, "CopyThat")
        except Exception as error:
            log(f"Could not show a notification ({type(error).__name__})")

    # -- Quit --------------------------------------------------------------

    def _toggle_startup(self):
        import startup
        try:
            startup.set_enabled(not startup.enabled())
            self._tray._icon.update_menu()
        except OSError:
            self._tray._icon.notify("Could not change Start with Windows.", "CopyThat")

    def _quit(self):
        self._closing = True
        if self._meeting:
            self._meeting.abandon()  # Audio is kept; the next launch writes the transcript.
        self._theme_controller.close()
        self._engine.stop()
        self._tray.stop()
        self._root.after(0, self._root.destroy)


def main():
    # The Parakeet decoder makes ~175 short native calls per window, each
    # reacquiring the GIL. At the default 5ms switch interval, UI threads made
    # a 0.11s decode take 2.3s; at 0.2ms it took 0.14s.
    sys.setswitchinterval(0.0002)
    parser = argparse.ArgumentParser(description="CopyThat Engine (GUI)")
    parser.add_argument("--device", choices=["NPU", "GPU", "CPU"], help="Override device")
    parser.add_argument("--model", choices=list(MODEL_REGISTRY.keys()), help="Model size")
    parser.add_argument("--language", type=str, help="Language code")
    parser.add_argument("--auto-enter", action="store_true", help="Press Enter after typing")
    parser.add_argument("--hotkey", type=str, help="Global hotkey")
    parser.add_argument("--import-models", type=str, help="Explicitly install an adjacent offline model pack")
    parser.add_argument("--self-test", type=str, help="Write a packaged-runtime verification report and exit")
    parser.add_argument("--test-audio", type=str, help="Synthetic 16kHz mono WAV for --self-test")
    parser.add_argument("--test-paste", action="store_true", help="Use disposable text fields during --self-test")
    args = parser.parse_args()

    if args.import_models:
        from installed_setup import import_models
        import_models(args.import_models)
        return
    if args.self_test:
        from installed_setup import self_test
        from windows_io import single_instance
        with single_instance() as acquired:
            if not acquired:
                raise RuntimeError("Quit the tray app before running verification")
            self_test(args.self_test, args.test_audio, args.test_paste)
        return

    config = load_config()

    if args.device:
        config["device"] = args.device
    if args.model:
        config["model_size"] = args.model
    if args.language:
        config["language"] = args.language
    if args.auto_enter:
        config["auto_enter"] = True
    if args.hotkey:
        config["hotkey"] = args.hotkey

    # Auto-select device if not explicitly overridden
    if not args.device:
        preferred = launch_device(config)
        if config["device"] != preferred:
            log(f"Auto-selecting {preferred} for {config['model_size']}")
            config["device"] = preferred

    validate_config(config)
    log(f"CopyThat {version_label()}")  # Which build wrote the lines that follow

    from windows_io import single_instance
    with single_instance() as acquired:
        if not acquired:
            log("CopyThat is already running in the system tray")
            return
        app = GUIApp(config)
        app.run()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # The packaged app also runs speaker separation (speakers.py)
    main()
