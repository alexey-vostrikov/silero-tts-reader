"""Audio player — streams synthesized audio with pause/resume/stop and speed control."""
from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from . import rate as rate_module
from .tts_worker import TTSWorker


# Скорость меняет rubberband-cli: он в зависимостях пакета, а Python-обёртка
# pyrubberband в архиве Ubuntu отсутствует. Раньше требовались оба, поэтому в
# установленной системе регулировка скорости молча не работала.
_SPEED_AVAILABLE = rate_module.available()
SPEED_AVAILABLE = _SPEED_AVAILABLE

_sounddevice = None


def _sd():
    """sounddevice по требованию: без PortAudio модуль должен импортироваться.

    Раньше импорт на уровне модуля делал неимпортируемым всё приложение в
    headless-окружении (CI, сервер), хотя звук там и не нужен.
    """
    global _sounddevice
    if _sounddevice is None:
        import sounddevice  # noqa: PLC0415
        _sounddevice = sounddevice
    return _sounddevice


class AudioPlayer(threading.Thread):
    """Plays audio from a queue produced by TTSWorker.

    Signals (called from the player thread, but safe to connect to Qt slots):
        on_started()                 — playback begun
        on_segment_progress(float)  — 0.0–1.0 progress within current segment
        on_paused()
        on_resumed()
        on_finished()                — all audio consumed
        on_stopped()                 — stopped by user
        on_speed_unavailable()       — rubberband not installed
    """

    def __init__(
        self,
        audio_queue: queue.Queue,
        stop_event: threading.Event,
        sample_rate: int = 48000,
        speed: float = 1.0,
        on_started: Callable[[], None] | None = None,
        on_segment_progress: Callable[[float], None] | None = None,
        on_paused: Callable[[], None] | None = None,
        on_resumed: Callable[[], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        on_stopped: Callable[[], None] | None = None,
        on_speed_unavailable: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="AudioPlayer")
        self._queue = audio_queue
        self._stop_event = stop_event
        self._sample_rate = sample_rate
        self._speed = float(speed)
        self._speed_lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()   # not paused initially

        # Callbacks
        self._on_started = on_started
        self._on_segment_progress = on_segment_progress
        self._on_paused = on_paused
        self._on_resumed = on_resumed
        self._on_finished = on_finished
        self._on_stopped = on_stopped
        self._on_speed_unavailable = on_speed_unavailable

        self._seek_request: float | None = None
        self._seek_fraction_request: float | None = None

        if speed != 1.0 and not SPEED_AVAILABLE:
            if on_speed_unavailable:
                on_speed_unavailable()
            self._speed = 1.0

    @property
    def speed(self) -> float:
        with self._speed_lock:
            return self._speed

    def set_speed(self, speed: float) -> None:
        """Dynamically update playback speed in real-time."""
        with self._speed_lock:
            if SPEED_AVAILABLE:
                self._speed = max(0.25, min(4.0, float(speed)))

    def seek_relative(self, seconds: float) -> None:
        """Seek relative to current playback position by specified seconds (+5 or -5)."""
        with self._speed_lock:
            self._seek_request = float(seconds)

    def seek_fraction(self, fraction: float) -> None:
        """Seek to a fraction (0.0 to 1.0) of current audio segment."""
        with self._speed_lock:
            self._seek_fraction_request = max(0.0, min(1.0, float(fraction)))

    def pause(self) -> None:
        self._pause_event.clear()
        if self._on_paused:
            self._on_paused()

    def resume(self) -> None:
        self._pause_event.set()
        if self._on_resumed:
            self._on_resumed()

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()   # unblock if paused

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    # ── Thread body ───────────────────────────────────────────────────────────

    def run(self) -> None:
        started = False

        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if TTSWorker.is_done_sentinel(item):
                break

            audio: np.ndarray = item  # type: ignore[assignment]

            current_speed = self.speed
            if current_speed != 1.0 and SPEED_AVAILABLE:
                audio = self._stretch(audio, current_speed)

            if not started:
                started = True
                if self._on_started:
                    self._on_started()

            self._play_segment(audio)

        if self._stop_event.is_set():
            if self._on_stopped:
                self._on_stopped()
        else:
            if self._on_finished:
                self._on_finished()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _play_segment(self, audio: np.ndarray) -> None:
        """Play a single audio segment with chunk-based pause/stop and seek support."""
        chunk_size = int(self._sample_rate * 0.1)   # 100 ms chunks
        total_samples = len(audio)
        pos = 0

        with _sd().OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
        ) as stream:
            while pos < total_samples and not self._stop_event.is_set():
                # Process pending seek request even if paused
                with self._speed_lock:
                    if self._seek_request is not None:
                        delta_samples = int(self._seek_request * self._sample_rate)
                        pos = max(0, min(total_samples, pos + delta_samples))
                        self._seek_request = None
                        if self._on_segment_progress and total_samples > 0:
                            self._on_segment_progress(min(pos / total_samples, 1.0))
                    elif self._seek_fraction_request is not None:
                        pos = int(self._seek_fraction_request * total_samples)
                        pos = max(0, min(total_samples, pos))
                        self._seek_fraction_request = None
                        if self._on_segment_progress and total_samples > 0:
                            self._on_segment_progress(min(pos / total_samples, 1.0))

                # Honor pause (timeout allows processing seeks while paused)
                if not self._pause_event.wait(timeout=0.05):
                    continue

                if self._stop_event.is_set():
                    break

                chunk = audio[pos: pos + chunk_size]
                if len(chunk) > 0:
                    stream.write(chunk.reshape(-1, 1))
                    pos += len(chunk)

                if self._on_segment_progress and total_samples > 0:
                    self._on_segment_progress(min(pos / total_samples, 1.0))

    def _stretch(self, audio: np.ndarray, speed: float) -> np.ndarray:
        """Изменить скорость, не меняя тон: speed > 1 быстрее, < 1 медленнее."""
        return rate_module.time_stretch(audio, self._sample_rate, float(speed))
