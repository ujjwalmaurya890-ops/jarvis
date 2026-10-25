"""Text to speech with interrupt support.

pyttsx3 drives SAPI5 on Windows, which is offline and free.  SAPI voices are
functional but not cinematic; say so in the README instead of promising Tony
Stark.  If you want a nicer voice, the "windows-sapi" path uses the
System.Speech API directly so rate/pitch can be nudged per-voice.

Interruption: pyttsx3's run-loop lives on its own thread, so we run it here and
expose stop() which calls `end_loop` + `stop`.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time

log = logging.getLogger("jarvis.tts")

_STRIP = {
    "**": "",
    "__": "",
    "`": "",
    "###": "",
}


def clean_for_speech(text: str) -> str:
    """Remove markdown, links and code fences; expand a few symbols."""
    if not text:
        return ""
    out = re.sub(r"```.*?```", " code block omitted ", text, flags=re.S)
    out = re.sub(r"https?://\S+", "a link", out)
    for bad, good in _STRIP.items():
        out = out.replace(bad, good)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)      # markdown links
    out = re.sub(r"\s*\n\s*", ". ", out)
    out = re.sub(r"\.{2,}", ".", out)
    return out.strip()[:1200]


class TextToSpeech:
    def __init__(self, engine: str = "pyttsx3", rate: int = 178, volume: float = 0.9, voice_name: str = "") -> None:
        self.engine_name = engine
        self.rate = rate
        self.volume = max(0.0, min(1.0, volume))
        self.voice_name = voice_name
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._ready = threading.Event()
        self._speaking = threading.Event()
        self._engine = None
        self._lock = threading.Lock()
        self.available = False
        self.error: str | None = None
        self.started_at = 0.0

    # ---- lifecycle ----------------------------------------------------------
    def start(self, *, wait_ready: bool = False) -> bool:
        if self._thread and self._thread.is_alive():
            return self.available
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()
        if wait_ready:
            self._ready.wait(timeout=3.0)
        return self.error is None

    def _init_engine(self):  # noqa: ANN202
        if self.engine_name == "windows-sapi":
            return self._init_sapi()
        import pyttsx3  # type: ignore

        eng = pyttsx3.init()
        eng.setProperty("rate", self.rate)
        eng.setProperty("volume", self.volume)
        voices = eng.getProperty("voices") or []
        if self.voice_name:
            for v in voices:
                if self.voice_name.lower() in (v.name or "").lower():
                    eng.setProperty("voice", v.id)
                    break
        elif voices:
            default = next((v for v in voices if "zira" in (v.name or "").lower()), voices[0])
            eng.setProperty("voice", default.id)
        return eng

    def _init_sapi(self):  # noqa: ANN202
        """Direct SAPI via comtypes - optional, gives us SpeakAsync + interrupt."""
        try:
            import comtypes.client  # type: ignore

            sp = comtypes.client.CreateObject("SAPI.SpVoice")
            sp.Rate = max(-10, min(10, int((self.rate - 180) / 20)))
            sp.Volume = int(self.volume * 100)
            return sp
        except Exception as exc:  # pragma: no cover
            log.warning("System.Speech unavailable (%s); falling back to pyttsx3", exc)
            return None

    def _run(self) -> None:
        try:
            eng = self._init_engine()
            if eng is None:
                self.engine_name = "pyttsx3"
                eng = self._init_engine()
            self._engine = eng
            self.available = True
        except Exception as exc:  # no SAPI, missing comtypes, etc.
            self.error = f"TTS unavailable: {exc}"
            log.warning(self.error)
            self._ready.set()
            return
        kind = "sapi" if self.engine_name == "windows-sapi" and not hasattr(eng, "say") else "pyttsx3"
        self._ready.set()
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if not text:
                continue
            spoken = clean_for_speech(text)
            if not spoken:
                continue
            self._speaking.set()
            self._interrupt.clear()
            try:
                if kind == "sapi":
                    # SAPI COM objects only belong to THIS thread. 1=async,
                    # 3=async|purge; WaitUntilDone(50) returns on completion.
                    eng.Speak(spoken, 1)
                    while not self._stop.is_set():
                        if self._interrupt.is_set():
                            eng.Speak("", 3)
                            break
                        if eng.WaitUntilDone(50):
                            break
                else:
                    eng.say(spoken)
                    eng.runAndWait()
            except Exception:
                log.exception("speech output failed")
            finally:
                self._speaking.clear()

    # ---- api -----------------------------------------------------------------
    def say(self, text: str) -> None:
        """Queue text for speaking. Never blocks the caller."""
        if not text or self._stop.is_set():
            return
        if not self._thread or not self._thread.is_alive():
            self.start()
        self.started_at = time.time()
        self._queue.put(text)

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = time.time() + (timeout or 60)
        while time.time() < deadline:
            if self.error:
                return False
            if self._queue.empty() and not self._speaking.is_set():
                return True
            time.sleep(0.08)
        return False

    def stop(self) -> None:
        """Clear queued speech; direct SAPI interrupts on its own COM thread."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._interrupt.set()
        with self._lock:
            eng = self._engine
            if eng is None:
                return
            try:
                # pyttsx3 stop() is best-effort across threads on SAPI5; direct
                # windows-sapi mode avoids cross-thread COM access altogether.
                if self.engine_name == "pyttsx3" and hasattr(eng, "stop"):
                    eng.stop()
            except Exception:
                log.warning("pyttsx3 could not interrupt this voice immediately", exc_info=True)

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()
        thread, self._thread = self._thread, None
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
            if thread.is_alive():
                log.warning("Speech engine did not stop within one second")

    def list_voices(self) -> list[str]:
        try:
            import pyttsx3  # type: ignore

            return [v.name for v in (pyttsx3.init().getProperty("voices") or [])]
        except Exception:
            return []
