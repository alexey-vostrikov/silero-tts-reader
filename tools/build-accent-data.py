#!/usr/bin/env python3
"""Собрать данные ударений из проекта russian_g2p в файлы пакета.

Источник — репозиторий https://github.com/nsu-ai/russian_g2p (лицензия MIT).
Берутся два файла:

    russian_g2p/data/Accents.json    1,5 млн словоформ
    russian_g2p/data/homographs.json 10,9 тыс. омографов с морфологическими ключами

Главное, что здесь происходит: смена нотации. В источнике знак стоит ПОСЛЕ
ударной гласной (``за+мок`` — это за́мок, ``ноги+`` — ноги́), а Silero TTS ждёт
знак ПЕРЕД гласной (``з+амок``, ``ног+и``). Без этой замены каждая пометка
сдвинула бы ударение на слог, и модель послушно произнесла бы неверно.

Плоский список пишется по одной ударённой форме на строку и сортируется по
слову без пометки: от этого порядка зависит двоичный поиск в
``silero_tts_reader/core/accent.py``. Сортировка по слову без пометки, а не по
самой строке, гарантирует, что поиск найдёт все варианты слова подряд.

Запуск:

    python3 tools/build-accent-data.py --source ~/russian_g2p

Результат кладётся в silero_tts_reader/assets/stress/.
"""

from __future__ import annotations

import argparse
import json
import lzma
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "silero_tts_reader" / "assets" / "stress"

VOWELS = set("аеёиоуыэюя")


def to_before_vowel(form: str) -> str | None:
    """Переставить знак из позиции «после гласной» в «перед гласной».

    ``за+мок`` -> ``з+амок``, ``ноги+`` -> ``ног+и``. Возвращает ``None``, если
    разметка в источнике неверна: знак не после гласной, знаков несколько или
    гласной нет вовсе.
    """
    plain = form.replace("'", "")
    if plain.count("+") != 1:
        return None
    mark = plain.index("+")
    if mark == 0:
        return None
    stressed = mark - 1
    letters = plain.replace("+", "")
    if letters[stressed] not in VOWELS:
        return None
    return "".join(("+" if index == stressed else "") + letter
                   for index, letter in enumerate(letters))


def convert_homographs(entries: dict) -> tuple[dict, int]:
    """Привести словарь омографов к формату Silero, выбросив негодные записи."""
    result: dict[str, dict[str, str]] = {}
    dropped = 0
    for word, variants in entries.items():
        if not isinstance(variants, dict):
            dropped += 1
            continue
        converted: dict[str, str] = {}
        for tag, form in variants.items():
            if not isinstance(form, str):
                dropped += 1
                continue
            fixed = to_before_vowel(form)
            if fixed is None:
                dropped += 1
                continue
            converted[tag] = fixed
        if converted:
            result[word] = converted
    return result, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="каталог russian_g2p с подкаталогом russian_g2p/data")
    parser.add_argument("--keep-plain", action="store_true",
                        help="оставить распакованный forms.txt рядом")
    args = parser.parse_args()

    data = args.source / "russian_g2p" / "data"
    accents_path = data / "Accents.json"
    homographs_path = data / "homographs.json"
    for path in (accents_path, homographs_path):
        if not path.is_file():
            print(f"  ✗ нет файла: {path}", file=sys.stderr)
            return 1

    with accents_path.open(encoding="utf-8", errors="ignore") as handle:
        parts = json.load(handle)
    # Часть 1 — омографы с разборами, часть 2 — плоский список ударённых форм.
    flat = parts[1]

    forms: set[str] = set()
    dropped = 0
    for form in flat:
        if not form:
            continue
        fixed = to_before_vowel(form)
        if fixed is None:
            # Неверная разметка: лучше не ставить ничего, чем поставить
            # неверно — модель умеет угадывать сама, но не спорит с меткой.
            dropped += 1
            continue
        forms.add(fixed)

    ordered = sorted(forms, key=lambda form: (form.replace("+", ""), form))
    blob = "\n".join(ordered) + "\n"
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "forms.txt.xz").write_bytes(lzma.compress(blob.encode("utf-8"), preset=6))
    if args.keep_plain:
        (TARGET / "forms.txt").write_text(blob, encoding="utf-8")

    with homographs_path.open(encoding="utf-8", errors="ignore") as handle:
        raw_homographs = json.load(handle)
    homographs, homograph_dropped = convert_homographs(raw_homographs)
    homograph_blob = json.dumps(
        homographs, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    (TARGET / "homographs.json.xz").write_bytes(
        lzma.compress(homograph_blob, preset=6))

    forms_size = (TARGET / "forms.txt.xz").stat().st_size
    homographs_size = (TARGET / "homographs.json.xz").stat().st_size
    with_tags = sum(1 for variants in homographs.values()
                    if all(tag[:1].isalpha() for tag in variants))

    print(f"  ✓ словоформ: {len(ordered):,}")
    if dropped:
        print(f"  — пропущено словоформ с негодной разметкой: {dropped:,}")
    print(f"  ✓ омографов: {len(homographs):,}, из них с разбором: {with_tags:,}")
    if homograph_dropped:
        print(f"  — пропущено записей омографов: {homograph_dropped:,}")
    print(f"  ✓ forms.txt.xz: {forms_size / 1e6:.1f} МБ")
    print(f"  ✓ homographs.json.xz: {homographs_size / 1e6:.1f} МБ")
    print(f"  ✓ всего: {(forms_size + homographs_size) / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
