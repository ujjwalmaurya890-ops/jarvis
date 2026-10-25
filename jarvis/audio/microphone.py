"""Shared microphone pipeline.

One stream, many consumers: the clap detector, the wake-word detector and the
command recorder all read from the same 16 kHz mono callback so we never fight
over the capture device (Windows allows very few concurrent inputs).

CPU budget: the callback only computes RMS and pushes floats into a deque.
Everything heavy happens on worker threads.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

try:  # sounddevice is optional at import time so tests can run without PortAudio
    import sounddevice as sd
except (ImportError, OSError):  # pragma: no cover - depends on the machine
    sd = None

log = logging.getLogger("jarvis.audio")


@dataclass
class Chunk:
    samples: np.ndarray      # float32 mono, -1..1
    rms: float
    peak: float
    t: float


class Microphone:
    """Continuous capture with a ring buffer and subscriber callbacks."""

    def __init__(
        self,
        sample_rate: int = 16000,
        block_duration: float = 0.05,
        device: int | str | None = None,
    ) -> None:
        if sd is None:
            raise RuntimeError(
                "sounddevice is not installed or PortAudio is missing. "
                "Run: pip install sounddevice"
            )
        self.sample_rate = int(sample_rate)
        self.block_size = max(160, int(self.sample_rate * block_duration))
        self.device = device
        self._stream: "sd.InputStream | None" = None
        self._subs: dict[int, Callable[[Chunk], None]] = {}
        self._keys = iter(range(10**9))
        self._lock = threading.Lock()
        self._recent: deque[Chunk] = deque(maxlen=int(6.0 / block_duration))
        self._running = False
        self.last_error: str | None = None
        self.level = 0.0
        self.level_high = 0.0
        self._started_at = 0.0

    # ---- lifecycle ---------------------------------------------------------
    def available_devices(self) -> list[dict]:
        if sd is None:
            return []
        out = []
        default_input = getattr(sd.default, "device", (None, None))[0]
        for idx, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                out.append(
                    {
                        "index": idx,
                        "name": d.get("name", f"Device {idx}"),
                        "rate": int(d.get("default_samplerate", 44100) or 44100),
                        "is_default": idx == default_input,
                    }
                )
        return out

    def start(self) -> None:
        if self._running:
            return
        self.last_error = None
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=self._on_audio,
            )
            self._stream.start()
            self._running = True
            self._started_at = time.time()
            log.info("Microphone started (%d Hz, %.0f ms blocks)", self.sample_rate, self.block_size * 1000 / self.sample_rate)
        except Exception as exc:  # device busy, muted, removed, wrong index...
            self._running = False
            self.last_error = _explain_device_error(exc)
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            raise RuntimeError(self.last_error) from exc

    def stop(self) -> None:
        self._running = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover - never crash on shutdown
                pass

    @property
    def is_running(self) -> bool:
        return bool(self._running and self._stream is not None and getattr(self._stream, "active", True))

    # ---- fan-out -----------------------------------------------------------
    def subscribe(self, callback: Callable[[Chunk], None]) -> Callable[[], None]:
        with self._lock:
            key = next(self._keys)
            self._subs[key] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subs.pop(key, None)

        return unsubscribe

    def recent_seconds(self, seconds: float = 5.0) -> np.ndarray:
        """Return the tail of the ring buffer as float32 mono (for Whisper)."""
        blocks = max(1, int(seconds / (self.block_size / self.sample_rate)))
        chunks = list(self._recent)[-blocks:]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate([c.samples for c in chunks]).astype(np.float32)

    def wait_for_silence(self, threshold: float, timeout: float) -> bool:
        """Block until recent audio is quiet, or until timeout. Used after a wake hit."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state == "quiet" and self.level < threshold:
                return True
            time.sleep(0.05)
        return False

    @property
    def state(self) -> str:
        return "speech" if self.level > 0.02 else "quiet"

    # ---- callback ----------------------------------------------------------
    def _on_audio(self, indata, frames: int, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("PortAudio status: %s", status)
        # PortAudio owns indata and reuses it after the callback returns.
        data = np.asarray(indata[:, 0], dtype=np.float32).copy()
        if data.size == 0:
            return
        rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
        peak = float(np.max(np.abs(data))) if data.size else 0.0
        # Fast attack, slow release -> smooth UI meter without a timer thread.
        self.level = max(rms, self.level * 0.86)
        self.level_high = max(self.level, self.level_high * 0.97)
        chunk = Chunk(samples=data, rms=rms, peak=peak, t=float(getattr(time_info, "currentTime", time.monotonic())))
        self._recent.append(chunk)
        with self._lock:
            subs = list(self._subs.values())
        for cb in subs:
            try:
                cb(chunk)
            except Exception:  # one bad subscriber must not kill capture
                log.exception("audio subscriber failed")

    def uptime(self) -> float:
        return time.time() - self._started_at if self._running else 0.0


class CommandRecorder:
    """Records one utterance after activation, stopping on silence.

    Uses an adaptive energy VAD (noise floor tracking) instead of a fixed
    threshold, which behaves far better on laptop microphones with fan noise.
    """

    def __init__(
        self,
        mic: Microphone,
        *,
        min_energy: float = 0.006,
        silence_seconds: float = 0.9,
        max_seconds: float = 20.0,
        start_timeout: float = 6.0,
        lead_in_seconds: float = 0.35,
    ) -> None:
        self.mic = mic
        self.min_energy = min_energy
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.start_timeout = start_timeout
        self.lead_in = lead_in_seconds
        self._q: queue.Queue[Chunk] = queue.Queue(maxsize=4096)
        self._off: Callable[[], None] | None = None

    def _sub(self, chunk: Chunk) -> None:
        try:
            self._q.put_nowait(chunk)
        except queue.Full:
            pass

    def record(self, cancel: threading.Event | None = None) -> np.ndarray:
        """Blocking record. Call from a worker thread, never from the GUI thread."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._off = self.mic.subscribe(self._sub)
        frames: list[np.ndarray] = []
        noise_floor = self.min_energy
        started = False
        silence = 0.0
        t0 = time.time()
        lead_blocks = max(1, int(self.lead_in / (self.mic.block_size / self.mic.sample_rate)))
        pre: deque[np.ndarray] = deque(maxlen=lead_blocks)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    break
                elapsed = time.time() - t0
                if elapsed > self.max_seconds:
                    break
                if not started and elapsed > self.start_timeout:
                    break
                try:
                    chunk = self._q.get(timeout=0.25)
                except queue.Empty:
                    continue
                if not started:
                    pre.append(chunk.samples)
                    if chunk.rms > max(self.min_energy, noise_floor * 2.4):
                        started = True
                        frames.extend(pre)
                    else:
                        # Do not teach the noise floor that the first word is
                        # room noise before testing the onset.
                        noise_floor = 0.97 * noise_floor + 0.03 * chunk.rms
                    continue
                frames.append(chunk.samples)
                if chunk.rms < max(self.min_energy, noise_floor * 1.8):
                    silence += chunk.samples.size / self.mic.sample_rate
                    if silence >= self.silence_seconds and elapsed > 0.6:
                        break
                else:
                    silence = 0.0
                    noise_floor = 0.97 * noise_floor + 0.03 * chunk.rms
        finally:
            if self._off:
                self._off()
                self._off = None
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames).astype(np.float32)


def _explain_device_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "initialization" in text or "invalid device" in text:
        return "No microphone found or the selected input device is invalid. Pick another device in Settings."
    if "busy" in text or "in use" in text:
        return "The microphone is busy. Close other apps that use it (Meet, Teams, Recorder) and retry."
    if "callback" in text:
        return "Audio callback error - usually a sample-rate mismatch. Try 44100 -> 16000 resample off, or another device."
    return f"Microphone error: {exc}"
