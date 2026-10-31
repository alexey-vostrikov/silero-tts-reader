#!/usr/bin/env python3
"""Сгенерировать иконки приложения и значки трея.

Знак — микрофон с речевым облачком, «text to speech» из набора Material Design
(иконка 135108 с icon-icons.com, лицензия Apache 2.0). Источник — SVG с
контуром знака: ``packaging/assets/tts-glyph.svg``.

Основной формат — SVG: тема иконок отдаёт его на любом размере через librsvg,
поэтому значок остаётся резким и в панели 22 px, и в списке окон 512 px, без
набора растров под каждый размер. PNG всё же генерируются: часть панелей и
файловых менеджеров просит готовые пиксели, а для 16 px пиксельный рисунок
читается лучше уменьшенного контура.

Что получается:

* иконка приложения — знак белым на скруглённой плашке, ``scalable`` плюс
  растры 16…512;
* значки трея — знак без плашки в двух цветах: светлый для тёмной панели и
  тёмный для светлой. Панель чужие значки не перекрашивает, поэтому вариант
  выбирает само приложение (см. ``ui/tray_icons.py``).

Запуск: python3 packaging/make-icons.py [каталог-назначения]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ICON_NAME = "silero-tts-reader"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
TRAY_SIZES = (16, 22, 24, 32, 48)

ROOT = Path(__file__).resolve().parent.parent
GLYPH_SVG = ROOT / "packaging" / "assets" / "tts-glyph.svg"
GLYPH_PNG = ROOT / "packaging" / "assets" / "tts-glyph.png"

# Плашка иконки приложения: от индиго к фиолетовому. Контраст с белым знаком
# не ниже 4.5:1 на всём градиенте — иначе значок «плывёт» на светлой панели.
BACK_TOP = (68, 56, 176)
BACK_BOTTOM = (124, 76, 212)
SIGN = "#ffffff"

# Цвет значка трея. Не чистый белый и не чистый чёрный: панель сама подмешивает
# прозрачность, и крайние значения выглядят грубее соседних значков.
TRAY_LIGHT = (250, 250, 250)   # для тёмной панели
TRAY_DARK = (43, 43, 46)       # для светлой панели

SS = 8   # кратность суперсэмплинга для растров: собираем крупно, уменьшаем Ланцошем

# Утолщение штриха для мелких растров. У знака тонкие линии: в 16–24 px они
# рассыпаются на отдельные серые пиксели, и микрофон перестаёт отличаться от
# облачка. Ядро 3 при восьмикратном суперсэмплинге добавляет около 0.4 px —
# этого хватает, чтобы линии стали плотными, но зазор между частями остался.
SMALL_BOLD_BELOW = 32
SMALL_BOLD_KERNEL = 3

# Знак занимает такую долю стороны значка. У контура есть свои поля (он
# нарисован в квадрате 24×24 с отступом 2), поэтому доля считается по видимой
# части: 0.56 от 24 = 13.4 единицы против 20 видимых.
SIGN_SHARE = 0.56


def glyph_path() -> str:
    """Контур знака из SVG-источника."""
    text = GLYPH_SVG.read_text(encoding="utf-8")
    match = re.search(r'<path[^>]*\sd="([^"]+)"', text)
    if match is None:
        raise SystemExit(f"в {GLYPH_SVG} нет контура <path d=...>")
    return match.group(1)


def _hex(colour: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


def tray_svg(path: str, colour: tuple[int, int, int]) -> str:
    """Значок трея: один контур нужного цвета, без плашки."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24">\n'
        f'  <path fill="{_hex(colour)}" d="{path}"/>\n'
        '</svg>\n'
    )


def app_svg(path: str, size: int = 512) -> str:
    """Иконка приложения: скруглённая плашка с градиентом и белый знак."""
    radius = round(size * 0.22, 2)
    # Масштаб и сдвиг выводятся из видимой части контура (2…22 при 24 единицах).
    layout = 24 - 4
    scale = size * SIGN_SHARE / layout
    offset = round((size - layout * scale) / 2 - 2 * scale, 2)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">\n'
        '  <defs>\n'
        '    <linearGradient id="plate" x1="0" y1="0" x2="0" y2="1">\n'
        f'      <stop offset="0" stop-color="{_hex(BACK_TOP)}"/>\n'
        f'      <stop offset="1" stop-color="{_hex(BACK_BOTTOM)}"/>\n'
        '    </linearGradient>\n'
        '  </defs>\n'
        f'  <rect width="{size}" height="{size}" rx="{radius}" ry="{radius}" '
        'fill="url(#plate)"/>\n'
        f'  <g transform="translate({offset},{offset}) scale({round(scale, 4)})">\n'
        f'    <path fill="{SIGN}" d="{path}"/>\n'
        '  </g>\n'
        '</svg>\n'
    )


def _rounded_gradient(size: int, radius: float = 0.22) -> Image.Image:
    """Скруглённый квадрат с вертикальным градиентом — для растров."""
    side = size * SS
    gradient = Image.new("RGB", (1, side))
    for y in range(side):
        k = y / max(side - 1, 1)
        gradient.putpixel((0, y), tuple(
            round(a + (b - a) * k) for a, b in zip(BACK_TOP, BACK_BOTTOM, strict=True)
        ))
    plate = gradient.resize((side, side), Image.Resampling.NEAREST).convert("RGBA")

    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, side - 1, side - 1), radius=round(side * radius), fill=255
    )
    plate.putalpha(mask)
    return plate


def glyph_mask() -> Image.Image:
    """Маска знака из PNG: белый по прозрачному."""
    source = Image.open(GLYPH_PNG).convert("RGBA")
    white = Image.new("RGBA", source.size, (255, 255, 255, 0))
    white.putalpha(source.split()[3])
    return white


def _resized_mask(mask: Image.Image, side: int, bold: bool) -> Image.Image:
    """Уменьшить маску, при необходимости утолщив штрих до уменьшения."""
    if bold:
        big = mask.resize((side * SS, side * SS), Image.Resampling.LANCZOS)
        big = big.filter(ImageFilter.MaxFilter(SMALL_BOLD_KERNEL))
        return big.resize((side, side), Image.Resampling.LANCZOS)
    return mask.resize((side, side), Image.Resampling.LANCZOS)


def app_icon(size: int, mask: Image.Image) -> Image.Image:
    """Растровая иконка приложения: знак белым на плашке.

    В 16–24 px плашка скруглена меньше, а знак занимает больше места: иначе
    поля и скругление съедают полезную площадь и знак мельчает.
    """
    small = size < SMALL_BOLD_BELOW
    plate = _rounded_gradient(size, 0.18 if small else 0.22)
    side = plate.size[0]
    # В малом размере знак занимает больше места: поля плашки и без того
    # съедают полезную площадь.
    share = 0.62 if small else SIGN_SHARE
    inset = side * (1 - share) / 2
    glyph_side = int(side * share)
    plate.alpha_composite(
        _resized_mask(mask, glyph_side, bold=small), (int(inset), int(inset)))
    return plate.resize((size, size), Image.Resampling.LANCZOS)


def tray_icon(size: int, colour: tuple[int, int, int],
              mask: Image.Image) -> Image.Image:
    """Растровый значок трея без плашки."""
    # Отступ в 1 px по краю от 24 px: панель обрезает значок по своей рамке,
    # и знак вплотную к границе выглядит крупнее соседних значков.
    pad = 0 if size <= 16 else max(1, round(size * 0.04))
    inner = size - 2 * pad
    art = Image.new("RGBA", (inner, inner), (*colour, 0))
    art.putalpha(_resized_mask(mask, inner, bold=size < SMALL_BOLD_BELOW).split()[3])
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(art, (pad, pad))
    return canvas


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}  ({path.stat().st_size} байт)")


def main() -> int:
    dst = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "icons"
    for required in (GLYPH_SVG, GLYPH_PNG):
        if not required.exists():
            print(f"нет файла знака: {required}", file=sys.stderr)
            return 1

    path = glyph_path()
    mask = glyph_mask()

    # Основной формат: SVG. Тема иконок отдаёт его на любом размере.
    _write(dst / f"hicolor/scalable/apps/{ICON_NAME}.svg", app_svg(path))
    for variant, colour in (("light", TRAY_LIGHT), ("dark", TRAY_DARK)):
        _write(dst / f"hicolor/scalable/apps/{ICON_NAME}-tray-{variant}.svg",
               tray_svg(path, colour))

    # Растры: для панелей и программ, которым нужны готовые пиксели.
    for size in SIZES:
        out = dst / f"hicolor/{size}x{size}/apps/{ICON_NAME}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        app_icon(size, mask).save(out, "PNG", optimize=True)
    print(f"  hicolor/{{16…512}}x{{…}}/apps/{ICON_NAME}.png")

    # Копия для .desktop без установленной темы и для диалога «О программе»
    big = dst / f"{ICON_NAME}.png"
    app_icon(512, mask).save(big, "PNG", optimize=True)
    print(f"  {big.name}  ({big.stat().st_size} байт)")

    for variant, colour in (("light", TRAY_LIGHT), ("dark", TRAY_DARK)):
        name = f"{ICON_NAME}-tray-{variant}"
        for size in TRAY_SIZES:
            out = dst / f"hicolor/{size}x{size}/apps/{name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            tray_icon(size, colour, mask).save(out, "PNG", optimize=True)
        print(f"  hicolor/*/apps/{name}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
