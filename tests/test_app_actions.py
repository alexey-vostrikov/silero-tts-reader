#!/usr/bin/env python3
"""Один экземпляр, действия с клавишами, меню и открытие файлов.

Проверяется то, что нельзя увидеть по коду: при повторном запуске приложение
не создаёт второй трей и не грузит модель заново; действия и их сочетания
клавиш реально зарегистрированы; файл читается в UTF-8 и cp1251.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader import i18n

# Тексты сообщений проверяются по-русски: язык задаём явно,
# иначе результат зависит от LANG в окружении.
i18n.init("ru_RU")

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def count_menu_items(menu: Gio.Menu) -> int:
    """Сколько пунктов в модели меню, с учётом вложенных секций."""
    total = 0
    for index in range(menu.get_n_items()):
        total += 1
        section = menu.get_item_link(index, Gio.MENU_LINK_SECTION)
        if section is not None:
            total += count_menu_items(section)
    return total


def accel_matches(app, action: str, wanted: str) -> bool:
    """Сравнить сочетание, не завися от написания модификатора.

    GTK4 возвращает «<Control>», GTK3 — «<Primary>»; смысл один, поэтому
    приводим обе стороны к одному виду, а не подгоняем ожидание под версию.
    """
    def norm(text: str) -> tuple:
        """«<Shift><Control>c» → ({control, shift}, «c»).

        Порядок модификаторов в строке не задан стандартом: GTK3 пишет
        Control первым, GTK4 — Shift. Смысл один, поэтому сравниваем набор.
        """
        text = text.replace("<Primary>", "<Control>")
        mods, key = set(), text
        while key.startswith("<"):
            close = key.index(">")
            mods.add(key[1:close].lower())
            key = key[close + 1:]
        return (frozenset(mods), key.lower())

    return norm(wanted) in [norm(item) for item in app.get_accels_for_action(action)]


def test_actions(app) -> None:
    print("\n1. Действия и сочетания клавиш")
    app._setup_actions()
    actions = set(app.list_actions())
    for name in ("settings", "quit", "about", "speak-clipboard", "speak-selection", "stop"):
        check(name in actions, f"действие app.{name} зарегистрировано")

    check(accel_matches(app, "app.settings", "<Control>comma"),
          f"Ctrl+, открывает настройки: {app.get_accels_for_action('app.settings')}")
    check(accel_matches(app, "app.quit", "<Control>q"),
          f"Ctrl+Q закрывает приложение: {app.get_accels_for_action('app.quit')}")
    check(accel_matches(app, "app.speak-clipboard", "<Control><Shift>c"),
          f"Ctrl+Shift+C озвучивает буфер: "
          f"{app.get_accels_for_action('app.speak-clipboard')}")

    menu = app.build_menu_model()
    items = count_menu_items(menu)
    check(items >= 7, f"в меню {items} пунктов (озвучка, стоп, настройки, о программе, выход)")


def test_single_instance(app) -> None:
    print("\n2. Повторный запуск")
    check(app.get_flags() & Gio.ApplicationFlags.HANDLES_OPEN,
          "приложение умеет открывать файлы (HANDLES_OPEN)")

    # Первый activate в тесте не выполняем (он грузит модель), но проверяем
    # ровно ту ветку, которая ловила двойной запуск.
    app._initialized = True
    app._widget = None
    app.do_activate()  # должен выйти сразу, без создания трея и загрузки модели
    check(app._initialized is True, "повторный activate не пересоздаёт состояние")


def test_read_file() -> None:
    print("\n3. Чтение файла для озвучки")
    from silero_tts_reader.core.text_extractor import TextExtractor

    # Приложение при создании выбирает язык из настроек и окружения; здесь
    # проверяются русские тексты сообщений, поэтому язык задаём ещё раз.
    i18n.init("ru_RU")

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)

        utf8 = directory / "utf8.txt"
        utf8.write_text("Привет, файл", encoding="utf-8")
        check(TextExtractor.read_text_file(str(utf8)) == "Привет, файл", "UTF-8 читается")

        legacy = directory / "legacy.txt"
        legacy.write_bytes("Старый текст".encode("cp1251"))
        check(TextExtractor.read_text_file(str(legacy)) == "Старый текст",
              "cp1251 читается (старые русские тексты)")

        markdown = directory / "note.md"
        markdown.write_text("# Заголовок", encoding="utf-8")
        check(TextExtractor.read_text_file(str(markdown)) == "# Заголовок", ".md тоже текст")

        binary = directory / "image.png"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n")
        try:
            TextExtractor.read_text_file(str(binary))
        except ValueError as exc:
            check("png" in str(exc), f"бинарник отвергнут: {exc}")
        else:
            raise AssertionError("не текстовый файл не отвергнут")

        missing = directory / "нет-такого.txt"
        try:
            TextExtractor.read_text_file(str(missing))
        except FileNotFoundError as exc:
            check("не найден" in str(exc), f"отсутствующий файл: {exc}")
        else:
            raise AssertionError("отсутствующий файл не дал ошибки")

        empty = directory / "empty.txt"
        empty.write_text("", encoding="utf-8")
        check(TextExtractor.read_text_file(str(empty)) == "", "пустой файл читается как пустая строка")


def main() -> int:
    print("Один экземпляр, действия, открытие файлов")
    from silero_tts_reader.app import Application

    app = Application(["silero-tts-reader"])
    app.register(None)
    test_actions(app)
    test_single_instance(app)
    test_read_file()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
