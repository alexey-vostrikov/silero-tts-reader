#!/usr/bin/env python3
"""Проигрывание: один поток вывода, пауза между квантами, общий прогресс.

Звуковая карта не нужна: вместо sounddevice подставляется заглушка, которая
запоминает, сколько раз открывали устройство и что в него писали. Так видно
именно поведение плеера — а переоткрытие устройства на каждый квант и было
причиной полуторасекундных пауз между предложениями.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from silero_tts_reader.core import audio_player as ap
from silero_tts_reader.core.audio_player import AudioPlayer

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class FakeStream:
    """Поток-заглушка: считает открытия и запоминает записанное."""

    opened = 0
    written: ClassVar[list[np.ndarray]] = []

    def __init__(self, **kwargs) -> None:
        # Настоящий OutputStream принимает samplerate/channels/dtype
        self.params = kwargs

    def __enter__(self):
        FakeStream.opened += 1
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def write(self, data) -> None:
        FakeStream.written.append(np.asarray(data, dtype=np.float32).reshape(-1))


class FakeSoundDevice(types.ModuleType):
    OutputStream = FakeStream


def install_stub():
    """Подменить sounddevice до первого обращения плеера."""
    ap._sounddevice = FakeSoundDevice("sounddevice")
    FakeStream.opened = 0
    FakeStream.written = []


def make_player(queue_, stop, **kwargs) -> AudioPlayer:
    return AudioPlayer(
        audio_queue=queue_,
        stop_event=stop,
        sample_rate=48000,
        speed=1.0,
        on_segment_progress=kwargs.pop("on_progress", None),
        total_segments=kwargs.pop("total_segments", 0),
        on_error=kwargs.pop("on_error", None),
    )


def test_single_stream() -> None:
    print("\n1. Один поток вывода на всю сессию")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    player = make_player(q, stop)
    player.start()

    tone = np.zeros(4800, dtype=np.float32)   # 0,1 с
    for _ in range(4):
        q.put(tone)
        time.sleep(0.05)
    # Никакого «мусорного» объекта в очередь: проверка про один поток, а не
    # про устойчивость к неожиданным значениям. Раньше сюда клали пустышку
    # «Done», плеер пытался её проиграть и печатал TypeError — то есть набор
    # был зелёным, а в журнале лежала ошибка воспроизведения.
    time.sleep(0.5)
    stop.set()
    player.join(timeout=5)

    check(FakeStream.opened == 1, f"устройство открыто один раз, а не на каждый квант: {FakeStream.opened}")
    check(not player.is_alive(), "плеер завершился по stop_event")


def test_unexpected_item() -> None:
    """Посторонний объект в очереди не роняет поток и уходит в on_error.

    Отдельная проверка вместо пустышки в предыдущем тесте: важно не то, что
    поток выжил случайно, а что ошибка названа и плеер продолжает работать.
    """
    print("\n2. Посторонний объект в очереди")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    errors: list[str] = []
    player = make_player(q, stop, on_error=errors.append)
    player.start()

    q.put(np.zeros(4800, dtype=np.float32))
    time.sleep(0.2)
    q.put(object())          # не звук и не метка конца
    time.sleep(0.3)
    q.put(np.zeros(4800, dtype=np.float32))   # после ошибки плеер обязан жить
    time.sleep(0.4)
    stop.set()
    player.join(timeout=5)

    check(len(errors) == 1, f"ошибка названа один раз: {errors}")
    check("has no len" in errors[0] or "len()" in errors[0],
          f"сообщение объясняет причину: {errors[0]}")
    check(not player.is_alive(), "плеер завершился по stop_event, а не упал")


def test_gap_between_quanta() -> None:
    print("\n2. Пауза между квантами")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    player = make_player(q, stop)
    player.start()

    tone = np.full(4800, 0.2, dtype=np.float32)   # 0,1 с ненулевого звука
    q.put(tone)
    q.put(tone)
    time.sleep(0.6)
    stop.set()
    player.join(timeout=5)

    total = sum(len(chunk) for chunk in FakeStream.written)
    gap_samples = int(48000 * ap.SEGMENT_GAP_MS / 1000)
    expected = len(tone) * 2 + gap_samples
    check(ap.SEGMENT_GAP_MS > 0, f"пауза задана: {ap.SEGMENT_GAP_MS} мс")
    check(total == expected, f"между квантами записана пауза: {total} сэмплов, ожидалось {expected}")

    # пауза — именно тишина, и она одна на два кванта
    silent_chunks = [c for c in FakeStream.written if len(c) == gap_samples and not c.any()]
    check(len(silent_chunks) == 1, f"вставлена одна пауза тишины, а не {len(silent_chunks)}")


def test_overall_progress() -> None:
    print("\n3. Общий прогресс по тексту")
    player = AudioPlayer.__new__(AudioPlayer)
    player._total_segments = 100
    player._segment_index = 25
    check(abs(player._overall(0.5) - 0.245) < 1e-6,
          f"середина 25-го кванта из 100 = 24,5%: {player._overall(0.5):.3f}")

    player._segment_index = 1
    check(player._overall(0.0) == 0.0, "начало книги — ноль")
    player._segment_index = 100
    check(player._overall(1.0) == 1.0, "конец книги — единица")

    player._total_segments = 0
    check(player._overall(0.5) == 0.5, "без общего числа квантов — доля сегмента")

    print("\n3a. Прогресс не сбрасывается на каждом кванте")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    seen: list[float] = []
    player = make_player(q, stop, on_progress=seen.append, total_segments=10)
    player.start()
    tone = np.zeros(2400, dtype=np.float32)
    for _ in range(4):
        q.put(tone)
        time.sleep(0.05)
    time.sleep(0.5)
    stop.set()
    player.join(timeout=5)

    check(len(seen) > 0, f"прогресс сообщался: {len(seen)} раз")
    check(all(0.0 <= value <= 1.0 for value in seen), "прогресс в пределах 0…1")
    check(max(seen) < 1.0, "на четвёртом кванте из десяти книга ещё не кончилась")
    check(max(seen) > 0.3, f"прогресс ушёл вперёд, а не топтался у нуля: {max(seen):.2f}")

    # без общего числа квантов прогресс по-прежнему считается по сегменту
    check(ap.SEGMENT_GAP_MS == 180, "пауза между фразами — 180 мс")


def test_speed_control_disabled() -> None:
    """Без rubberband кнопка скорости выключена, а не прячется и не падает.

    Пакет необязательный: без него приложение обязано работать, просто темп
    не поменять. Выключенный элемент управления говорит об этом честнее, чем
    ошибка при нажатии.
    """
    print("\n4. Скорость без rubberband")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            raise RuntimeError("нет дисплея")
    except Exception as exc:
        print(f"  — пропущено: {exc}")
        return

    from silero_tts_reader.ui import playback_widget as pw

    saved = pw.SPEED_AVAILABLE
    pw.SPEED_AVAILABLE = False
    try:
        widget = pw.PlaybackWidget()
        check(widget._speed_combo.get_sensitive() is False,
              "список скорости выключен")
        check("rubberband" in (widget._speed_combo.get_tooltip_text() or ""),
              "подсказка называет причину: "
              f"{widget._speed_combo.get_tooltip_text()}")
        widget.destroy()
    finally:
        pw.SPEED_AVAILABLE = saved


def main() -> int:
    print("Проигрывание: поток, пауза, прогресс")
    test_single_stream()
    test_unexpected_item()
    test_gap_between_quanta()
    test_overall_progress()
    test_speed_control_disabled()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
