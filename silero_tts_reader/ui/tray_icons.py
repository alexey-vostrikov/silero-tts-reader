"""Значок трея: монохромный рисунок под цвет панели.

Панель не перекрашивает чужие значки: если отдать ей цветную иконку
приложения, на тёмной панели она выглядит ярким пятном, а на светлой —
тёмным. Поэтому значок трея — отдельный рисунок без плашки, в двух
вариантах цвета штриха:

* ``-tray-light`` — светлый штрих, для тёмной панели;
* ``-tray-dark``  — тёмный штрих, для светлой панели.

Вариант выбирается по системной теме (``auto``) или задаётся в настройках.
Состояния воспроизведения значком не показываются: в панели значок живёт в
16–24 px, и знак паузы там не отличить от знака чтения.
"""
from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

ICON_NAME = "silero-tts-reader"

# Значения настройки «значок в трее».
PREFER_AUTO = "auto"
PREFER_LIGHT = "light"   # светлый штрих → тёмная панель
PREFER_DARK = "dark"     # тёмный штрих → светлая панель

_THEME_SCHEMA = "org.gnome.desktop.interface"


def _interface_settings():
    """Настройки рабочего стола или None, если схемы нет (сервер, тесты)."""
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(_THEME_SCHEMA, True) is None:
            return None
        return Gio.Settings.new(_THEME_SCHEMA)
    except Exception:
        return None


def _theme_name() -> str:
    """Имя темы GTK: из настроек рабочего стола или из окружения."""
    settings = _interface_settings()
    if settings is not None:
        try:
            name = settings.get_string("gtk-theme")
            if name:
                return name
        except Exception:
            pass
    return os.environ.get("GTK_THEME", "")


def _color_scheme() -> str:
    settings = _interface_settings()
    if settings is None:
        return ""
    try:
        return settings.get_string("color-scheme")
    except Exception:
        return ""


def system_is_dark() -> bool:
    """Тёмное ли оформление системы.

    Сначала смотрим ``color-scheme``: в GNOME 42+ это основной признак, и он
    честнее имени темы — Yaru-dark при светлой схеме тоже встречается.
    Затем имя темы: в нём «dark» появляется у большинства тем. Если ничего
    не известно, считаем панель светлой: на светлой панели тёмный значок
    заметнее, чем светлый на тёмной в обратном случае.
    """
    scheme = _color_scheme()
    if scheme == "prefer-dark":
        return True
    if scheme == "prefer-light":
        return False
    return "dark" in _theme_name().lower()


def icon_name(prefer: str = PREFER_AUTO) -> str:
    """Имя значка трея для выбранного варианта цвета.

    ``prefer`` — значение настройки: ``auto`` смотрит на системную тему,
    ``light``/``dark`` задают цвет штриха явно (панель бывает не системная,
    например тёмная панель на светлом рабочем столе).
    """
    if prefer == PREFER_LIGHT:
        variant = "light"
    elif prefer == PREFER_DARK:
        variant = "dark"
    else:
        variant = "light" if system_is_dark() else "dark"
    return f"{ICON_NAME}-tray-{variant}"


def icon_file(name: str, size: int = 32) -> str:
    """Файл значка: из установленного пакета или из дерева с исходниками.

    Нужен для запасного пути, когда панель просит готовые пиксели
    (``IconPixmap``), а не имя из темы.
    """
    candidates = (
        Path(f"/usr/share/icons/hicolor/{size}x{size}/apps") / f"{name}.png",
        Path(f"/usr/share/icons/hicolor/{size}x{size}/apps")
        / f"{ICON_NAME}.png",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""
