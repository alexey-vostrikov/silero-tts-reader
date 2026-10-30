"""Чтение книг FB2 (FictionBook) для озвучки.

FB2 — это XML, обычно в windows-1251, внутри которого текст лежит в
``<body><section>``, а картинки — в ``<binary>`` как base64. Для синтеза речи
нужен только текст, поэтому:

* контейнер определяется **по магии файла**, а не по расширению: книги лежат в
  zip, xz, lzma, gzip, bzip2, zstd, lz4 и tar, а человек свободно переименовывает
  ``.fb2.zip`` в ``.fb2`` — расширение только подсказка;
* вложенные контейнеры разворачиваются по очереди (``.fb2.xz`` внутри ``.zip``);
* кодировка берётся из XML-декларации (``encoding="windows-1251"``), из BOM
  или подбирается (utf-8 → cp1251);
* из дерева берутся заголовки, абзацы, эпиграфы, стихи и подзаголовки, а
  ``binary``/``image`` отбрасываются — иначе в речь попадёт base64.

Результат — обычный текст с пустой строкой между абзацами: его дальше делят на
сегменты и читают как любой другой.
"""
from __future__ import annotations

import bz2
import gzip
import html.entities
import io
import lzma
import re
import shutil
import struct
import subprocess
import tarfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from ..i18n import _

# Расширения контейнеров. Нужны, чтобы отличить «книгу в архиве» от «просто
# архива», и как подсказка, если магия не опознана: решает всё равно содержимое.
CONTAINER_SUFFIXES = (
    ".zip", ".fbz", ".xz", ".lzma", ".gz", ".bz2", ".zst", ".zstd", ".lz4", ".tar",
    # сжатый tar: .tar.gz и .tgz — одно и то же, дальше по аналогии
    ".tgz", ".txz", ".tbz2", ".tbz", ".tzst", ".tlz",
)

# Магия tar стоит не в начале файла, а на 257-м байте: без неё сжатый tar
# (.fb2.tar.gz) после распаковки не опознаётся и падает на догадке «lzma».
#
# Слово «magic» здесь обманчиво: у вариантов tar оно разное.
#   POSIX ustar и pax:  "ustar\0" "00"
#   GNU tar:            "ustar " " \0"   (пробел вместо версии)
#   старый v7 tar:      магии нет вовсе, только контрольная сумма заголовка
# Поэтому проверяем и префикс «ustar», и контрольную сумму — иначе v7 внутри
# .tar.gz/.tgz молча не опознаётся (после распаковки имя теряет «.tar»).
_TAR_MAGIC_OFFSET = 257
_TAR_CHECKSUM_FIELD = slice(148, 156)


def _looks_like_tar(raw: bytes) -> bool:
    """Заголовок похож на tar: магия ustar/GNU или сходящаяся контрольная сумма.

    Контрольная сумма — единственный признак, общий для всех вариантов, включая
    v7, где поля «ustar» нет вообще. Считается по всему 512-байтному заголовку,
    если поле суммы заменить пробелами.
    """
    if len(raw) < 512:
        return False
    if raw[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == b"ustar":
        return True

    field = raw[_TAR_CHECKSUM_FIELD]
    try:
        declared = int(field.split(b"\x00")[0].split()[0], 8)
    except (ValueError, IndexError):
        return False

    header = bytearray(raw[:512])
    header[_TAR_CHECKSUM_FIELD] = b" " * 8
    return declared == sum(header)

LZ4_LEGACY_MAGIC = b"\x02\x21\x4c\x18"

_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),            # пустой архив
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\x04\x22\x4d\x18", "lz4"),      # LZ4 frame
    (LZ4_LEGACY_MAGIC, "lz4"),        # LZ4 legacy (старый формат)
)

# Элементы, текст которых идёт в озвучку (по локальному имени, без namespace)
_PARAGRAPH_TAGS = {
    "p", "subtitle", "v", "text-author", "date", "poem-title",
    "epigraph", "annotation", "cite", "td", "th",
}
_SKIP_TAGS = {"binary", "image", "stylesheet", "script", "coverpage"}

# Сколько раз разворачивать вложенные контейнеры (zip → xz → XML)
_MAX_DEPTH = 4


def _strip_containers(name: str) -> str:
    """Убрать из имени все расширения контейнеров: ``a.fb2.xz.zip`` → ``a.fb2``."""
    lowered = name.lower()
    changed = True
    while changed:
        changed = False
        for suffix in CONTAINER_SUFFIXES:
            if lowered.endswith(suffix):
                lowered = lowered[: -len(suffix)]
                changed = True
                break
    return lowered


def is_fb2(path: str | Path) -> bool:
    """Это файл книги: ``book.fb2``, ``book.fb2.zip``, ``book.fb2.xz`` и т.п."""
    return _strip_containers(str(path)).endswith((".fb2", ".fbz"))


def _local(tag: str) -> str:
    """``{http://www.gribuser.ru/xml/fictionbook/2.0}p`` → ``p``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# ── Распаковка ───────────────────────────────────────────────────────────────


def _detect(raw: bytes, name: str) -> str | None:
    """Тип контейнера: по магии, расширение — запасной вариант."""
    for magic, kind in _MAGICS:
        if raw.startswith(magic):
            return kind

    # tar: магия или контрольная сумма заголовка. Так распознаётся и голый
    # .tar, и tar внутри уже развёрнутого .tar.gz/.tar.xz — имя к этому моменту
    # расширений лишено, и без этой проверки tar уходит в догадку «lzma».
    if _looks_like_tar(raw):
        return "tar"

    lowered = name.lower()
    for suffix in CONTAINER_SUFFIXES:
        if lowered.endswith(suffix):
            kind = suffix.lstrip(".")
            return {"fbz": "zip", "zst": "zstd"}.get(kind, kind)

    # lzma без заголовка (FORMAT_ALONE) магии не имеет вовсе: узнаём по тому,
    # что это не XML, а первый байт похож на поле свойств LZMA (обычно 0x5d).
    # Проверяем фактической распаковкой: иначе неподдерживаемый формат даёт
    # «Input format not supported by decoder» вместо понятного сообщения.
    if len(raw) > 13 and 0 < raw[0] < 0xE1 and not raw.lstrip().startswith(b"<"):
        try:
            lzma.decompress(raw, format=lzma.FORMAT_ALONE)
        except (lzma.LZMAError, EOFError):
            return None
        return "lzma"
    return None


def _zstd_decompress(raw: bytes) -> bytes:
    """zstd: из коробки в Python 3.14, иначе модуль zstandard, иначе бинарник."""
    try:
        from compression import zstd  # Python 3.14+

        return zstd.decompress(raw)
    except ImportError:
        pass
    except Exception as exc:  # повреждённые данные — понятная ошибка
        raise ValueError(_("не удалось распаковать zstd: {error}").format(error=exc)) from exc

    try:
        import zstandard  # type: ignore[import-not-found]

        return zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 30)
    except ImportError:
        pass

    if shutil.which("zstd"):
        result = subprocess.run(
            ["zstd", "-dc"], input=raw, capture_output=True, timeout=120, check=False
        )
        if result.returncode == 0:
            return result.stdout
        # Имя утилиты и её stderr одинаковы на любом языке — переводить нечего
        raise ValueError(
            f"zstd -dc: {result.stderr.decode('utf-8', 'replace').strip()}"
        )
    raise ValueError(_("нет поддержки zstd: установите zstd или python3-zstandard"))


def _lz4_block_decompress(block: bytes, max_size: int, prefix: bytes = b"") -> bytes:
    """Распаковать один LZ4-блок (последовательности литералов и совпадений).

    ``prefix`` — уже выведенные ранее данные: на них ссылаются совпадения
    связанных блоков. Важно, что это именно начальное содержимое вывода, а не
    часть сжатого потока: иначе декодер прочитал бы окно как токены.
    """
    out = bytearray(prefix)
    produced_start = len(out)
    pos = 0
    length = len(block)
    while pos < length:
        token = block[pos]
        pos += 1

        # Литералы: старшие 4 бита — длина, 15 означает «продолжение»
        literal_length = token >> 4
        if literal_length == 15:
            while True:
                if pos >= length:
                    raise ValueError(_("LZ4: обрыв в длине литералов"))
                extra = block[pos]
                pos += 1
                literal_length += extra
                if extra != 255:
                    break
        if literal_length:
            if pos + literal_length > length:
                raise ValueError(_("LZ4: литералы за границей блока"))
            out += block[pos : pos + literal_length]
            pos += literal_length

        # Последняя последовательность блока заканчивается сразу после литералов
        if pos >= length:
            break

        if pos + 2 > length:
            raise ValueError(_("LZ4: обрыв в смещении совпадения"))
        offset = block[pos] | (block[pos + 1] << 8)
        pos += 2
        if offset == 0 or offset > len(out):
            raise ValueError(_("LZ4: некорректное смещение совпадения"))

        match_length = token & 0x0F
        if match_length == 15:
            while True:
                if pos >= length:
                    raise ValueError(_("LZ4: обрыв в длине совпадения"))
                extra = block[pos]
                pos += 1
                match_length += extra
                if extra != 255:
                    break
        match_length += 4  # минимальное совпадение в LZ4

        start = len(out) - offset
        for index in range(match_length):
            out.append(out[start + index])

        if len(out) - produced_start > max_size:
            raise ValueError(_("LZ4: блок больше заявленного размера"))

    return bytes(out[produced_start:])


def _lz4_decompress(raw: bytes) -> bytes:
    """Распаковать LZ4-фрейм. Модуля lz4 в системе обычно нет — разбираем сами.

    Поддерживаются независимые и связанные блоки (словарь — уже выведенные
    данные). Контрольные суммы пропускаются: они нужны для проверки целостности,
    декодирование от них не зависит.
    """
    if raw.startswith(LZ4_LEGACY_MAGIC):
        # Legacy: магия, затем блоки «длина + сжатые данные» без оглавления и
        # без признака конца — поток заканчивается вместе с файлом.
        out = bytearray()
        pos = 0
        while pos + 4 <= len(raw):
            if raw[pos : pos + 4] != LZ4_LEGACY_MAGIC:
                raise ValueError(_("LZ4 legacy: ожидалась магия блока"))
            pos += 4
            if pos + 4 > len(raw):
                raise ValueError(_("LZ4 legacy: обрыв в длине блока"))
            block_size = struct.unpack_from("<I", raw, pos)[0]
            pos += 4
            block = raw[pos : pos + block_size]
            pos += block_size
            out += _lz4_block_decompress(block, 8 * 1024 * 1024)
        if not out:
            raise ValueError(_("LZ4 legacy: пустой фрейм"))
        return bytes(out)

    out = bytearray()
    pos = 0
    size = len(raw)
    while pos + 4 <= size:
        magic = raw[pos : pos + 4]

        # Пропускаемые фреймы: 0x184D2A5x — длина и данные
        if magic[:3] == b"\x50\x2a\x4d" and 0x50 <= magic[3] <= 0x5F:
            skip_size = struct.unpack_from("<I", raw, pos + 4)[0]
            pos += 8 + skip_size
            continue

        if magic != b"\x04\x22\x4d\x18":
            raise ValueError(_("LZ4: неизвестный фрейм"))

        pos += 4
        if pos + 2 > size:
            raise ValueError(_("LZ4: обрыв в описании фрейма"))
        flg = raw[pos]
        pos += 1
        bd = raw[pos]
        pos += 1

        independent = bool(flg & 0x20)
        has_block_checksum = bool(flg & 0x10)
        has_content_size = bool(flg & 0x08)
        has_dict_id = bool(flg & 0x01)

        if has_content_size:
            pos += 8
        if has_dict_id:
            pos += 4
        pos += 1  # контрольный байт заголовка

        block_max = {4: 1 << 16, 5: 1 << 18, 6: 1 << 20, 7: 1 << 22}.get(
            (bd >> 4) & 0x07, 1 << 16
        )

        while pos + 4 <= size:
            block_size = struct.unpack_from("<I", raw, pos)[0]
            pos += 4
            if block_size == 0:  # конец фрейма
                break

            uncompressed = bool(block_size & 0x80000000)
            block_size &= 0x7FFFFFFF
            block = raw[pos : pos + block_size]
            pos += block_size
            if has_block_checksum:
                pos += 4

            if uncompressed:
                out += block
            elif independent:
                out += _lz4_block_decompress(block, block_max)
            else:
                # Связанный блок ссылается на предыдущие 64 КБ вывода
                out += _lz4_block_decompress(block, block_max, prefix=bytes(out[-65536:]))

        if flg & 0x04:  # контрольная сумма содержимого
            pos += 4

    if not out:
        raise ValueError(_("LZ4: пустой фрейм"))
    return bytes(out)


def _from_tar(raw: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() and _is_book_name(member.name):
                extracted = archive.extractfile(member)
                if extracted is not None:
                    return extracted.read()
    raise ValueError(_("в архиве tar нет файла .fb2"))


def _is_book_name(name: str) -> bool:
    """Имя внутри архива — книга, в том числе в контейнере (``book.fb2.xz``)."""
    return _strip_containers(Path(name).name).endswith((".fb2", ".fbz"))


def _unpack_once(raw: bytes, kind: str) -> bytes:
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            candidates = [
                info for info in archive.infolist()
                if not info.is_dir() and _is_book_name(info.filename)
            ]
            if not candidates:
                raise ValueError(_("в архиве нет файла .fb2"))
            return archive.read(candidates[0])
    if kind == "xz":
        return lzma.decompress(raw, format=lzma.FORMAT_XZ)
    if kind == "lzma":
        return lzma.decompress(raw, format=lzma.FORMAT_ALONE)
    if kind == "gzip":
        return gzip.decompress(raw)
    if kind == "bzip2":
        return bz2.decompress(raw)
    if kind == "zstd":
        return _zstd_decompress(raw)
    if kind == "lz4":
        return _lz4_decompress(raw)
    if kind == "tar":
        return _from_tar(raw)
    raise ValueError(_("неизвестный формат контейнера: {kind}").format(kind=kind))


def _unpack(raw: bytes, name: str) -> bytes:
    """Развернуть все контейнеры до самого XML."""
    current, current_name = raw, name
    for _depth in range(_MAX_DEPTH):
        if current.lstrip().startswith(b"<"):
            return current
        kind = _detect(current, current_name)
        if kind is None:
            return current
        current = _unpack_once(current, kind)
        current_name = _strip_containers(current_name)
        if not current:
            raise ValueError(_("архив пустой"))
    return current


def _decode(raw: bytes) -> str:
    """Определить кодировку: BOM, XML-декларация, затем utf-8 → cp1251."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")

    head = raw[:400].decode("ascii", errors="replace")
    declared = re.search(r"""encoding\s*=\s*["']([\w.-]+)["']""", head, re.IGNORECASE)
    if declared:
        encoding = declared.group(1).lower()
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            pass

    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ── Разбор FB2 ───────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _header(root: ElementTree.Element) -> str:
    """«Название. Имя Фамилия, Имя Фамилия» из ``<description>``.

    Порядок в XML не гарантирован (имя автора идёт раньше названия), поэтому
    собираем по смыслу, а не «как встретилось»: иначе получается
    «Лев. Толстой. Проверочная книга».
    """
    title = ""
    authors: list[str] = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "book-title" and not title and element.text:
            title = _clean(element.text)
        elif tag == "author":
            fields: list[str] = []
            nickname = ""
            for child in element:
                child_tag = _local(child.tag)
                value = _clean(child.text or "")
                if not value:
                    continue
                if child_tag == "nickname":
                    nickname = value
                elif child_tag in ("first-name", "middle-name", "last-name"):
                    fields.append(value)
            name = " ".join(fields) or nickname
            if name:
                authors.append(name)

    parts = [title] if title else []
    # повторяющихся авторов (несколько <author> с тем же именем) убираем
    parts.extend(list(dict.fromkeys(authors))[:3])
    return ". ".join(part for part in parts if part)


def _walk(element: ElementTree.Element, out: list[str], header: str = "") -> None:
    """Собрать текст в порядке документа, пропуская картинки и бинарники.

    ``header`` — уже прочитанный заголовок книги: многие FB2 повторяют автора и
    название в первом ``<title>`` тела, и без сверки книга начинается дважды.
    """
    tag = _local(element.tag)
    if tag in _SKIP_TAGS:
        return

    if tag in _PARAGRAPH_TAGS:
        text = _clean("".join(element.itertext()))
        if text:
            out.append(text)
        return

    if tag == "empty-line":
        # Разделитель сцен: паузу делает сам синтезатор на границе предложения,
        # а пустая строка дала бы двойной перевод строки при склейке.
        return

    if tag == "title":
        # Заголовок раздела: каждая строка — отдельный абзац
        lines: list[str] = []
        for child in element.iter():
            child_tag = _local(child.tag)
            if child_tag in ("p", "v") and child.text:
                line = _clean("".join(child.itertext()))
                if line:
                    lines.append(line)
        if lines and header:
            header_lower = header.lower()
            if all(line.lower() in header_lower for line in lines):
                return  # это повтор названия книги, уже прочитанного в начале
        out.extend(lines)
        return

    for child in element:
        _walk(child, out, header)


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ROOT_TAG = re.compile(r"<(?![?!])([A-Za-z_][\w.:-]*)([^>]*)>", re.DOTALL)


def _bind_prefixes(xml_text: str) -> str:
    """Объявить используемые, но не объявленные префиксы пространств имён.

    Настоящие FB2 сплошь и рядом содержат ``l:href`` или ``xlink:href`` без
    ``xmlns:l``/``xmlns:xlink`` в корне — строгий разбор на этом падает, хотя
    человеку файл читается нормально. Подставляем фиктивный namespace: для
    озвучки адреса ссылок не нужны, важен сам текст.
    """
    match = _ROOT_TAG.search(xml_text)
    if not match:
        return xml_text

    root_tag = match.group(1)
    attributes = match.group(2)
    declared = set(re.findall(r"xmlns:([\w.-]+)\s*=", attributes))

    # префиксы в именах элементов и атрибутов: <x:p>, l:href=...
    used = set(re.findall(r"[<\s]([A-Za-z_][\w.-]*):[A-Za-z_]", xml_text))
    used -= {"xmlns", "xml", root_tag.split(":")[0]}
    missing = sorted(used - declared)
    if not missing:
        return xml_text

    additions = "".join(
        f' xmlns:{prefix}="urn:x-undelared:{prefix}"' for prefix in missing
    )
    start = match.start(2)
    return xml_text[:start] + additions + xml_text[start:]


def _fix_entities(xml_text: str) -> str:
    """Заменить HTML-сущности (``&nbsp;``) на символы: их нет в XML.

    В старых конвертерах FB2 они встречаются постоянно, и строгий разбор на них
    падает целиком — из-за одного неразрывного пробела человек не услышит книгу.
    """
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _XML_ENTITIES:
            return match.group(0)
        value = html.entities.html5.get(f"{name};")
        return value if value else " "

    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", replace, xml_text)


def _strip_control_chars(xml_text: str) -> str:
    """Убрать символы, недопустимые в XML 1.0 (кроме табуляции и переводов)."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)


def _looks_like_html(text: str) -> bool:
    """Сохранённая страница сайта вместо книги: так бывает при скачивании."""
    head = text[:2000].lower()
    return "<!doctype html" in head or "<html" in head


def to_text(xml_text: str, include_header: bool = True) -> str:
    """XML книги → текст для озвучки."""
    # Комментарии и объявления внутри FB2 иногда ломают разбор
    cleaned = re.sub(r"<!--.*?-->", "", xml_text, flags=re.DOTALL)
    cleaned = _strip_control_chars(cleaned)
    cleaned = _bind_prefixes(cleaned)
    cleaned = _fix_entities(cleaned)
    try:
        root = ElementTree.fromstring(cleaned)
    except ElementTree.ParseError as exc:
        if _looks_like_html(cleaned):
            raise ValueError(
                _("это не книга, а сохранённая веб-страница (HTML): "
                  "у файла расширение .fb2, но внутри сайт")
            ) from exc
        raise ValueError(_("это не FB2 или файл повреждён: {error}").format(error=exc)) from exc

    if _local(root.tag) != "FictionBook":
        # Корректный XML — ещё не книга: под .fb2 часто лежит сохранённая страница
        if _local(root.tag).lower() in ("html", "head", "body"):
            raise ValueError(
                _("это не книга, а сохранённая веб-страница (HTML): "
                  "у файла расширение .fb2, но внутри сайт")
            )
        raise ValueError(_("это не FB2: нет корневого элемента FictionBook"))

    lines: list[str] = []
    header = _header(root) if include_header else ""
    if header:
        lines.append(header)

    for body in root.iter():
        if _local(body.tag) == "body":
            _walk(body, lines, header)

    # Каждый абзац — отдельная строка. Одинаковые подряд идущие строки не
    # склеиваем: в книге это законный повтор (рефрен, диалог, припев).
    return "\n\n".join(line for line in lines if line.strip()).strip()


def read(path: str | Path, include_header: bool = True) -> str:
    """Прочитать книгу FB2 из любого поддерживаемого контейнера."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise FileNotFoundError(_("файл не найден: {path}").format(path=file_path))

    raw = file_path.read_bytes()
    if not raw:
        raise ValueError(_("файл пустой"))
    return to_text(_decode(_unpack(raw, file_path.name)), include_header=include_header)
