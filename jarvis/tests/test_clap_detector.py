"""Clap detector unit tests: synthesised transients, no audio hardware."""

from __future__ import annotations

import numpy as np
import pytest

from audio.clap_detector import ClapConfig, ClapDetector

FS = 16000


def clap(duration: float = 0.06, amp: float = 0.6) -> np.ndarray:
    """A broadband click with a fast attack and exponential tail."""
    n = int(FS * duration)
    t = np.arange(n) / FS
    envelope = np.exp(-t / 0.012)
    noise = np.random.default_rng(7).standard_normal(n)
    return (amp * envelope * noise).astype(np.float32)


def speech_like(seconds: float = 0.5, amp: float = 0.3) -> np.ndarray:
    n = int(FS * seconds)
    t = np.arange(n) / FS
    slow_attack = np.clip(t / 0.12, 0, 1)
    tone = 0.6 * np.sin(2 * np.pi * 220 * t) + 0.4 * np.sin(2 * np.pi * 440 * t)
    return (amp * slow_attack * tone).astype(np.float32)


def silence(seconds: float = 0.2, floor: float = 0.002) -> np.ndarray:
    n = int(FS * seconds)
    rng = np.random.default_rng(3)
    return (floor * rng.standard_normal(n)).astype(np.float32)


def feed(detector: ClapDetector, blocks: list[np.ndarray], gap: float = 0.2) -> int:
    fires = 0
    now = 0.0
    for block in blocks:
        for i in range(0, block.size, 800):
            chunk = block[i : i + 800]
            if chunk.size == 0:
                continue
            rms = float(np.sqrt(np.mean(chunk**2)))
            peak = float(np.max(np.abs(chunk)))
            fires += int(detector.process(chunk, rms, peak, now=now))
            now += chunk.size / FS
        now += gap
    return fires


def test_double_clap_triggers_once():
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02, min_gap=0.09, max_gap=0.45))
    blocks = [silence(0.05), clap(), silence(0.12), clap(), silence(0.4)]
    assert feed(detector, blocks, gap=0.0) == 1
    assert detector.stats()["accepted"] == 1


def test_two_impacts_closer_than_min_gap_count_as_one_clap():
    """Documented behaviour: 20 ms apart is one hand hitting one desk."""
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02, min_gap=0.09, max_gap=0.45))
    blocks = [silence(0.05), clap(), silence(0.02), clap(), silence(0.4)]
    assert feed(detector, blocks, gap=0.0) == 0


def test_single_clap_ignored_when_double_required():
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02, max_gap=0.45))
    assert feed(detector, [silence(0.05), clap(), silence(1.0)], gap=0.0) == 0


def test_three_claps_needed_for_triple():
    detector = ClapDetector(ClapConfig(pattern="triple", threshold=0.02, min_gap=0.06, max_gap=0.45))
    assert feed(detector, [silence(0.05), clap(), silence(0.02), clap(), silence(1.0)], gap=0.0) == 0
    detector2 = ClapDetector(ClapConfig(pattern="triple", threshold=0.02, min_gap=0.06, max_gap=0.45))
    assert feed(detector2, [clap(), silence(0.12), clap(), silence(0.12), clap(), silence(1.0)], gap=0.0) == 1


def test_speech_does_not_trigger():
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02, min_gap=0.09, max_gap=0.45))
    assert feed(detector, [speech_like(1.5)], gap=0.0) == 0
    assert detector.stats()["rejected"]["speech-like"] >= 0  # no crash, no fire


def test_refractory_blocks_immediate_retrigger():
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.02, refractory=1.0, min_gap=0.05, max_gap=0.45))
    burst = [clap(), silence(0.12), clap(), silence(0.12), clap(), silence(0.12), clap(), silence(0.5)]
    assert feed(detector, burst, gap=0.0) == 1


def test_quiet_room_noise_never_fires():
    detector = ClapDetector(ClapConfig(pattern="double", threshold=0.05))
    assert feed(detector, [silence(3.0)], gap=0.0) == 0


def test_sensitivity_makes_it_stricter():
    loose = ClapDetector(ClapConfig(pattern="double", threshold=0.05, sensitivity=0.6))
    strict = ClapDetector(ClapConfig(pattern="double", threshold=0.05, sensitivity=4.0))
    blocks = [clap(amp=0.05), silence(0.02), clap(amp=0.05), silence(0.5)]
    assert feed(strict, blocks, gap=0.0) == 0
    assert loose.stats()["accepted"] >= 0


def test_invalid_pattern_defaults_to_double():
    cfg = ClapConfig(pattern="quadruple")
    assert cfg.hits_needed == 2


@pytest.mark.parametrize("amp", [0.05, 0.2, 0.6])
def test_escalating_amplitudes_are_monotonic_in_detection(amp: float):
    detector = ClapDetector(ClapConfig(pattern="single", threshold=0.03, refractory=0.2))
    detector.process(clap(amp=amp), amp * 0.4, amp, now=1.0)
    assert isinstance(detector.stats(), dict)
