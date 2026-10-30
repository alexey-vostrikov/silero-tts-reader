"""Локализация интерфейса: en_US и ru_RU.

Исходный язык строк в коде — русский: он и есть ``msgid``, поэтому для ru_RU
каталог не нужен (gettext возвращает сам msgid). Для en_US рядом лежит
``locale/en_US/LC_MESSAGES/silero-tts-reader.mo``.

Выбор языка, по убыванию приоритета:

1. явный аргумент ``language`` (``--lang`` у CLI, ``ui.language`` в конфиге);
2. переменные окружения ``LANGUAGE``, ``LC_ALL``, ``LC_MESSAGES``, ``LANG``;
3. ``ru_RU``, если ничего не задано: приложение русское по происхождению, и
   молча переключать уже настроенных пользователей на английский нельзя.

``ui.language = auto`` означает «взять из окружения».
"""
from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "silero-tts-reader"
DEFAULT_LANGUAGE = "ru_RU"
AVAILABLE = {"ru_RU": "Русский", "en_US": "English"}

# Каталоги переводов. Сначала каталог рядом с кодом: он собран из этих же
# исходников. Общий /usr/share/locale идёт вторым — там может лежать каталог от
# ранее установленного пакета со старыми строками, и он не должен заслонять
# свежий (иначе новые строки молча остаются на языке исходника).
_LOCALEDIRS = (
    Path(__file__).resolve().parent.parent / "locale",
    Path("/usr/local/share/locale"),
    Path("/usr/share/locale"),
)

_translation: gettext.NullTranslations | None = None
_current: str = DEFAULT_LANGUAGE


def _candidate_dirs() -> list[str]:
    return [str(path) for path in _LOCALEDIRS if path.is_dir()]


def normalize_language(value: str | None) -> str | None:
    """``ru``, ``ru_RU.UTF-8``, ``en_US``, ``en-GB`` → код из AVAILABLE."""
    if not value:
        return None
    code = str(value).split(":")[0].split(".")[0].strip().replace("-", "_")
    if not code:
        return None
    if code in AVAILABLE:
        return code

    short = code.split("_")[0].lower()
    for known in AVAILABLE:
        if known.split("_")[0].lower() == short:
            return known
    return None


def detect_language(explicit: str | None = None) -> str:
    """Определить язык: явное значение, затем окружение, затем ru_RU."""
    if explicit and explicit not in ("auto", "system"):
        resolved = normalize_language(explicit)
        if resolved:
            return resolved

    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(variable, "")
        if not raw:
            continue
        # LANGUAGE может содержать список через двоеточие — берём первый
        for chunk in raw.split(":"):
            resolved = normalize_language(chunk)
            if resolved:
                return resolved
    return DEFAULT_LANGUAGE


def init(language: str | None = None) -> str:
    """Загрузить каталог и запомнить язык. Возвращает выбранный код."""
    global _translation, _current

    _current = detect_language(language)
    _translation = None
    for directory in _candidate_dirs():
        try:
            _translation = gettext.translation(
                DOMAIN, localedir=directory, languages=[_current], fallback=False
            )
            break
        except (OSError, FileNotFoundError):
            continue
    if _translation is None:
        # Нет каталога — строки остаются исходными (русскими)
        _translation = gettext.NullTranslations()

    # Для ru_RU каталога может не быть вовсе: msgid и есть русский текст
    return _current


def current_language() -> str:
    return _current


def language_name(code: str | None = None) -> str:
    return AVAILABLE.get(code or _current, code or _current)


def _(message: str) -> str:
    """Перевести строку."""
    if _translation is None:
        init()
    assert _translation is not None
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, count: int) -> str:
    if _translation is None:
        init()
    assert _translation is not None
    return _translation.ngettext(singular, plural, count)


def enabled() -> bool:
    """True, если загружен реальный перевод (а не заглушка)."""
    return not isinstance(_translation, gettext.NullTranslations) or _current == DEFAULT_LANGUAGE


# Короткий псевдоним в стиле gettext: from .i18n import _ as _t
_t = _

__all__ = [
    "AVAILABLE",
    "DEFAULT_LANGUAGE",
    "DOMAIN",
    "_",
    "_t",
    "current_language",
    "detect_language",
    "enabled",
    "init",
    "language_name",
    "ngettext",
    "normalize_language",
]
