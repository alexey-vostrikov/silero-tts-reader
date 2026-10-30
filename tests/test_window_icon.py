#!/usr/bin/env python3
"""Значок окна: согласие кода с .desktop-файлом и установка значка на окно.

Главная проверка — согласие идентификаторов. GNOME ищет иконку окна по app-id
(на Wayland) и по WM_CLASS (на X11), а файл .desktop — по тому же имени. Когда
они расходятся, рабочий стол рисует вместо значка серую «шестерёнку», и это
единственный симптом: ни ошибки, ни предупреждения в журнале нет.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.ui import window_icon

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def desktop_value(key: str) -> str:
    """Значение ключа из .desktop в репозитории."""
    for line in (ROOT / "silero-tts-reader.desktop").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def test_contract_with_desktop() -> None:
    """Идентификаторы в коде и в .desktop — одни и те же."""
    print("\n1. Согласие с .desktop-файлом")
    check(window_icon.APP_ID == "ru.skala.silero-tts-reader",
          f"app-id в коде: {window_icon.APP_ID}")
    check(desktop_value("StartupWMClass") == window_icon.APP_ID,
          "StartupWMClass совпадает с app-id")
    check(desktop_value("Icon") == window_icon.ICON_NAME,
          f"Icon совпадает с именем иконки: {window_icon.ICON_NAME}")


def test_packaging_name() -> None:
    """Пакет ставит .desktop под именем app-id.

    Файл с другим именем (silero-tts-reader.desktop) рабочий стол не находит:
    под Wayland поиск идёт ровно по app-id окна.
    """
    print("\n2. Имя установленного .desktop")
    script = (ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8")
    check('APP_ID=ru.skala.silero-tts-reader' in script,
          "скрипт сборки задаёт app-id")
    check('applications/$APP_ID.desktop' in script,
          "файл ставится под этим именем")
    check(not re.search(r'applications/\$READER\.desktop', script),
          "старого имени файла ($READER.desktop) не осталось")


def test_icon_file() -> None:
    """Файл иконки находится и существует."""
    print("\n3. Файл иконки")
    path = window_icon.icon_file()
    check(bool(path), f"путь найден: {path}")
    check(Path(path).is_file(), "файл существует")
    check(Path(path).name == f"{window_icon.ICON_NAME}.png",
          f"имя файла совпадает с именем иконки: {Path(path).name}")


def test_apply_icon() -> None:
    """Окно получает и класс, и имя иконки — оба нужны разным рабочим столам."""
    print("\n4. Значок на окне")
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    try:
        if not Gtk.init_check():
            raise RuntimeError("GTK не инициализировался")
        window = Gtk.Window()
    except Exception as exc:
        # Без дисплея окно не создать; молча «пройти» здесь значит соврать,
        # поэтому пропуск называется вслух и с причиной.
        print(f"  — пропущено: нет дисплея ({exc}); нужен Xvfb или живая сессия")
        return

    window_icon.apply_icon(window, "Проверка значка")
    check(window.get_icon_name() == window_icon.ICON_NAME,
          f"icon-name выставлен: {window.get_icon_name()}")
    check(window.get_title() == "Проверка значка",
          "заголовок выставлен тем же вызовом")
    # В GTK4 пиксели окну не подставляются: тема ищет иконку по имени, а имя
    # по умолчанию задаётся на процесс — это и проверяем.
    check(Gtk.Window.get_default_icon_name() == window_icon.ICON_NAME,
          f"иконка по умолчанию: {Gtk.Window.get_default_icon_name()}")


def main() -> int:
    print("Значок окна")
    test_contract_with_desktop()
    test_packaging_name()
    test_icon_file()
    test_apply_icon()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
