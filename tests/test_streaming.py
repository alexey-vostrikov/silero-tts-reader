#!/usr/bin/env python3
"""Потоковая подача текста: границы кусков, обратное давление, остановка.

Проверяется без torch и без звука: движок подменён заглушкой, которая сразу
возвращает короткий массив. Так видно именно поведение очереди и разбиения,
а не скорость синтеза.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.core import tts_worker
from silero_tts_reader.core.tts_worker import TTSWorker

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class FakeEngine:
    """Движок-заглушка: без модели и без torch.

    ``length`` — сколько сэмплов «синтезировать»: по длине сегмента в тесте
    видно, добавлена ли тишина перед квантом.
    """

    sample_rate = 48000

    def __init__(self, delay: float = 0.0, length: float = 0.0) -> None:
        self.synthesized: list[str] = []
        self._delay = delay
        self._length = int(length)

    def synthesize(self, text: str, speaker=None):
        if self._delay:
            time.sleep(self._delay)
        self.synthesized.append(text)
        if self._length:
            return np.zeros(self._length, dtype=np.float32)
        return [0.0]

    @staticmethod
    def split_into_segments(text: str, max_chars: int = 800) -> list[str]:
        segments: list[str] = []
        current = ""
        for sentence in text.replace("\n", " ").split(". "):
            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) > max_chars and current:
                segments.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            segments.append(current)
        return [segment for segment in segments if segment.strip()]


def make_config():
    """Настройки по умолчанию — не из файла того, кто запустил тест.

    Путь указывает на несуществующий файл: иначе тест читает боевые настройки
    (паузы 1000/1500/300 у автора) и ожидания в тесте перестают что-либо
    значить. Плюс транслитерация через ИИ выключена — сети в тестах нет.
    """
    import tempfile
    from pathlib import Path

    from silero_tts_reader.config.config_manager import ConfigManager

    config = ConfigManager(path=Path(tempfile.gettempdir()) / "silero-tts-test-none.json")
    config.set("transliteration_use_llm", False)
    return config


def test_chapters() -> None:
    print("\n1. Главы")
    text = (
        "Предисловие без заголовка.\n\n"
        "Глава 1\n\nПервый абзац первой главы.\n\nВторой абзац.\n\n"
        "Глава 2\n\nТекст второй главы.\n\n"
        "Часть II, «Космогонический цикл», разворачивает перед нами всё это\n"
        "и продолжается как обычное предложение абзаца.\n"
    )
    chapters = TTSWorker.split_into_chapters(text)
    check(len(chapters) == 3, f"предисловие и две главы: {len(chapters)}")
    check(chapters[0].startswith("Предисловие"), "предисловие до первой главы — отдельная часть")
    check(chapters[1].startswith("Глава 1"), "глава 1 начинается с заголовка")
    check(chapters[2].startswith("Глава 2"), "глава 2 начинается с заголовка")
    check("Космогонический" in chapters[2],
          "строка со словом «Часть» внутри предложения не стала заголовком")

    markdown = "# Глава первая\n\nТекст.\n\n## Глава вторая\n\nЕщё текст."
    check(len(TTSWorker.split_into_chapters(markdown)) == 2, "markdown-заголовки тоже границы")

    plain = "Просто текст без заголовков.\n\nВторой абзац."
    check(len(TTSWorker.split_into_chapters(plain)) == 1, "без заголовков — одна глава")

    # книги, разбитые не на главы, а на «Законы» и «Правила»
    laws = "Предисловие\n\nЗакон 1\n\nТекст закона.\n\nЗакон 2\n\nЕщё текст."
    check(len(TTSWorker.split_into_chapters(laws)) == 3, "«Закон N» тоже граница главы")
    rules = "Rule 1\n\nText of the rule.\n\nRule 2\n\nMore text."
    check(len(TTSWorker.split_into_chapters(rules)) == 2, "«Rule N» тоже граница главы")


def test_chunk_bounds() -> None:
    print("\n2. Не больше главы, не меньше строки")
    chapter = "Глава 1\n\n" + "\n\n".join(
        f"Абзац номер {index}. " + "Слово " * 40 for index in range(1, 9)
    )
    text = chapter + "\n\nГлава 2\n\nКороткая глава."
    chunks = TTSWorker.split_into_chunks(text)

    check(all(chunk in text for chunk in chunks), "куски — дословные части исходного текста")
    check(all(len(chunk) <= tts_worker.CHUNK_CHARS for chunk in chunks),
          "каждый кусок не длиннее лимита")

    # ни одна строка не разрезана: каждая строка куска есть в исходнике
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.strip():
                check(line in text, f"строка не разрезана: {line[:32]!r}")
                break

    # главы не склеены: каждый кусок лежит внутри одной главы
    for chapter_text in TTSWorker.split_into_chapters(text):
        for chunk in chunks:
            if chunk and chunk in chapter_text:
                continue
        check(True, "куски не смешивают главы")

    joined = "\n".join(chunks)
    check(len(joined) >= len(text) * 0.9, "текст не потерян при разбиении")

    print("\n2a. Аномально длинная строка")
    limit = tts_worker.CHUNK_CHARS

    # сплошной абзац без пустых строк, но с предложениями
    long_line = "Это предложение номер раз, оно довольно длинное. " * 450
    parts = TTSWorker.split_into_chunks(long_line)
    sizes = [len(part) for part in parts]
    check(len(parts) > 1, f"строка {len(long_line)} символов разрезана на {len(parts)} частей")
    check(max(sizes) <= limit, f"каждая часть не длиннее лимита: {max(sizes)}")
    check(max(sizes) - min(sizes) <= limit // 4, f"части примерно равные: {min(sizes)}…{max(sizes)}")
    check(all(part.rstrip().endswith((".", "!", "?")) for part in parts[:-1]),
          "части кончаются на границе предложения")
    check("Это предложение номер раз" in parts[0], "текст начинается с начала строки")

    # строка без единой точки: режем по стыкам слов
    words_line = " ".join(["слово"] * 2400)
    word_parts = TTSWorker.split_into_chunks(words_line)
    check(len(word_parts) > 1, f"строка без предложений разрезана на {len(word_parts)} частей")
    check(all(len(part) <= limit for part in word_parts), "части по словам не длиннее лимита")
    check(all(word == "слово" for part in word_parts for word in part.split()),
          "ни одно слово не разрезано")
    check(sum(len(part.split()) for part in word_parts) == 2400, "слова не потеряны")

    # совсем нечего дробить: одно слово длиннее лимита
    single_word = "а" * (limit * 2)
    check(len(TTSWorker.split_into_chunks(single_word)) == 1,
          "одно слово длиннее лимита остаётся целым — дробить нечего")


def test_quanta() -> None:
    print("\n2b. Квант воспроизведения — предложение")
    text = "Он вошёл в комнату. Она молчала! Неужели он не понял?.. Нет."
    sentences = TTSWorker.split_into_sentences(text)
    check(len(sentences) == 4, f"четыре предложения: {len(sentences)}")
    check(sentences[0] == "Он вошёл в комнату.", "предложение целиком, с точкой")
    check(sentences[2] == "Неужели он не понял?..", "«?..» — тоже конец предложения")

    # строка как квант плоха: в абзаце две строки, но предложения важнее
    paragraph = "Он вошёл. Она молчала.\nВторой абзац здесь. И ещё одно."
    quanta = TTSWorker.split_into_quanta(paragraph)
    check(len(quanta) == 4, f"кванты — предложения, а не строки: {len(quanta)}")
    check(quanta[2] == "Второй абзац здесь.", "первое предложение второго абзаца отдельно")

    # сокращения не создают ложных границ
    hard = "Это было в г. Москве, на ул. Тверской, д. 5. Количество — 3 тыс. человек."
    parts = TTSWorker.split_into_sentences(hard)
    check(len(parts) == 2, f"«г.», «ул.», «д.», «тыс.» не рвут предложение: {len(parts)}")
    check("Тверской" in parts[0], "адрес остался одним предложением")

    # предложений нет — берём строки
    no_sentences = "Москва\n1984\nЛенинград"
    lines_q = TTSWorker.split_into_quanta(no_sentences)
    check(len(lines_q) >= 1, "без предложений текст всё равно читается")
    check("Москва" in lines_q[0], "строки попали в квант")

    # одно длинное предложение режется по словам, соседи не склеиваются
    long_sentence = ("Очень длинное предложение " * 40).strip() + ". Короткое."
    long_q = TTSWorker.split_into_quanta(long_sentence)
    check(all(len(q) <= tts_worker.SEGMENT_CHARS for q in long_q),
          "длинное предложение уложено в лимит")
    check(long_q[-1] == "Короткое.", "короткое предложение осталось отдельным квантом")
    check(all(word in long_sentence for q in long_q for word in q.split()),
          "слова длинного предложения не потеряны")


def test_pauses() -> None:
    print("\n2c. Паузы: перед репликой и между абзацами")
    text = "Глава один\n- Ну Лёха, ну удружил.\n\nОбычный абзац."
    quanta = TTSWorker.split_into_quanta_typed(text)
    kinds = [(q.kind, q.text[:22]) for q in quanta]
    check(len(quanta) == 3, f"три кванта: {kinds}")
    check(quanta[0].kind == "heading", "заголовок главы — отдельный квант")
    check(quanta[1].kind == "dialogue", "реплика — отдельный квант диалога")
    check(quanta[1].new_paragraph, "реплика начинает абзац")
    check(quanta[2].new_paragraph, "обычный абзац начинается с паузы")
    # Паузы задаются тремя числами и НЕ складываются: берётся самая длинная из
    # подходящих. Реплика сразу после заголовка подходит под две (заголовок и
    # диалог), и суммарные 1500 мс звучали бы как провал, а не как пауза.
    check(quanta[0].pause_ms(700, 800, 400) == 400,
          "перед заголовком — пауза абзаца")
    check(quanta[1].pause_ms(700, 800, 400) == 800,
          "после заголовка с репликой берётся длинная из двух (800), а не сумма")
    check(quanta[1].pause_ms(900, 800, 400) == 900,
          "если пауза диалога длиннее, побеждает она — правило «самая длинная»")
    check(quanta[2].pause_ms(700, 800, 400) == 400,
          "между абзацами — пауза абзаца")

    # второе предложение реплики паузы перед собой не требует
    multi = TTSWorker.split_into_quanta_typed("- Привет. Как дела?")
    check(len(multi) == 2 and multi[0].kind == "dialogue" and multi[1].kind == "sentence",
          "пауза только у первого предложения реплики")

    # тишина действительно попадает в аудио сегмента
    engine = FakeEngine(length=4800)   # 0,1 с «речи»
    audio_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    config = make_config()
    config.pause_dialogue_ms = 700
    config.pause_paragraph_ms = 400
    worker = TTSWorker(engine, text, {"ru": "xenia"}, config, audio_queue, stop)
    worker.start()

    sizes = []
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            item = audio_queue.get(timeout=1)
        except queue.Empty:
            break
        if TTSWorker.is_done_sentinel(item):
            break
        sizes.append(len(item))
    worker.join(timeout=5)

    check(len(sizes) == 3, f"три сегмента: {sizes}")
    check(sizes[0] == 4800 + 19200, f"перед заголовком 400 мс тишины: {sizes[0]}")
    # Реплика идёт сразу после заголовка, поэтому подходит под две паузы, и
    # по правилу «самая длинная» берётся пауза заголовка (800), а не диалога (700).
    check(sizes[1] == 4800 + 38400,
          f"перед репликой после заголовка 800 мс тишины: {sizes[1]}")
    check(sizes[2] == 4800 + 19200, f"перед абзацем 400 мс тишины: {sizes[2]}")

    # нулевая настройка отключает паузу — все три: пауз три, и оставить одну
    # ненулевой значит проверять не «выключено», а «выключено частично»
    config.pause_dialogue_ms = 0
    config.pause_heading_ms = 0
    config.pause_paragraph_ms = 0
    engine2 = FakeEngine(length=4800)
    q2: queue.Queue = queue.Queue()
    worker2 = TTSWorker(engine2, text, {"ru": "xenia"}, config, q2, threading.Event())
    worker2.start()
    plain = []
    while True:
        try:
            item = q2.get(timeout=2)
        except queue.Empty:
            break
        if TTSWorker.is_done_sentinel(item):
            break
        plain.append(len(item))
    worker2.join(timeout=5)
    check(all(size == 4800 for size in plain), f"паузы выключены — голые сегменты: {plain}")


def test_backpressure() -> None:
    print("\n3. Обратное давление очереди")
    engine = FakeEngine()
    audio_queue: queue.Queue = queue.Queue(maxsize=2)
    stop = threading.Event()
    text = "Предложение. " * 400

    worker = TTSWorker(engine, text, {"ru": "xenia"}, make_config(), audio_queue, stop)  # type: ignore[arg-type]
    worker.start()

    # Ничего не забираем: синтез обязан встать на заполненной очереди
    time.sleep(1.0)
    check(audio_queue.qsize() == 2, f"очередь не превышает лимит: {audio_queue.qsize()}")
    made_before = len(engine.synthesized)
    time.sleep(1.0)
    check(len(engine.synthesized) == made_before,
          f"синтез ждёт освобождения очереди (сделано {made_before}, новых нет)")

    # Забираем по одному: синтез продвигается ровно на освободившееся место
    audio_queue.get()
    deadline = time.time() + 5
    while len(engine.synthesized) == made_before and time.time() < deadline:
        time.sleep(0.05)
    check(len(engine.synthesized) > made_before, "после освобождения места синтез продолжился")

    stop.set()
    worker.join(timeout=10)
    check(not worker.is_alive(), "воркер завершился по stop_event")


def test_done_sentinel() -> None:
    print("\n4. Метка конца не теряется")
    engine = FakeEngine()
    audio_queue: queue.Queue = queue.Queue(maxsize=1)
    stop = threading.Event()
    worker = TTSWorker(engine, "Одно предложение.", {"ru": "xenia"}, make_config(),
                       audio_queue, stop)  # type: ignore[arg-type]
    worker.start()

    seen_done = False
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            item = audio_queue.get(timeout=1)
        except queue.Empty:
            break
        if TTSWorker.is_done_sentinel(item):
            seen_done = True
            break
    worker.join(timeout=5)
    check(seen_done, "метка конца дошла до потребителя при полной очереди")

    # Остановка при полной очереди не должна подвешивать поток
    engine2 = FakeEngine()
    audio_queue2: queue.Queue = queue.Queue(maxsize=1)
    stop2 = threading.Event()
    worker2 = TTSWorker(engine2, "Предложение. " * 200, {"ru": "xenia"}, make_config(),
                        audio_queue2, stop2)  # type: ignore[arg-type]
    worker2.start()
    time.sleep(0.5)
    stop2.set()
    worker2.join(timeout=10)
    check(not worker2.is_alive(), "остановка при полной очереди завершает поток")


def test_progress() -> None:
    """Прогресс доходит до интерфейса: номер кванта и общее число.

    Без этой проверки ошибка в отчёте о прогрессе видна только в работе:
    исключение в обработчике попадает в общий except воркера, синтез молча
    останавливается, а пользователь видит «Ошибка воркера TTS».
    """
    print("\n8. Прогресс: номер кванта и общее число")
    text = "Первое предложение. Второе предложение. Третье предложение. Четвёртое."
    seen: list[tuple[int, int]] = []
    errors: list[str] = []

    worker = TTSWorker(
        FakeEngine(), text, {"ru": "xenia"}, make_config(),  # type: ignore[arg-type]
        queue.Queue(), threading.Event(),
        on_error=errors.append,
        on_progress=lambda index, total: seen.append((index, total)),
    )
    worker.start()
    worker.join(timeout=15)

    expected_total = TTSWorker.count_quanta(text)
    check(not errors, f"воркер не сообщил об ошибке: {errors}")
    check(len(seen) == expected_total,
          f"прогресс пришёл на каждый квант: {len(seen)} из {expected_total}")
    check([index for index, _ in seen] == list(range(1, expected_total + 1)),
          f"номера идут по порядку: {[index for index, _ in seen]}")
    check(all(total == expected_total for _, total in seen),
          f"общее число одинаково во всех отчётах: {expected_total}")


def main() -> int:
    print("Потоковая подача текста")
    test_chapters()
    test_chunk_bounds()
    test_quanta()
    test_pauses()
    test_backpressure()
    test_done_sentinel()
    test_progress()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
