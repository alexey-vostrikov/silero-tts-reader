#!/usr/bin/env python3
"""Значок окна: согласие кода с .desktop-файлом и установка значка на окно.

Главная проверка — согласие идентификаторов. GNOME ищет иконку окна по app-id
(на Wayland) и по WM_CLASS (на X11), а файл .desktop — по тому же имени. Когда
они расходятся, рабочий стол рисует вместо значка серую «шестерёнку», и это
единственный симптом: ни ошибки, ни предупреждения в журнале нет.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# WM_CLASS — поле оконной системы X11; под Wayland значок ищется по app-id.
# Backend выбирается при первом обращении к дисплею, поэтому задаётся здесь,
# до импорта всего, что тянет GTK.
os.environ.setdefault("GDK_BACKEND", "x11")

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
    # StartupWMClass — это класс окна из WM_CLASS, а не app-id: под X11
    # сопоставление идёт по короткому имени латиницей, без домена.
    check(desktop_value("StartupWMClass") == window_icon.WM_CLASS,
          f"StartupWMClass = {desktop_value('StartupWMClass')}, "
          f"класс окна = {window_icon.WM_CLASS}")
    check("." not in window_icon.WM_CLASS,
          f"в классе окна нет домена и точек: {window_icon.WM_CLASS}")
    check(desktop_value("Icon") == window_icon.ICON_NAME,
          f"Icon совпадает с именем иконки: {window_icon.ICON_NAME}")
    check(desktop_value("StartupWMClass") != window_icon.APP_ID,
          "класс окна отличается от app-id: это разные механизмы X11 и Wayland")


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
    # Сборка подставляла app-id в StartupWMClass, и ярлык расходился с кодом:
    # пакет ставил домен, а окно представлялось коротким классом.
    check("StartupWMClass=$APP_ID" not in script,
          "сборка не подменяет StartupWMClass на app-id")


def test_icon_file() -> None:
    """Файл иконки находится и существует.

    Имя файла обязано совпадать с именем иконки только для УСТАНОВЛЕННОЙ
    иконки: тему GTK ищет по имени, и опечатка в имени означает «шестерёнку».
    Исходник в дереве (`assets/icon.png`) называется иначе — это не контракт с
    темой, а входной файл для `packaging/make-icons.py`, и требовать от него
    совпадения значит проверять не то. Проверка зависит от окружения: на
    машине с установленным пакетом берётся системный файл, в CI — исходник.
    """
    print("\n3. Файл иконки")
    path = window_icon.icon_file()
    check(bool(path), f"путь найден: {path}")
    check(Path(path).is_file(), "файл существует")

    installed = Path(path).parent.parent.parent == Path("/usr/share")
    if installed:
        check(Path(path).name == f"{window_icon.ICON_NAME}.png",
              f"имя установленной иконки совпадает с именем в теме: {Path(path).name}")
    else:
        print(f"  — имя файла не проверяется: это исходник из дерева, "
              f"а не установленная иконка ({Path(path).name})")
        check(Path(path).suffix == ".png", "исходник в формате PNG")


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


def test_wm_class() -> None:
    """WM_CLASS окна — короткая пара X11, совпадающая с ярлыком."""
    print("\n5. Класс окна для оконной системы")
    from gi.repository import GLib

    window_icon.set_program_name()
    check(GLib.get_prgname() == window_icon.WM_INSTANCE,
          f"имя программы: {GLib.get_prgname()}")

    # Настоящее окно: WM_CLASS — то, что видит оконная система. Без панели
    # проверка пропускается, но не молча: строка ниже объясняет причину.
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11, GLib, Gtk
        from Xlib import display as xdisplay

        Gtk.init()
    except Exception as exc:
        print(f"  — оконной панели нет ({exc.__class__.__name__}), проверка WM_CLASS пропущена")
        return

    window = Gtk.Window(title="проба значка")
    try:
        window.present()
        # В GTK4 нет Gtk.main_iteration: события прокачиваются через главный
        # контекст, пока окно не встанет на экран.
        context = GLib.MainContext.default()
        surface = None
        for _ in range(400):
            while context.pending():
                context.iteration(False)
            surface = window.get_surface()
            if isinstance(surface, GdkX11.X11Surface):
                break
        check(isinstance(surface, GdkX11.X11Surface),
              f"окно получило X11-поверхность: {surface}")
        xid = surface.get_xid()

        connection = xdisplay.Display()
        try:
            prop = connection.create_resource_object("window", xid).get_full_property(
                connection.intern_atom("WM_CLASS"), 0)
        finally:
            connection.close()
        if prop is None:                      # для проверяющего типов: ниже проверка
            raise AssertionError("WM_CLASS отсутствует")
        check(True, "WM_CLASS выставлен")
        names = [part.decode() for part in prop.value.split(b"\0") if part]
        check(len(names) >= 2, f"в WM_CLASS два поля: {names}")
        # Первое поле — instance, второе — class. Ярлык сверяется с классом.
        check(names[0] == window_icon.WM_INSTANCE,
              f"instance = {names[0]}")
        check(names[1] == window_icon.WM_CLASS,
              f"class = {names[1]}")
        check(window_icon.WM_CLASS in names,
              "класс окна совпадает с StartupWMClass ярлыка")
        check("." not in names[1],
              f"в классе окна нет домена: {names[1]}")
    finally:
        window.destroy()


def main() -> int:
    print("Значок окна")
    test_contract_with_desktop()
    test_packaging_name()
    test_icon_file()
    test_apply_icon()
    test_wm_class()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
