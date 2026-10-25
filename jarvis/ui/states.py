"""UI status-state metadata.

Kept deliberately free of any Qt import so it can be unit-tested without a
display, and so the React dashboard and the PySide6 window cannot drift apart -
both render the same eight states with the same meaning.
"""

# The canonical states JARVIS can be in, in roughly the order they occur.
STATUS_STATES = (
    "offline",
    "idle",
    "listening",
    "transcribing",
    "thinking",
    "executing",
    "speaking",
    "error",
)

# States where the orb animates (audio is flowing or work is happening).
ACTIVE_STATES = frozenset({"listening", "transcribing", "thinking", "executing", "speaking"})

STATE_COLOR = {
    "offline": "#5b6b80",
    "idle": "#38bdf8",
    "listening": "#22d3ee",
    "transcribing": "#a3e635",
    "thinking": "#fbbf24",
    "executing": "#f97316",
    "speaking": "#60a5fa",
    "error": "#f87171",
}

STATE_LABEL = {
    "offline": "OFFLINE",
    "idle": "STANDBY",
    "listening": "LISTENING",
    "transcribing": "TRANSCRIBING",
    "thinking": "THINKING",
    "executing": "EXECUTING",
    "speaking": "SPEAKING",
    "error": "ERROR",
}

STATE_NOTE = {
    "offline": "ear stopped - nothing is being captured",
    "idle": "listening for the activation pattern",
    "listening": "speak now",
    "transcribing": "decoding audio",
    "thinking": "routing the request",
    "executing": "running a tool",
    "speaking": "voice output - say stop to interrupt",
    "error": "recoverable - see the activity log",
}


def is_active_state(state: str) -> bool:
    return state in ACTIVE_STATES
