"""Speech to text: Faster-Whisper on CPU, tuned for 8 GB RAM.

Default model is ``tiny.en`` (~75 MB, ~1-2 s per 5 s of speech on an i5).
Switch to ``base.en`` for better accuracy, or ``small`` only if you have 16 GB.
``int8`` compute keeps the resident set small.

Hinglish: Whisper was trained on code-mixed Hindi-English audio, so it does
handle Hinglish reasonably when you force language="hi" (Hindi script may
appear).  Two practical modes are exposed below: "en" for English, and
"hinglish" which sends language="hi" with a Roman-script initial prompt so the
output stays in Latin script.  No audio leaves the machine.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger("jarvis.stt")


@dataclass
class STTResult:
    text: str
    language: str = "en"
    duration: float = 0.0
    audio_seconds: float = 0.0
    engine: str = "none"
    error: str | None = None
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


class SpeechToText:
    """Lazy-loading wrapper around faster-whisper with a graceful fallback."""

    def __init__(
        self,
        model_size: str = "tiny.en",
        compute_type: str = "int8",
        threads: int = 4,
        beam_size: int = 1,
        language: str = "en",
        initial_prompt: str = "",
        condition_on_previous_text: bool = False,
    ) -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self.threads = max(1, int(threads))
        self.beam_size = max(1, int(beam_size))
        self.language = language
        self.initial_prompt = initial_prompt
        self.condition_on_previous_text = condition_on_previous_text
        self._model = None
        self._lock = threading.Lock()
        self.load_error: str | None = None
        self.load_seconds: float = 0.0

    # ---- model -------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Download/load the model. Safe to call repeatedly (thread-safe)."""
        with self._lock:
            if self._model is not None or self.load_error:
                return
            import time as _t

            t0 = _t.monotonic()
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on install
                self.load_error = (
                    "faster-whisper is not installed. Run: pip install faster-whisper "
                    "(first run downloads the model to ~/.cache/huggingface)."
                )
                log.warning(self.load_error, exc_info=exc)
                return
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type=self.compute_type,
                    cpu_threads=self.threads,
                    num_workers=1,
                )
                self.load_seconds = _t.monotonic() - t0
                log.info("Whisper '%s' ready in %.1fs", self.model_size, self.load_seconds)
            except Exception as exc:
                self.load_error = f"Could not load Whisper model '{self.model_size}': {exc}"
                log.exception(self.load_error)

    def preload(self) -> None:
        threading.Thread(target=self.load, name="stt-load", daemon=True).start()

    # ---- transcription --------------------------------------------------------
    def _lang_pair(self) -> tuple[str | None, str]:
        """Map our friendly language name to (language, task)."""
        lang = (self.language or "en").lower()
        if lang in ("hinglish", "hi-en", "hindi-english"):
            return "hi", "transcribe"
        if lang in ("en", ""):
            return "en", "transcribe"
        if lang == "auto":
            return None, "transcribe"
        return lang, "transcribe"

    def transcribe(self, samples: np.ndarray, sample_rate: int = 16000) -> STTResult:
        if samples is None or samples.size == 0:
            return STTResult(text="", error="Empty recording - the microphone captured nothing.")
        language, task = self._lang_pair()
        if language not in {"en", None} and self.model_size.endswith(".en"):
            return STTResult(text="", error="Hindi/Hinglish needs a multilingual Whisper model such as 'tiny' or 'base', not 'tiny.en'.")
        if sample_rate <= 0:
            return STTResult(text="", error="Invalid recording sample rate.")
        if sample_rate != 16000:
            try:
                from math import gcd
                from scipy.signal import resample_poly

                divisor = gcd(sample_rate, 16000)
                samples = resample_poly(samples, 16000 // divisor, sample_rate // divisor)
                sample_rate = 16000
            except (ImportError, ValueError) as exc:
                return STTResult(text="", error=f"Could not resample audio to 16 kHz: {exc}")
        audio_seconds = samples.size / float(sample_rate)
        if audio_seconds < 0.35:
            return STTResult(text="", audio_seconds=audio_seconds, error="Too short to transcribe.")
        self.load()
        if self._model is None:
            return STTResult(
                text="",
                audio_seconds=audio_seconds,
                error=self.load_error or "Speech recognition unavailable.",
            )
        # float32 mono in -1..1 is exactly what faster-whisper wants.
        audio = np.clip(samples.astype(np.float32), -1.0, 1.0)
        import time as _t

        t0 = _t.monotonic()
        try:
            segments, info = self._model.transcribe(
                audio,
                language=language,
                task=task,
                beam_size=self.beam_size,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
                condition_on_previous_text=self.condition_on_previous_text,
                initial_prompt=self.initial_prompt or None,
                without_timestamps=False,
            )
            words: list[dict[str, Any]] = []
            parts: list[str] = []
            for seg in segments:
                parts.append(seg.text.strip())
                for w in getattr(seg, "words", None) or []:
                    words.append({"word": w.word, "start": w.start, "end": w.end})
            text = " ".join(p for p in parts if p).strip()
            return STTResult(
                text=text,
                language=getattr(info, "language", language or "en") or "en",
                duration=_t.monotonic() - t0,
                audio_seconds=audio_seconds,
                engine=f"faster-whisper:{self.model_size}",
                words=words,
            )
        except Exception as exc:  # corrupted buffer, OOM, etc.
            log.exception("transcription failed")
            return STTResult(text="", audio_seconds=audio_seconds, error=f"Transcription failed: {exc}")

    def transcribe_file(self, path: str) -> STTResult:
        """Transcribe an existing WAV - handy for testing without speaking."""
        try:
            import wave

            with wave.open(path, "rb") as wf:
                rate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
                if wf.getsampwidth() != 2:
                    return STTResult(text="", error="Use a 16-bit PCM WAV file.")
                data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if wf.getnchannels() > 1:
                    data = data.reshape(-1, wf.getnchannels()).mean(axis=1).astype(np.float32)
            return self.transcribe(data, rate)
        except Exception as exc:
            return STTResult(text="", error=f"Could not read '{path}': {exc}")
