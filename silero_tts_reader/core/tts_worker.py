"""TTS worker thread — синтез сегментов и подача их в аудиоочередь.

Книга целиком в память не попадает: текст режется на куски по абзацам, каждый
кусок обрабатывается (ссылки, числа, транслитерация) и делится на предложения,
а сегменты уходят в очередь с ограничением. Когда очередь полна, синтез ждёт,
пока проигрыватель не заберёт готовое, — так книга на несколько часов читается
в темпе речи, а не занимает память часами звука.
"""
from __future__ import annotations

import queue
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..config.config_manager import ConfigManager
from .text_processor import TextProcessor
from .tts_engine import TTSEngine

# Sentinel to signal end of stream
_DONE = object()


@dataclass(frozen=True)
class Quantum:
    """Единица воспроизведения вместе с тем, как она начинается.

    Пауза ставится не «между всеми сегментами одинаково», а по смыслу: перед
    репликой диалога нужна заметная, на границе абзаца — покороче. Поэтому
    воркеру мало текста, ему нужен вид кванта.
    """

    text: str
    kind: str = "sentence"          # sentence | dialogue | heading
    new_paragraph: bool = False     # первый квант абзаца

    def pause_ms(self, dialogue_ms: int, paragraph_ms: int) -> int:
        """Сколько тишины поставить перед этим квантом."""
        if self.kind == "dialogue":
            return max(0, int(dialogue_ms))
        if self.new_paragraph:
            return max(0, int(paragraph_ms))
        return 0

# Размер куска для обработки текста: достаточно, чтобы не дробить обращения к
# ИИ-транслитератору на каждое предложение, и мало, чтобы не держать в памяти
# обработанную книгу целиком.
CHUNK_CHARS = 4000

# Границы глав: строка-заголовок. Проверка строгая — короткая строка без
# запятых, иначе обычное предложение «Часть II, «Космогонический цикл»,
# разворачивает перед нами…» было бы принято за начало главы.
# Предел для одного сегмента. Обычно квант — предложение и он короче, но одно
# предложение бывает и на 1000 символов: тогда режем по словам, а не склеиваем
# соседние предложения.
SEGMENT_CHARS = 800

# Сокращения, после которых точка не заканчивает предложение: «г. Москва»,
# «т. е.», «рис. 3». Без защиты здесь появлялись бы ложные границы.
_ABBR = re.compile(
    r"\b(?:г|гр|д|доц|зам|зав|им|ин|кг|кв|км|л|м|мг|мл|мм|"
    r"млн|млрд|напр|оз|пр|проф|р|руб|рис|с|см|стр|т|тел|"
    r"тыс|ул|ф|чел|эт|т\.\s*е|т\.\s*д|т\.\s*п)\.(?=\s|$)"
)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# Кроме глав встречаются книги, разбитые на «Законы» и «Правила»: у Грина это
# и есть единица чтения, иначе вся книга считается одной главой.
_CHAPTER_KEYWORDS = (
    r"(?:Глава|ГЛАВА|Chapter|CHAPTER|Часть|ЧАСТЬ|Part|PART|Пролог|ПРОЛОГ|"
    r"Эпилог|ЭПИЛОГ|Prologue|Epilogue|Книга|BOOK|Закон|ЗАКОН|Rule|RULE|"
    r"Раздел|SECTION)"
)
_CHAPTER_RE = re.compile(
    rf"(?m)^[ \t]*(?:{_CHAPTER_KEYWORDS}\b[^\n]{{0,50}}|#{{1,6}}[ \t][^\n]{{1,60}})[ \t]*$"
)


def _is_heading(line: str) -> bool:
    """Похожа ли строка на заголовок главы, а не на начало абзаца."""
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return False
    if "," in stripped or "—" in stripped and len(stripped.split()) > 8:
        return False
    if len(stripped.split()) > 8:
        return False
    return bool(_CHAPTER_RE.match(stripped))


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
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="TTSWorker")
        self._engine = engine
        self._text = text
        self._speaker = speaker
        self._config = config
        self._audio_queue = audio_queue
        self._stop_event = stop_event
        self._on_error = on_error
        self._on_progress = on_progress

    # ── Разбиение ────────────────────────────────────────────────────────────

    @staticmethod
    def split_into_chapters(text: str) -> list[str]:
        """Разделить текст по заголовкам глав.

        Нет заголовков — весь текст считается одной главой: тогда работает
        разбиение по строкам внутри неё.
        """
        lines = text.splitlines(keepends=True)
        starts: list[int] = []
        offset = 0
        for line in lines:
            if _is_heading(line):
                starts.append(offset)
            offset += len(line)

        if not starts:
            return [text.strip()] if text.strip() else []

        chapters: list[str] = []
        if starts[0] > 0:
            preface = text[: starts[0]].strip()
            if preface:
                chapters.append(preface)
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            chapter = text[start:end].strip()
            if chapter:
                chapters.append(chapter)
        return chapters

    @staticmethod
    def _split_long_line(line: str, limit: int) -> list[str]:
        """Разрезать аномально длинную строку на примерно равные части.

        Строка длиннее лимита — это уже не абзац, а сбой вёрстки или книги,
        поэтому её режем: сначала по стыкам предложений, а если предложений нет
        (сплошной поток без точек) — по стыкам слов. Части делаем равными, чтобы
        не получить «одна огромная и хвост в два слова».
        """
        line = line.strip()
        if len(line) <= limit:
            return [line]

        parts_needed = max(2, (len(line) + limit - 1) // limit)
        target = max(1, len(line) // parts_needed)

        sentences = [part for part in re.split(r"(?<=[.!?…])\s+", line) if part.strip()]
        if len(sentences) > 1 and max(len(part) for part in sentences) <= limit:
            units, joiner = sentences, " "
        else:
            words = line.split()
            units = words if len(words) > 1 else [line]
            joiner = " "

        parts: list[str] = []
        current = ""
        for unit in units:
            if current and (len(current) + len(unit) + 1 > limit or len(current) >= target):
                parts.append(current)
                current = unit
            else:
                current = f"{current}{joiner}{unit}".strip() if current else unit
        if current:
            parts.append(current)
        return parts or [line]

    @staticmethod
    def split_into_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
        """Куски для синтеза: не больше главы, не меньше строки.

        Глава целиком — обычный случай. Если она длиннее лимита, она режется по
        строкам (абзацам). Строка — минимальная единица, но аномально длинную
        строку (больше лимита) приходится делить: по стыкам предложений, а если
        их нет — по стыкам слов.
        """
        chunks: list[str] = []
        for chapter in TTSWorker.split_into_chapters(text):
            if len(chapter) <= limit:
                chunks.append(chapter)
                continue

            current = ""
            for line in chapter.splitlines():
                if not line.strip():
                    continue
                if len(line) > limit:
                    if current.strip():
                        chunks.append(current.strip())
                        current = ""
                    chunks.extend(TTSWorker._split_long_line(line, limit))
                    continue
                if current and len(current) + len(line) + 1 > limit:
                    chunks.append(current.strip())
                    current = line
                elif current:
                    current = f"{current}\n{line}"
                else:
                    current = line
            if current.strip():
                chunks.append(current.strip())
        return chunks or ([text.strip()] if text.strip() else [])

    @staticmethod
    def split_into_sentences(text: str) -> list[str]:
        """Разбить текст на предложения — это и есть квант воспроизведения."""
        protected = _ABBR.sub(lambda match: match.group()[:-1] + "\u00b7", text)
        parts = [part.replace("\u00b7", ".").strip() for part in _SENTENCE_END.split(protected)]
        return [part for part in parts if part]

    @staticmethod
    def _is_dialogue(line: str) -> bool:
        """Реплика диалога: строка начинается с тире.

        «- Ну Лёха, ну удружил.» — отдельная реплика, а не продолжение заголовка
        главы. Без этого заголовок и первая реплика склеиваются в один квант:
        «Глава один\n- Ну Лёха, ну удружил.»
        """
        return line.lstrip().startswith(("-", "–", "—"))

    @staticmethod
    def split_into_quanta_typed(text: str, limit: int = SEGMENT_CHARS) -> list["Quantum"]:
        """Кванты с видом: предложение, заголовок, реплика диалога.

        Строка как квант плоха: в абзаце их несколько, и синтез получает обрывки
        без конца фразы. Поэтому основа — предложение. Квантуются строкой две
        вещи, иначе они слипаются с соседним текстом:

        * заголовок главы («Глава один») — отдельная фраза;
        * реплика диалога, начинающаяся с тире, — она начинается с паузы.

        Если предложений в тексте нет вовсе (список, стихи без точек), берём
        строки, а совсем короткие строки склеиваем, чтобы не сыпать обрывками.
        """
        quanta: list[Quantum] = []
        buffer: list[str] = []
        pending_paragraph = True   # следующий квант начинает абзац

        def emit(texts: list[str], kind: str, rest_kind: str = "sentence") -> None:
            """Добавить кванты; вид получает только первый — пауза нужна ему.

            Второе предложение реплики паузы перед собой не требует: оно
            продолжает ту же прямую речь.
            """
            nonlocal pending_paragraph
            for index, piece in enumerate(texts):
                if not piece.strip():
                    continue
                quanta.append(Quantum(
                    piece.strip(),
                    kind if index == 0 else rest_kind,
                    pending_paragraph,
                ))
                pending_paragraph = False

        def split_piece(piece: str) -> list[str]:
            if len(piece) <= limit:
                return [piece]
            return TTSWorker._split_long_line(piece, limit)

        def flush() -> None:
            """Отдать накопленные строки, разрезав их по предложениям."""
            if not buffer:
                return
            chunk = " ".join(buffer)
            buffer.clear()
            sentences = TTSWorker.split_into_sentences(chunk)
            units = sentences if len(sentences) > 1 else [chunk]
            pieces: list[str] = []
            for unit in units:
                pieces.extend(split_piece(unit))
            emit(pieces, "sentence")

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                flush()
                pending_paragraph = True
                continue
            if _is_heading(stripped):
                flush()
                pending_paragraph = True
                emit(split_piece(stripped), "heading")
                pending_paragraph = True
                continue
            if TTSWorker._is_dialogue(stripped):
                flush()
                # Реплика всегда начинается с паузы: это прямая речь, а не
                # продолжение предыдущей фразы.
                pending_paragraph = True
                pieces: list[str] = []
                for sentence in TTSWorker.split_into_sentences(stripped):
                    pieces.extend(split_piece(sentence))
                emit(pieces, "dialogue")
                pending_paragraph = True
                continue
            buffer.append(stripped)

        flush()

        if not quanta:
            sentences = TTSWorker.split_into_sentences(text)
            units = sentences if len(sentences) > 1 else [text.strip()]
            pieces = []
            for unit in units:
                pieces.extend(split_piece(unit))
            emit(pieces, "sentence")

        return [q for q in quanta if q.text]

    @staticmethod
    def split_into_quanta(text: str, limit: int = SEGMENT_CHARS) -> list[str]:
        """Только текст квантов — для проверок и подсчёта прогресса."""
        return [q.text for q in TTSWorker.split_into_quanta_typed(text, limit)]

    @staticmethod
    def _merge_short(lines: list[str], limit: int) -> list[str]:
        """Склеить короткие строки в блоки до лимита: одна строка — плохой квант."""
        merged: list[str] = []
        current = ""
        for line in lines:
            if current and len(current) + len(line) + 1 > limit:
                merged.append(current)
                current = line
            else:
                current = f"{current} {line}".strip() if current else line
        if current:
            merged.append(current)
        return merged

    @staticmethod
    def count_quanta(text: str) -> int:
        """Сколько квантов будет у всего текста — для общей полосы прогресса.

        Считается по необработанному тексту: обработка (ссылки, числа) меняет
        длину, но не число фраз. Полоска прогресса не требует точности до
        единицы, а полный проход обработки стоил бы секунд на старте.
        """
        try:
            return len(TTSWorker.split_into_quanta(text))
        except Exception:
            return 0

    @staticmethod
    def iter_quanta(
        engine: TTSEngine,
        text: str,
        config: ConfigManager,
    ) -> Iterator["Quantum"]:
        """Кванты по всему тексту, с видом: воркеру нужны паузы по смыслу."""
        for chunk in TTSWorker.split_into_chunks(text):
            processed = TextProcessor.process_text(chunk, config)
            yield from TTSWorker.split_into_quanta_typed(processed)

    @staticmethod
    def iter_segments(
        engine: TTSEngine,
        text: str,
        config: ConfigManager,
    ) -> Iterator[str]:
        """Сегменты по всему тексту, без сборки списка.

        Обрабатывается кусок текста (глава), а синтезируется квант —
        предложение. Куски нужны только чтобы обработка (ссылки, числа,
        транслитерация) шла пачками, а не по одному предложению.
        """
        for chunk in TTSWorker.split_into_chunks(text):
            processed = TextProcessor.process_text(chunk, config)
            for segment in TTSWorker.split_into_quanta(processed):
                if segment.strip():
                    yield segment

    # ── Поток ────────────────────────────────────────────────────────────────

    def _with_pause(self, audio, quantum: "Quantum"):
        """Добавить тишину перед квантом прямо в аудио.

        Пауза живёт внутри сегмента, а не в проигрывателе: тогда она слышна
        независимо от того, как устроен вывод, и её видно в тестах без
        звуковой карты. Раньше «пауза» получалась случайно — переоткрытием
        аудиоустройства между сегментами.
        """
        pause_ms = quantum.pause_ms(
            getattr(self._config, "pause_dialogue_ms", 0),
            getattr(self._config, "pause_paragraph_ms", 0),
        )
        if pause_ms <= 0:
            return audio
        samples = int(self._engine.sample_rate * pause_ms / 1000)
        if samples <= 0:
            return audio
        silence = np.zeros(samples, dtype=np.float32)
        return np.concatenate([silence, audio])

    def _put(self, item: object) -> bool:
        """Положить в очередь, дождавшись места. False — воспроизведение остановлено."""
        while not self._stop_event.is_set():
            try:
                self._audio_queue.put(item, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def run(self) -> None:
        try:
            chunks = self.split_into_chunks(self._text)
            print(f"[TTSWorker] Старт: {len(self._text)} символов, кусков {len(chunks)}", flush=True)
            index = 0
            for quantum in self.iter_quanta(self._engine, self._text, self._config):
                if self._stop_event.is_set():
                    print("[TTSWorker] Остановлено", flush=True)
                    break
                index += 1
                audio = self._engine.synthesize(quantum.text, self._speaker)
                audio = self._with_pause(audio, quantum)
                if not self._put(audio):
                    print("[TTSWorker] Остановлено при ожидании места в очереди", flush=True)
                    break
                if self._on_progress:
                    self._on_progress(index, len(segment))
            print(f"[TTSWorker] Синтезировано сегментов: {index}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTSWorker] Ошибка воркера TTS: {exc}", flush=True)
            if self._on_error:
                self._on_error(str(exc))
        finally:
            # Метка конца тоже уходит в ограниченную очередь — с ожиданием,
            # иначе остановка проигрывания подвесила бы поток навсегда.
            while True:
                try:
                    self._audio_queue.put(_DONE, timeout=0.2)
                    break
                except queue.Full:
                    if self._stop_event.is_set():
                        break
                    continue

    @staticmethod
    def is_done_sentinel(item: object) -> bool:
        return item is _DONE
