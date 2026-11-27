#!/usr/bin/env python3
import asyncio
import io
import os
import queue
import sys
import threading
import time
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk
import sounddevice as sd
import numpy as np
import httpx
from shazamio import Shazam

# Import functions/classes from the existing recognizer script
from recognizer import (
    np_array_to_wav_bytes,
    recognize_song,
    init_lastfm,
    record_audio,
    check_input_levels,
    CREDENTIALS_PATH,
    load_credentials,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_RECORD_DURATION,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_SONG_DURATION_FALLBACK
)
from audio import AudioRecorderMonitor
from pathlib import Path
import webbrowser

# Colors for a modern dark theme
COLOR_BG = "#121212"          # Dark background
COLOR_CARD = "#1C1C1E"        # Slightly lighter card background
COLOR_ACCENT = "#1DB954"      # Spotify/Last.fm active green
COLOR_ACCENT_HOVER = "#1ED760"
COLOR_STOP = "#FF3B30"        # System red
COLOR_TEXT_PRIMARY = "#FFFFFF"
COLOR_TEXT_SECONDARY = "#A7A7A7"
COLOR_BORDER = "#2C2C2E"      # Thin border line


class SettingsDialog(tk.Toplevel):
    """Modal dialog for configuring Last.fm API credentials."""

    def __init__(self, parent: tk.Tk, on_save_callback=None, first_run: bool = False):
        super().__init__(parent)
        self.on_save_callback = on_save_callback
        self.title("Last.fm Settings")
        self.configure(bg=COLOR_BG)
        self.resizable(False, False)
        self.transient(parent)

        # Form variables pre-populated from current environment
        self.api_key_var = tk.StringVar(value=os.getenv("LASTFM_API_KEY", ""))
        self.api_secret_var = tk.StringVar(value=os.getenv("LASTFM_API_SECRET", ""))
        self.username_var = tk.StringVar(value=os.getenv("LASTFM_USERNAME", ""))
        self.password_var = tk.StringVar(value=os.getenv("LASTFM_PASSWORD", ""))
        self.session_key_var = tk.StringVar(value=os.getenv("LASTFM_SESSION_KEY", ""))
        self.auth_mode_var = tk.StringVar(
            value="session" if os.getenv("LASTFM_SESSION_KEY") else "password"
        )
        self.show_secrets_var = tk.BooleanVar(value=False)

        self._build_dialog(first_run)

        # Center over parent window
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")

        self.grab_set()
        self.focus_set()

    def _build_dialog(self, first_run: bool):
        container = tk.Frame(self, bg=COLOR_BG, padx=20, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text="Last.fm Credentials",
            bg=COLOR_BG,
            fg=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 14, "bold"),
        ).pack(anchor=tk.W)

        subtitle = (
            "Welcome! Enter your Last.fm API credentials to enable scrobbling,\n"
            "or close this window to use Dry Run mode (track recognition only)."
            if first_run
            else f"Saved to: {CREDENTIALS_PATH}"
        )
        tk.Label(
            container,
            text=subtitle,
            bg=COLOR_BG,
            fg=COLOR_TEXT_SECONDARY,
            font=("Helvetica", 9),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 10))

        # Clickable link to Last.fm API creation page
        link_lbl = tk.Label(
            container,
            text="Get a free Last.fm API Key & Secret →",
            bg=COLOR_BG,
            fg=COLOR_ACCENT,
            font=("Helvetica", 9, "underline"),
            cursor="hand2",
        )
        link_lbl.pack(anchor=tk.W, pady=(0, 12))
        link_lbl.bind("<Button-1>", lambda _e: webbrowser.open("https://www.last.fm/api/account/create"))

        card = tk.Frame(
            container,
            bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        card.pack(fill=tk.X)

        self._secret_entries = []

        def add_field(label_text: str, var: tk.StringVar, is_secret: bool = False):
            tk.Label(
                card,
                text=label_text,
                bg=COLOR_CARD,
                fg=COLOR_TEXT_SECONDARY,
                font=("Helvetica", 8, "bold"),
            ).pack(anchor=tk.W, pady=(6, 2))
            entry = tk.Entry(
                card,
                textvariable=var,
                bg=COLOR_BG,
                fg=COLOR_TEXT_PRIMARY,
                disabledbackground="#181818",
                disabledforeground="#666666",
                insertbackground=COLOR_TEXT_PRIMARY,
                relief="flat",
                highlightbackground=COLOR_BORDER,
                highlightthickness=1,
                width=46,
                show="•" if is_secret else "",
                font=("Helvetica", 10),
            )
            entry.pack(fill=tk.X, ipady=4)
            if is_secret:
                self._secret_entries.append(entry)
            return entry

        add_field("API Key", self.api_key_var)
        add_field("API Secret", self.api_secret_var, is_secret=True)
        add_field("Last.fm Username", self.username_var)

        # Auth mode selector
        mode_frame = tk.Frame(card, bg=COLOR_CARD)
        mode_frame.pack(fill=tk.X, pady=(10, 2))
        tk.Label(
            mode_frame,
            text="Authentication Method:",
            bg=COLOR_CARD,
            fg=COLOR_TEXT_SECONDARY,
            font=("Helvetica", 8, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        for val, text in (("password", "Password"), ("session", "Session Key")):
            tk.Radiobutton(
                mode_frame,
                text=text,
                variable=self.auth_mode_var,
                value=val,
                command=self._toggle_auth_fields,
                bg=COLOR_CARD,
                fg=COLOR_TEXT_PRIMARY,
                selectcolor=COLOR_BG,
                activebackground=COLOR_CARD,
                activeforeground=COLOR_TEXT_PRIMARY,
                font=("Helvetica", 8),
            ).pack(side=tk.LEFT, padx=4)

        self.password_entry = add_field("Last.fm Password", self.password_var, is_secret=True)
        self.session_entry = add_field("Last.fm Session Key", self.session_key_var, is_secret=True)
        self._toggle_auth_fields()

        tk.Checkbutton(
            card,
            text="Show secrets",
            variable=self.show_secrets_var,
            command=self._toggle_secret_visibility,
            bg=COLOR_CARD,
            fg=COLOR_TEXT_SECONDARY,
            selectcolor=COLOR_BG,
            activebackground=COLOR_CARD,
            activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 8),
        ).pack(anchor=tk.W, pady=(8, 0))

        # Action buttons
        btn_row = tk.Frame(container, bg=COLOR_BG)
        btn_row.pack(fill=tk.X, pady=(14, 0))

        tk.Button(
            btn_row,
            text="Cancel",
            bg=COLOR_BORDER,
            fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_CARD,
            activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9),
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.destroy,
        ).pack(side=tk.RIGHT, padx=(8, 0))

        tk.Button(
            btn_row,
            text="Save Credentials",
            bg=COLOR_ACCENT,
            fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9, "bold"),
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._save,
        ).pack(side=tk.RIGHT)

    def _toggle_auth_fields(self):
        if self.auth_mode_var.get() == "password":
            self.password_entry.configure(state="normal")
            self.session_entry.configure(state="disabled")
        else:
            self.password_entry.configure(state="disabled")
            self.session_entry.configure(state="normal")

    def _toggle_secret_visibility(self):
        char = "" if self.show_secrets_var.get() else "•"
        for entry in self._secret_entries:
            entry.configure(show=char)

    def _save(self):
        api_key = self.api_key_var.get().strip()
        api_secret = self.api_secret_var.get().strip()
        username = self.username_var.get().strip()
        auth_mode = self.auth_mode_var.get()
        password = self.password_var.get() if auth_mode == "password" else ""
        session_key = self.session_key_var.get().strip() if auth_mode == "session" else ""

        if not api_key or not api_secret:
            messagebox.showwarning(
                "Missing Fields",
                "API Key and API Secret are required to scrobble to Last.fm.",
                parent=self,
            )
            return

        if auth_mode == "password" and (not username or not password):
            messagebox.showwarning(
                "Missing Fields",
                "Please enter both your Last.fm Username and Password.",
                parent=self,
            )
            return

        if auth_mode == "session" and not session_key:
            messagebox.showwarning(
                "Missing Fields",
                "Please enter your Last.fm Session Key.",
                parent=self,
            )
            return

        try:
            CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# Aux Audio Scrobbler — Last.fm Credentials",
                f"LASTFM_API_KEY={api_key}",
                f"LASTFM_API_SECRET={api_secret}",
                f"LASTFM_USERNAME={username}",
            ]
            if auth_mode == "session":
                lines.append(f"LASTFM_SESSION_KEY={session_key}")
            else:
                lines.append(f"LASTFM_PASSWORD={password}")

            CREDENTIALS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(CREDENTIALS_PATH, 0o600)

            # Update current process environment immediately
            os.environ["LASTFM_API_KEY"] = api_key
            os.environ["LASTFM_API_SECRET"] = api_secret
            if username:
                os.environ["LASTFM_USERNAME"] = username
            else:
                os.environ.pop("LASTFM_USERNAME", None)

            if auth_mode == "session":
                os.environ["LASTFM_SESSION_KEY"] = session_key
                os.environ.pop("LASTFM_PASSWORD", None)
            else:
                os.environ["LASTFM_PASSWORD"] = password
                os.environ.pop("LASTFM_SESSION_KEY", None)

            load_credentials()

            if self.on_save_callback:
                self.on_save_callback()

            messagebox.showinfo(
                "Saved",
                f"Credentials saved to:\n{CREDENTIALS_PATH}",
                parent=self,
            )
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save credentials:\n{exc}", parent=self)


class ScrobblerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Aux Audio Scrobbler")
        self.root.geometry("1200x700")
        self.root.configure(bg=COLOR_BG)

        # Min size — just large enough to show all cards without clipping
        self.root.minsize(900, 600)

        # Threading queue for thread-safe communication
        self.msg_queue = queue.Queue()
        
        # Scrobbler background task variables
        self.scrobble_thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.is_running = False
        self.stream_lock = threading.Lock()
        self.current_song_info = {"title": "Not Playing", "artist": "No active track recognized", "image": None}

        # UI state variables
        self.input_devices_dict = {}
        self.output_devices_dict = {}
        self.device_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.monitor_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.skip_silence_var = tk.BooleanVar(value=True)
        self.silence_threshold_var = tk.DoubleVar(value=0.0020)
        self.duration_var = tk.IntVar(value=DEFAULT_RECORD_DURATION)
        self.interval_var = tk.IntVar(value=DEFAULT_CHECK_INTERVAL)
        
        # Equalizer variables
        self.bass_var = tk.DoubleVar(value=1.0)
        self.treble_var = tk.DoubleVar(value=1.0)
        self.volume_var = tk.DoubleVar(value=1.0)
        self.bass_gain = 1.0
        self.treble_gain = 1.0
        self.volume_multiplier = 1.0
        self.N = 50
        self.kernel = np.ones(self.N, dtype='float32') / self.N

        # Populate devices
        self.query_audio_devices()

        # Build GUI Layout
        self.setup_styles()
        self.build_ui()

        # Start checking the message queue for updates
        self.root.after(100, self.poll_queue)
        # Prompt for Last.fm credentials on first launch if none are configured
        self.root.after(200, self.check_first_run_credentials)

    def check_first_run_credentials(self):
        """Opens the settings dialog on startup if Last.fm credentials are not configured."""
        if not (os.getenv("LASTFM_API_KEY") and os.getenv("LASTFM_API_SECRET")):
            self.log("[Last.fm] No credentials found. Opening setup dialog...")
            SettingsDialog(
                self.root,
                on_save_callback=lambda: self.log("[Last.fm] Credentials updated."),
                first_run=True,
            )

    def open_settings(self):
        """Opens the Last.fm credentials settings dialog."""
        SettingsDialog(
            self.root,
            on_save_callback=lambda: self.log("[Last.fm] Credentials updated."),
            first_run=False,
        )

    def setup_styles(self):
        """Sets up ttk styles to match the dark theme."""
        style = ttk.Style()
        style.theme_use("clam")
        
        # Configure frames
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD, borderwidth=1, relief="solid")
        
        # Configure labels
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT_PRIMARY, font=("Helvetica", 10))
        style.configure("CardTitle.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT_PRIMARY, font=("Helvetica", 12, "bold"))
        style.configure("Header.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_PRIMARY, font=("Helvetica", 18, "bold"))
        
        # Configure dropdowns
        style.configure(
            "TCombobox",
            fieldbackground=COLOR_CARD,
            background=COLOR_BG,
            foreground=COLOR_TEXT_PRIMARY,
            arrowcolor=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLOR_CARD), ("disabled", "#181818")],
            foreground=[("readonly", COLOR_TEXT_PRIMARY), ("disabled", "#666666")],
            selectbackground=[("readonly", COLOR_CARD)],
            selectforeground=[("readonly", COLOR_TEXT_PRIMARY)],
        )
        self.root.option_add("*TCombobox*Listbox.background", COLOR_CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT_PRIMARY)
        self.root.option_add("*TCombobox*Listbox.selectBackground", COLOR_ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", COLOR_TEXT_PRIMARY)

    def get_system_monitors(self):
        """Queries PulseAudio/PipeWire for monitor/loopback sources."""
        monitors = {}
        if sys.platform.startswith("linux"):
            try:
                import subprocess
                out = subprocess.check_output(["pactl", "list", "sources"], text=True, stderr=subprocess.DEVNULL)
                blocks = out.split("Source #")
                for block in blocks:
                    name = None
                    desc = None
                    for line in block.split("\n"):
                        line_stripped = line.strip()
                        if line_stripped.startswith("Name:"):
                            name = line_stripped.split("Name:", 1)[1].strip()
                        elif line_stripped.startswith("Description:"):
                            desc = line_stripped.split("Description:", 1)[1].strip()
                    if name and name.endswith(".monitor"):
                        display_name = desc if desc else name
                        # Avoid duplicates
                        monitors[display_name] = name
            except Exception:
                try:
                    import subprocess
                    out = subprocess.check_output(["pactl", "list", "short", "sources"], text=True, stderr=subprocess.DEVNULL)
                    for line in out.strip().split("\n"):
                        parts = line.split()
                        if len(parts) >= 2:
                            name = parts[1]
                            if name.endswith(".monitor"):
                                monitors[name] = name
                except Exception:
                    pass
        return monitors

    def resolve_pulse_device(self):
        """Finds the index of 'pulse' or 'pipewire' or 'default' device in sd.query_devices()."""
        try:
            devices = sd.query_devices()
            # Check for 'pulse' first
            for idx, dev in enumerate(devices):
                if dev["name"].lower() == "pulse":
                    return idx
            # Check for 'pipewire'
            for idx, dev in enumerate(devices):
                if dev["name"].lower() == "pipewire":
                    return idx
            # Check for 'default'
            for idx, dev in enumerate(devices):
                if dev["name"].lower() == "default":
                    return idx
        except Exception:
            pass
        return None

    def auto_detect_active_output(self):
        """Detects which PulseAudio/PipeWire sink is currently producing audio and selects its monitor source.
        Refreshes the monitor source list first so newly connected devices (Bluetooth, USB, etc.) are found."""
        if not sys.platform.startswith("linux"):
            messagebox.showinfo("Auto-Detect", "Auto-detect is only available on Linux (PulseAudio/PipeWire).")
            return

        self.btn_autodetect.config(state="disabled", text="Detecting...")
        self.root.update_idletasks()

        try:
            import subprocess

            # ── Step 1: find sinks that are currently RUNNING ────────────────────
            out = subprocess.check_output(["pactl", "list", "sinks"], text=True, stderr=subprocess.DEVNULL)
            blocks = out.split("Sink #")
            running_sink_names = []
            for block in blocks:
                if not block.strip():
                    continue
                name = state = None
                for line in block.split("\n"):
                    ls = line.strip()
                    if ls.startswith("Name:"):
                        name = ls.split("Name:", 1)[1].strip()
                    elif ls.startswith("State:"):
                        state = ls.split("State:", 1)[1].strip()
                if name and state == "RUNNING":
                    running_sink_names.append(name)

            if not running_sink_names:
                messagebox.showinfo(
                    "Auto-Detect",
                    "No active audio output detected.\n\nMake sure something is playing and try again."
                )
                return

            # ── Step 2: refresh all monitor sources from pactl ───────────────────
            # Build a fresh map:  monitor_source_name → human description
            src_out = subprocess.check_output(["pactl", "list", "sources"], text=True, stderr=subprocess.DEVNULL)
            src_blocks = src_out.split("Source #")
            fresh_monitors = {}   # actual_name -> display_name
            for src_block in src_blocks:
                src_name = src_desc = None
                for line in src_block.split("\n"):
                    ls = line.strip()
                    if ls.startswith("Name:"):
                        src_name = ls.split("Name:", 1)[1].strip()
                    elif ls.startswith("Description:"):
                        src_desc = ls.split("Description:", 1)[1].strip()
                if src_name and src_name.endswith(".monitor"):
                    display = src_desc if src_desc else src_name
                    fresh_monitors[src_name] = display

            # Merge any new monitors into input_devices_dict and combobox values
            added = False
            for actual_name, display_name in fresh_monitors.items():
                ui_key = f"System Sound: {display_name}"
                if ui_key not in self.input_devices_dict:
                    self.input_devices_dict[ui_key] = actual_name
                    added = True
            if added:
                self.combo_input.config(values=list(self.input_devices_dict.keys()))
                self.log("[Auto-Detect] Device list refreshed with newly found monitor sources.")

            # ── Step 3: match a running sink's monitor to an entry in the dict ───
            monitor_candidates = {f"{s}.monitor" for s in running_sink_names}

            found_key = None
            # Primary match: actual pactl source name
            for display_key, actual_name in self.input_devices_dict.items():
                if isinstance(actual_name, str) and actual_name in monitor_candidates:
                    found_key = display_key
                    break

            # Secondary match: description substring (handles cases where pactl name
            # doesn't end with .monitor but the description still matches)
            if found_key is None:
                for candidate in monitor_candidates:
                    candidate_desc = fresh_monitors.get(candidate, "")
                    if not candidate_desc:
                        continue
                    for display_key in self.input_devices_dict:
                        if candidate_desc in display_key or display_key.endswith(candidate_desc):
                            found_key = display_key
                            break
                    if found_key:
                        break

            if found_key:
                self.combo_input.set(found_key)
                self.on_input_device_changed(None)
                self.log(f"[Auto-Detect] Active output found → {found_key}")
                messagebox.showinfo("Auto-Detect", f"Active audio output detected:\n{found_key}")
            else:
                self.log(f"[Auto-Detect] Running sinks: {running_sink_names} — no matching monitor source found after refresh.")
                messagebox.showwarning(
                    "Auto-Detect",
                    f"Detected active sink(s):\n{', '.join(running_sink_names)}\n\n"
                    "No matching monitor source was found even after refreshing.\n"
                    "Select the input manually from the dropdown."
                )

        except FileNotFoundError:
            messagebox.showerror("Auto-Detect", "pactl not found. Install PulseAudio or PipeWire to use auto-detect.")
        except Exception as e:
            messagebox.showerror("Auto-Detect", f"Detection failed:\n{e}")
        finally:
            self.btn_autodetect.config(state="normal", text="AUTO-DETECT")

    def set_device_env_vars(self):
        """Sets or clears the PULSE_SOURCE and PIPEWIRE_NODE environment variables."""
        in_device_key = self.combo_input.get()
        in_device = self.input_devices_dict.get(in_device_key, None)
        if isinstance(in_device, str):
            os.environ["PULSE_SOURCE"] = in_device
            os.environ["PIPEWIRE_NODE"] = in_device
        else:
            if "PULSE_SOURCE" in os.environ:
                del os.environ["PULSE_SOURCE"]
            if "PIPEWIRE_NODE" in os.environ:
                del os.environ["PIPEWIRE_NODE"]

    def on_input_device_changed(self, event):
        """Callback when the selected input device is changed in the combobox."""
        in_device_key = self.combo_input.get()
        in_device = self.input_devices_dict.get(in_device_key, None)
        
        if isinstance(in_device, str):  # It is a system monitor source
            # Save the previous monitor state so we can restore it if they switch back to microphone
            if not hasattr(self, '_prev_monitor_val'):
                self._prev_monitor_val = self.monitor_var.get()
            self.monitor_var.set(False)
            self.chk_monitor.configure(state="disabled")
            self.combo_output.configure(state="disabled")
            self.lbl_output.configure(fg="#555555")
            self.slider_volume.configure(state="disabled")
            self.slider_bass.configure(state="disabled")
            self.slider_treble.configure(state="disabled")
            self.btn_playback.configure(state="disabled")
        else:
            self.chk_monitor.configure(state="normal")
            if hasattr(self, '_prev_monitor_val'):
                self.monitor_var.set(self._prev_monitor_val)
                delattr(self, '_prev_monitor_val')
            self.toggle_monitor_fields()
            self.slider_volume.configure(state="normal")
            self.slider_bass.configure(state="normal")
            self.slider_treble.configure(state="normal")
            self.btn_playback.configure(state="normal")

    def query_audio_devices(self):
        """Retrieves and groups available input/output sound cards."""
        try:
            devices = sd.query_devices()
            self.input_devices_dict = {"Default Input": None}
            self.output_devices_dict = {"Default Output": None}
            
            for idx, dev in enumerate(devices):
                name = dev["name"]
                max_in = dev.get("max_input_channels", 0)
                max_out = dev.get("max_output_channels", 0)
                
                # Deduplicate and format names
                if max_in > 0:
                    self.input_devices_dict[f"Index {idx}: {name}"] = idx
                if max_out > 0:
                    self.output_devices_dict[f"Index {idx}: {name}"] = idx
            
            # Populate system loopback/monitor sources
            monitors = self.get_system_monitors()
            for display_name, actual_name in monitors.items():
                self.input_devices_dict[f"System Sound: {display_name}"] = actual_name
                
        except Exception as e:
            messagebox.showerror("Audio Error", f"Could not query audio devices: {e}")

    def build_ui(self):
        """Draws the main user interface."""
        # Top Header Frame
        header_frame = tk.Frame(self.root, bg=COLOR_BG, pady=15)
        header_frame.pack(fill=tk.X, padx=20)

        # App Logo / Title
        self.status_dot = tk.Canvas(header_frame, width=15, height=15, bg=COLOR_BG, highlightthickness=0)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 10))
        self.draw_status_dot(COLOR_STOP)

        title_label = ttk.Label(header_frame, text="AUX AUDIO SCROBBLER", style="Header.TLabel")
        title_label.pack(side=tk.LEFT)

        # Status text
        self.status_text = ttk.Label(header_frame, text="Stopped", font=("Helvetica", 10, "italic"), foreground=COLOR_TEXT_SECONDARY)
        self.status_text.pack(side=tk.LEFT, padx=15)

        # Settings button (top right)
        self.btn_settings = tk.Button(
            header_frame,
            text="⚙ Settings",
            bg=COLOR_CARD,
            fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_BORDER,
            activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9, "bold"),
            bd=0,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
            padx=12,
            pady=5,
            cursor="hand2",
            command=self.open_settings,
        )
        self.btn_settings.pack(side=tk.RIGHT)

        # Main horizontal split
        main_split = tk.Frame(self.root, bg=COLOR_BG)
        main_split.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        # Top panel containing Now Playing (left) and Activity Log (right)
        top_panel = tk.Frame(main_split, bg=COLOR_BG)
        top_panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 10))

        # Now Playing Card (Left)
        np_card = tk.Frame(top_panel, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1, width=460)
        np_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)
        np_card.pack_propagate(False)

        np_card_padding = tk.Frame(np_card, bg=COLOR_CARD, padx=15, pady=15)
        np_card_padding.pack(fill=tk.BOTH, expand=True)

        tk.Label(np_card_padding, text="NOW PLAYING", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 10, "bold")).pack(anchor=tk.W, pady=(0, 10))

        # Song Details layout (Cover art on left, text details on right)
        song_layout = tk.Frame(np_card_padding, bg=COLOR_CARD)
        song_layout.pack(fill=tk.BOTH, expand=True)

        # Artwork label (fallback is a generated block)
        self.art_label = tk.Label(song_layout, bg=COLOR_BG, width=190, height=190, relief="solid", borderwidth=0)
        self.art_label.pack(side=tk.LEFT, padx=(0, 15), pady=5)
        self.draw_default_art()

        # Text labels on right
        details_frame = tk.Frame(song_layout, bg=COLOR_CARD)
        details_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.lbl_song_title = tk.Label(details_frame, text="Not Listening", bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 13, "bold"), wraplength=200, justify=tk.LEFT)
        self.lbl_song_title.pack(anchor=tk.W, pady=(10, 2))

        self.lbl_song_artist = tk.Label(details_frame, text="Start the scrobbler to identify playing tracks.", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 9), wraplength=200, justify=tk.LEFT)
        self.lbl_song_artist.pack(anchor=tk.W)

        self.lbl_scrobble_status = tk.Label(details_frame, text="", bg=COLOR_CARD, fg=COLOR_ACCENT, font=("Helvetica", 9, "italic"), wraplength=200, justify=tk.LEFT)
        self.lbl_scrobble_status.pack(anchor=tk.W, pady=(15, 0))

        def _update_details_wrap(event):
            wrap = max(120, event.width - 8)
            self.lbl_song_title.config(wraplength=wrap)
            self.lbl_song_artist.config(wraplength=wrap)
            self.lbl_scrobble_status.config(wraplength=wrap)

        details_frame.bind("<Configure>", _update_details_wrap)

        # Activity Log Panel (Right)
        log_frame = tk.Frame(top_panel, bg=COLOR_BG)
        log_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(15, 0))

        log_label = ttk.Label(log_frame, text="ACTIVITY LOG", font=("Helvetica", 9, "bold"))
        log_label.pack(anchor=tk.W, pady=(0, 5))

        self.log_text = scrolledtext.ScrolledText(
            log_frame, bg=COLOR_CARD, fg="#CCCCCC", insertbackground=COLOR_TEXT_PRIMARY,
            state="disabled", borderwidth=0, highlightbackground=COLOR_BORDER, highlightthickness=1,
            font=("Courier", 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Bottom panel containing Settings and Buttons (side-by-side cards)
        bottom_panel = tk.Frame(main_split, bg=COLOR_BG)
        bottom_panel.pack(side=tk.BOTTOM, fill=tk.X, expand=False, pady=(10, 0))

        # Give each of the 4 card columns equal weight so they resize together
        for col in range(4):
            bottom_panel.columnconfigure(col, weight=1, uniform="card")

        # 1. Device Selection Card
        dev_card = tk.Frame(bottom_panel, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        dev_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
        
        dev_card_padding = tk.Frame(dev_card, bg=COLOR_CARD, padx=12, pady=10)
        dev_card_padding.pack(fill=tk.BOTH, expand=True)

        lbl = tk.Label(dev_card_padding, text="AUDIO ROUTING SETTINGS", bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 9, "bold"))
        lbl.pack(anchor=tk.W, pady=(0, 8))

        # Input label row + AUTO-DETECT button on the same line
        input_lbl_row = tk.Frame(dev_card_padding, bg=COLOR_CARD)
        input_lbl_row.pack(fill=tk.X)
        tk.Label(input_lbl_row, text="Input Port (Aux Source)", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(side=tk.LEFT)
        self.btn_autodetect = tk.Button(
            input_lbl_row, text="AUTO-DETECT", bg="#2C2C3E", fg=COLOR_ACCENT,
            activebackground=COLOR_CARD, activeforeground=COLOR_ACCENT_HOVER,
            font=("Helvetica", 7, "bold"), bd=0, padx=6, pady=1, cursor="hand2",
            command=self.auto_detect_active_output
        )
        self.btn_autodetect.pack(side=tk.RIGHT)

        self.combo_input = ttk.Combobox(dev_card_padding, textvariable=self.device_var, values=list(self.input_devices_dict.keys()), state="readonly")
        self.combo_input.pack(fill=tk.X, pady=(1, 8))
        self.combo_input.set("Default Input")
        self.combo_input.bind("<<ComboboxSelected>>", self.on_input_device_changed)

        # Monitor checkbox
        self.chk_monitor = tk.Checkbutton(
            dev_card_padding, text="Enable Audio Monitoring", 
            variable=self.monitor_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
            selectcolor=COLOR_BG, activebackground=COLOR_CARD, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 8), command=self.toggle_monitor_fields
        )
        self.chk_monitor.pack(anchor=tk.W, pady=(0, 4))

        # Output select (linked to monitoring)
        self.lbl_output = tk.Label(dev_card_padding, text="Output Port (Headphones/Speakers)", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8))
        self.lbl_output.pack(anchor=tk.W)
        self.combo_output = ttk.Combobox(dev_card_padding, textvariable=self.output_var, values=list(self.output_devices_dict.keys()), state="readonly")
        self.combo_output.pack(fill=tk.X, pady=(1, 8))
        self.combo_output.set("Default Output")

        # Volume slider (grouped with routing since it controls monitor output level)
        tk.Label(dev_card_padding, text="Monitor Volume", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(anchor=tk.W)
        self.slider_volume = tk.Scale(
            dev_card_padding, from_=0.0, to=3.0, resolution=0.1, orient=tk.HORIZONTAL,
            variable=self.volume_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
            highlightthickness=0, activebackground=COLOR_ACCENT, command=self.update_volume
        )
        self.slider_volume.pack(fill=tk.X, pady=(1, 4))
        self.slider_volume.set(1.0)

        # 2. Scrobble Configuration Card
        cfg_card = tk.Frame(bottom_panel, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        cfg_card.grid(row=0, column=1, sticky="nsew", padx=4, pady=0)
        
        cfg_card_padding = tk.Frame(cfg_card, bg=COLOR_CARD, padx=12, pady=10)
        cfg_card_padding.pack(fill=tk.BOTH, expand=True)

        lbl = tk.Label(cfg_card_padding, text="SCROBBLER CONFIGURATION", bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 9, "bold"))
        lbl.pack(anchor=tk.W, pady=(0, 8))

        # Sliders
        tk.Label(cfg_card_padding, text="Snippet Duration (seconds)", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(anchor=tk.W)
        self.slider_duration = tk.Scale(cfg_card_padding, from_=5, to=30, orient=tk.HORIZONTAL, variable=self.duration_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, highlightthickness=0, activebackground=COLOR_ACCENT)
        self.slider_duration.pack(fill=tk.X, pady=(1, 8))

        tk.Label(cfg_card_padding, text="Interval Between Checks (seconds)", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(anchor=tk.W)
        self.slider_interval = tk.Scale(cfg_card_padding, from_=15, to=120, orient=tk.HORIZONTAL, variable=self.interval_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, highlightthickness=0, activebackground=COLOR_ACCENT)
        self.slider_interval.pack(fill=tk.X, pady=(1, 8))

        # Dry Run
        self.chk_dry = tk.Checkbutton(
            cfg_card_padding, text="Dry Run Mode (No scrobbles)", 
            variable=self.dry_run_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
            selectcolor=COLOR_BG, activebackground=COLOR_CARD, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 8)
        )
        self.chk_dry.pack(anchor=tk.W, pady=(0, 2))

        # Skip on Silence
        self.chk_silence = tk.Checkbutton(
            cfg_card_padding, text="Skip Shazam on Silence (No sound)", 
            variable=self.skip_silence_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY,
            selectcolor=COLOR_BG, activebackground=COLOR_CARD, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 8)
        )
        self.chk_silence.pack(anchor=tk.W)

        # 3. Audio Equalizer Card
        eq_card = tk.Frame(bottom_panel, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        eq_card.grid(row=0, column=2, sticky="nsew", padx=4, pady=0)
        
        eq_card_padding = tk.Frame(eq_card, bg=COLOR_CARD, padx=12, pady=10)
        eq_card_padding.pack(fill=tk.BOTH, expand=True)

        lbl = tk.Label(eq_card_padding, text="AUDIO EQUALIZER (MONITOR)", bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 9, "bold"))
        lbl.pack(anchor=tk.W, pady=(0, 8))

        # Bass slider
        tk.Label(eq_card_padding, text="Bass Gain", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(anchor=tk.W)
        self.slider_bass = tk.Scale(
            eq_card_padding, from_=0.0, to=2.0, resolution=0.1, orient=tk.HORIZONTAL, 
            variable=self.bass_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, 
            highlightthickness=0, activebackground=COLOR_ACCENT, command=self.update_bass
        )
        self.slider_bass.pack(fill=tk.X, pady=(1, 8))
        self.slider_bass.set(1.0)

        # Treble slider
        tk.Label(eq_card_padding, text="Treble Gain", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(anchor=tk.W)
        self.slider_treble = tk.Scale(
            eq_card_padding, from_=0.0, to=2.0, resolution=0.1, orient=tk.HORIZONTAL, 
            variable=self.treble_var, bg=COLOR_CARD, fg=COLOR_TEXT_PRIMARY, 
            highlightthickness=0, activebackground=COLOR_ACCENT, command=self.update_treble
        )
        self.slider_treble.pack(fill=tk.X, pady=(1, 8))
        self.slider_treble.set(1.0)



        # 4. Action Buttons Card
        btn_card = tk.Frame(bottom_panel, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        btn_card.grid(row=0, column=3, sticky="nsew", padx=(4, 0), pady=0)

        
        btn_card_padding = tk.Frame(btn_card, bg=COLOR_CARD, padx=12, pady=10)
        btn_card_padding.pack(fill=tk.BOTH, expand=True)

        self.btn_toggle = tk.Button(
            btn_card_padding, text="START SCROBBLER", bg=COLOR_ACCENT, fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_ACCENT_HOVER, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9, "bold"), bd=0, pady=6, cursor="hand2", command=self.toggle_scrobbler
        )
        self.btn_toggle.pack(fill=tk.X, pady=3)

        self.btn_test = tk.Button(
            btn_card_padding, text="TEST INPUT LEVELS", bg=COLOR_BORDER, fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_CARD, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9), bd=0, pady=5, cursor="hand2", command=self.test_levels
        )
        self.btn_test.pack(fill=tk.X, pady=3)

        self.btn_playback = tk.Button(
            btn_card_padding, text="TEST PLAYBACK (LOOPBACK)", bg=COLOR_BORDER, fg=COLOR_TEXT_PRIMARY,
            activebackground=COLOR_CARD, activeforeground=COLOR_TEXT_PRIMARY,
            font=("Helvetica", 9), bd=0, pady=5, cursor="hand2", command=self.toggle_playback_test
        )
        self.btn_playback.pack(fill=tk.X, pady=3)

        # Signal Meter Frame
        meter_frame = tk.Frame(btn_card_padding, bg=COLOR_CARD)
        meter_frame.pack(fill=tk.X, pady=(6, 0))
        tk.Label(meter_frame, text="Signal:", bg=COLOR_CARD, fg=COLOR_TEXT_SECONDARY, font=("Helvetica", 8)).pack(side=tk.LEFT)
        
        self.meter_canvas = tk.Canvas(meter_frame, height=12, bg=COLOR_BG, highlightthickness=0)
        self.meter_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        self.draw_level_meter(0)

        # Toggle monitor states on startup
        self.on_input_device_changed(None)

    def toggle_monitor_fields(self):
        """Enables/disables the output device selection based on monitor checkbox state."""
        if self.monitor_var.get():
            self.combo_output.configure(state="readonly")
            self.lbl_output.configure(fg=COLOR_TEXT_SECONDARY)
        else:
            self.combo_output.configure(state="disabled")
            self.lbl_output.configure(fg="#555555")

    def update_bass(self, val):
        """Updates the active bass frequency gain value."""
        self.bass_gain = float(val)
        if self.recorder is not None:
            self.recorder.bass_gain = self.bass_gain

    def update_treble(self, val):
        """Updates the active treble frequency gain value."""
        self.treble_gain = float(val)
        if self.recorder is not None:
            self.recorder.treble_gain = self.treble_gain

    def update_volume(self, val):
        """Updates the active monitor playback volume multiplier."""
        self.volume_multiplier = float(val)
        if self.recorder is not None:
            self.recorder.volume_multiplier = self.volume_multiplier

    def draw_status_dot(self, color):
        """Draws a status dot indicating if scrobbling loop is active."""
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 13, 13, fill=color, outline="")

    def draw_default_art(self):
        """Draws a nice abstract cover art placeholder."""
        img = Image.new("RGB", (190, 190), "#333333")
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 180, 180], outline="#444444", width=3)
        draw.line([40, 95, 150, 95], fill=COLOR_TEXT_SECONDARY, width=4)
        draw.line([65, 60, 65, 130], fill=COLOR_TEXT_SECONDARY, width=4)
        draw.line([115, 60, 115, 130], fill=COLOR_TEXT_SECONDARY, width=4)
        
        photo = ImageTk.PhotoImage(img)
        self.art_label.config(image=photo)
        self.art_label.image = photo  # Keep reference

    def draw_level_meter(self, level_ratio):
        """Draws the signal amplitude indicator bar."""
        self.meter_canvas.delete("all")
        w = self.meter_canvas.winfo_width()
        if w < 10:
            w = 150  # Fallback initial width
        
        h = 12
        self.meter_canvas.create_rectangle(0, 0, w, h, fill="#222222", outline="")
        
        if level_ratio > 0:
            meter_w = int(w * min(level_ratio, 1.0))
            if level_ratio < 0.005:
                color = "#FFA500"  # Orange (too low)
            elif level_ratio < 0.8:
                color = COLOR_ACCENT  # Green (healthy)
            else:
                color = COLOR_STOP   # Red (clipping)
                
            self.meter_canvas.create_rectangle(0, 0, meter_w, h, fill=color, outline="")

    def log(self, text):
        """Appends formatted messages to the console log widget."""
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def toggle_scrobbler(self):
        """Toggles the background scrobbler thread on and off."""
        if not self.is_running:
            self.start_scrobbler()
        else:
            self.stop_scrobbler()

    def start_scrobbler(self):
        """Configures and runs the scrobbler process in a background thread."""
        self.is_running = True
        self.btn_toggle.config(text="STOP SCROBBLER", bg=COLOR_STOP, activebackground="#FF453A")
        self.draw_status_dot(COLOR_ACCENT)
        self.status_text.config(text="Active", foreground=COLOR_ACCENT)
        self.log("Starting Scrobbler...")

        # Disable configuration settings during operation
        self.combo_input.config(state="disabled")
        self.combo_output.config(state="disabled")
        self.chk_monitor.config(state="disabled")
        self.chk_dry.config(state="disabled")
        self.chk_silence.config(state="disabled")
        self.slider_duration.config(state="disabled")
        self.slider_interval.config(state="disabled")
        self.btn_test.config(state="disabled")
        self.btn_autodetect.config(state="disabled")


        # Resolve selected device indices
        in_device_key = self.combo_input.get()
        out_device_key = self.combo_output.get()
        in_device = self.input_devices_dict.get(in_device_key, None)
        out_device = self.output_devices_dict.get(out_device_key, None)

        # Set or clear env vars for system loopback
        self.set_device_env_vars()
        
        is_loopback = isinstance(in_device, str)
        if is_loopback:
            in_device = self.resolve_pulse_device()

        monitor = self.monitor_var.get()
        if is_loopback:
            monitor = False  # No playback needed for system loopback/monitor sources

        dry_run = self.dry_run_var.get()
        skip_silence = self.skip_silence_var.get()
        silence_threshold = self.silence_threshold_var.get()
        duration = self.duration_var.get()
        interval = self.interval_var.get()

        self.recorder = None
        with self.stream_lock:
            self.stream = None

        if monitor:
            self.log("[Monitor] Setting up pass-through...")
            if out_device is None:
                try:
                    out_device = sd.default.device[1]
                except Exception:
                    out_device = 0
            
            self.recorder = AudioRecorderMonitor()
            self.recorder.bass_gain = self.bass_gain
            self.recorder.treble_gain = self.treble_gain
            self.recorder.volume_multiplier = self.volume_multiplier
            try:
                # Use blocksize=2048 to significantly reduce callback frequency/overhead
                self.stream = sd.Stream(
                    device=(in_device, out_device),
                    samplerate=DEFAULT_SAMPLE_RATE,
                    channels=(1, 2),
                    blocksize=2048,
                    callback=self.recorder.callback
                )
                self.stream.start()
                self.log("[Monitor] Passive audio routing active.")
            except Exception as stream_err:
                self.log(f"[Monitor Error] Could not start: {stream_err}")
                self.recorder = None
                with self.stream_lock:
                    self.stream = None
        else:
            self.log("[Monitor] Playback disabled. Setting up input-only recording...")
            self.recorder = AudioRecorderMonitor()
            self.recorder.bass_gain = self.bass_gain
            self.recorder.treble_gain = self.treble_gain
            self.recorder.volume_multiplier = self.volume_multiplier
            try:
                self.stream = sd.InputStream(
                    device=in_device,
                    samplerate=DEFAULT_SAMPLE_RATE,
                    channels=1,
                    blocksize=2048,
                    callback=self.recorder.input_callback
                )
                self.stream.start()
                self.log("[Monitor] Input-only stream active.")
            except Exception as stream_err:
                self.log(f"[Audio Error] Could not start input stream: {stream_err}")
                self.recorder = None
                with self.stream_lock:
                    self.stream = None

        # Start event loop in thread
        self.loop = asyncio.new_event_loop()
        self.scrobble_thread = threading.Thread(
            target=self.run_async_loop,
            args=(self.loop, in_device, out_device, duration, interval, dry_run, monitor, skip_silence, silence_threshold),
            daemon=True
        )
        self.scrobble_thread.start()

    def stop_scrobbler(self):
        """Instructs the background loop to stop and cleans up UI."""
        self.is_running = False
        self.log("Stopping Scrobbler thread...")
        
        # Stop and close the audio playback stream immediately if active
        with self.stream_lock:
            if hasattr(self, 'stream') and self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                    self.log("[Monitor] Audio playback stream stopped.")
                except Exception as e:
                    self.log(f"[Monitor Error] Error stopping stream: {e}")
                self.stream = None

        # Stop the asyncio event loop
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        # Restore UI inputs
        self.btn_toggle.config(text="START SCROBBLER", bg=COLOR_ACCENT, activebackground=COLOR_ACCENT_HOVER)
        self.draw_status_dot(COLOR_STOP)
        self.status_text.config(text="Stopped", foreground=COLOR_TEXT_SECONDARY)
        
        self.combo_input.config(state="readonly")
        self.on_input_device_changed(None)
        self.chk_dry.config(state="normal")
        self.chk_silence.config(state="normal")
        self.slider_duration.config(state="normal")
        self.slider_interval.config(state="normal")
        self.btn_test.config(state="normal")
        self.btn_autodetect.config(state="normal")

        
        self.draw_level_meter(0)

    def run_async_loop(self, loop, in_dev, out_dev, duration, interval, dry_run, monitor, skip_silence, silence_threshold):
        """Target run method for background thread running asyncio."""
        asyncio.set_event_loop(loop)
        
        # Run our scrobbler coroutine
        task = loop.create_task(self.scrobble_coro(in_dev, out_dev, duration, interval, dry_run, monitor, skip_silence, silence_threshold))
        try:
            loop.run_forever()
        finally:
            # Cancel the main task and run the loop until it is cleanly canceled
            task.cancel()
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[Loop Cleanup Error] {e}", file=sys.stderr)
            loop.close()

    async def scrobble_coro(self, in_dev, out_dev, duration, interval, dry_run, monitor, skip_silence, silence_threshold):
        """Asynchronous execution loop mirroring CLI recognizer structure."""
        loop = asyncio.get_running_loop()
        try:
            shazam = Shazam()
            lastfm_network = None
            
            if not dry_run:
                self.msg_queue.put({"type": "log", "text": "[Last.fm] Authenticating..."})
                # init_lastfm is blocking (network) — run in thread pool
                lastfm_network = await loop.run_in_executor(None, init_lastfm)
                if lastfm_network is None:
                    self.msg_queue.put({"type": "log", "text": "[Last.fm] Authentication failed. Forcing Dry-Run."})
                    dry_run = True

            # Use the recorder created on the main thread
            recorder = self.recorder

            last_artist = None
            last_title = None
            last_scrobble_time = 0.0
            current_song_duration = DEFAULT_SONG_DURATION_FALLBACK

            while self.is_running:
                self.msg_queue.put({"type": "log", "text": f"Recording {duration}s snippet..."})
                self.msg_queue.put({"type": "status_change", "text": "Recording..."})
                
                is_silent = False
                recording = np.zeros((0, 1), dtype='float32')

                if recorder is not None:
                    recorder.start_recording()
                    
                    # If silence detection is enabled, probe for 1.5 seconds first
                    probe_duration = 1.5 if skip_silence else duration
                    probe_steps = int(min(probe_duration, duration) * 10)
                    
                    for _ in range(probe_steps):
                        if not self.is_running:
                            break
                        await asyncio.sleep(0.1)
                        chunk = None
                        with recorder.lock:
                            if recorder.recorded_chunks:
                                chunk = recorder.recorded_chunks[-1]
                        if chunk is not None:
                            max_val = np.max(np.abs(chunk)) if len(chunk) > 0 else 0
                            self.msg_queue.put({"type": "level", "ratio": float(max_val)})
                    
                    # Check probe recording for early silence exit
                    if skip_silence and self.is_running:
                        probe_recording = recorder.get_current_recording()
                        if len(probe_recording) > 0:
                            probe_rms = np.sqrt(np.mean(probe_recording**2))
                            probe_peak = np.max(np.abs(probe_recording))
                            if probe_rms < silence_threshold and probe_peak < (silence_threshold * 2.5):
                                is_silent = True
                                recording = recorder.stop_recording()
                                self.msg_queue.put({"type": "level", "ratio": float(probe_peak)})

                    # If sound was detected during probe, record remaining duration
                    if not is_silent and self.is_running:
                        remaining_steps = int(duration * 10) - probe_steps
                        for _ in range(remaining_steps):
                            if not self.is_running:
                                break
                            await asyncio.sleep(0.1)
                            chunk = None
                            with recorder.lock:
                                if recorder.recorded_chunks:
                                    chunk = recorder.recorded_chunks[-1]
                            if chunk is not None:
                                max_val = np.max(np.abs(chunk)) if len(chunk) > 0 else 0
                                self.msg_queue.put({"type": "level", "ratio": float(max_val)})
                        recording = recorder.stop_recording()

                else:
                    try:
                        recording = await loop.run_in_executor(
                            None, record_audio, in_dev, duration, DEFAULT_SAMPLE_RATE
                        )
                    except Exception as record_err:
                        self.msg_queue.put({
                            "type": "log", 
                            "text": f"⚠️ [Audio Error] Recording crashed: {record_err}"
                        })
                        recording = np.zeros((0, 1), dtype='float32')
                    max_val = np.max(np.abs(recording)) if len(recording) > 0 else 0
                    self.msg_queue.put({"type": "level", "ratio": float(max_val)})

                if not self.is_running:
                    break

                if len(recording) == 0:
                    self.msg_queue.put({"type": "log", "text": "⚠️ Recording failed or empty."})
                    await asyncio.sleep(2)
                    continue

                # Final silence check on recording snippet
                rms_val = np.sqrt(np.mean(recording**2)) if len(recording) > 0 else 0.0
                peak_val = np.max(np.abs(recording)) if len(recording) > 0 else 0.0

                if skip_silence and (is_silent or (rms_val < silence_threshold and peak_val < (silence_threshold * 2.5))):
                    self.msg_queue.put({
                        "type": "log",
                        "text": f"🔇 Silence detected (RMS: {rms_val:.5f}, Peak: {peak_val:.5f} < Threshold {silence_threshold:.4f}). Skipping Shazam API call."
                    })
                    self.msg_queue.put({"type": "status_change", "text": "Silence / No sound"})
                    self.msg_queue.put({"type": "level", "ratio": float(peak_val)})
                    self.msg_queue.put({"type": "no_match"})

                    # Extend the duplicate-guard window so it doesn't expire during silence.
                    # Without this, a long quiet gap would push elapsed > current_song_duration
                    # and cause the same track to be re-scrobbled when music resumes.
                    if last_artist is not None:
                        last_scrobble_time = time.time()

                    self.msg_queue.put({"type": "status_change", "text": "Sleeping (Silence)..."})
                    for _ in range(int(interval)):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
                    continue


                self.msg_queue.put({"type": "status_change", "text": "Identifying track..."})
                self.msg_queue.put({"type": "level", "ratio": 0.0})

                # np_array_to_wav_bytes calls sf.write which is CPU/IO — run in executor
                try:
                    audio_bytes = await loop.run_in_executor(
                        None, np_array_to_wav_bytes, recording, DEFAULT_SAMPLE_RATE
                    )
                except Exception as wav_err:
                    self.msg_queue.put({"type": "log", "text": f"⚠️ [WAV Error] {wav_err}"})
                    continue

                try:
                    match = await recognize_song(shazam, audio_bytes)
                except Exception as recognize_err:
                    self.msg_queue.put({
                        "type": "log", 
                        "text": f"⚠️ [Shazam Error] Recognition crashed: {recognize_err}"
                    })
                    match = None

                if not self.is_running:
                    break
                
                if match:
                    artist, title, artwork_url = match
                    self.msg_queue.put({"type": "log", "text": f"🎵 MATCH: {artist} - {title}"})
                    
                    now = time.time()
                    elapsed = now - last_scrobble_time
                    is_duplicate = (artist == last_artist and title == last_title)

                    if is_duplicate and elapsed < current_song_duration:
                        remaining = current_song_duration - elapsed
                        self.msg_queue.put({
                            "type": "log", 
                            "text": f"👉 Duplicate track still playing ({int(remaining)}s left). Skipping."
                        })
                        self.msg_queue.put({
                            "type": "status_change", 
                            "text": f"Playing: {artist} - {title} (Scrobbled)"
                        })
                    else:
                        current_song_duration = DEFAULT_SONG_DURATION_FALLBACK
                        
                        # Download artwork bytes in executor to avoid blocking
                        art_bytes = None
                        if artwork_url:
                            try:
                                def download_art(url=artwork_url):
                                    import urllib.request
                                    with urllib.request.urlopen(url, timeout=10) as response:
                                        return response.read()
                                art_bytes = await loop.run_in_executor(None, download_art)
                            except Exception:
                                pass

                        self.msg_queue.put({
                            "type": "match",
                            "artist": artist,
                            "title": title,
                            "art_bytes": art_bytes,
                            "scrobbled": not dry_run
                        })

                        if not dry_run and lastfm_network:
                            try:
                                self.msg_queue.put({"type": "log", "text": f"[Last.fm] Scrobbling track..."})
                                def _scrobble(a=artist, t=title, ts=int(now)):
                                    lastfm_network.scrobble(artist=a, title=t, timestamp=ts)
                                await loop.run_in_executor(None, _scrobble)
                                self.msg_queue.put({"type": "log", "text": "[Last.fm] Scrobble successful!"})
                            except Exception as scrobble_err:
                                self.msg_queue.put({"type": "log", "text": f"[Last.fm Error] Scrobble failed: {scrobble_err}"})

                        last_artist = artist
                        last_title = title
                        last_scrobble_time = now
                else:
                    self.msg_queue.put({"type": "log", "text": "🔇 No match found."})
                    self.msg_queue.put({"type": "status_change", "text": "Listening... (No Match)"})
                    self.msg_queue.put({"type": "no_match"})

                self.msg_queue.put({"type": "status_change", "text": "Sleeping..."})
                for _ in range(int(interval)):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.msg_queue.put({"type": "log", "text": f"❌ CRITICAL LOOP ERROR:\n{tb}"})
        finally:
            # Safely delegate stream clean up to the main thread via the message queue
            self.msg_queue.put({"type": "cleanup_stream"})


    def test_levels(self):
        """Records 3 seconds of audio to verify input signal strength."""
        self.btn_test.config(state="disabled")
        self.btn_toggle.config(state="disabled")
        self.log("Testing audio levels for 3 seconds...")
        
        self.set_device_env_vars()
        in_device_key = self.combo_input.get()
        in_device = self.input_devices_dict.get(in_device_key, None)
        if isinstance(in_device, str):
            in_device = self.resolve_pulse_device()

        def worker():
            try:
                fs = DEFAULT_SAMPLE_RATE
                rec = sd.rec(int(3.0 * fs), samplerate=fs, channels=1, device=in_device, dtype='float32')
                for idx in range(30):
                    time.sleep(0.1)
                    if idx < len(rec):
                        slice_idx = int((idx / 30.0) * len(rec))
                        level = np.max(np.abs(rec[:slice_idx])) if slice_idx > 0 else 0
                        self.msg_queue.put({"type": "level", "ratio": float(level)})
                
                sd.wait()
                max_val = np.max(np.abs(rec))
                rms_val = np.sqrt(np.mean(rec**2))
                self.msg_queue.put({"type": "level_test_done", "max": float(max_val), "rms": float(rms_val)})
            except Exception as err:
                self.msg_queue.put({"type": "level_test_error", "err": str(err)})

        threading.Thread(target=worker, daemon=True).start()

    def toggle_playback_test(self):
        """Toggles real-time playback (loopback) monitoring on/off manually for testing."""
        if hasattr(self, 'playback_test_stream') and self.playback_test_stream is not None:
            self.stop_playback_test()
        else:
            self.start_playback_test()

    def start_playback_test(self):
        """Opens stream to route input to output and display levels dynamically."""
        in_device_key = self.combo_input.get()
        out_device_key = self.combo_output.get()
        in_device = self.input_devices_dict.get(in_device_key, None)
        out_device = self.output_devices_dict.get(out_device_key, None)

        if isinstance(in_device, str):
            self.log("[Test] Playback loopback test disabled for system audio.")
            self.stop_playback_test()
            return

        self.log("Starting playback loopback test...")
        self.btn_playback.config(text="STOP PLAYBACK TEST", bg=COLOR_STOP, activebackground="#FF453A")
        self.btn_toggle.config(state="disabled")
        self.btn_test.config(state="disabled")

        if out_device is None:
            try:
                out_device = sd.default.device[1]
            except Exception:
                out_device = 0

        # We need a callback to copy input to output and visualize levels
        def callback(indata, outdata, frames, time, status):
            bass_gain = self.bass_gain
            treble_gain = self.treble_gain
            volume = self.volume_multiplier
            
            mono_in = indata[:, 0]
            if bass_gain == 1.0 and treble_gain == 1.0:
                mixed = mono_in
            else:
                if len(mono_in) >= self.N:
                    bass_comp = np.convolve(mono_in, self.kernel, mode='same')
                    treble_comp = mono_in - bass_comp
                    mixed = bass_comp * bass_gain + treble_comp * treble_gain
                else:
                    mixed = mono_in

            mixed = np.clip(mixed * volume, -1.0, 1.0)
            outdata[:, 0] = mixed
            outdata[:, 1] = mixed
            max_val = np.max(np.abs(mixed)) if len(mixed) > 0 else 0
            self.msg_queue.put({"type": "level", "ratio": float(max_val)})

        try:
            self.playback_test_stream = sd.Stream(
                device=(in_device, out_device),
                samplerate=DEFAULT_SAMPLE_RATE,
                channels=(1, 2),
                blocksize=2048,
                callback=callback
            )
            self.playback_test_stream.start()
            self.log("[Test] Loopback playback active. You should hear aux audio now.")
        except Exception as err:
            self.log(f"[Test Error] Could not start playback: {err}")
            self.stop_playback_test()

    def stop_playback_test(self):
        """Stops and closes the manual test stream."""
        self.log("Stopping playback loopback test...")
        if hasattr(self, 'playback_test_stream') and self.playback_test_stream is not None:
            try:
                self.playback_test_stream.stop()
                self.playback_test_stream.close()
            except Exception:
                pass
            self.playback_test_stream = None

        self.btn_playback.config(text="TEST PLAYBACK (LOOPBACK)", bg=COLOR_BORDER, activebackground=COLOR_CARD)
        self.btn_toggle.config(state="normal")
        self.btn_test.config(state="normal")
        self.draw_level_meter(0)

    def poll_queue(self):
        """Pulls and processes messages from the background thread Queue."""
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                msg_type = msg.get("type")
                
                if msg_type == "log":
                    self.log(msg["text"])
                    
                elif msg_type == "cleanup_stream":
                    with self.stream_lock:
                        if hasattr(self, 'stream') and self.stream is not None:
                            try:
                                self.stream.stop()
                                self.stream.close()
                                self.log("[Monitor] Audio playback stream stopped via cleanup.")
                            except Exception:
                                pass
                            self.stream = None
                    
                elif msg_type == "status_change":
                    self.status_text.config(text=msg["text"])
                    
                elif msg_type == "level":
                    self.draw_level_meter(msg["ratio"])
                    
                elif msg_type == "level_test_done":
                    max_val = msg["max"]
                    rms_val = msg["rms"]
                    self.log(f"Level Test Done: Max Amplitude={max_val:.6f}, RMS={rms_val:.6f}")
                    
                    if max_val < 0.001:
                        messagebox.showwarning("Silence Detected", "The input is completely silent!\nCheck your connection or system volume.")
                    elif max_val < 0.05:
                        messagebox.showwarning("Low Level", "Audio signal is very weak.\nConsider increasing the radio's volume.")
                    else:
                        messagebox.showinfo("Input Healthy", "Success! Sound detected at a healthy volume.")
                    
                    self.btn_test.config(state="normal")
                    self.btn_toggle.config(state="normal")
                    self.draw_level_meter(0)
                    
                elif msg_type == "level_test_error":
                    self.log(f"Level Test Error: {msg['err']}")
                    self.btn_test.config(state="normal")
                    self.btn_toggle.config(state="normal")
                    
                elif msg_type == "no_match":
                    self.lbl_song_title.config(text="Listening...", fg=COLOR_TEXT_PRIMARY)
                    self.lbl_song_artist.config(text="No match found yet (talking, silence, or static)", fg=COLOR_TEXT_SECONDARY)
                    self.lbl_scrobble_status.config(text="")
                    
                elif msg_type == "match":
                    artist = msg["artist"]
                    title = msg["title"]
                    art_bytes = msg["art_bytes"]
                    scrobbled = msg["scrobbled"]
                    
                    self.lbl_song_title.config(text=title, fg=COLOR_TEXT_PRIMARY)
                    self.lbl_song_artist.config(text=artist, fg=COLOR_TEXT_SECONDARY)
                    
                    if scrobbled:
                        self.lbl_scrobble_status.config(text="✓ Scrobbled to Last.fm", fg=COLOR_ACCENT)
                    else:
                        self.lbl_scrobble_status.config(text="✓ Recognized (Dry Run - Not Scrobbled)", fg="#FFA500")

                    if art_bytes:
                        try:
                            img = Image.open(io.BytesIO(art_bytes))
                            img = img.resize((190, 190), Image.Resampling.LANCZOS)
                            photo = ImageTk.PhotoImage(img)
                            self.art_label.config(image=photo)
                            self.art_label.image = photo
                        except Exception as e:
                            self.log(f"Error loading cover art image: {e}")
                            self.draw_default_art()
                    else:
                        self.draw_default_art()

        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.poll_queue)


def main():
    root = tk.Tk()
    root.configure(bg=COLOR_BG)
    app = ScrobblerApp(root)
    
    def on_closing():
        if app.is_running:
            app.stop_scrobbler()
        if hasattr(app, 'playback_test_stream') and app.playback_test_stream is not None:
            try:
                app.playback_test_stream.stop()
                app.playback_test_stream.close()
            except Exception:
                pass
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
