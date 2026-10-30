"""TTS worker thread — synthesizes segments and feeds the audio queue."""
from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from .tts_engine import TTSEngine
from .text_processor import TextProcessor
from ..config.config_manager import ConfigManager


# Sentinel to signal end of stream
_DONE = object()


class TTSWorker(threading.Thread):
    """Synthesizes text segments in a background thread.

    Produces numpy audio arrays into `audio_queue`.
    Emits `on_error(str)` on synthesis failures.
    """

    def __init__(
        self,
        engine: TTSEngine,
        text: str,
        speaker: str | dict[str, str],
        config: ConfigManager,
        audio_queue: queue.Queue,
        stop_event: threading.Event,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="TTSWorker")
        self._engine = engine
        self._text = text
        self._speaker = speaker
        self._config = config
        self._audio_queue = audio_queue
        self._stop_event = stop_event
        self._on_error = on_error

    def run(self) -> None:
        try:
            print(f"[TTSWorker] Запуск фонового воркера для текста: {self._text!r}", flush=True)
            processed_text = TextProcessor.process_text(self._text, self._config)
            segments = self._engine.split_into_segments(processed_text)
            print(f"[TTSWorker] Текст разбит на {len(segments)} сегментов: {segments!r}", flush=True)
            for idx, segment in enumerate(segments, 1):
                if self._stop_event.is_set():
                    print("[TTSWorker] Воспроизведение остановлено по событию stop_event.", flush=True)
                    break
                if not segment.strip():
                    continue
                print(f"[TTSWorker] Синтез сегмента [{idx}/{len(segments)}]: {segment!r}", flush=True)
                audio = self._engine.synthesize(segment, self._speaker)
                self._audio_queue.put(audio)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTSWorker] Ошибка воркера TTS: {exc}", flush=True)
            if self._on_error:
                self._on_error(str(exc))
        finally:
            self._audio_queue.put(_DONE)

    @staticmethod
    def is_done_sentinel(item: object) -> bool:
        return item is _DONE
