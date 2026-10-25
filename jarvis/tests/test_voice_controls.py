"""Voice system tests: push-to-talk, mute, interrupt, status, optional wake."""

from __future__ import annotations

from config.settings import Settings
from core.assistant import Jarvis
from core.state import State


class FakeMic:
    def __init__(self, running: bool = True) -> None:
        self.is_running = running
        self.start_calls = 0

    def start(self) -> None:
        self.is_running = True
        self.start_calls += 1

    def stop(self) -> None:
        self.is_running = False

    def subscribe(self, callback):
        return lambda: None


class FakeTTS:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.stopped = 0
        self.available = True

    def say(self, text: str) -> None:
        self.said.append(text)

    def stop(self) -> None:
        self.stopped += 1

    def wait(self, timeout=None):  # noqa: ANN001
        return True

    def shutdown(self) -> None:
        pass


def _jarvis() -> Jarvis:
    return Jarvis(Settings())


def test_push_to_talk_triggers_when_microphone_is_live():
    jarvis = _jarvis()
    jarvis.mic = FakeMic(running=True)
    jarvis.clap = None
    jarvis.wake = None
    events: list[str] = []
    jarvis.on_event(lambda event, data: events.append(event))
    try:
        assert jarvis.push_to_talk() is True
        assert "activation" in events
    finally:
        jarvis.shutdown()


def test_push_to_talk_fails_without_microphone():
    jarvis = _jarvis()
    jarvis.mic = None
    try:
        assert jarvis.push_to_talk() is False
        assert jarvis.state.state is State.ERROR
    finally:
        jarvis.shutdown()


def test_mute_stops_speech_and_blocks_further_output():
    jarvis = _jarvis()
    tts = FakeTTS()
    jarvis.tts = tts
    jarvis.set_muted(True)
    jarvis.speak("you should not hear this")
    assert tts.said == [] and tts.stopped == 1
    jarvis.set_muted(False)
    jarvis.speak("now you can hear me")
    assert tts.said == ["now you can hear me"]


def test_interrupt_during_speech_resets_state_and_stops_tts():
    jarvis = _jarvis()
    tts = FakeTTS()
    jarvis.tts = tts
    jarvis.state.set_state(State.SPEAKING)
    jarvis.interrupt()
    assert jarvis.state.state is State.IDLE
    assert tts.stopped == 1


def test_voice_status_reports_all_subsystems():
    jarvis = _jarvis()
    jarvis.mic = FakeMic(running=True)
    jarvis.muted = True
    try:
        status = jarvis.voice_status()
        assert status["mic_running"] is True
        assert status["muted"] is True
        assert status["wake_mode"] == "none"
        assert status["tts_available"] is False
        assert "state" in status and "clap_enabled" in status
    finally:
        jarvis.shutdown()


def test_wake_word_is_optional_without_a_model():
    from audio.wake_word import WakeConfig, WakeWordDetector

    detector = WakeWordDetector(WakeConfig(wake_word="hello jarvis"))
    assert detector.available is False
    assert detector.mode == "fallback-only"
    # The transcript fallback still recognises the phrase.
    assert detector.matches_text("hello jarvis, open youtube")


def test_clap_detector_can_be_switched_off():
    from audio.clap_detector import ClapConfig, ClapDetector

    detector = ClapDetector(ClapConfig(pattern="none"))
    assert detector.enabled is False
