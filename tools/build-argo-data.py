"""Сбор словаря русского арго: заголовок статьи и место ударения.

Источник — «Словарь русского арго» В. С. Елистратова (ГРАМОТА.РУ, 2002),
зеркало на academic.ru. Статьи пронумерованы подряд, ударная гласная в теле
статьи выделена тегом ``<u>``: «БОД<u>У</u>Н» — боду́н. Это и есть надёжный
источник ударения, в отличие от догадок модели на незнакомых словах.

Сбор идёт долго (тысячи страниц, сервер отвечает 1,5–3 с и включает
ограничение частоты), поэтому пишем построчно в JSONL сразу после каждой
статьи: прерванный прогон продолжается с места остановки, а не с начала.

    python3 tools/build-argo-data.py --from 1 --to 300 --out /tmp/argo.jsonl
    python3 tools/build-argo-data.py                       # весь словарь
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = "https://russian_argo.academic.ru"
USER_AGENT = "Mozilla/5.0 (silero-tts-reader stress data build)"
DEFAULT_LAST_ID = 15600          # последняя статья словаря
PAUSE = 0.4                       # пауза между запросами: сервер отвечает 429
RETRIES = 4

# Заголовок статьи: <dt itemprop="title" class="term">бодун</dt>
_TERM = re.compile(r'<dt[^>]*class="term"[^>]*>(.*?)</dt>', re.S)
# Тело статьи: <dd itemprop="content" class="descript">БОД<u>У</u>Н, -а, …
_ENTRY = re.compile(r'<dd[^>]*class="descript"[^>]*>(.*?)</dd>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё\-]*")


def fetch(entry_id: int) -> str | None:
    """Скачать страницу статьи. ``None`` — статьи с таким номером нет.

    Через curl, а не urllib: хост называется ``russian_argo.academic.ru``, и
    проверка имени в Python отвергает сертификат ``*.academic.ru`` из-за
    подчёркивания в метке (curl этот сертификат принимает и проверяет
    по-настоящему). Отключать проверку нельзя — это был бы другой риск.

    Ошибки разделены: 429 и 502 — «подожди и повтори», редирект за пределы
    словаря означает, что нумерация кончилась.
    """
    url = f"{BASE}/{entry_id}"
    for attempt in range(RETRIES):
        result = subprocess.run(
            ["curl", "-sS", "-m", "25", "-A", USER_AGENT,
             "-w", "\n%{http_code}", url],
            capture_output=True, text=True, check=False,
        )
        body, _, code = result.stdout.rpartition("\n")
        if code == "200":
            return body
        if code in ("301", "302", "404"):
            return None
        time.sleep(1.5 * (attempt + 1))
    return None


def stress_from_markup(marked: str) -> str | None:
    """Превратить «БОД<u>У</u>Н» в «бодун» с пометкой «+» перед ударной гласной.

    Именно перед гласной — в такой нотации ждёт синтез, и в такой же лежит
    наш словарь ударений.
    """
    letters: list[str] = []
    stress_at: int | None = None
    position = 0
    for chunk in re.split(r"(<u>.*?</u>)", marked, flags=re.S):
        if not chunk:
            continue
        inner = re.fullmatch(r"<u>(.*?)</u>", chunk, re.S)
        if inner is not None:
            text = _TAGS.sub("", inner.group(1))
            if stress_at is None and text:
                stress_at = position
            letters.append(text)
            position += len(text)
        else:
            text = _TAGS.sub("", chunk)
            letters.append(text)
            position += len(text)
    word = "".join(letters).strip()
    if not word or stress_at is None:
        return None
    marked_word = word[:stress_at] + "+" + word[stress_at:]
    return marked_word


def parse_entry(entry_id: int, html: str) -> dict | None:
    """Достать слово и ударение из разметки статьи.

    Ударная гласная выделена тегом ``<u>`` и стоит в теле статьи, в записи
    заголовка: «БО<u>Д</u>…». Второй и третий ``<u>`` в той же строке — это
    окончания («-<u>а</u>»), поэтому берём только часть до первой запятой.
    """
    term_match = _TERM.search(html)
    entry_match = _ENTRY.search(html)
    if term_match is None or entry_match is None:
        return None

    term = _TAGS.sub("", term_match.group(1)).strip().lower()
    # Многословные заголовки — идиомы: в словарь ударений они не годятся.
    if not term or " " in term or not _WORD.fullmatch(term):
        return None

    body = entry_match.group(1)
    # Запись заголовка кончается первой запятой: дальше идут падежные формы
    # и пометы, у которых своя разметка ударения.
    head = body.split(",")[0]
    marked = stress_from_markup(head)
    if marked is None:
        return None

    word = marked.replace("+", "").strip().lower()
    if word != term:
        # Заголовок и разметка разошлись — запись непонятна, лучше пропустить.
        return None
    if marked.count("+") != 1:
        return None
    index = marked.index("+")
    if index == 0 or index + 1 >= len(marked):
        return None
    if marked[index + 1].lower() not in "аеёиоуыэюя":
        return None

    return {
        "id": entry_id,
        "word": word,
        "marked": marked.lower(),
    }


def pack(source: Path, target: Path) -> int:
    """Собрать JSONL в сжатый файл данных «слово<TAB>помеченное».

    Формат простой и построчный: словарь грузится целиком в память при первом
    обращении, и разбор JSON на шесть тысяч строк дороже, чем чтение пар.
    """
    import lzma

    rows: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        word, marked = item.get("word"), item.get("marked")
        if not word or not marked or marked.count("+") != 1:
            continue
        # Одна запись на слово: первая встреченная считается основной.
        rows.setdefault(word, marked)

    payload = "\n".join(f"{w}\t{m}" for w, m in sorted(rows.items())) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(lzma.compress(payload.encode("utf-8"), preset=9))
    print(f"  упаковано {len(rows)} слов → {target} "
          f"({target.stat().st_size / 1024:.0f} КБ)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="stop", type=int, default=DEFAULT_LAST_ID)
    parser.add_argument("--out", default="argo.jsonl")
    parser.add_argument("--pack", nargs="?", const="argo.jsonl", metavar="ФАЙЛ",
                        default=None,
                        help="только упаковать уже собранный JSONL в данные")
    args = parser.parse_args()

    if args.pack:
        # --pack без значения: упаковываем файл, из которого шёл сбор.
        target = Path(__file__).resolve().parent.parent \
            / "silero_tts_reader" / "assets" / "stress" / "argo.txt.xz"
        return pack(Path(args.pack), target)

    out = Path(args.out)
    done: set[int] = set()
    if out.is_file():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
        print(f"  продолжаю: уже собрано {len(done)} статей", flush=True)

    stats = {"read": 0, "kept": 0, "empty": 0, "bad": 0}
    began = time.monotonic()
    with out.open("a", encoding="utf-8") as handle:
        for entry_id in range(args.start, args.stop + 1):
            if entry_id in done:
                continue
            html = fetch(entry_id)
            stats["read"] += 1
            if html is None:
                stats["empty"] += 1
                time.sleep(PAUSE)
                continue
            parsed = parse_entry(entry_id, html)
            if parsed is None:
                stats["bad"] += 1
            else:
                handle.write(json.dumps(parsed, ensure_ascii=False) + "\n")
                handle.flush()
                stats["kept"] += 1
            if stats["read"] % 100 == 0:
                rate = stats["read"] / (time.monotonic() - began)
                left = (args.stop - entry_id) / rate if rate else 0
                print(f"  {entry_id}: собрано {stats['kept']}, "
                      f"{rate:.1f} статей/с, осталось ~{left / 60:.0f} мин",
                      flush=True)
            time.sleep(PAUSE)

    print(f"  итог: прочитано {stats['read']}, собрано {stats['kept']}, "
          f"пустых {stats['empty']}, без разметки {stats['bad']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
