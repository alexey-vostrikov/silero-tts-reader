#!/usr/bin/env python3
"""Значок трея: выбор варианта цвета штриха и состояния.

Панель не перекрашивает чужие значки, поэтому цвет штриха выбирает само
приложение. Здесь проверяется именно это решение: что «авто» следует
системной теме, что настройка перекрывает автоопределение и что для каждого
состояния в пакете есть файл значка.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.ui import tray_icons

ROOT = Path(__file__).resolve().parent.parent
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def test_states() -> None:
    """Состояние меняет имя значка, цвет штриха — вариант."""
    print("\n1. Состояния и варианты цвета")
    names = {
        state: tray_icons.icon_name(state, tray_icons.PREFER_LIGHT)
        for state in tray_icons.STATE_SUFFIX
    }
    check(len(set(names.values())) == 3, f"три разных значка: {list(names.values())}")
    check(names["idle"].endswith("-tray-light"), "покой без суффикса состояния")
    check(names["playing"].endswith("-wave"), "чтение — с волнами")
    check(names["paused"].endswith("-pause"), "пауза — со знаком паузы")

    dark = tray_icons.icon_name("idle", tray_icons.PREFER_DARK)
    check(dark.endswith("-tray-dark"), f"тёмный штрих для светлой панели: {dark}")

    unknown = tray_icons.icon_name("нет-такого", tray_icons.PREFER_DARK)
    check(unknown.endswith("-tray-dark"), "неизвестное состояние — как покой")


def test_auto() -> None:
    """«Авто» следует системной теме, настройка её перекрывает."""
    print("\n2. Автоопределение темы")
    saved = tray_icons.system_is_dark
    try:
        tray_icons.system_is_dark = lambda: True
        check(tray_icons.icon_name("idle", "auto").endswith("-tray-light"),
              "тёмная тема → светлый штрих")
        tray_icons.system_is_dark = lambda: False
        check(tray_icons.icon_name("idle", "auto").endswith("-tray-dark"),
              "светлая тема → тёмный штрих")
    finally:
        tray_icons.system_is_dark = saved

    # Явная настройка сильнее темы: панель бывает не системной.
    tray_icons.system_is_dark = lambda: True
    try:
        check(tray_icons.icon_name("idle", "dark").endswith("-tray-dark"),
              "явный выбор перекрывает тёмную тему")
    finally:
        tray_icons.system_is_dark = saved


def test_theme_detection() -> None:
    """Признаки тёмной темы: color-scheme сильнее имени темы."""
    print("\n3. Признаки темы")
    saved_scheme, saved_theme = tray_icons._color_scheme, tray_icons._theme_name
    try:
        tray_icons._color_scheme = lambda: "prefer-dark"
        tray_icons._theme_name = lambda: "Adwaita"
        check(tray_icons.system_is_dark(), "color-scheme prefer-dark — тёмная")

        tray_icons._color_scheme = lambda: "prefer-light"
        tray_icons._theme_name = lambda: "Yaru-dark"
        check(not tray_icons.system_is_dark(),
              "prefer-light сильнее слова dark в имени темы")

        tray_icons._color_scheme = lambda: ""
        tray_icons._theme_name = lambda: "Yaru-dark"
        check(tray_icons.system_is_dark(), "тёмная тема по имени")

        tray_icons._theme_name = lambda: ""
        check(not tray_icons.system_is_dark(),
              "ничего не известно — считаем панель светлой")
    finally:
        tray_icons._color_scheme, tray_icons._theme_name = saved_scheme, saved_theme


def test_icons_exist() -> None:
    """Для каждого имени в пакете есть файл нужных размеров."""
    print("\n4. Файлы значков")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make-icons.py"), tmp],
                       check=True, capture_output=True)
        base = Path(tmp)
        missing = []
        for state in tray_icons.STATE_SUFFIX:
            for prefer in ("light", "dark"):
                name = tray_icons.icon_name(state, prefer)
                for size in (16, 24, 32):
                    path = base / f"hicolor/{size}x{size}/apps/{name}.png"
                    if not path.is_file():
                        missing.append(str(path.relative_to(base)))
        check(not missing, f"все значки состояний на месте: {missing}")

        app = base / "hicolor/256x256/apps/silero-tts-reader.png"
        check(app.is_file(), "иконка приложения 256 px есть")

        # Значок панели не должен нести плашку: у него прозрачные углы.
        from PIL import Image

        tray_png = Image.open(
            base / "hicolor/24x24/apps/silero-tts-reader-tray-light.png").convert("RGBA")
        alpha = tray_png.split()[3]
        check(alpha.getpixel((0, 0)) == 0, "у значка трея прозрачный фон")
        check(tray_png.getbbox() is not None, "значок трея не пустой")


def test_icon_file() -> None:
    """Запасной путь для панелей, которым нужны готовые пиксели.

    Неизвестное имя не должно ронять приложение: лучше показать иконку
    приложения, чем ничего. Пустая строка — только если не установлено ни то,
    ни другое.
    """
    print("\n5. Файл по имени")
    found = tray_icons.icon_file("нет-такого-значка")
    check(found == "" or found.endswith("silero-tts-reader.png"),
          f"неизвестное имя — иконка приложения или пусто: {found!r}")


def main() -> int:
    print("Значок трея: вариант цвета и состояния")
    test_states()
    test_auto()
    test_theme_detection()
    test_icons_exist()
    test_icon_file()
    print(f"\n  все проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
