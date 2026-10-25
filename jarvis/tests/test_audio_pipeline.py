"""Deterministic audio-device, VAD, wake-frame and speech tests (no hardware)."""

import threading
import time
from types import SimpleNamespace

import numpy as np

from audio import microphone as mic_module
from audio.clap_detector import ClapConfig, ClapDetector
from audio.microphone import CommandRecorder, Microphone
from audio.speech_to_text import SpeechToText
from audio.text_to_speech import TextToSpeech, clean_for_speech
from audio.wake_word import WakeWordDetector


class FakeStream:
    def __init__(self, *, callback, **kwargs):
        self.callback = callback
        self.active = False
        self.closed = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True


def test_portaudio_timestamp_and_owned_samples(monkeypatch):
    fake_sd = SimpleNamespace(
        InputStream=FakeStream,
        default=SimpleNamespace(device=(0, 1)),
        query_devices=lambda: [{"max_input_channels": 1, "name": "Mock mic", "default_samplerate": 16000}],
    )
    monkeypatch.setattr(mic_module, "sd", fake_sd)
    mic = Microphone()
    mic.start()
    source = np.ones((800, 1), dtype=np.float32) * 0.12
    received = []
    off = mic.subscribe(received.append)
    mic._stream.callback(source, 800, SimpleNamespace(currentTime=12.5), None)
    source[:] = 0  # PortAudio recycles this buffer after the callback returns
    assert received[0].t == 12.5
    assert received[0].samples[0] == np.float32(0.12)
    assert mic.available_devices()[0]["is_default"]
    off()
    mic.stop()
    assert mic._stream is None and not mic.is_running


def test_microphone_busy_error_is_useful(monkeypatch):
    def busy(**kwargs):
        raise RuntimeError("Device busy")

    monkeypatch.setattr(mic_module, "sd", SimpleNamespace(InputStream=busy))
    mic = Microphone()
    try:
        mic.start()
    except RuntimeError as exc:
        assert "busy" in str(exc).lower() and "Close other apps" in str(exc)
    else:
        assert False, "Expected an actionable microphone error"


class FakeMic:
    block_size = 800
    sample_rate = 16000

    def __init__(self):
        self.callbacks = []
        self.ready = threading.Event()

    def subscribe(self, callback):
        self.callbacks.append(callback)
        self.ready.set()
        return lambda: self.callbacks.remove(callback)

    def feed(self, value, blocks):
        for i in range(blocks):
            chunk = np.full(800, value, dtype=np.float32)
            for cb in list(self.callbacks):
                cb(SimpleNamespace(samples=chunk, rms=abs(value)))
            time.sleep(0.005)


def test_vad_records_word_then_stops_after_silence():
    mic = FakeMic()
    rec = CommandRecorder(mic, min_energy=0.006, silence_seconds=0.15, start_timeout=1.0, max_seconds=2)
    result = []
    thread = threading.Thread(target=lambda: result.append(rec.record()))
    thread.start()
    assert mic.ready.wait(timeout=1.0)
    mic.feed(0.002, 4)
    mic.feed(0.10, 8)
    mic.feed(0.001, 12)
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result[0].size >= 800 * 8
    assert not mic.callbacks


def test_vad_empty_timeout_is_short():
    mic = FakeMic()
    rec = CommandRecorder(mic, start_timeout=0.1, max_seconds=3)
    started = time.monotonic()
    data = rec.record()
    assert data.size == 0 and time.monotonic() - started < 1


def test_wake_frames_are_complete_and_scores_come_from_predict():
    wake = WakeWordDetector()
    seen = []

    class Engine:
        def predict(self, frame):
            seen.append(frame.copy())
            return {"custom_hello_jarvis": 0.91 if len(seen) == 3 else 0.0}

    wake._engine = Engine()
    wake.available = True
    for n in (800, 800, 800):
        assert not wake.process(np.full(n, 0.12, dtype=np.float32))
    assert wake.process(np.full(1440, 0.12, dtype=np.float32))
    assert len(seen) == 3
    assert all(f.size == 1280 for f in seen)
    assert wake.stats()["frames"] == 3


def test_hinglish_requires_multilingual_whisper_not_tiny_en():
    stt = SpeechToText(model_size="tiny.en", language="hinglish")
    result = stt.transcribe(np.ones(16000, dtype=np.float32) * 0.1)
    assert not result.ok and "multilingual" in result.error


def test_transcribe_passes_supported_task_and_vad_options():
    seen = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return [SimpleNamespace(text=" hello", words=[])], SimpleNamespace(language="en")

    stt = SpeechToText()
    stt._model = Model()
    result = stt.transcribe(np.ones(16000, dtype=np.float32) * 0.1)
    assert result.ok and result.text == "hello"
    assert seen["task"] == "transcribe" and seen["vad_filter"]


def test_clap_disabled_and_sustained_bursts_not_counted():
    silence = np.zeros(800, dtype=np.float32)
    loud = np.random.default_rng(3).standard_normal(800).astype(np.float32) * 0.25
    off = ClapDetector(ClapConfig(pattern="none", threshold=0.02))
    assert not off.enabled and not off.process(loud, 0.2, 0.9, now=0.1)
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02))
    for start in (0.1, 1.1):
        for i in range(9):
            assert not detector.process(loud, 0.2, 0.9, now=start + 0.05 * i)
        detector.process(silence, 0, 0, now=start + 0.5)
    assert detector.stats()["accepted"] == 0
    assert detector.stats()["rejected"]["speech-like"] >= 1


def test_speech_cleanup_and_lazy_worker(monkeypatch):
    assert "code block omitted" in clean_for_speech("Before ```print('secret')``` after")
    said = []

    class Engine:
        def say(self, text):
            said.append(text)

        def runAndWait(self):
            pass

        def stop(self):
            pass

    tts = TextToSpeech()
    monkeypatch.setattr(tts, "_init_engine", lambda: Engine())
    assert tts._thread is None
    tts.say("hello world")
    assert tts.wait(timeout=1)
    assert said == ["hello world"]
    tts.shutdown()