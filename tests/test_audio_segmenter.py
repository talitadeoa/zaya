from __future__ import annotations

import numpy as np

from zora.audio.recorder import UtteranceSegmenter
from zora.config import AudioConfig

SR = 16000
BLOCK = int(SR * 0.03)  # 30ms


def config(**overrides) -> AudioConfig:
    base = {
        "sample_rate": SR,
        "silence_ms": 180,
        "min_speech_ms": 90,
        "preroll_ms": 90,
        "max_seconds": 0.0,
        "speech_threshold_db": -40.0,
    }
    base.update(overrides)
    return AudioConfig(**base)


def frame(amplitude: float) -> np.ndarray:
    return np.full(BLOCK, amplitude, dtype=np.float32)


def test_ends_after_silence():
    segmenter = UtteranceSegmenter(config())
    for _ in range(3):
        assert segmenter.feed(frame(1e-4)) is None
    for _ in range(10):
        assert segmenter.feed(frame(0.2)) is None

    utterance = None
    for _ in range(30):
        utterance = segmenter.feed(frame(1e-4))
        if utterance is not None:
            break
    assert utterance is not None
    assert len(utterance) >= BLOCK * 10
    assert segmenter.speech_started is False


def test_max_seconds_emits_without_silence():
    segmenter = UtteranceSegmenter(config(max_seconds=0.2))
    utterance = None
    for _ in range(20):
        utterance = segmenter.feed(frame(0.2))
        if utterance is not None:
            break
    assert utterance is not None


def test_no_speech_returns_nothing():
    segmenter = UtteranceSegmenter(config())
    for _ in range(20):
        assert segmenter.feed(frame(1e-4)) is None
    assert segmenter.flush() is None


def test_snapshot_returns_captured_audio():
    segmenter = UtteranceSegmenter(config())
    segmenter.start_manual()
    for _ in range(5):
        segmenter.feed(frame(0.2))
    snapshot = segmenter.snapshot()
    assert snapshot is not None
    assert len(snapshot) >= BLOCK * 5

    tail = segmenter.snapshot(max_seconds=0.06)
    assert tail is not None
    assert len(tail) <= BLOCK * 2


def test_manual_start_and_flush():
    segmenter = UtteranceSegmenter(config())
    segmenter.feed(frame(1e-4))
    segmenter.start_manual()
    assert segmenter.speech_started is True
    for _ in range(5):
        segmenter.feed(frame(0.2))
    utterance = segmenter.flush()
    assert utterance is not None
    assert len(utterance) > 0
    assert segmenter.speech_started is False
