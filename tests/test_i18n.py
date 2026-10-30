#!/usr/bin/env python3
"""i18n: выбор языка и применение каталога en_US.

Проверяется то, что легко сломать незаметно: язык определяется из окружения,
каталог реально применяется, а непереведённых строк не остаётся (иначе в
английском интерфейсе всплывал бы русский текст).
"""
from __future__ import annotations

import gettext
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader import i18n

CHECKS = 0
MO = Path(__file__).resolve().parent.parent / "locale" / "en_US" / "LC_MESSAGES" / "silero-tts-reader.mo"


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def with_env(**values: str | None) -> dict[str, str | None]:
    saved = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return saved


def restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_normalize() -> None:
    print("\n1. Разбор кодов языков")
    cases = {
        "ru": "ru_RU",
        "ru_RU": "ru_RU",
        "ru_RU.UTF-8": "ru_RU",
        "en": "en_US",
        "en_US.UTF-8": "en_US",
        "en-GB": "en_US",
        "de_DE": None,
        "": None,
        None: None,
    }
    for raw, expected in cases.items():
        got = i18n.normalize_language(raw)
        check(got == expected, f"{raw!r} → {got!r}")


def test_detect() -> None:
    print("\n2. Выбор языка")
    empty = {"LANGUAGE": None, "LC_ALL": None, "LC_MESSAGES": None, "LANG": None}
    saved = with_env(**empty)
    try:
        check(i18n.detect_language(None) == "ru_RU", "без окружения — ru_RU")
        check(i18n.detect_language("en_US") == "en_US", "явный языковой аргумент важнее всего")
        check(i18n.detect_language("auto") == "ru_RU", "auto означает «смотреть окружение»")
    finally:
        restore(saved)

    saved = with_env(LANGUAGE=None, LC_ALL=None, LC_MESSAGES=None, LANG="en_US.UTF-8")
    try:
        check(i18n.detect_language(None) == "en_US", "LANG=en_US.UTF-8 → en_US")
        check(i18n.detect_language("ru_RU") == "ru_RU", "явный ru_RU важнее LANG")
    finally:
        restore(saved)

    saved = with_env(LANGUAGE="en_US:ru_RU", LC_ALL=None, LC_MESSAGES=None, LANG="ru_RU.UTF-8")
    try:
        check(i18n.detect_language(None) == "en_US", "список в LANGUAGE — берём первый")
    finally:
        restore(saved)

    saved = with_env(LANGUAGE=None, LC_ALL=None, LC_MESSAGES=None, LANG="C")
    try:
        check(i18n.detect_language(None) == "ru_RU", "LANG=C → язык по умолчанию")
    finally:
        restore(saved)


def test_catalog() -> None:
    print("\n3. Каталог en_US")
    check(MO.is_file(), f"каталог собран: {MO.name}")
    with MO.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)

    pairs = {
        "Текст не выделен": "No text selected",
        "Буфер обмена не содержит текст": "The clipboard contains no text",
        "Не удалось синтезировать речь": "Could not synthesise speech",
        "Отмена": "Cancel",
        "Сохранить": "Save",
        "Скорость воспроизведения": "Playback speed",
        "🔊 Воспроизведение": "🔊 Playback",
        "📋  Озвучить буфер обмена": "📋  Speak the clipboard",
        "✖  Выход": "✖  Quit",
    }
    for russian, english in pairs.items():
        got = catalog.gettext(russian)
        check(got == english, f"{russian!r} → {got!r}")


def test_init_and_apply() -> None:
    print("\n4. Переключение языка на живом модуле")
    i18n.init("ru_RU")
    check(i18n.current_language() == "ru_RU", "режим ru_RU")
    check(i18n._("Текст не выделен") == "Текст не выделен", "ru_RU оставляет исходный текст")
    check(i18n.language_name() == "Русский", "имя языка ru_RU")

    i18n.init("en_US")
    check(i18n.current_language() == "en_US", "режим en_US")
    check(i18n._("Текст не выделен") == "No text selected", "en_US переводит")
    check(i18n.language_name() == "English", "имя языка en_US")

    # подстановка значений в переведённую строку работает
    sheet = i18n._("Привет! Это голос {speaker} со скоростью {speed}x.")
    check(
        sheet.format(speaker="xenia", speed="1.5")
        == "Hello! This is the voice xenia at speed 1.5x.",
        f"форматирование: {sheet!r}",
    )

    # неизвестная строка возвращается как есть, без падения
    check(i18n._("нет такой строки") == "нет такой строки", "неизвестная строка не ломает вывод")

    i18n.init("ru_RU")  # вернуть исходное состояние для остальных тестов


def test_no_untranslated() -> None:
    print("\n5. Полнота перевода")
    pot = Path(__file__).resolve().parent.parent / "locale" / "silero-tts-reader.pot"
    if not pot.is_file():
        check(False, "нет .pot — нечего сверять")
        return

    import re

    raw = pot.read_text(encoding="utf-8")
    msgids: list[str] = []
    for block in re.findall(r'(?m)^msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)', raw):
        text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block))
        if text:
            msgids.append(text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\"))

    with MO.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)
    untranslated = [msg for msg in msgids if catalog.gettext(msg) == msg]
    check(not untranslated,
          f"переведены все {len(msgids)} строк"
          + (f"; без перевода: {untranslated[:3]}" if untranslated else ""))


def main() -> int:
    print("Локализация en_US / ru_RU")
    test_normalize()
    test_detect()
    test_catalog()
    test_init_and_apply()
    test_no_untranslated()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
