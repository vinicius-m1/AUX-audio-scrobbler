#!/usr/bin/env python3
import asyncio
import argparse
import io
import os
import sys
import time
import threading
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from shazamio import Shazam
import pylast

from audio import AudioRecorderMonitor
from pathlib import Path

# Safe print wrapper to prevent crashes in non-terminal environments or encoding mismatches
_print = print
def safe_print(*args, **kwargs):
    try:
        _print(*args, **kwargs)
    except Exception:
        pass

print = safe_print

# Load credentials — XDG config first (AppImage / installed users), local .env overrides (devs)
CREDENTIALS_PATH = Path.home() / ".config" / "aux-audio-scrobbler" / "credentials.env"


def load_credentials() -> None:
    """Loads credentials from XDG config, and local .env when not running as an AppImage."""
    if CREDENTIALS_PATH.exists():
        load_dotenv(CREDENTIALS_PATH, override=True)
    if not os.getenv("APPIMAGE"):
        load_dotenv(override=True)  # Local .env takes priority for developers


load_credentials()

DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHANNELS = 1
DEFAULT_RECORD_DURATION = 10  # Seconds of audio to record for Shazam
DEFAULT_CHECK_INTERVAL = 70    # Seconds to sleep between recognition checks
DEFAULT_SONG_DURATION_FALLBACK = 240  # 4 minutes if Last.fm doesn't know the duration





def list_devices():
    """Lists all available audio input and output devices separately."""
    devices = sd.query_devices()
    
    print("=" * 60)
    print("AVAILABLE INPUT DEVICES (Line-in, Aux, Mics)")
    print("=" * 60)
    for idx, dev in enumerate(devices):
        max_in = dev.get("max_input_channels", 0)
        if max_in > 0:
            default_str = " (default)" if idx == sd.default.device[0] else ""
            print(f"Index {idx:2d}: {dev['name']}{default_str}")
            print(f"          Input Channels: {max_in} | Default Rate: {dev['default_samplerate']} Hz")
            print("-" * 60)

    print("\n" + "=" * 60)
    print("AVAILABLE OUTPUT DEVICES (Headphones, Speakers)")
    print("=" * 60)
    for idx, dev in enumerate(devices):
        max_out = dev.get("max_output_channels", 0)
        if max_out > 0:
            default_str = " (default)" if idx == sd.default.device[1] else ""
            print(f"Index {idx:2d}: {dev['name']}{default_str}")
            print(f"          Output Channels: {max_out} | Default Rate: {dev['default_samplerate']} Hz")
            print("-" * 60)


def init_lastfm() -> Optional[pylast.LastFMNetwork]:
    """Initializes the Last.fm network using the current environment credentials.

    Reads directly from ``os.environ`` so that credentials saved at runtime
    (e.g. via the GUI settings dialog) are picked up without a restart.
    """
    api_key    = os.getenv("LASTFM_API_KEY")
    api_secret = os.getenv("LASTFM_API_SECRET")
    username   = os.getenv("LASTFM_USERNAME")
    password   = os.getenv("LASTFM_PASSWORD")
    session_key = os.getenv("LASTFM_SESSION_KEY")

    if not api_key or not api_secret:
        print("[Last.fm] API Key or Secret not found. Scrobbling is disabled.")
        print("[Last.fm] Open Settings (⚙) in the GUI, or add credentials to .env.")
        return None

    try:
        if session_key:
            print(f"[Last.fm] Authenticating via session key for user: {username or 'unknown'}")
            return pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                session_key=session_key,
            )
        elif username and password:
            print(f"[Last.fm] Authenticating via password for user: {username}")
            return pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                username=username,
                password_hash=pylast.md5(password),
            )
        else:
            print("[Last.fm] Insufficient credentials. Provide either a session key or username + password.")
            return None
    except Exception as e:
        print(f"[Last.fm] Authentication failed: {e}")
        return None






def record_audio(device_idx: Optional[int], duration: int, sample_rate: int) -> np.ndarray:
    """Records audio from the specified input device (standard recording fallback)."""
    print(f"\nRecording {duration}s of audio from input device {device_idx if device_idx is not None else 'Default'}...")
    recording = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=DEFAULT_CHANNELS,
        device=device_idx,
        dtype='float32'
    )
    sd.wait()  # Wait until the recording finishes
    print("Recording finished.")
    return recording


def np_array_to_wav_bytes(recording: np.ndarray, sample_rate: int) -> bytes:
    """Encodes a numpy array recording into in-memory WAV bytes."""
    buffer = io.BytesIO()
    buffer.name = "audio.wav"
    sf.write(buffer, recording, sample_rate, format='WAV')
    return buffer.getvalue()


def check_input_levels(device_idx: Optional[int], duration: float = 3.0, sample_rate: int = DEFAULT_SAMPLE_RATE) -> dict:
    """Records a short clip and returns amplitude diagnostics.

    Returns a dict with keys: ``max``, ``mean``, ``rms`` (all float).
    Prints a human-readable summary to stdout.
    """
    print(f"Recording {duration}s from device {device_idx if device_idx is not None else 'Default'} to check audio levels...")
    try:
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            device=device_idx,
            dtype='float32'
        )
        sd.wait()

        max_val = float(np.max(np.abs(recording)))
        mean_val = float(np.mean(np.abs(recording)))
        rms_val = float(np.sqrt(np.mean(recording ** 2)))

        print("\n--- Audio Level Results ---")
        print(f"Max Amplitude : {max_val:.6f}")
        print(f"Mean Amplitude: {mean_val:.6f}")
        print(f"RMS Level     : {rms_val:.6f}")

        if max_val < 0.001:
            print("\n⚠️  WARNING: Input is almost completely silent.")
            print("   Check: aux cable connected? radio volume up? input unmuted in pavucontrol/alsamixer?")
        elif max_val < 0.05:
            print("\n⚠️  WARNING: Signal is very weak. Try increasing the radio's output volume.")
        else:
            print("\n✅  SUCCESS: Audio input detected at a healthy level.")

        return {"max": max_val, "mean": mean_val, "rms": rms_val}

    except Exception as e:
        print(f"Error checking device: {e}")
        return {"max": 0.0, "mean": 0.0, "rms": 0.0}


async def recognize_song(shazam: Shazam, audio_bytes: bytes) -> Optional[Tuple[str, str, Optional[str]]]:
    """Sends audio bytes to Shazam to identify the track."""
    print("Sending audio to Shazam API...")
    try:
        result = await shazam.recognize(audio_bytes)
        
        # Check if a track was matched
        if result and "track" in result:
            track = result["track"]
            title = track.get("title")
            artist = track.get("subtitle")  # Shazam stores artist name under 'subtitle'
            artwork_url = track.get("images", {}).get("coverart")
            if title and artist:
                return artist, title, artwork_url
        
        # No track matching
        return None
    except Exception as e:
        print(f"[Shazam] Error recognizing audio: {e}")
        return None


async def run_scrobbler(
    device_idx: Optional[int],
    duration: int,
    check_interval: int,
    sample_rate: int,
    dry_run: bool,
    monitor: bool,
    output_device_idx: Optional[int]
):
    """Main scrobbler loop with optional loopback audio monitoring."""
    shazam = Shazam()
    
    # Initialize Last.fm client
    lastfm_network = None
    if not dry_run:
        lastfm_network = init_lastfm()
    else:
        print("[Mode] DRY RUN: Track recognition only, scrobbling is disabled.")

    # Configure monitoring stream if requested
    recorder = None
    stream = None
    if monitor:
        # Resolve output device
        try:
            out_device = output_device_idx if output_device_idx is not None else sd.default.device[1]
            if out_device == -1 or out_device is None:
                out_device = sd.query_hostapis()[0]['default_output_device']
        except Exception as device_err:
            print(f"[Monitor] Error finding default output device: {device_err}")
            print("[Monitor] Disabling playback monitoring.")
            monitor = False
        
        if monitor:
            print(f"[Monitor] Enabling real-time playback: Input {device_idx if device_idx is not None else 'Default'} -> Output {out_device}")
            recorder = AudioRecorderMonitor()
            try:
                # Open duplex stream: 1 channel in (mono), 2 channels out (stereo headphones)
                stream = sd.Stream(
                    device=(device_idx, out_device),
                    samplerate=sample_rate,
                    channels=(1, 2),
                    callback=recorder.callback
                )
                stream.start()
                print("[Monitor] Audio playback active (monitoring through headphones/speakers).")
            except Exception as stream_err:
                print(f"[Monitor Error] Could not start monitoring stream: {stream_err}")
                print("[Monitor] Continuing without playback monitoring.")
                recorder = None
                stream = None

    # State variables for duplicate prevention
    last_artist = None
    last_title = None
    last_scrobble_time = 0.0
    current_song_duration = DEFAULT_SONG_DURATION_FALLBACK

    print("\nStarting Aux Audio Scrobbler loop. Press Ctrl+C to exit.")

    try:
        while True:
            try:
                # 1. Record audio snippet
                if recorder is not None:
                    print(f"\nRecording {duration}s of audio from active monitor stream...")
                    recorder.start_recording()
                    await asyncio.sleep(duration)
                    recording = recorder.stop_recording()
                    print("Recording finished.")
                else:
                    recording = record_audio(device_idx, duration, sample_rate)
                
                # Check for recording errors or empty chunks
                if len(recording) == 0:
                    print("⚠️ Recording failed: Empty audio snippet.")
                    await asyncio.sleep(check_interval)
                    continue
                
                # Check for silence (RMS and Peak) before converting to WAV and querying Shazam
                rms_val = np.sqrt(np.mean(recording**2))
                peak_val = np.max(np.abs(recording))
                if rms_val < 0.002 and peak_val < 0.005:
                    print(f"🔇 Silence detected (RMS: {rms_val:.5f}, Peak: {peak_val:.5f}). Skipping Shazam lookup.")
                    print(f"Sleeping for {check_interval} seconds...\n")
                    await asyncio.sleep(check_interval)
                    continue
                
                # 2. Convert to WAV bytes in memory
                audio_bytes = np_array_to_wav_bytes(recording, sample_rate)
                
                # 3. Attempt to recognize the track via Shazam
                match = await recognize_song(shazam, audio_bytes)
                
                if match:
                    artist, title, _ = match
                    print(f"🎵 Recognized: {artist} - {title}")
                    
                    now = time.time()
                    elapsed = now - last_scrobble_time
                    
                    # Check if it is the same track as previously recognized
                    is_duplicate = (artist == last_artist and title == last_title)
                    
                    if is_duplicate and elapsed < current_song_duration:
                        remaining = current_song_duration - elapsed
                        print(f"👉 Same track is still playing. Scrobbled {int(elapsed)}s ago. Skipping scrobble. "
                              f"({int(remaining)}s left for this track)")
                    else:
                        # Use fallback song duration for duplicate check
                        current_song_duration = DEFAULT_SONG_DURATION_FALLBACK
                        
                        # Log scrobble info
                        if is_duplicate:
                            print(f"🔄 Replaying same song (duration elapsed). Scrobbling again!")
                        
                        if lastfm_network:
                            print(f"[Last.fm] Scrobbling track: {artist} - {title}")
                            try:
                                lastfm_network.scrobble(
                                    artist=artist,
                                    title=title,
                                    timestamp=int(now)
                                )
                                print("[Last.fm] Scrobble successful!")
                            except Exception as scrobble_err:
                                print(f"[Last.fm] Failed to scrobble: {scrobble_err}")
                        else:
                            print(f"[Dry Run] Would have scrobbled: {artist} - {title}")
                        
                        # Update state
                        last_artist = artist
                        last_title = title
                        last_scrobble_time = now
                else:
                    print("🔇 No song matched. (Ambient noise, talking, or unknown track)")
                    
            except sd.PortAudioError as pa_err:
                print(f"\n[Audio Error] PortAudio Error occurred: {pa_err}")
                print("Please check if the selected device index is correct.")
                list_devices()
                sys.exit(1)
            except Exception as err:
                print(f"\n[Error] Unexpected error in main loop: {err}")
            
            # Sleep until the next check
            print(f"Sleeping for {check_interval} seconds...\n")
            await asyncio.sleep(check_interval)
    finally:
        # Clean up monitoring stream when exiting
        if stream is not None:
            # Temporarily ignore SIGINT to prevent double Ctrl+C from interrupting PortAudio cleanup (avoiding Segfaults)
            import signal
            try:
                old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            except ValueError:
                # signal.signal only works in the main thread
                old_handler = None
            
            print("\nStopping audio playback monitoring...")
            try:
                stream.stop()
                stream.close()
                print("[Monitor] Monitoring stream closed.")
            except Exception as e:
                print(f"[Monitor Error] Error cleaning up stream: {e}")
            
            if old_handler is not None:
                try:
                    signal.signal(signal.SIGINT, old_handler)
                except ValueError:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Aux Port Audio to Last.fm Scrobbler using Shazam")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input and output devices and exit"
    )
    parser.add_argument(
        "--check-levels",
        action="store_true",
        help="Record 3 seconds from the selected input and print amplitude diagnostics, then exit"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio input device index to record from (defaults to default input)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_RECORD_DURATION,
        help=f"Recording snippet duration in seconds (default: {DEFAULT_RECORD_DURATION})"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_CHECK_INTERVAL,
        help=f"Seconds to wait between audio capture intervals (default: {DEFAULT_CHECK_INTERVAL})"
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"Sample rate for recording in Hz (default: {DEFAULT_SAMPLE_RATE})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode: run Shazam recognition but do not scrobble to Last.fm"
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Audio monitoring mode: play back the captured input audio to your headphones/speakers in real-time"
    )
    parser.add_argument(
        "--output-device",
        type=int,
        default=None,
        help="Audio output device index to play monitoring audio to (defaults to default output)"
    )
    
    args = parser.parse_args()
    
    if args.list_devices:
        list_devices()
        return

    if args.check_levels:
        check_input_levels(device_idx=args.device)
        return

    # Check if credentials are available and output warning if missing and not in dry-run
    if not args.dry_run and not (os.getenv("LASTFM_API_KEY") and os.getenv("LASTFM_API_SECRET")):
        print("⚠️ Warning: Last.fm credentials not found in environment, ~/.config/aux-audio-scrobbler/credentials.env, or .env.")
        print("Running in DRY-RUN mode. Songs will be identified but not scrobbled.")
        args.dry_run = True

    try:
        asyncio.run(
            run_scrobbler(
                device_idx=args.device,
                duration=args.duration,
                check_interval=args.interval,
                sample_rate=args.rate,
                dry_run=args.dry_run,
                monitor=args.monitor,
                output_device_idx=args.output_device
            )
        )
    except KeyboardInterrupt:
        print("\nExiting. Goodbye!")


if __name__ == "__main__":
    main()
