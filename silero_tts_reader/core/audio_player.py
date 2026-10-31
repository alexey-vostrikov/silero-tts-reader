"""Audio player — streams synthesized audio with pause/resume/stop and speed control."""
from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable

import numpy as np

from . import rate as rate_module
from .tts_worker import TTSWorker

# Скорость меняет rubberband-cli: он в зависимостях пакета, а Python-обёртка
# pyrubberband в архиве Ubuntu отсутствует. Раньше требовались оба, поэтому в
# установленной системе регулировка скорости молча не работала.
_SPEED_AVAILABLE = rate_module.available()
SPEED_AVAILABLE = _SPEED_AVAILABLE

# Пауза между квантами: граница фразы должна слышаться, но коротко. Раньше её
# роль играл разрыв от переоткрытия аудиоустройства на каждый квант — он выходил
# около полутора секунд и звучал как пауза после дефиса в начале реплики.
SEGMENT_GAP_MS = 180

# Кнопки перемотки по фразам. Границы заданы в секундах, а не в долях:
# человек слышит время, а не проценты, и «две секунды» понятнее, чем «5 %».
# Внутри одного кванта 2 с — это самое начало фразы, 5 с — уже середина.
PREVIOUS_JUMP_SECONDS = 2.0     # меньше — уходим к середине прошлой фразы
RESTART_SECONDS = 5.0           # меньше — начинаем текущую фразу заново
NEXT_JUMP_SECONDS = 7.0         # меньше осталось — переходим к следующей фразе
SEEK_STEP = 5.0                 # обычный шаг перемотки внутри фразы

# Где начинать повтор предыдущей фразы: середина.
PREVIOUS_START_FRACTION = 0.5

# Сколько квантов помнить для возврата. Один квант — это фраза, обычно
# 5–15 секунд звука; восемь дают возврат на несколько фраз назад и занимают
# десятки мегабайт, а не сотни.
HISTORY_DEPTH = 8

ACTION_PREVIOUS = "previous"    # к середине предыдущей фразы
ACTION_RESTART = "restart"      # к началу текущей
ACTION_SEEK_BACK = "seek_back"  # на 5 секунд назад
ACTION_NEXT = "next"            # к началу следующей
ACTION_SEEK_FORWARD = "seek"    # на 5 секунд вперёд


def previous_action(played_seconds: float, has_history: bool) -> str:
    """Что делает «◀◀» через столько секунд после начала фразы.

    Правило из трёх ступеней: совсем начало фразы (меньше 2 с) — вернуться к
    середине предыдущей; до 5 с — услышать текущую с начала; дальше — обычная
    перемотка на 5 секунд назад.
    """
    if played_seconds < PREVIOUS_JUMP_SECONDS and has_history:
        return ACTION_PREVIOUS
    if played_seconds < RESTART_SECONDS:
        return ACTION_RESTART
    return ACTION_SEEK_BACK


def forward_action(remaining_seconds: float, has_next: bool) -> str:
    """Что делает «▶▶» за столько секунд до конца фразы.

    Симметрично «◀◀»: если до конца фразы меньше 7 секунд, переходим к началу
    следующей, иначе перематываем вперёд на 5 секунд.
    """
    if remaining_seconds < NEXT_JUMP_SECONDS and has_next:
        return ACTION_NEXT
    return ACTION_SEEK_FORWARD


_sounddevice = None


def _sd():
    """sounddevice по требованию: без PortAudio модуль должен импортироваться.

    Раньше импорт на уровне модуля делал неимпортируемым всё приложение в
    headless-окружении (CI, сервер), хотя звук там и не нужен.
    """
    global _sounddevice
    if _sounddevice is None:
        import sounddevice
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
        total_segments: int = 0,
        on_started: Callable[[], None] | None = None,
        on_segment_progress: Callable[[float], None] | None = None,
        on_paused: Callable[[], None] | None = None,
        on_resumed: Callable[[], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        on_stopped: Callable[[], None] | None = None,
        on_speed_unavailable: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
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
        # Сколько всего квантов в тексте: полоска должна показывать
        # место в книге, а не «сколько сыграно от предложения».
        self._total_segments = int(total_segments)
        self._segment_index = 0
        self._on_paused = on_paused
        self._on_resumed = on_resumed
        self._on_finished = on_finished
        self._on_stopped = on_stopped
        self._on_speed_unavailable = on_speed_unavailable
        self._on_error = on_error

        self._seek_request: float | None = None
        self._seek_fraction_request: float | None = None
        # Возврат к предыдущему кванту: очередь расходуется по одному, поэтому
        # сыгранное хранится отдельно, а повтор идёт мимо очереди.
        self._history: deque[np.ndarray] = deque(maxlen=HISTORY_DEPTH)
        self._pending: list[np.ndarray] = []
        # Повтор фразы: массив и доля, с которой начинать (0.5 — середина).
        self._replay: tuple[np.ndarray, float] | None = None
        self._back_requested = False
        self._next_requested = False
        self._previous_request = False
        self._forward_request = False
        self._played_fraction = 0.0

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

    def previous_quant(self) -> None:
        """Кнопка «предыдущий квант».

        Решение принимает поток воспроизведения: ему известна проигранная доля
        текущего кванта. Здесь только ставится запрос.
        """
        with self._speed_lock:
            self._previous_request = True

    def next_quant(self) -> None:
        """Кнопка «следующий квант»: шаг вперёд по правилу forward_action."""
        with self._speed_lock:
            self._forward_request = True

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
        first = True

        try:
            self._run_loop_locked(started, first)
        except Exception as exc:
            # Поток звука не должен умирать молча: без этого сообщения
            # приложение просто перестаёт говорить, и причину не видно ни в
            # журнале, ни в интерфейсе.
            import traceback

            traceback.print_exc()
            print(f"[AudioPlayer] Ошибка воспроизведения: {exc}", flush=True)
            if self._on_error:
                self._on_error(str(exc))

    def _run_loop_locked(self, started: bool, first: bool) -> None:
        # Один поток вывода на всю сессию. Раньше устройство открывалось на
        # каждый квант: закрытие и открытие PortAudio/PipeWire давало разрыв
        # около полутора секунд между предложениями — он и звучал как «пауза
        # после дефиса», хотя в самом аудио тишины почти нет.
        with _sd().OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
        ) as stream:
            while not self._stop_event.is_set():
                # Порядок источников: отложенный квант, повтор из истории,
                # затем очередь. Очередь расходуется по одному и обратно не
                # отдаёт, поэтому возврат идёт через два своих списка.
                start_fraction = 0.0
                if self._replay is not None:
                    # Возврат: этот квант играется немедленно, отложенный
                    # (текущий) ждёт в pending до своего конца.
                    raw, start_fraction = self._replay
                    self._replay = None
                elif self._pending:
                    raw = self._pending.pop()
                else:
                    try:
                        item = self._queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    if TTSWorker.is_done_sentinel(item):
                        break

                    raw: np.ndarray = item  # type: ignore[assignment]

                audio = raw
                current_speed = self.speed
                if current_speed != 1.0 and SPEED_AVAILABLE:
                    audio = self._stretch(audio, current_speed)

                if not started:
                    started = True
                    if self._on_started:
                        self._on_started()

                # Пауза на границе фраз — намеренная и короткая, вместо
                # случайного разрыва от переоткрытия устройства.
                if not first:
                    self._write_gap(stream)

                self._segment_index += 1
                self._back_requested = False
                self._next_requested = False
                self._play_segment(audio, stream, start_fraction)
                if self._next_requested:
                    # Переход к следующей фразе: текущая остаётся в истории, но
                    # в pending не идёт — её хвост пропускается намеренно.
                    self._history.append(raw)
                    first = False
                    continue
                if self._back_requested:
                    # Текущий квант отложен: после повторного предыдущего он
                    # должен прозвучать снова, поэтому пропускать его нельзя.
                    self._pending.append(raw)
                else:
                    self._history.append(raw)
                first = False

        if self._stop_event.is_set():
            if self._on_stopped:
                self._on_stopped()
        else:
            if self._on_finished:
                self._on_finished()

    def _write_gap(self, stream) -> None:
        """Тишина между квантами: пауза на границе предложения."""
        if SEGMENT_GAP_MS <= 0:
            return
        samples = int(self._sample_rate * SEGMENT_GAP_MS / 1000)
        if samples > 0:
            stream.write(np.zeros((samples, 1), dtype=np.float32))

    # ── Private helpers ───────────────────────────────────────────────────────

    def _play_segment(self, audio: np.ndarray, stream,
                      start_fraction: float = 0.0) -> None:
        """Проиграть один квант в уже открытый поток.

        Поток открывается один раз на всю сессию (см. run): переоткрытие
        устройства на каждый квант давало полуторасекундный разрыв между
        предложениями.
        """
        chunk_size = int(self._sample_rate * 0.1)   # 100 ms chunks
        total_samples = len(audio)
        pos = int(max(0.0, min(1.0, start_fraction)) * total_samples)

        while pos < total_samples and not self._stop_event.is_set():
            # Process pending seek request even if paused
            with self._speed_lock:
                if self._previous_request:
                    self._previous_request = False
                    played = pos / self._sample_rate
                    action = previous_action(played, bool(self._history))
                    if action == ACTION_PREVIOUS:
                        # К середине предыдущей фразы. Счётчик откатываем на
                        # один: полоска не должна прыгнуть вперёд.
                        self._replay = (self._history.pop(),
                                        PREVIOUS_START_FRACTION)
                        self._back_requested = True
                        self._segment_index = max(0, self._segment_index - 1)
                        self._played_fraction = 0.0
                        return
                    if action == ACTION_RESTART:
                        pos = 0
                    else:
                        pos = max(0, pos - int(SEEK_STEP * self._sample_rate))
                    self._played_fraction = pos / total_samples if total_samples else 0.0
                    if self._on_segment_progress and total_samples > 0:
                        self._on_segment_progress(self._overall(pos / total_samples))
                    continue

                if self._forward_request:
                    self._forward_request = False
                    remaining = (total_samples - pos) / self._sample_rate
                    has_next = self._queue.qsize() > 0
                    if forward_action(remaining, has_next) == ACTION_NEXT:
                        # К началу следующей фразы: текущую не доигрываем.
                        self._next_requested = True
                        return
                    pos = min(total_samples, pos + int(SEEK_STEP * self._sample_rate))
                    self._played_fraction = pos / total_samples if total_samples else 0.0
                    if self._on_segment_progress and total_samples > 0:
                        self._on_segment_progress(self._overall(pos / total_samples))
                    continue
                if self._seek_request is not None:
                    delta_samples = int(self._seek_request * self._sample_rate)
                    pos = max(0, min(total_samples, pos + delta_samples))
                    self._seek_request = None
                    if self._on_segment_progress and total_samples > 0:
                        self._on_segment_progress(self._overall(pos / total_samples))
                elif self._seek_fraction_request is not None:
                    pos = int(self._seek_fraction_request * total_samples)
                    pos = max(0, min(total_samples, pos))
                    self._seek_fraction_request = None
                    if self._on_segment_progress and total_samples > 0:
                        self._on_segment_progress(self._overall(pos / total_samples))

            # Honor pause (timeout allows processing seeks while paused)
            if not self._pause_event.wait(timeout=0.05):
                continue

            if self._stop_event.is_set():
                break

            chunk = audio[pos: pos + chunk_size]
            if len(chunk) > 0:
                stream.write(chunk.reshape(-1, 1))
                pos += len(chunk)

            if total_samples > 0:
                self._played_fraction = pos / total_samples
            if self._on_segment_progress and total_samples > 0:
                self._on_segment_progress(self._overall(pos / total_samples))

    def _overall(self, segment_fraction: float) -> float:
        """Доля по всему тексту: (номер кванта − 1 + доля внутри) / всего.

        Полоска прогресса должна показывать место в книге. Раньше в неё уходила
        доля текущего предложения, поэтому она бежала с нуля на каждой фразе.
        """
        if self._total_segments <= 0:
            return min(segment_fraction, 1.0)
        done = max(0, self._segment_index - 1) + max(0.0, min(segment_fraction, 1.0))
        return min(done / self._total_segments, 1.0)

    def _stretch(self, audio: np.ndarray, speed: float) -> np.ndarray:
        """Изменить скорость, не меняя тон: speed > 1 быстрее, < 1 медленнее."""
        return rate_module.time_stretch(audio, self._sample_rate, float(speed))
