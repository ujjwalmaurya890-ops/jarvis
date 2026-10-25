"""Assistant state machine.

The UI, the audio thread and the worker threads all need to agree on what
JARVIS is doing right now.  A single lock-protected object with listener
callbacks keeps that cheap and thread-safe (Qt signals cannot be emitted from
an arbitrary thread, so we notify through plain callbacks and let the UI
re-emit them safely).
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class State(str, Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    ERROR = "error"


# Which states allow the microphone to keep streaming.
_ALLOW_STREAM = {State.IDLE, State.LISTENING, State.SPEAKING}


@dataclass
class Transcript:
    id: int
    role: str                     # "user" | "jarvis" | "system"
    text: str
    ts: float = field(default_factory=time.time)
    meta: dict[str, object] = field(default_factory=dict)


class StateMachine:
    """Small observable state holder.  All listeners run off the UI thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: State = State.OFFLINE
        self._detail: str = ""
        self._mic_level: float = 0.0
        self._last_error: str | None = None
        self._ids = itertools.count(1)
        self._history: list[Transcript] = []
        self._listeners: dict[int, Callable[[State, str], None]] = {}
        self._level_listeners: dict[int, Callable[[float], None]] = {}

    # ---- state --------------------------------------------------------------
    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def set_state(self, state: State, detail: str = "") -> None:
        with self._lock:
            if state is self._state and detail == self._detail:
                return
            self._state = state
            self._detail = detail
            if state is not State.ERROR:
                self._last_error = None
            listeners = list(self._listeners.values())
        for cb in listeners:
            try:
                cb(state, detail)
            except Exception:  # a broken listener must not break the audio loop
                pass

    def fail(self, message: str, *, recoverable: bool = True) -> None:
        with self._lock:
            self._last_error = message
        self.set_state(State.ERROR, message)
        if recoverable:
            # Drop back to IDLE shortly after so the app never feels bricked.
            timer = threading.Timer(3.0, self._recover, args=(message,))
            timer.daemon = True
            timer.start()

    def _recover(self, message: str) -> None:
        with self._lock:
            if self._state is State.ERROR and self._last_error == message:
                self._state = State.IDLE
                self._detail = ""
                listeners = list(self._listeners.values())
            else:
                return
        for cb in listeners:
            try:
                cb(State.IDLE, "")
            except Exception:
                pass

    @property
    def mic_level(self) -> float:
        with self._lock:
            return self._mic_level

    def set_mic_level(self, level: float) -> None:
        with self._lock:
            self._mic_level = max(0.0, min(1.0, level))
            listeners = list(self._level_listeners.values())
        for cb in listeners:
            try:
                cb(self._mic_level)
            except Exception:
                pass

    @property
    def streaming_allowed(self) -> bool:
        with self._lock:
            return self._state in _ALLOW_STREAM

    # ---- transcript log ------------------------------------------------------
    def add_transcript(self, role: str, text: str, **meta: object) -> Transcript:
        item = Transcript(id=next(self._ids), role=role, text=text, meta=meta)
        with self._lock:
            self._history.append(item)
            if len(self._history) > 500:
                del self._history[: len(self._history) - 500]
        return item

    @property
    def history(self) -> list[Transcript]:
        with self._lock:
            return list(self._history)

    # ---- subscriptions -------------------------------------------------------
    def on_state(self, callback: Callable[[State, str], None]) -> Callable[[], None]:
        with self._lock:
            key = next(self._ids)
            self._listeners[key] = callback
        return lambda: self._listeners.pop(key, None)

    def on_level(self, callback: Callable[[float], None]) -> Callable[[], None]:
        with self._lock:
            key = next(self._ids)
            self._level_listeners[key] = callback
        return lambda: self._level_listeners.pop(key, None)
