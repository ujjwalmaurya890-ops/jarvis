"""UI state metadata tests (Qt-free, so they run without PySide6 installed)."""

from ui.states import STATUS_STATES, STATE_COLOR, STATE_LABEL, STATE_NOTE, is_active_state


def test_every_state_has_color_label_and_note():
    assert set(STATUS_STATES) == set(STATE_COLOR) == set(STATE_LABEL) == set(STATE_NOTE)


def test_required_states_are_present():
    for state in ("listening", "thinking", "speaking", "error"):
        assert state in STATUS_STATES


def test_active_states_match_the_orb():
    for state in ("listening", "transcribing", "thinking", "executing", "speaking"):
        assert is_active_state(state)
    for state in ("offline", "idle", "error"):
        assert not is_active_state(state)


def test_state_labels_are_uppercase():
    assert all(label == label.upper() for label in STATE_LABEL.values())
