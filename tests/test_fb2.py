#!/usr/bin/env python3
"""Проверка чтения книг FB2 во всех контейнерах.

Тесты не зависят от внешних утилит: архивы для проверки собираются здесь же.
Отдельно проверяется LZ4 — своего модуля в системе нет, декодер написан вручную,
поэтому ему нужны и «простые» блоки, и блоки со совпадениями, и связанные блоки
(когда второй блок ссылается на данные первого).
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader import i18n
from silero_tts_reader.core import fb2

# Тексты сообщений проверяются по-русски: язык задаём явно,
# иначе результат зависит от LANG в окружении.
i18n.init("ru_RU")
from silero_tts_reader.core.text_extractor import TextExtractor

CHECKS = 0

BOOK_TEMPLATE = """<?xml version="1.0" encoding="{encoding}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info>
      <author><first-name>Лев</first-name><last-name>Толстой</last-name></author>
      <book-title>Проверочная книга</book-title>
    </title-info>
  </description>
  <body>
    <title><p>Часть первая</p></title>
    <section>
      <p>Первый абзац.</p>
      <p>Второй абзац.</p>
      <section><title><p>Вложенный раздел</p></title><p>Текст вложенного раздела.</p></section>
    </section>
  </body>
  <binary id="cover.png" content-type="image/png">iVBORw0KGgoAAAANSUhEUg</binary>
</FictionBook>
"""


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def expect_error(function, needle: str, message: str) -> None:
    global CHECKS
    CHECKS += 1
    try:
        function()
    except Exception as exc:
        if needle.lower() not in str(exc).lower():
            raise AssertionError(f"{message}: не то сообщение — {exc}") from exc
        print(f"  ✓ {message}: {exc}")
        return
    raise AssertionError(f"{message}: ошибки не было")


# ── Сборка контейнеров вручную ───────────────────────────────────────────────


def lz4_block_literals(data: bytes) -> bytes:
    """LZ4-блок без совпадений (только литералы) — корректен по формату."""
    length = len(data)
    if length < 15:
        return bytes([length << 4]) + data
    extra = length - 15
    out = bytearray([0xF0])
    while extra >= 255:
        out.append(255)
        extra -= 255
    out.append(extra)
    return bytes(out) + data


def lz4_frame(blocks: list[bytes], independent: bool = True) -> bytes:
    """LZ4-фрейм из готовых блоков: версия 1, B.MaxSize = 64 КБ."""
    flg = 0b0100_0000 | (0b0010_0000 if independent else 0)
    frame = bytearray(b"\x04\x22\x4d\x18" + bytes([flg, 0b0100_0000, 0]))
    for block in blocks:
        frame += struct.pack("<I", len(block) & 0x7FFFFFFF) + block
    frame += struct.pack("<I", 0)
    return bytes(frame)


def main() -> int:
    print("FB2: форматы, кодировки, ошибки")
    tmp = Path(tempfile.mkdtemp(prefix="fb2-test-"))
    payload = BOOK_TEMPLATE.format(encoding="windows-1251").encode("cp1251")

    # ── 1. Все контейнеры дают один и тот же текст ───────────────────────────
    print("\n1. Контейнеры")
    variants: dict[str, bytes] = {
        "book.fb2": payload,
        "book.fb2.xz": lzma.compress(payload, format=lzma.FORMAT_XZ),
        "book.fb2.lzma": lzma.compress(payload, format=lzma.FORMAT_ALONE),
        "book.fb2.gz": gzip.compress(payload),
        "book.fb2.bz2": bz2.compress(payload),
        "book.fb2.lz4": lz4_frame([lz4_block_literals(payload)]),
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("book.fb2", payload)
    variants["book.fb2.zip"] = zip_buffer.getvalue()

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        info = tarfile.TarInfo("book.fb2")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    tarred = tar_buffer.getvalue()

    # Голый tar для одной книги выгод не даёт, но пусть читается как вырожденный
    # случай. Осмысленны сжатые: .tar.gz и .tgz — одно и то же, дальше по аналогии.
    variants["book.fb2.tar"] = tarred
    variants["book.fb2.tar.gz"] = gzip.compress(tarred)
    variants["book.fb2.tgz"] = gzip.compress(tarred)
    variants["book.fb2.tar.xz"] = lzma.compress(tarred, format=lzma.FORMAT_XZ)
    variants["book.fb2.txz"] = lzma.compress(tarred, format=lzma.FORMAT_XZ)
    variants["book.fb2.tar.bz2"] = bz2.compress(tarred)
    variants["book.fb2.tbz2"] = bz2.compress(tarred)
    variants["book.fb2.tar.lzma"] = lzma.compress(tarred, format=lzma.FORMAT_ALONE)

    # zip, внутри которого книга в контейнере; и двойная упаковка tar.gz
    nested_buffer = io.BytesIO()
    with zipfile.ZipFile(nested_buffer, "w") as archive:
        archive.writestr("book.fb2.xz", lzma.compress(payload, format=lzma.FORMAT_XZ))
    variants["nested.fb2.zip"] = nested_buffer.getvalue()

    double_buffer = io.BytesIO()
    with zipfile.ZipFile(double_buffer, "w") as archive:
        archive.writestr("book.fb2.tar.gz",
                         gzip.compress(tarred))
    variants["nested-targz.fb2.zip"] = double_buffer.getvalue()

    reference = None
    for name, content in variants.items():
        path = tmp / name
        path.write_bytes(content)
        text = fb2.read(path)
        if reference is None:
            reference = text
            check("Проверочная книга" in text, f"{name}: название разобрано")
            check("Лев Толстой" in text, f"{name}: автор разобран")
            check(text.splitlines()[0].startswith("Проверочная книга"),
                  f"{name}: название идёт первым")
            check("iVBORw0KGgo" not in text, f"{name}: base64-картинка не попала в текст")
            check("Первый абзац." in text and "Вложенный раздел" in text,
                  f"{name}: абзацы и вложенный раздел на месте")
            check("\n\n" in text, f"{name}: абзацы разделены пустой строкой")
        else:
            check(text == reference, f"{name}: текст совпадает с эталоном")

    # ── 2. Формат по магии, а не по имени ────────────────────────────────────
    print("\n2. Определение по содержимому")
    mislabeled = tmp / "mislabeled.fb2"
    mislabeled.write_bytes(lzma.compress(payload, format=lzma.FORMAT_XZ))
    check(fb2.read(mislabeled) == reference, "xz с именем .fb2 читается")

    noext = tmp / "noext"
    noext.write_bytes(lzma.compress(payload, format=lzma.FORMAT_ALONE))
    check(fb2.read(noext) == reference, "lzma без расширения читается")

    # ── 3. Кодировки ─────────────────────────────────────────────────────────
    print("\n3. Кодировки")
    utf8 = tmp / "utf8.fb2"
    utf8.write_text(BOOK_TEMPLATE.format(encoding="utf-8"), encoding="utf-8")
    check("Лев Толстой" in fb2.read(utf8), "utf-8 по декларации")

    bom = tmp / "bom.fb2"
    bom.write_bytes(b"\xef\xbb\xbf" + BOOK_TEMPLATE.format(encoding="utf-8").encode("utf-8"))
    check("Лев Толстой" in fb2.read(bom), "utf-8 с BOM")

    utf16 = tmp / "utf16.fb2"
    utf16.write_bytes(BOOK_TEMPLATE.format(encoding="utf-16").encode("utf-16"))
    check("Лев Толстой" in fb2.read(utf16), "utf-16 по BOM")

    no_decl = tmp / "nodecl.fb2"
    no_decl.write_bytes(BOOK_TEMPLATE.format(encoding="windows-1251")
                        .replace('<?xml version="1.0" encoding="windows-1251"?>', "")
                        .encode("cp1251"))
    check("Лев Толстой" in fb2.read(no_decl), "cp1251 без декларации (подбор кодировки)")

    # ── 4. LZ4: совпадения и связанные блоки ─────────────────────────────────
    print("\n4. LZ4")
    # Блок со совпадением: литералы «abc», затем ссылка на них с offset 3
    text = b"abcabcabc"
    # токен 0x32: старшие 4 бита — 3 литерала, младшие — совпадение 0x2 + 4 = 6
    compressed_block = bytes([0x32]) + b"abc" + b"\x03\x00" + bytes([0x00])
    frame = lz4_frame([compressed_block])
    check(fb2._lz4_block_decompress(compressed_block, 1 << 16) == text,
          "блок со совпадением: abcabcabc")
    check(fb2._lz4_decompress(frame) == text, "фрейм со совпадением распаковывается")

    # Связанные блоки: второй ссылается на данные первого
    first = b"0123456789" * 10                      # 100 байт
    second = bytes([0x00]) + b"\x64\x00" + bytes([0x00])  # 0 литералов, offset 100
    linked = lz4_frame([lz4_block_literals(first), second], independent=False)
    decoded = fb2._lz4_decompress(linked)
    check(decoded.startswith(first), "связанные блоки: первый на месте")
    check(len(decoded) > len(first), "связанные блоки: второй дописан")

    # Legacy: магия + длина блока, без оглавления
    legacy = fb2.LZ4_LEGACY_MAGIC + struct.pack("<I", len(lz4_block_literals(text))) \
        + lz4_block_literals(text)
    check(fb2._lz4_decompress(legacy) == text, "старый LZ4 legacy читается")

    if shutil.which("lz4"):
        print("\n4a. LZ4 утилитой (если установлена)")
        source = tmp / "real.fb2"
        source.write_bytes(payload)
        for option, note in (([], "по умолчанию"), (["-BX", "--content-size"], "с суммами"),
                             (["-BD", "-B4"], "связанные блоки"), (["-l"], "legacy")):
            target = tmp / f"real-{note.replace(' ', '-')}.fb2.lz4"
            subprocess.run(["lz4", "-q", "-f", *option, str(source), str(target)], check=True)
            check(fb2.read(target) == reference, f"lz4 {note}: текст совпадает")

    # ── 5. zstd: основной путь и запасной через бинарник ──────────────────────
    print("\n5. zstd")
    if shutil.which("zstd"):
        for name, note in (("book.fb2.tar.zst", "tar внутри zstd"), ("book.fb2.tzst", ".tzst")):
            path = tmp / name
            path.write_bytes(
                subprocess.run(["zstd", "-q", "-c"], input=tarred,
                               capture_output=True, check=True).stdout
            )
            check(fb2.read(path) == reference, f"{note}: текст совпадает")

    if shutil.which("zstd"):
        zstd_file = tmp / "book.fb2.zst"
        zstd_file.write_bytes(
            subprocess.run(["zstd", "-q", "-c"], input=payload,
                           capture_output=True, check=True).stdout
        )
        check(fb2.read(zstd_file) == reference, "zstd от утилиты читается")
    else:
        check(True, "zstd не установлен — пропущено")

    saved = sys.modules.get("compression.zstd")
    sys.modules["compression.zstd"] = None  # type: ignore[assignment]  # как будто модуля нет
    try:
        if shutil.which("zstd"):
            check(fb2._zstd_decompress(
                subprocess.run(["zstd", "-q", "-c"], input=payload,
                               capture_output=True, check=True).stdout
            ) == payload, "zstd через внешнюю утилиту (Python без compression.zstd)")
        else:
            check(True, "zstd: запасной путь не проверить без утилиты")
    finally:
        if saved is None:
            sys.modules.pop("compression.zstd", None)
        else:
            sys.modules["compression.zstd"] = saved

    # ── 5a. Варианты tar: магии у них разные ────────────────────────────────
    print("\n5a. Форматы tar (ustar, GNU, pax, v7)")

    def make_tar(fmt: int, name: str = "book.fb2") -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=fmt) as archive:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()

    def make_v7() -> bytes:
        """Старый v7 tar: поля «ustar» нет вовсе, только контрольная сумма."""
        header = bytearray(512)
        header[0:8] = b"book.fb2"
        header[100:108] = b"0000644\x00"
        header[108:116] = b"0000000\x00"
        header[116:124] = b"0000000\x00"
        header[124:136] = f"{len(payload):011o}\x00".encode()
        header[136:148] = b"00000000000\x00"
        header[148:156] = b" " * 8
        header[156:157] = b"0"
        header[148:156] = f"{sum(header):06o}\x00 ".encode()
        padding = (512 - len(payload) % 512) % 512
        return bytes(header) + payload + b"\x00" * padding + b"\x00" * 1024

    variants_tar = {
        "ustar": make_tar(tarfile.USTAR_FORMAT),
        "gnu": make_tar(tarfile.GNU_FORMAT),
        "pax": make_tar(tarfile.PAX_FORMAT),
        "gnu-long": make_tar(tarfile.GNU_FORMAT, "очень_длинное_" + "х" * 120 + ".fb2"),
        "v7": make_v7(),
    }

    # магии действительно разные — это и есть причина отдельной проверки
    check(variants_tar["ustar"][257:262] == b"ustar", "ustar: магия 'ustar'")
    check(variants_tar["gnu"][257:263] == b"ustar ", "GNU: магия 'ustar ' (пробел)")
    check(variants_tar["v7"][257:262] != b"ustar", "v7: магии нет, только контрольная сумма")

    for label, raw in variants_tar.items():
        for suffix, packer in (
            (".fb2.tar", lambda blob: blob),
            (".fb2.tar.gz", gzip.compress),
            (".fb2.tgz", gzip.compress),
        ):
            path = tmp / f"{label}{suffix}"
            path.write_bytes(packer(raw))
            check(fb2.read(path) == reference, f"{label}{suffix} читается")

    # ── 6. Ошибки ────────────────────────────────────────────────────────────
    print("\n6. Понятные ошибки")
    empty = tmp / "empty.fb2"
    empty.write_bytes(b"")
    expect_error(lambda: fb2.read(empty), "пустой", "пустой файл")
    expect_error(lambda: fb2.read(tmp / "нет-такого.fb2"), "не найден", "нет файла")

    not_fb2 = tmp / "other.fb2"
    not_fb2.write_text("<?xml version='1.0'?><other><p>текст</p></other>", encoding="utf-8")
    expect_error(lambda: fb2.read(not_fb2), "не FB2", "чужой XML")

    broken = tmp / "broken.fb2"
    broken.write_text("<FictionBook><body><p>не закрыт", encoding="utf-8")
    expect_error(lambda: fb2.read(broken), "поврежд", "битый XML")

    tar_no_book = tmp / "nobook.fb2.tar"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        text_info = tarfile.TarInfo("readme.txt")
        text_info.size = 5
        archive.addfile(text_info, io.BytesIO(b"hello"))
    tar_no_book.write_bytes(buffer.getvalue())
    expect_error(lambda: fb2.read(tar_no_book), "нет файла .fb2", "tar без книги")

    empty_zip = tmp / "empty.fb2.zip"
    with zipfile.ZipFile(empty_zip, "w") as archive:
        archive.writestr("readme.txt", "нет книги")
    expect_error(lambda: fb2.read(empty_zip), "нет файла .fb2", "zip без книги")

    # ── 6a. Что встречается в настоящих книгах ──────────────────────────────
    print("\n6a. Настоящие книги (найдено на живых файлах)")
    # ссылка с префиксом l: без объявления xmlns:l — так пишет большинство конвертеров
    unbound = tmp / "unbound.fb2"
    unbound.write_text(
        BOOK_TEMPLATE.format(encoding="utf-8").replace(
            "</body>",
            '<p>Ссылка на <a l:href="https://example.org">пример</a>.</p></body>',
        ),
        encoding="utf-8",
    )
    check("пример" in fb2.read(unbound), "префикс l: без xmlns объявления")

    # тот же случай с xlink
    unbound2 = tmp / "unbound2.fb2"
    unbound2.write_text(
        BOOK_TEMPLATE.format(encoding="utf-8").replace(
            "</body>",
            '<p>Ссылка <a xlink:href="#note">на сноску</a>.</p></body>',
        ),
        encoding="utf-8",
    )
    check("на сноску" in fb2.read(unbound2), "префикс xlink: без объявления")

    # HTML-сущности, которых нет в XML
    entities = tmp / "entities.fb2"
    entities.write_text(
        BOOK_TEMPLATE.format(encoding="utf-8").replace(
            "</body>", "<p>Неразрывный&nbsp;пробел и &#171;кавычки&#187;.</p></body>"
        ),
        encoding="utf-8",
    )
    text = fb2.read(entities)
    check("Неразрывный" in text and "&nbsp;" not in text, "HTML-сущности развёрнуты")

    # zip, переименованный в .fb2 — встречается в реальных коллекциях
    renamed_zip = tmp / "zip-as-fb2.fb2"
    renamed_zip.write_bytes(zip_buffer.getvalue())
    check(fb2.read(renamed_zip) == reference, "zip с именем .fb2 читается по магии")

    # заголовок книги повторяется в первом <title> тела
    duplicate = tmp / "duplicate.fb2"
    duplicate.write_text(
        BOOK_TEMPLATE.format(encoding="utf-8").replace(
            "<body>",
            "<body><title><p>Лев Толстой</p><p>Проверочная книга</p></title>",
        ),
        encoding="utf-8",
    )
    text = fb2.read(duplicate)
    check(text.count("Проверочная книга") == 1,
          "повтор названия в теле не читается дважды")

    # сохранённая веб-страница вместо книги
    page = tmp / "page.fb2"
    page.write_text(
        "<!DOCTYPE html><html><head><title>Сайт</title></head><body><p>не книга</p></body></html>",
        encoding="utf-8",
    )
    expect_error(lambda: fb2.read(page), "веб-страница", "HTML с расширением .fb2")

    # ── 7. Приложение: маршрутизация чтения файлов ───────────────────────────
    print("\n7. Открытие файлов в приложении")
    for name in ("book.fb2", "book.fb2.zip", "book.fb2.xz", "book.fb2.zst", "book.fb2.lz4"):
        path = tmp / name
        if not path.exists():
            continue
        text = TextExtractor.read_text_file(str(path))
        check("Проверочная книга" in text, f"TextExtractor открывает {name}")

    plain = tmp / "заметка.txt"
    plain.write_text("обычный текст", encoding="utf-8")
    check(TextExtractor.read_text_file(str(plain)) == "обычный текст",
          "текстовый файл по-прежнему читается")

    check(fb2.is_fb2("book.fb2"), "is_fb2: книга")
    check(fb2.is_fb2("book.fb2.zip"), "is_fb2: книга в zip")
    check(fb2.is_fb2("book.fb2.xz"), "is_fb2: книга в xz")
    check(fb2.is_fb2("book.fb2.lz4"), "is_fb2: книга в lz4")
    for name in ("book.fb2.tar", "book.fb2.tar.gz", "book.fb2.tgz", "book.fb2.tar.xz",
                 "book.fb2.txz", "book.fb2.tar.bz2", "book.fb2.tbz2"):
        check(fb2.is_fb2(name), f"is_fb2: {name}")
    check(not fb2.is_fb2("book.txt"), "is_fb2: не книга")
    check(not fb2.is_fb2("архив.zip"), "is_fb2: архив без книги")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
