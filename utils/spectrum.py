"""WASAPI loopback audio spectrum capture for the music workspace waveform.

The Lianxin music player runs in mpv on the desktop, so the webview never
receives the audio stream and a Web Audio AnalyserNode has nothing to read.
Instead we capture the system default playback device (loopback), compute
log-spaced frequency band magnitudes with numpy, and expose them through
``/api/music/spectrum`` so the frontend can draw a real, rhythm-following
equalizer. All capture work happens on a daemon thread; any failure simply
reports ``on: false`` and the UI falls back to its simulated animation.

Blocking ``stream.read`` is known to hang on some WASAPI loopback setups,
so we use a callback stream that pushes raw frames onto a small queue.
"""
from __future__ import annotations

import collections
import threading
import time

import numpy as np

BAND_COUNT = 28
FMIN = 40.0
FMAX = 16000.0
SAMPLE_RATE = 48000
CHANNELS = 2
FRAMES = 2048

_lock = threading.Lock()
_started = False
_on = False
_error: str | None = None
_bars = np.zeros(BAND_COUNT, dtype=np.float32)
_ref = 1e-3

_frames: collections.deque = collections.deque(maxlen=16)
_frames_lock = threading.Lock()


def snapshot() -> tuple[bool, list[float]]:
    with _lock:
        if not _on:
            return False, [0.0] * BAND_COUNT
        return True, [round(float(v), 4) for v in _bars]


def ensure_started() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_worker, name="music-spectrum", daemon=True).start()


def _on_audio(in_data, frame_count, time_info, status):
    if in_data:
        with _frames_lock:
            _frames.append(in_data)
    return (None, 0)

def _worker() -> None:
    global _on, _error, _ref, _bars
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        try:
            loopback = pa.get_default_wasapi_loopback()
        except Exception:
            loopback = None
        if loopback is None:
            with _lock:
                _error = "no wasapi loopback device"
            return

        rate = int(loopback.get("defaultSampleRate") or SAMPLE_RATE)
        channels = min(int(loopback.get("maxInputChannels") or CHANNELS), 2)
        stream = pa.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=FRAMES,
            input_device_index=int(loopback.get("index")),
            stream_callback=_on_audio,
        )
        stream.start_stream()

        window = np.hanning(FRAMES).astype(np.float32)
        edges = np.geomspace(FMIN, min(FMAX, rate / 2.0), BAND_COUNT + 1)

        while True:
            with _frames_lock:
                if _frames:
                    raw = _frames.popleft()
                else:
                    raw = None
            if raw is None:
                time.sleep(0.01)
                continue
            if len(raw) != FRAMES * 4 * channels:
                continue
            frame = np.frombuffer(raw, dtype=np.float32)
            if channels > 1:
                frame = frame.reshape(-1, channels).mean(axis=1)
            power = np.abs(np.fft.rfft(frame * window)) ** 2
            freqs = np.fft.rfftfreq(FRAMES, 1.0 / rate)
            bars = np.zeros(BAND_COUNT, dtype=np.float32)
            for i in range(BAND_COUNT):
                mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
                if mask.any():
                    bars[i] = float(np.sqrt(float(np.mean(power[mask]))))
            peak = float(bars.max())
            _ref = max(_ref * 0.985, peak, 1e-3)
            if _ref > 1e-6:
                bars = np.clip(bars / _ref, 0.0, 1.0).astype(np.float32)
            with _lock:
                _bars = (_bars * 0.55 + bars * 0.45).astype(np.float32)
                _on = True
                _error = None
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        with _lock:
            _error = repr(exc)
