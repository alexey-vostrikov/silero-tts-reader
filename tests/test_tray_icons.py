#!/usr/bin/env python3
"""Значок трея: вариант цвета под панель и проверка готовых файлов.

Панель не перекрашивает чужие значки, поэтому цвет выбирает само приложение.
Здесь проверяется это решение и то, что в пакете действительно лежат оба
варианта — и в SVG (основной формат), и растром нужных размеров.
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


def test_variants() -> None:
    """Два варианта цвета, имена различимы."""
    print("\n1. Варианты цвета")
    light = tray_icons.icon_name(tray_icons.PREFER_LIGHT)
    dark = tray_icons.icon_name(tray_icons.PREFER_DARK)
    check(light == "silero-tts-reader-tray-light", f"светлый штрих: {light}")
    check(dark == "silero-tts-reader-tray-dark", f"тёмный штрих: {dark}")
    check(light != dark, "имена вариантов различаются")


def test_auto() -> None:
    """«Авто» следует системной теме, настройка её перекрывает."""
    print("\n2. Автоопределение темы")
    saved = tray_icons.system_is_dark
    try:
        tray_icons.system_is_dark = lambda: True
        check(tray_icons.icon_name("auto").endswith("-tray-light"),
              "тёмная тема → светлый штрих")
        tray_icons.system_is_dark = lambda: False
        check(tray_icons.icon_name("auto").endswith("-tray-dark"),
              "светлая тема → тёмный штрих")
    finally:
        tray_icons.system_is_dark = saved

    # Явная настройка сильнее темы: панель бывает не системная.
    tray_icons.system_is_dark = lambda: True
    try:
        check(tray_icons.icon_name("dark").endswith("-tray-dark"),
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
    """Для каждого имени в пакете есть файлы: SVG и растры нужных размеров."""
    print("\n4. Файлы значков")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make-icons.py"), tmp],
                       check=True, capture_output=True)
        base = Path(tmp)
        missing = []
        for prefer in ("light", "dark"):
            name = tray_icons.icon_name(prefer)
            svg = base / f"hicolor/scalable/apps/{name}.svg"
            if not svg.is_file():
                missing.append(str(svg.relative_to(base)))
            for size in (16, 22, 24, 32, 48):
                png = base / f"hicolor/{size}x{size}/apps/{name}.png"
                if not png.is_file():
                    missing.append(str(png.relative_to(base)))
        check(not missing, f"оба варианта на месте (SVG и 5 размеров): {missing}")

        app_svg = base / "hicolor/scalable/apps/silero-tts-reader.svg"
        app_png = base / "hicolor/256x256/apps/silero-tts-reader.png"
        check(app_svg.is_file(), "иконка приложения в SVG есть")
        check(app_png.is_file(), "иконка приложения 256 px есть")

        from PIL import Image

        tray_png = Image.open(
            base / "hicolor/24x24/apps/silero-tts-reader-tray-light.png").convert("RGBA")
        alpha = tray_png.split()[3]
        check(alpha.getpixel((0, 0)) == 0, "у значка трея прозрачный фон")
        check(tray_png.getbbox() is not None, "значок трея не пустой")

        # Цвет штриха разный: на тёмной панели светлый, на светлой тёмный.
        def ink(path: Path) -> tuple[int, int, int, int]:
            img = Image.open(path).convert("RGBA")
            px = img.load()
            width, height = img.size
            for y in range(height):
                for x in range(width):
                    r, g, b, a = px[x, y]
                    if a > 200:
                        return r, g, b, a
            return 0, 0, 0, 0

        light_ink = ink(base / "hicolor/24x24/apps/silero-tts-reader-tray-light.png")
        dark_ink = ink(base / "hicolor/24x24/apps/silero-tts-reader-tray-dark.png")
        check(light_ink[0] > 200 and light_ink[1] > 200,
              f"вариант light светлый: {light_ink}")
        check(dark_ink[0] < 90 and dark_ink[1] < 90,
              f"вариант dark тёмный: {dark_ink}")


def test_svg_colours() -> None:
    """В SVG тот же цвет штриха, что и в растре: панель берёт любой из них."""
    print("\n5. Цвет в SVG")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make-icons.py"), tmp],
                       check=True, capture_output=True)
        base = Path(tmp)
        light = (base / "hicolor/scalable/apps/silero-tts-reader-tray-light.svg"
                 ).read_text(encoding="utf-8")
        dark = (base / "hicolor/scalable/apps/silero-tts-reader-tray-dark.svg"
                ).read_text(encoding="utf-8")
        app = (base / "hicolor/scalable/apps/silero-tts-reader.svg"
               ).read_text(encoding="utf-8")

        check('fill="#fafafa"' in light, "светлый штрих в SVG")
        check('fill="#2b2b2e"' in dark, "тёмный штрих в SVG")
        check("viewBox" in light, "у значка трея есть viewBox: масштабируется")
        check("linearGradient" in app and "<path" in app,
              "иконка приложения: плашка с градиентом и знак")
        # Тот же контур, что в исходнике: иначе варианты разъедутся.
        d_light = light.split('d="')[1].split('"')[0]
        d_dark = dark.split('d="')[1].split('"')[0]
        check(d_light == d_dark, "контур у обоих вариантов одинаковый")


def test_icon_file() -> None:
    """Запасной путь для панелей, которым нужны готовые пиксели.

    Неизвестное имя не должно ронять приложение: лучше показать иконку
    приложения, чем ничего. Пустая строка — только если не установлено ни то,
    ни другое.
    """
    print("\n6. Файл по имени")
    found = tray_icons.icon_file("нет-такого-значка")
    check(found == "" or found.endswith("silero-tts-reader.png"),
          f"неизвестное имя — иконка приложения или пусто: {found!r}")


def main() -> int:
    print("Значок трея: вариант цвета под панель")
    test_variants()
    test_auto()
    test_theme_detection()
    test_icons_exist()
    test_svg_colours()
    test_icon_file()
    print(f"\n  все проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
