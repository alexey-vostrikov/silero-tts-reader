#!/usr/bin/env python3
"""Создать тестовые FB2 во всех поддерживаемых контейнерах.

Книга намеренно сделана «как в жизни»: windows-1251, вложенные разделы,
эпиграф, стихи, картинка в base64 и лишние пробелы — всё это должно быть
разобрано в чистый текст без мусора.

Форматы: голый .fb2, zip, xz, lzma (старый FORMAT_ALONE), gzip, bzip2, zstd,
lz4-фрейм (пишется вручную: утилиты lz4 в системе может не быть), tar,
переименованный контейнер и вложенные (zip → xz → fb2).
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import struct
import sys
import tarfile
import zipfile
from pathlib import Path

BOOK = """<?xml version="1.0" encoding="windows-1251"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
             xmlns:l="http://www.w3.org/1999/xlink">
  <description>
    <title-info>
      <genre>prose</genre>
      <author>
        <first-name>Лев</first-name>
        <last-name>Толстой</last-name>
      </author>
      <book-title>Проверочная книга</book-title>
      <annotation><p>Короткая аннотация для проверки.</p></annotation>
    </title-info>
  </description>
  <body>
    <title><p>Часть первая</p></title>
    <section>
      <title><p>Глава первая</p></title>
      <epigraph><p>Эпиграф к главе.</p></epigraph>
      <p>Первый   абзац
         с переносами и лишними пробелами.</p>
      <p>Второй абзац.</p>
      <empty-line/>
      <section>
        <title><p>Вложенный раздел</p></title>
        <p>Текст вложенного раздела.</p>
        <poem>
          <stanza><v>Строка стиха первая,</v><v>строка стиха вторая.</v></stanza>
        </poem>
      </section>
    </section>
  </body>
  <binary id="cover.png" content-type="image/png">iVBORw0KGgoAAAANSUhEUg</binary>
</FictionBook>
"""


def lz4_literals_block(data: bytes) -> bytes:
    """LZ4-блок без сжатия: одна последовательность из литералов.

    Такой блок корректен по формату (совпадений просто нет), поэтому им можно
    проверить разбор фрейма даже там, где нет утилиты lz4.
    """
    length = len(data)
    if length < 15:
        return bytes([length << 4]) + data
    extra = length - 15
    out = bytearray([0xF0])
    while extra >= 255:
        out.append(255)
        extra -= 255
    out.append(extra)
    out += data
    return bytes(out)


def lz4_frame(data: bytes) -> bytes:
    """Собрать минимальный LZ4-фрейм: версия 01, независимые блоки, без сумм."""
    header = bytearray(b"\x04\x22\x4d\x18")
    flg = 0b0100_0000 | 0b0010_0000   # версия 1, B.Indep = 1
    bd = 0b0110_0000                  # B.MaxSize = 4 (1 МБ)
    header += bytes([flg, bd])
    header.append(0)                   # контрольный байт заголовка не проверяем
    frame = bytearray(header)
    frame += struct.pack("<I", len(lz4_literals_block(data)) & 0x7FFFFFFF)
    frame += lz4_literals_block(data)
    frame += struct.pack("<I", 0)
    return bytes(frame)


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fb2")
    target.mkdir(parents=True, exist_ok=True)
    payload = BOOK.encode("cp1251")

    (target / "book.fb2").write_bytes(payload)
    with zipfile.ZipFile(target / "book.fb2.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("book.fb2", payload)
    (target / "book.fb2.xz").write_bytes(lzma.compress(payload, format=lzma.FORMAT_XZ))
    (target / "book.fb2.lzma").write_bytes(lzma.compress(payload, format=lzma.FORMAT_ALONE))
    (target / "book.fb2.gz").write_bytes(gzip.compress(payload))
    (target / "book.fb2.bz2").write_bytes(bz2.compress(payload))
    (target / "book.fb2.lz4").write_bytes(lz4_frame(payload))

    try:
        from compression import zstd

        (target / "book.fb2.zst").write_bytes(zstd.compress(payload))
    except ImportError:
        import subprocess

        (target / "book.fb2.zst").write_bytes(
            subprocess.run(["zstd", "-q", "-c"], input=payload, capture_output=True, check=True).stdout
        )

    with tarfile.open(target / "book.fb2.tar", "w") as archive:
        book = target / "book.fb2"
        archive.add(book, arcname="book.fb2")

    # тот же xz, но с «человеческим» именем .fb2 — формат по магии
    (target / "mislabeled.fb2").write_bytes(lzma.compress(payload, format=lzma.FORMAT_XZ))
    # lzma без расширения: опознаётся только по содержимому
    (target / "noext").write_bytes(lzma.compress(payload, format=lzma.FORMAT_ALONE))
    # вложенные контейнеры: zip → xz → fb2
    inner = lzma.compress(payload, format=lzma.FORMAT_XZ)
    with zipfile.ZipFile(target / "nested.fb2.zip", "w") as archive:
        archive.writestr("book.fb2.xz", inner)

    for path in sorted(target.iterdir()):
        print(f"  {path.name}: {path.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
