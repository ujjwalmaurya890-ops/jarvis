"""Local wake word.

Two paths, both free and both offline:

1. openwakeword (recommended): a tiny ONNX model, ~1% CPU on an i5. If you
   want a *custom* "Jarvis" model this module documents the training recipe.
2. Fuzzy match against the faster-whisper hypothesis when a wake-word model is
   unavailable.  Slower, but needs no extra download.

No pretrained custom "JARVIS" model ships with this project - do not assume one
exists.  Train it (see TRAINING.md steps below) or use the built-in
"hey_jarvis" model from the openWakeWord repository.
"""

from __future__ import annotations

import difflib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.wake")

TRAINING_STEPS = """
Training a custom "Hello JARVIS" model (do this once, on any machine, free):

1. pip install openwakeword colab-friendly tooling (or use the free Google Colab).
2. Record ~300 short clips saying "hello jarvis" in your own voice:
   different distances (0.3-2 m), room noise (fan, music), and 200 clips of
   *not* saying it (speech, TV, door knocks) as negatives.
3. Convert clips to 16 kHz mono WAV:  ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav
4. Label them and pack as a directory of wavs plus a text file of paths, then:
     python train_model.py --data_dir ./jarvis_clips --model_name hello_jarvis
   (train_model.py comes from openwakeword/training in their repo).
5. Export:  the training script writes hello_jarvis_v0.1.tar.gz (ONNX inside).
6. Put the file in  jarvis/models/  and set WAKE_MODEL_DIR=./models in .env
   or pick the folder in the Settings dialog.
7. Re-run the false-trigger test in the UI: 5 minutes of TV/dialogue should
   produce 0 activations. Raise `threshold` if it fires, lower it if it misses.
"""


@dataclass
class WakeConfig:
    wake_word: str = "hello jarvis"
    model_dir: str = ""
    threshold: float = 0.55
    min_interval: float = 2.0
    use_fuzzy_transcript: bool = True
    fuzzy_cutoff: float = 0.78


class WakeWordDetector:
    """Wraps openwakeword if installed, else stays in transcript-fallback mode."""

    def __init__(self, config: WakeConfig | None = None, sample_rate: int = 16000) -> None:
        self.cfg = config or WakeConfig()
        self.fs = sample_rate
        self._engine = None
        self._lock = threading.Lock()
        self._last_hit = 0.0
        self._frames = 0
        self.available = False
        self.mode = "fallback-only"
        self._buffer = np.zeros(1280, dtype=np.int16)
        self._buf_pos = 0
        if self.cfg.model_dir:
            self._try_open_model()

    # ---- setup --------------------------------------------------------------
    def _try_open_model(self) -> bool:
        try:
            from openwakeword.model import Model  # type: ignore
        except Exception as exc:  # ImportError or missing onnxruntime
            log.info("openwakeword unavailable (%s); using transcript matching.", exc)
            self.available = False
            return False
        model_files = list(Path(self.cfg.model_dir).glob("*.onnx")) + list(Path(self.cfg.model_dir).glob("*.tflite"))
        if not model_files:
            log.warning("No wake-word model files in %s\n%s", self.cfg.model_dir, TRAINING_STEPS)
            return False
        try:
            self._engine = Model(  # type: ignore[operator]
                wakeword_models=[str(p) for p in model_files],
                inference_framework="onnx" if any(p.suffix == ".onnx" for p in model_files) else "tflite",
            )
            self.available = True
            self.mode = "openwakeword"
            log.info("Wake word model(s) loaded from %s", self.cfg.model_dir)
        except Exception:
            log.exception("Wake-word model failed to load")
            self.available = False
        return self.available

    def set_model_dir(self, path: str) -> bool:
        self.cfg.model_dir = path
        return self._try_open_model()

    # ---- streaming ---------------------------------------------------------
    def process(self, samples: np.ndarray) -> bool:
        """int16-equivalent float chunks in, True when the wake word fires."""
        if not self.available or self._engine is None:
            return False
        pcm = np.clip(samples, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        fired = False
        with self._lock:
            pos = 0
            while pos < pcm16.size:
                room = self._buffer.size - self._buf_pos
                count = min(room, pcm16.size - pos)
                self._buffer[self._buf_pos : self._buf_pos + count] = pcm16[pos : pos + count]
                self._buf_pos += count
                pos += count
                if self._buf_pos < self._buffer.size:
                    continue
                frame = self._buffer.copy()
                self._buf_pos = 0
                self._frames += 1
                try:
                    scores = self._engine.predict(frame)  # returns {model_name: float}
                    best = max((float(v) for v in scores.values()), default=0.0)
                except Exception:
                    log.exception("wake-word predict failed; disabling model path")
                    self.available = False
                    return False
                now = time.monotonic()
                if best >= self.cfg.threshold and now - self._last_hit > self.cfg.min_interval:
                    self._last_hit = now
                    log.info("wake word hit (%.2f)", best)
                    fired = True
        return fired

    # ---- fallback matcher ---------------------------------------------------
    def matches_text(self, text: str) -> bool:
        """Used when no model is present: match a near-transcription of the wake word.

        Deliberately tolerant of "hello jervis", "hey jarvis", "al jarvis" etc.
        """
        if not self.cfg.use_fuzzy_transcript or not text:
            return False
        norm = " ".join(text.lower().replace("'", "").split())
        target = self.cfg.wake_word.lower()
        if target in norm:
            return True
        window = " ".join(norm.split()[:4])
        score = difflib.SequenceMatcher(None, window, target).ratio()
        return score >= self.cfg.fuzzy_cutoff

    def reset(self) -> None:
        with self._lock:
            self._buffer = np.zeros(1280, dtype=np.int16)
            self._buf_pos = 0
            if self._engine is not None and hasattr(self._engine, "reset"):
                try:
                    self._engine.reset()  # type: ignore[attr-defined]
                except Exception:
                    pass

    def stats(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "available": self.available,
            "frames": self._frames,
            "threshold": self.cfg.threshold,
            "model_dir": self.cfg.model_dir or "(none)",
        }
