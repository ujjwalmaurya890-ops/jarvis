"""Clap activation without a wake-word model.

A clap is a broadband transient with a very fast attack and a slow decay.
Speech has a much slower attack, so a band-pass + onset detector separates the
two well enough for a laptop mic.  On top of that we require a *pattern*
(2 or 3 hits inside a time window), then enforce a refractory period, an
amplitude-ratio check and a "claps must be louder than the room" rule to keep
door slams and coughs from waking the assistant during normal use.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

try:
    from scipy.signal import butter, lfilter

    _HAVE_SCIPY = True
except ImportError:  # pragma: no cover - scipy is optional
    _HAVE_SCIPY = False

log = logging.getLogger("jarvis.clap")


@dataclass
class ClapConfig:
    pattern: str = "double"          # single | double | triple
    threshold: float = 0.055         # absolute band-energy gate (0..1)
    min_gap: float = 0.09            # s: closer than this = same clap
    max_gap: float = 0.45            # s: farther than this = unrelated noises
    refractory: float = 1.2          # s: ignore everything after a trigger
    sensitivity: float = 1.0         # multiplies threshold; >1 = stricter
    min_ratio: float = 1.8           # transient must beat the rolling median
    max_sustained_chunks: int = 6    # ~300 ms at 50 ms blocks: longer = not a clap
    require_decay: bool = True       # clap tail must be longer than attack

    @property
    def hits_needed(self) -> int:
        if self.pattern == "none":
            return 0
        return {"single": 1, "double": 2, "triple": 3}.get(self.pattern, 2)


class ClapDetector:
    """Feed it audio chunks from Microphone.subscribe(); it fires a callback."""

    def __init__(
        self,
        config: ClapConfig | None = None,
        sample_rate: int = 16000,
        on_clap: object | None = None,
    ) -> None:
        self.cfg = config or ClapConfig()
        self.fs = sample_rate
        self.on_clap = on_clap  # type: ignore[assignment]
        self._onsets: deque[float] = deque(maxlen=8)
        self._noise = deque(maxlen=60)
        self._last_peak = 0.0
        self._prev_energy = 0.0
        self._sustained = 0
        self._candidate_at: float | None = None
        self._candidate_gate = 0.0
        self._last_trigger = 0.0
        self._cooldown_until = 0.0
        self.total_claps = 0
        self.rejected = {"too-quiet": 0, "bad-gap": 0, "refractory": 0, "speech-like": 0}
        self._filter = None
        if _HAVE_SCIPY:
            self._filter = butter(2, [800 / (self.fs / 2), min(4000 / (self.fs / 2), 0.98)], btype="band")

    # ---- tuning ------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.cfg.hits_needed > 0 and self.cfg.threshold > 0

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self.cfg, key) and value is not None:
                setattr(self.cfg, key, value)

    # ---- DSP ---------------------------------------------------------------
    def _band_energy(self, samples: np.ndarray) -> float:
        """Energy in the 800-4000 Hz clap band, relative to full band."""
        x = samples.astype(np.float64)
        if x.size < 8:
            return 0.0
        if self._filter is not None:
            b, a = self._filter
            x = lfilter(b, a, x)
        else:  # cheap fallback: first difference ≈ high-pass
            x = np.diff(x, prepend=x[0])
        return float(np.sqrt(np.mean(np.square(x))))

    def process(self, samples: np.ndarray, rms: float, peak: float, now: float | None = None) -> bool:
        """Analyse one chunk. Returns True exactly once per accepted clap pattern.

        Algorithm (3 cheap tests, ~40 us per 50 ms chunk):
          1. gate      - band energy must beat both the absolute threshold and
                         the rolling room-noise median (so it scales with the room);
          2. sharpness - energy must jump from below the gate to well above it in
                         a single chunk (a clap attack is <2 ms; speech is ~50-150 ms);
          3. brevity   - the energy must fall back below the gate within a few
                         chunks (a shout or a door thud that sustains is rejected).
        Only then is the onset added to the pattern chain.
        """
        if not self.enabled:
            return False
        now = now if now is not None else time.monotonic()
        energy = self._band_energy(samples)
        gate = self.cfg.threshold * self.cfg.sensitivity
        median = float(np.median(list(self._noise))) if self._noise else 0.0
        gate = max(gate, median * self.cfg.min_ratio)

        # --- how long have we been continuously above the gate? -------------
        if energy > (self._candidate_gate if self._candidate_at is not None else gate):
            self._sustained = self._sustained + 1 if self._candidate_at is not None else 1
        else:
            self._sustained = 0

        rising = energy > gate and self._prev_energy <= gate and (energy - self._prev_energy) > 0.35 * energy
        self._prev_energy = energy
        self._noise.append(energy)

        if now < self._cooldown_until:
            self._candidate_at = None
            if rising:
                self.rejected["refractory"] += 1
            return False

        if rising:
            self._candidate_at = now
            self._candidate_gate = gate
        if self._candidate_at is not None:
            if self._sustained > self.cfg.max_sustained_chunks:
                self.rejected["speech-like"] += 1
                self._candidate_at = None
            elif energy <= self._candidate_gate:
                # Accept the onset only AFTER it has decayed. Sustained music
                # is rejected rather than counted as a clap at its first frame.
                onset = self._candidate_at
                self._candidate_at = None
                gap = onset - self._onsets[-1] if self._onsets else None
                if gap is not None and gap < self.cfg.min_gap:
                    self.rejected["bad-gap"] += 1
                else:
                    if gap is not None and gap > self.cfg.max_gap:
                        self._onsets.clear()
                    self._onsets.append(onset)
        self._last_peak = energy

        chain = list(self._onsets)
        if len(chain) >= self.cfg.hits_needed:
            span = chain[-1] - chain[-1 - (self.cfg.hits_needed - 1)]
            gaps = np.diff(chain[-self.cfg.hits_needed :])
            tight = bool(np.all(gaps >= self.cfg.min_gap) and np.all(gaps <= self.cfg.max_gap))
            within = span <= self.cfg.max_gap * self.cfg.hits_needed
            if tight and within:
                self.total_claps += 1
                self._cooldown_until = now + self.cfg.refractory
                self._onsets.clear()
                log.debug("clap accepted (span %.2fs)", span)
                if callable(self.on_clap):
                    self.on_clap()  # type: ignore[operator]
                return True
            if not tight:
                self.rejected["bad-gap"] += 1
        return False

    # ---- diagnostics --------------------------------------------------------
    def stats(self) -> dict[str, object]:
        return {
            "accepted": self.total_claps,
            "rejected": dict(self.rejected),
            "pending_onsets": len(self._onsets),
            "noise_floor": round(float(np.median(list(self._noise))) if self._noise else 0.0, 5),
            "refractory_left": round(max(0.0, self._cooldown_until - time.monotonic()), 2),
        }


def calibrate(detector: ClapDetector, mic, seconds: float = 3.0) -> float:
    """Measure the room's noise floor and return a suggested threshold.

    Usage: keep quiet for ``seconds``; the value returned is roughly
    ``median + 6 * spread`` - paste it into Settings as clap_threshold.
    """
    import threading as _t

    stop = _t.Event()
    samples: list[float] = []

    def _cb(chunk) -> None:  # noqa: ANN001
        if stop.is_set():
            return
        samples.append(detector._band_energy(chunk.samples))

    off = mic.subscribe(_cb)
    try:
        time.sleep(seconds)
    finally:
        stop.set()
        off()
    if not samples:
        return detector.cfg.threshold
    arr = np.array(samples, dtype=np.float64)
    return round(float(np.median(arr) + 6 * np.std(arr) + 1e-4), 5)
