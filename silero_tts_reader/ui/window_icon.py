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

import contextlib
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

# Тот же идентификатор, что у GtkApplication (см. app.APP_ID). Обратный порядок
# домена обязателен: по нему GNOME ищет ru.skala.silero-tts-reader.desktop.
APP_ID = "ru.skala.silero-tts-reader"
ICON_NAME = "silero-tts-reader"
APP_TITLE = "Silero TTS Reader"
# Пара WM_CLASS для X11: имя программы и класс. Класс совпадает со
# StartupWMClass в ярлыке; домена здесь быть не должно.
WM_INSTANCE = ICON_NAME
WM_CLASS = "SileroTtsReader"

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


def set_program_name() -> None:
    """Назвать окно так, как оконная система ищет значок приложения.

    Под Wayland оболочка берёт идентификатор окна (``_GTK_APPLICATION_ID``) и
    находит ``.desktop`` — там нужен обратный домен, и он задаётся через
    ``application_id``.

    Под X11 сопоставление идёт по ``WM_CLASS``, а это пара (instance, class):
    короткие имена латиницей, без домена и точек. Обратный домен в ``WM_CLASS``
    — чужое для X11 соглашение: часть панелей и доков такой класс не сопоставляет
    с ярлыком. Поэтому здесь:
      * instance — ``silero-tts-reader``, имя программы;
      * class     — ``SileroTtsReader``, то же, что ``StartupWMClass`` в ярлыке.

    По умолчанию GTK берёт оба из имени запущенного файла, а у установленного
    пакета это ``run.py``: с ярлыком такое имя не совпадает, и окно остаётся без
    значка. Вызывать до создания окон — ``WM_CLASS`` выставляется при показе.
    """
    with contextlib.suppress(Exception):
        GLib.set_prgname(WM_INSTANCE)
    # В GDK4 нет Gdk.set_program_class: класс окна задаётся только через
    # X11-дисплей. Под Wayland вызова нет — там класс не используется.
    with contextlib.suppress(Exception):
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
        display = Gdk.Display.get_default()
        if isinstance(display, GdkX11.X11Display):
            GdkX11.X11Display.set_program_class(display, WM_CLASS)


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
