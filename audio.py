#!/usr/bin/env python3
"""
Shared audio recording and loopback monitoring utilities.

``AudioRecorderMonitor`` is the single implementation used by both the CLI
(``recognizer.py``) and the GUI (``gui.py``).  EQ and volume controls default
to unity gain so the class is a simple pass-through unless you explicitly
adjust them.
"""

import sys
import threading

import numpy as np


class AudioRecorderMonitor:
    """
    Real-time audio loopback monitor with optional bass/treble EQ, volume
    control, and background snippet recording.

    Supports two PortAudio stream modes:

    - **Duplex** (``sd.Stream``): routes input to output with optional EQ.
      Pass ``callback`` as the stream's callback.
    - **Input-only** (``sd.InputStream``): records without any playback.
      Pass ``input_callback`` as the stream's callback.

    ``bass_gain``, ``treble_gain``, and ``volume_multiplier`` are plain
    attributes that can be safely updated from any thread at any time —
    changes take effect on the very next audio callback frame.
    """

    def __init__(self, eq_window: int = 50) -> None:
        """
        Args:
            eq_window: Moving-average filter length used for the bass/treble
                       frequency split.  Larger values lower the crossover
                       frequency.  Default (50 samples @ 44 100 Hz ≈ 882 Hz).
        """
        self.is_recording: bool = False
        self.recorded_chunks: list = []
        self.lock = threading.Lock()

        # EQ / volume — unity by default (no processing)
        self.bass_gain: float = 1.0
        self.treble_gain: float = 1.0
        self.volume_multiplier: float = 1.0

        # Pre-compute moving-average kernel for bass/treble split
        self.N: int = eq_window
        self.kernel: np.ndarray = np.ones(eq_window, dtype="float32") / eq_window

    # ------------------------------------------------------------------
    # PortAudio callbacks
    # ------------------------------------------------------------------

    def callback(
        self,
        indata: np.ndarray,
        outdata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """Duplex callback: apply EQ + volume and route mono in → stereo out."""
        if status:
            try:
                sys.stderr.write(f"[Audio Status] {status}\n")
                sys.stderr.flush()
            except Exception:
                pass

        try:
            bass_gain = self.bass_gain
            treble_gain = self.treble_gain
            volume = self.volume_multiplier

            mono_in = indata[:, 0]

            if bass_gain == 1.0 and treble_gain == 1.0:
                # Fast path: bypass EQ entirely
                mixed = mono_in
            elif len(mono_in) >= self.N:
                bass_comp = np.convolve(mono_in, self.kernel, mode="same")
                treble_comp = mono_in - bass_comp
                mixed = bass_comp * bass_gain + treble_comp * treble_gain
            else:
                # Block too small for the filter — pass through
                mixed = mono_in

            mixed = np.clip(mixed * volume, -1.0, 1.0)

            # Mono → stereo output
            outdata[:, 0] = mixed
            outdata[:, 1] = mixed

            if self.is_recording:
                with self.lock:
                    self.recorded_chunks.append(indata.copy())

        except Exception as exc:
            try:
                sys.stderr.write(f"[Callback Error] {exc}\n")
                sys.stderr.flush()
            except Exception:
                pass
            outdata.fill(0)

    def input_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """Input-only callback: record audio without producing any output."""
        if status:
            try:
                sys.stderr.write(f"[Audio Status] {status}\n")
                sys.stderr.flush()
            except Exception:
                pass

        try:
            if self.is_recording:
                with self.lock:
                    self.recorded_chunks.append(indata.copy())
        except Exception as exc:
            try:
                sys.stderr.write(f"[Callback Error] {exc}\n")
                sys.stderr.flush()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Recording control
    # ------------------------------------------------------------------

    def start_recording(self) -> None:
        """Begin accumulating audio frames from the active callback."""
        with self.lock:
            self.recorded_chunks = []
            self.is_recording = True

    def stop_recording(self) -> np.ndarray:
        """Stop accumulating and return all recorded frames as a numpy array."""
        with self.lock:
            self.is_recording = False
            chunks = self.recorded_chunks
            self.recorded_chunks = []

        if not chunks:
            return np.zeros((0, 1), dtype="float32")
        return np.concatenate(chunks, axis=0)

    def get_current_recording(self) -> np.ndarray:
        """Return accumulated frames so far without stopping the recording."""
        with self.lock:
            if not self.recorded_chunks:
                return np.zeros((0, 1), dtype="float32")
            return np.concatenate(self.recorded_chunks, axis=0)
