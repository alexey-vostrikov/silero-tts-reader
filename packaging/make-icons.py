#!/usr/bin/env python3
"""Сгенерировать иконки приложения и значки трея.

Иконки рисуются кодом, а не берутся из растрового исходника: нужен плоский
рисунок без свечений и мелкой решётки, который остаётся узнаваемым в 16 px.
Исходный JPEG из апстрима в 16–24 px превращался в размытое пятно.

Что получается:

* иконка приложения — скруглённый квадрат с градиентом и микрофоном, набор
  размеров hicolor (16…512) плюс крупная копия для диалога «О программе»;
* значки трея — те же микрофоны монохромно (микрофон; микрофон с волнами;
  пауза), в двух цветах: для тёмной панели (светлый штрих) и для светлой
  (тёмный штрих).

Запуск: python3 packaging/make-icons.py [каталог-назначения]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICON_NAME = "silero-tts-reader"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)

ROOT = Path(__file__).resolve().parent.parent

# Фон иконки приложения: от индиго к фиолетовому. Контраст с белым штрихом
# не ниже 4.5:1 на всём градиенте — иначе значок «плывёт» на светлой панели.
BACK_TOP = (74, 58, 178)
BACK_BOTTOM = (126, 78, 214)
GLYPH = (255, 255, 255)

SS = 8   # кратность суперсэмплинга: рисуем крупно, уменьшаем Ланцошем


def _rounded_gradient(size: int, radius: float = 0.22) -> Image.Image:
    """Скруглённый квадрат с вертикальным градиентом."""
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


def _speaker(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
             colour: tuple[int, int, int, int], waves: int = 2,
             pause: bool = False, simple: bool = False) -> None:
    """Нарисовать знак звука: динамик с дугами или со знаком паузы.

    Именно динамик, а не микрофон: микрофон из капсулы, дужки и стойки в
    16 px читается как голова с плечами или лицо (капсула — глаз, дужка —
    рот), а динамик узнаётся даже в 16 px без подписи. Проверено на листе
    сравнения; микрофон остаётся в памяти проекта как отброшенный вариант.

    ``simple`` — рисунок для 16–24 px: толще штрих, одна дуга вместо двух.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if simple:
        waves = min(waves, 1)
    stroke = max(w * (0.13 if simple else 0.10), 1.0)

    # Корпус и раструб: прямоугольник плюс треугольник, сросшиеся в одну
    # форму — разнесённые части на малых размерах распадаются.
    body_x0, body_x1 = x0 + w * 0.08, x0 + w * 0.30
    body_y0, body_y1 = y0 + h * 0.36, y0 + h * 0.64
    draw.rectangle((body_x0, body_y0, body_x1, body_y1), fill=colour)
    cone_x = x0 + w * (0.58 if (waves or pause) else 0.62)
    draw.polygon(
        [(body_x1, body_y0), (cone_x, y0 + h * 0.14),
         (cone_x, y1 - h * 0.14), (body_x1, body_y1)],
        fill=colour,
    )

    if pause:
        # Пауза — две полосы на месте дуг: их видно и в 16 px.
        bar_w = w * (0.10 if simple else 0.08)
        for index in range(2):
            bx = cone_x + w * (0.10 + index * 0.20)
            draw.rounded_rectangle(
                (bx, y0 + h * 0.30, bx + bar_w, y1 - h * 0.30),
                radius=bar_w * 0.4, fill=colour)
        return

    for index in range(waves):
        radius = w * (0.16 + 0.13 * index)
        centre_x = cone_x - w * 0.04
        centre_y = y0 + h * 0.5
        draw.arc((centre_x, centre_y - radius, centre_x + 2 * radius,
                  centre_y + radius),
                 start=-55, end=55, fill=colour, width=max(round(stroke), 1))


def app_icon(size: int) -> Image.Image:
    """Иконка приложения: плашка с микрофоном.

    Волны рисуются только от 32 px: в 16–24 px они превращаются в точки,
    слипаются с капсулой и мешают узнать микрофон.
    """
    side = size * SS
    # В 16–24 px скругление и поля съедают полезную площадь: значок мельчает
    # и сливается с плашкой. Там форма строже, а знак крупнее.
    small = size < 32
    img = _rounded_gradient(size, 0.18 if small else 0.22)
    draw = ImageDraw.Draw(img)
    inset = side * (0.10 if small else 0.18)
    _speaker(draw, (inset, inset, side - inset, side - inset),
             (*GLYPH, 255), waves=2, simple=small)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    dst = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "icons"

    for size in SIZES:
        out = dst / f"hicolor/{size}x{size}/apps/{ICON_NAME}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        app_icon(size).save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(dst)}  ({out.stat().st_size} байт)")

    # Копия для .desktop без установленной темы и для диалога «О программе»
    big = dst / f"{ICON_NAME}.png"
    app_icon(512).save(big, "PNG", optimize=True)
    print(f"  {big.relative_to(dst)}  ({big.stat().st_size} байт)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
