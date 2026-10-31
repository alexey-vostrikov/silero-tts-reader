"""Уровни журнала: сколько отладочных подробностей печатать.

Приложение пишет диагностику в консоль (иначе причину сбоя не видно: в journald
сообщения оболочки не попадают). Но подробный журнал нужен не всегда: при
обычной работе достаточно строк о запуске и ошибках, а при разборе — весь путь
текста через обработку, синтез и плеер.

Уровни:

* ``quiet`` — только ошибки;
* ``normal`` — запуск, состояние, ошибки (по умолчанию);
* ``detail`` — плюс решения обработки текста и параметры синтеза;
* ``debug`` — плюс содержимое текста, ответы ИИ и каждый сегмент.

Уровень задаётся ключом ``--log-level`` (или ``--debug``) и настройкой
``ui.log_level``; ключ сильнее настройки, потому что действует на один запуск.
"""
from __future__ import annotations

import sys
from typing import TextIO

QUIET = 0
NORMAL = 1
DETAIL = 2
DEBUG = 3

LEVELS = {
    "quiet": QUIET,
    "normal": NORMAL,
    "detail": DETAIL,
    "debug": DEBUG,
}

DEFAULT_LEVEL = NORMAL

_level = DEFAULT_LEVEL
_stream: TextIO | None = None


class LevelError(ValueError):
    """Неизвестный уровень журнала."""


def parse_level(name: str) -> int:
    """Имя уровня в число. Неизвестное имя — исключение, а не тишина.

    Молчаливое «не понял, значит обычный» опасно вдруг тем, что отладочный
    запуск выглядит рабочим, а подробностей в нём нет.
    """
    try:
        return LEVELS[name.strip().lower()]
    except KeyError as exc:
        known = ", ".join(LEVELS)
        # i18n: нет перевода — диагностика командной строки
        raise LevelError(f"неизвестный уровень журнала {name!r} (есть: {known})") from exc


def level_name(value: int) -> str:
    """Обратное преобразование — для журнала запуска."""
    for name, number in LEVELS.items():
        if number == value:
            return name
    return "normal"


def set_level(value: int | str) -> int:
    """Задать уровень: числом или именем. Возвращает установленное число."""
    global _level
    _level = parse_level(value) if isinstance(value, str) else int(value)
    return _level


def current_level() -> int:
    return _level


def enabled(value: int) -> bool:
    """Печатать ли сообщения этого уровня."""
    return _level >= value


def set_stream(stream: TextIO | None) -> None:
    """Куда писать. None — текущий stdout (так удобнее тестам и systemd).

    Подменяется в тестах: иначе пришлось бы перенаправлять stdout целиком и
    ловить посторонние сообщения библиотек.
    """
    global _stream
    _stream = stream


def _write(prefix: str, message: str) -> None:
    out = _stream if _stream is not None else sys.stdout
    print(f"{prefix}{message}", file=out, flush=True)


def error(message: str) -> None:
    """Ошибка: печатается всегда, даже в quiet."""
    _write("  ✗ ", message)


def warning(message: str) -> None:
    """Предупреждение: печатается со обычного уровня и выше."""
    if enabled(NORMAL):
        _write("  ⚠ ", message)


def info(message: str) -> None:
    """Состояние приложения: обычный уровень."""
    if enabled(NORMAL):
        _write("  ", message)


def detail(message: str) -> None:
    """Решения обработки: уровень detail и выше."""
    if enabled(DETAIL):
        _write("  ", message)


def debug(message: str) -> None:
    """Подробности: содержимое текста, ответы ИИ, каждый сегмент."""
    if enabled(DEBUG):
        _write("  ", message)
