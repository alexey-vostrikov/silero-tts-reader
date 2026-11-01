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
from silero_tts_reader.core.tts_worker import Speech

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


def put(queue_, audio, text: str = "") -> None:
    """Положить в очередь то, что кладёт воркер: звук вместе с текстом.

    Плеер с недавних пор принимает только ``Speech``: текст едет вместе со
    звуком, иначе при возврате к предыдущей фразе пришлось бы сопоставлять их
    по номеру заново. Голый массив — это ошибка, а не совместимость.
    """
    queue_.put(Speech(audio, text))


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


def test_previous_action() -> None:
    """Кнопки ◀◀ и ▶▶: правила заданы в секундах, а не в процентах."""
    print("\n0. Кнопки по фразам: границы в секундах")
    # ◀◀: меньше 2 с — середина прошлой; меньше 5 с — начало текущей;
    # иначе обычная перемотка на 5 секунд назад.
    previous_cases = [
        (0.0, True, ap.ACTION_PREVIOUS),
        (1.9, True, ap.ACTION_PREVIOUS),
        (2.0, True, ap.ACTION_RESTART),
        (4.9, True, ap.ACTION_RESTART),
        (5.0, True, ap.ACTION_SEEK_BACK),
        (9.0, True, ap.ACTION_SEEK_BACK),
        (0.5, False, ap.ACTION_RESTART),   # прошлой фразы нет
    ]
    for played, has_history, expected in previous_cases:
        got = ap.previous_action(played, has_history)
        check(got == expected,
              f"◀◀ {played} с, история {has_history} -> {got}")

    # ▶▶: до конца меньше 7 с — следующая фраза; иначе +5 секунд.
    forward_cases = [
        (0.0, True, ap.ACTION_NEXT),
        (6.9, True, ap.ACTION_NEXT),
        (7.0, True, ap.ACTION_SEEK_FORWARD),
        (12.0, True, ap.ACTION_SEEK_FORWARD),
        (1.0, False, ap.ACTION_SEEK_FORWARD),   # следующей ещё нет
    ]
    for remaining, has_next, expected in forward_cases:
        got = ap.forward_action(remaining, has_next)
        check(got == expected,
              f"▶▶ осталось {remaining} с, следующая {has_next} -> {got}")

    # Границы — именами, а не числами в коде.
    check(ap.PREVIOUS_JUMP_SECONDS == 2.0,
          f"граница «середина прошлой» — 2 с: {ap.PREVIOUS_JUMP_SECONDS}")
    check(ap.RESTART_SECONDS == 5.0,
          f"граница «начало текущей» — 5 с: {ap.RESTART_SECONDS}")
    check(ap.NEXT_JUMP_SECONDS == 7.0,
          f"граница «следующая фраза» — 7 с: {ap.NEXT_JUMP_SECONDS}")
    check(ap.SEEK_STEP == 5.0, f"шаг перемотки — 5 с: {ap.SEEK_STEP}")


def test_previous_returns_quant() -> None:
    """Возврат играет середину прошлой фразы, а текущая не теряется."""
    print("\n0б. ◀◀: середина прошлой фразы, текущая не пропадает")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    played: list[tuple[float, float]] = []   # (амплитуда, доля старта)
    once = threading.Event()

    player = make_player(q, stop)
    original = player._play_segment

    def spy(audio, stream, start_fraction=0.0):
        played.append((round(float(np.max(audio)) * 100, 2), round(start_fraction, 2)))
        # На втором кванте просим вернуться — как нажатие в начале фразы.
        if len(played) == 2 and not once.is_set():
            once.set()
            with player._speed_lock:
                player._previous_request = True
        original(audio, stream, start_fraction)

    player._play_segment = spy

    # Фразы по 0,1 с: проиграно меньше 2 с, значит уходим к середине прошлой.
    for level in (0.01, 0.02, 0.03):
        put(q, np.full(4800, level, dtype=np.float32), f"фраза {level}")
    player.start()

    deadline = time.monotonic() + 10
    while len(played) < 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    player.join(timeout=10)

    check(not player.is_alive(), "плеер завершился")
    check(len(played) >= 4, f"кванты проиграны: {played}")
    if len(played) >= 4:
        check(played[2][0] == played[0][0],
              f"после возврата звучит прошлая фраза: {played}")
        check(played[2][1] == ap.PREVIOUS_START_FRACTION,
              f"она начинается с середины: {played}")
        check(played[3][0] == played[1][0],
              f"текущая фраза не потеряна: {played}")


def test_next_jumps_forward() -> None:
    """▶▶ при малом остатке переходит к следующей фразе, а не доигрывает."""
    print("\n0г. ▶▶: следующая фраза, если до конца мало")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    played: list[float] = []
    once = threading.Event()

    player = make_player(q, stop)
    original = player._play_segment

    def spy(audio, stream, start_fraction=0.0):
        played.append(round(float(np.max(audio)) * 100, 2))
        # На первой фразе просим следующую — остаток заведомо меньше 7 с.
        if len(played) == 1 and not once.is_set():
            once.set()
            with player._speed_lock:
                player._forward_request = True
        original(audio, stream, start_fraction)

    player._play_segment = spy

    for level in (0.01, 0.02):
        put(q, np.full(4800, level, dtype=np.float32), f"фраза {level}")
    player.start()

    deadline = time.monotonic() + 10
    while len(played) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    player.join(timeout=10)

    check(not player.is_alive(), "плеер завершился")
    check(played == [1.0, 2.0], f"порядок фраз: {played}")


def test_previous_button() -> None:
    """Обе кнопки вызывают свои обработчики, а не перемотку вслепую."""
    print("\n0в. Кнопки ◀◀ и ▶▶ в окне плеера")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            raise RuntimeError("нет дисплея")
    except Exception as exc:
        print(f"  — пропущено: {exc}")
        return

    from silero_tts_reader.ui.playback_widget import PlaybackWidget

    widget = PlaybackWidget()
    calls = {"previous": 0, "next": 0, "rewind": 0}
    widget.on_previous = lambda: calls.update(
        previous=calls["previous"] + 1)
    widget.on_next = lambda: calls.update(next=calls["next"] + 1)
    widget.on_rewind = lambda seconds: calls.update(
        rewind=calls["rewind"] + 1)

    widget._btn_rewind.emit("clicked")
    check(calls["previous"] == 1 and calls["rewind"] == 0,
          f"◀◀ ушла в свой обработчик: {calls}")

    widget._btn_forward.emit("clicked")
    check(calls["next"] == 1,
          f"▶▶ ушла в свой обработчик: {calls}")
    check(calls["rewind"] == 0,
          f"перемотка вслепую не вызывается: {calls}")

    # Без обработчиков поведение прежнее — перемотка, а не падение.
    widget.on_previous = None
    widget.on_next = None
    widget._btn_rewind.emit("clicked")
    widget._btn_forward.emit("clicked")
    check(calls["rewind"] == 2, f"запасной путь работает: {calls}")
    widget.destroy()


def test_in_quant_actions() -> None:
    """Внутри фразы: до 5 с — с начала, после — на 5 секунд назад."""
    print("\n0д. ◀◀ внутри фразы: начало или −5 секунд")
    install_stub()
    rate = 48000
    second = 6.0                       # фраза длиной 6 с

    def run(trigger_at: float) -> tuple[list[float], int]:
        """Прогон с нажатием после trigger_at секунд звука."""
        progress: list[float] = []
        q: queue.Queue = queue.Queue()
        stop = threading.Event()
        player = make_player(q, stop, on_progress=progress.append,
                              total_segments=1)

        class Hooked:
            # Поток-заглушка, который сам нажимает кнопку после нужного
            # объёма записанного звука.
            written = 0
            fired = False

            def __init__(self, **kwargs) -> None:
                self.params = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> bool:
                return False

            def write(self, data) -> None:
                Hooked.written += np.asarray(data).size
                # Нажимаем ровно один раз: иначе кнопка сработает на каждой
                # следующей записи и тест уйдёт в бесконечную перемотку.
                if Hooked.written >= trigger_at * rate and not Hooked.fired:
                    Hooked.fired = True
                    with player._speed_lock:
                        player._previous_request = True

        class Device(types.ModuleType):
            OutputStream = Hooked

        ap._sounddevice = Device("sounddevice")

        put(q, np.full(int(rate * second), 0.01, dtype=np.float32),
            "длинная фраза")
        player.start()
        player.join(timeout=20)
        stop.set()
        return progress, Hooked.written

    # Нажатие на 2,5 с: меньше 5 с — фраза начинается заново (откат к нулю).
    restart, written = run(2.5)
    check(written >= int(rate * second) * 0.9,
          f"фраза доиграна после отката: {written / rate:.1f} с")
    check(any(restart[i] > restart[i + 1] for i in range(len(restart) - 1)),
          f"прогресс откатился назад: {[round(v, 2) for v in restart[:6]]}")
    check(min(restart) < 0.1, f"после отката прогресс мал: {round(min(restart), 2)}")

    # Нажатие на 5,5 с: больше 5 с — перемотка на 5 секунд назад.
    seek, _ = run(5.5)
    drops = [round(seek[i + 1], 2) for i in range(len(seek) - 1)
             if seek[i] > seek[i + 1]]
    check(bool(drops), f"прогресс ушёл назад: {drops[:5]}")
    if drops:
        # 5,5 с − 5 с = 0,5 с от начала фразы.
        check(any(v < 0.2 for v in drops),
              f"откат на 5 секунд, а не в начало: {drops[:5]}")


def test_segment_text() -> None:
    """Плеер сообщает текст кванта, который начал звучать."""
    print("\n0е. Текст кванта доходит до окна")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    shown: list[str] = []

    player = make_player(q, stop)
    player._on_segment_text = shown.append
    player.start()

    for level, text in ((0.01, "Первая фраза."), (0.02, "Втор+ая фр+аза.")):
        put(q, np.full(4800, level, dtype=np.float32), text)
    deadline = time.monotonic() + 10
    while len(shown) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    player.join(timeout=10)

    check(shown[:2] == ["Первая фраза.", "Втор+ая фр+аза."],
          f"текст по порядку и с разметкой: {shown}")
    # Разметка не вычищается: в окне должно быть ровно то, что получил синтез.
    check("+" in shown[1], f"знак ударения сохранён: {shown[1]!r}")


def test_segment_text_on_return() -> None:
    """При возврате к предыдущей фразе показывается её текст, а не чужой."""
    print("\n0ж. Возврат: текст совпадает со звуком")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    shown: list[str] = []
    once = threading.Event()

    player = make_player(q, stop)
    player._on_segment_text = shown.append
    original = player._play_segment

    def spy(audio, stream, start_fraction=0.0):
        # На второй фразе просим вернуться — как нажатие в начале.
        if len(shown) == 2 and not once.is_set():
            once.set()
            with player._speed_lock:
                player._previous_request = True
        original(audio, stream, start_fraction)

    player._play_segment = spy

    for level, text in ((0.01, "Первая."), (0.02, "Вторая."), (0.03, "Третья.")):
        put(q, np.full(4800, level, dtype=np.float32), text)
    player.start()

    deadline = time.monotonic() + 10
    while len(shown) < 4 and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    player.join(timeout=10)

    check(len(shown) >= 4, f"фразы показаны: {shown}")
    if len(shown) >= 4:
        check(shown[2] == shown[0], f"после возврата текст прошлой фразы: {shown}")
        check(shown[3] == shown[1], f"текущая фраза показана снова: {shown}")


def test_widget_text_room() -> None:
    """У окна есть место под текст, и надпись принимает строку как есть."""
    print("\n0з. Окно: место под текст синтеза")
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

    widget = pw.PlaybackWidget()
    widget.set_segment_text("Лет+ёха был скор+ее всег+о с бодун+а.")
    check(widget._text_view.get_text() == "Лет+ёха был скор+ее всег+о с бодун+а.",
          f"надпись показывает строку с разметкой: {widget._text_view.get_text()!r}")
    check(widget._text_view.get_wrap(), "длинная фраза переносится, а не обрезается")
    check(widget._text_view.get_lines() >= 2,
          f"строк под текст: {widget._text_view.get_lines()}")

    # Высота окна включает блок текста: иначе надпись обрезалась бы снизу.
    width = widget._metrics["width"]
    height = widget._metrics["height"]
    without_text = int(width * pw.WIDGET_HEIGHT / pw.WIDGET_WIDTH)
    check(height > without_text,
          f"высота {height} больше прежней {without_text} на блок текста")

    widget.set_segment_text("")
    check(widget._text_view.get_text() == "", "пустой текст очищает надпись")
    widget.destroy()


def test_text_goes_with_audio() -> None:
    """Воркер кладёт в очередь звук вместе с текстом, ушедшим в синтез.

    Проверяется настоящий путь: движок сам сообщает подготовленную строку
    (сокращения и ударения), воркер кладёт её рядом со звуком. Модель
    подменена: torch и веса для этой проверки не нужны.
    """
    print("\n0и. Текст едет вместе со звуком")
    from silero_tts_reader.config.config_manager import ConfigManager
    from silero_tts_reader.core.tts_engine import TTSEngine
    from silero_tts_reader.core.tts_worker import TTSWorker

    class FakeAudio:
        def numpy(self):
            return np.zeros(480, dtype=np.float32)

    class FakeModel:
        seen: ClassVar[list[str]] = []

        def apply_tts(self, text, speaker, sample_rate, **kwargs):
            FakeModel.seen.append(text)
            return FakeAudio()

    engine = TTSEngine(sample_rate=48000, models={}, accent_mode="dict",
                       abbreviations="known", stress_source="dictionary")
    engine._models["ru"] = FakeModel()
    engine._speakers["ru"] = ["xenia"]

    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    worker = TTSWorker(
        engine=engine, text="Летёха был скорее всего с бодуна.",
        speaker="xenia", config=ConfigManager(), audio_queue=q,
        stop_event=stop,
    )
    worker.start()
    deadline = time.monotonic() + 15
    while q.empty() and time.monotonic() < deadline:
        time.sleep(0.05)
    stop.set()
    worker.join(timeout=10)

    item = q.get_nowait() if not q.empty() else None
    check(isinstance(item, Speech), f"в очереди Speech, а не голый массив: {item!r}")
    if isinstance(item, Speech):
        check(item.text in FakeModel.seen,
              f"текст совпадает с тем, что получила модель: {item.text!r}")
        check("бодун+а" in item.text,
              f"разметка в тексте сохранилась: {item.text!r}")


def test_single_stream() -> None:
    print("\n1. Один поток вывода на всю сессию")
    install_stub()
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    player = make_player(q, stop)
    player.start()

    tone = np.zeros(4800, dtype=np.float32)   # 0,1 с
    for _ in range(4):
        put(q, tone, "тон")
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

    put(q, np.zeros(4800, dtype=np.float32), "тишина")
    time.sleep(0.2)
    # Битая единица очереди: у неё нет звука. Плеер обязан назвать причину и
    # продолжить работу, а не упасть молча.
    q.put(Speech(object(), "битый квант"))
    time.sleep(0.3)
    put(q, np.zeros(4800, dtype=np.float32), "тишина")   # после ошибки плеер обязан жить
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
    put(q, tone, "тон")
    put(q, tone, "тон")
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
        put(q, tone, "тон")
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
    test_previous_action()
    test_previous_returns_quant()
    test_next_jumps_forward()
    test_in_quant_actions()
    test_previous_button()
    test_segment_text()
    test_segment_text_on_return()
    test_widget_text_room()
    test_text_goes_with_audio()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
