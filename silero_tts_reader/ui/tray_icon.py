"""Иконка в трее и контекстное меню (GTK3).

Порядок выбора бэкенда:
1. ``AyatanaAppIndicator3`` — штатный путь для GNOME/Wayland (нужен пакет
   ``gir1.2-ayatanaappindicator3-0.1`` и расширение AppIndicator в GNOME Shell);
2. ``Gtk.StatusIcon`` — работает только в X11/XWayland, зато без лишних пакетов;
3. ничего — приложение продолжает работать, а сообщения идут через
   ``Gio.Notification`` (``TrayIcon.available == False``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk  # noqa: E402

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_ICON_NAME = "audio-headset"

_AppIndicator = None
for _namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(_namespace, "0.1")
        _AppIndicator = __import__("gi.repository", fromlist=[_namespace]).__dict__[_namespace]
        break
    except Exception:
        _AppIndicator = None


# Куда пакет кладёт иконку. В репозитории она лежит рядом с кодом (assets),
# а в установленной системе — в теме hicolor и в /usr/share/pixmaps.
ICON_CANDIDATES = (
    _ASSETS_DIR / "icon.png",
    Path("/usr/share/icons/hicolor/256x256/apps/silero-tts-reader.png"),
    Path("/usr/share/icons/hicolor/128x128/apps/silero-tts-reader.png"),
    Path("/usr/share/pixmaps/silero-tts-reader.png"),
)


def icon_path() -> str:
    """Путь к иконке приложения: из репозитория или из установленного пакета.

    Иначе в установленной системе функция возвращала пустую строку, и трей
    молча считался недоступным (`TrayIcon.available == False`).
    """
    for candidate in ICON_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return ""


class TrayIcon:
    """Иконка в трее с меню: озвучить буфер, настройки, о программе, выход."""

    def __init__(
        self,
        on_speak_clipboard: Callable[[], None],
        on_settings: Callable[[], None],
        on_about: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_speak_clipboard = on_speak_clipboard
        self._on_settings = on_settings
        self._on_about = on_about
        self._on_quit = on_quit

        self._indicator = None
        self._status_icon: Gtk.StatusIcon | None = None
        self.available = False

        self._menu = self._build_menu()
        self._setup_backend()

    # ── Меню ─────────────────────────────────────────────────────────────────

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        item = Gtk.MenuItem(label="📋  Озвучить буфер обмена")
        item.connect("activate", lambda _i: self._on_speak_clipboard())
        menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        item = Gtk.MenuItem(label="⚙️  Настройки")
        item.connect("activate", lambda _i: self._on_settings())
        menu.append(item)

        item = Gtk.MenuItem(label="ℹ️  О программе")
        item.connect("activate", lambda _i: self._on_about())
        menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        item = Gtk.MenuItem(label="✖  Выход")
        item.connect("activate", lambda _i: self._on_quit())
        menu.append(item)

        menu.show_all()
        return menu

    # ── Бэкенды ──────────────────────────────────────────────────────────────

    def _setup_backend(self) -> None:
        path = icon_path()

        if _AppIndicator is not None and path:
            try:
                self._indicator = _AppIndicator.Indicator.new(
                    "silero-tts-reader",
                    path,
                    _AppIndicator.IndicatorCategory.APPLICATION_STATUS,
                )
                self._indicator.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
                self._indicator.set_title("Silero TTS Reader")
                self._indicator.set_menu(self._menu)
                self.available = True
                return
            except Exception:
                self._indicator = None

        # Gtk.StatusIcon на Wayland не отображается — там нужен AppIndicator
        is_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        )
        if hasattr(Gtk, "StatusIcon") and not is_wayland:
            try:
                self._status_icon = Gtk.StatusIcon.new_from_file(path) if path else (
                    Gtk.StatusIcon.new_from_icon_name(_ICON_NAME)
                )
                self._status_icon.set_tooltip_text("Silero TTS Reader")
                self._status_icon.set_visible(True)
                self._status_icon.connect("popup-menu", self._on_popup_menu)
                self._status_icon.connect("activate", lambda _i: self._on_speak_clipboard())
                self.available = True
            except Exception:
                self._status_icon = None

    def _on_popup_menu(self, status_icon: Gtk.StatusIcon, button: int, time: int) -> None:
        self._menu.popup(None, None, Gtk.StatusIcon.position_menu, status_icon, button, time)

    # ── Внешний интерфейс ────────────────────────────────────────────────────

    def show(self) -> None:
        if self._indicator is not None:
            self._indicator.set_status(_AppIndicator.IndicatorStatus.ACTIVE)
        elif self._status_icon is not None:
            self._status_icon.set_visible(True)
        # Нет трея — приложение всё равно работает, см. notify()

    def hide(self) -> None:
        if self._indicator is not None:
            self._indicator.set_status(_AppIndicator.IndicatorStatus.PASSIVE)
        elif self._status_icon is not None:
            self._status_icon.set_visible(False)
