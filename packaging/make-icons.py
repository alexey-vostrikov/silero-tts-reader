#!/usr/bin/env python3
"""Сгенерировать набор иконок для пакета из исходного 1024×1024.

Исходник в репозитории — JPEG с расширением .png (так он пришёл из апстрима).
Для темы иконок нужны настоящие PNG в стандартных размерах hicolor.
Запуск: python3 packaging/make-icons.py [каталог-назначения]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "silero_tts_reader" / "assets" / "icon.png"


def main() -> int:
    dst = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "icons"
    if not SOURCE.exists():
        print(f"нет исходной иконки: {SOURCE}", file=sys.stderr)
        return 1

    img = Image.open(SOURCE)  # формат определяется по содержимому, расширение врёт
    img = img.convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((square.width - img.width) // 2, (square.height - img.height) // 2))

    for size in SIZES:
        out = dst / f"hicolor/{size}x{size}/apps/silero-tts-reader.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        square.resize((size, size), Image.Resampling.LANCZOS).save(out, "PNG", optimize=True)
        print(f"  {out.relative_to(dst)}  ({out.stat().st_size} байт)")

    # Крупная копия для диалога «О программе» и .desktop без установленной темы
    big = dst / "silero-tts-reader.png"
    big.parent.mkdir(parents=True, exist_ok=True)
    square.resize((512, 512), Image.Resampling.LANCZOS).save(big, "PNG", optimize=True)
    print(f"  {big.relative_to(dst)}  ({big.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
