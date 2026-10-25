"""Windows media-key control via SendInput (no extra dependency).

Pressing the hardware media keys (play/pause, next, previous, stop) is done with
``SendInput`` so it works even when the window that owns the media session is in
the background.  Nothing here touches the network or the disk.

The actual keypress is isolated in :func:`_press` so it can be mocked in tests;
:func:`media_action` does the validation and returns a small result object.
On non-Windows platforms it reports "not supported" rather than pretending to
work.
"""

from __future__ import annotations

import ctypes
import logging
import platform
from dataclasses import dataclass

log = logging.getLogger("jarvis.media")

# Virtual-key codes for the standard Windows media keys.
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREVIOUS = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

_VK = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT,
    "previous": VK_MEDIA_PREVIOUS,
    "stop": VK_MEDIA_STOP,
}

_ALIASES = {
    "play": "play_pause",
    "pause": "play_pause",
    "playpause": "play_pause",
    "toggle": "play_pause",
    "nexttrack": "next",
    "next_track": "next",
    "prev": "previous",
    "previoustrack": "previous",
    "previous_track": "previous",
}

KEYEVENTF_KEYUP = 0x02
INPUT_KEYBOARD = 1


@dataclass
class MediaResult:
    ok: bool
    action: str
    message: str = ""


def _structs():  # noqa: ANN202
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    class INPUT(ctypes.Structure):
        class _UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _UNION)]

    return KEYBDINPUT, INPUT


def _press(vk: int) -> bool:
    """Send one keydown + keyup for ``vk``. Windows-only; raises on failure."""
    if platform.system() != "Windows":
        return False
    KEYBDINPUT, INPUT = _structs()
    user32 = ctypes.windll.user32

    def make(flags: int) -> "INPUT":
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki = KEYBDINPUT(vk, 0, flags, 0, None)
        return inp

    sent = user32.SendInput(1, ctypes.byref(make(0)), ctypes.sizeof(INPUT))
    sent += user32.SendInput(1, ctypes.byref(make(KEYEVENTF_KEYUP)), ctypes.sizeof(INPUT))
    return sent >= 0


def media_action(action: str, *, _press_fn=None) -> MediaResult:  # noqa: ANN001
    """Press a media key. ``_press_fn`` is injectable for tests."""
    key = (action or "").strip().lower().replace("-", "_").replace(" ", "_")
    key = _ALIASES.get(key, key)
    if key not in _VK:
        return MediaResult(False, key, "Unknown media action. Use play_pause, next, previous or stop.")
    if platform.system() != "Windows":
        return MediaResult(False, key, "Media control is wired for Windows only.")
    press = _press_fn or _press
    try:
        ok = bool(press(_VK[key]))
    except Exception as exc:
        log.warning("media key failed", exc_info=True)
        return MediaResult(False, key, f"Media key failed: {exc}")
    return MediaResult(ok, key, f"Sent the {key} media key." if ok else "The media key could not be sent.")
