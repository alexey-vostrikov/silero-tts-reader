"""Иконка окна: чтобы вместо «шестерёнки» был значок приложения.

GNOME подбирает иконку окна по идентификатору приложения: под Wayland это
``app-id`` (он же id приложения GtkApplication), под X11 — ``WM_CLASS``, который
GTK4 выставляет сам из имени программы. Если ни тому, ни другому не
соответствует установленный ``.desktop``, рабочий стол показывает стандартную
заглушку — серую «шестерёнку».

Отсюда три правила, которые здесь и собраны:

* ``app-id`` приложения, имя ``.desktop``-файла и имя иконки согласованы;
* окну задаётся ``icon-name`` — имя иконки в теме (``Icon=`` в ``.desktop``), а
  не путь к файлу: по пути GTK подставляет иконку «файл», и она опять чужая;
* ``set_default_icon_name`` вызывается один раз на процесс: в GTK4
  ``set_icon_from_file`` удалён, и файл иконки окну больше не подставляется —
  тему просят по имени.
"""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

# Тот же идентификатор, что у GtkApplication (см. app.APP_ID). Обратный порядок
# домена обязателен: по нему GNOME ищет ru.skala.silero-tts-reader.desktop.
APP_ID = "ru.skala.silero-tts-reader"
ICON_NAME = "silero-tts-reader"
APP_TITLE = "Silero TTS Reader"

_default_icon_set = False


def icon_file() -> str:
    """Файл иконки: из установленного пакета или из дерева с исходниками."""
    candidates = (
        Path("/usr/share/icons/hicolor/256x256/apps") / f"{ICON_NAME}.png",
        Path("/usr/share/icons/hicolor/128x128/apps") / f"{ICON_NAME}.png",
        Path("/usr/share/pixmaps") / f"{ICON_NAME}.png",
        # Исходник в дереве с кодом: silero_tts_reader/assets/icon.png
        Path(__file__).resolve().parent.parent / "assets" / "icon.png",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def apply_icon(window: Gtk.Window, title: str | None = None) -> None:
    """Поставить окну значок приложения.

    Вызывать для каждого окна верхнего уровня: значок по умолчанию
    наследуется не всеми окнами, а диалог без него показывает заглушку.
    """
    global _default_icon_set
    try:
        if title:
            window.set_title(title)
        window.set_icon_name(ICON_NAME)
        if not _default_icon_set:
            Gtk.Window.set_default_icon_name(ICON_NAME)
            _default_icon_set = True
    except Exception:
        pass
