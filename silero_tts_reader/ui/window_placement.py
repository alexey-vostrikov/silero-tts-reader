"""Позиция окна под обычным GNOME Wayland — через хелпер оболочки по D-Bus.

Клиент Wayland не может поставить своё окно в точку: место назначает
композитор. Путей обойти это три:

* ``wlr-layer-shell`` — протокол, которого в mutter нет (см. ``layer_shell``);
* X11 — там место задаёт X-сервер (см. ``layer_shell.move_x11``);
* хелпер GNOME Shell, который уже стоит в системе и говорит с композитором
  от его имени: ``org.cua.WinRects`` (расширение ``winrects@cua``, ставится
  вместе с cua-driver).

Этот модуль — третий путь. Метод ``Move(id, x, y)`` появился в API 11 хелпера;
в API 10 его нет, поэтому версия проверяется явно, а не «попробуем и посмотрим».

Окно адресуется стабильным номером оболочки (stable sequence), а не заголовком:
`GetRects` отдаёт список окон с ``id``, ``pid`` и ``title``, и своё окно ищется
по pid процесса. Идентификатор не меняется от перестановки окон.

Координаты ``Move`` — физические пиксели сцены (то, что показывает
``GetRects``), а размеры виджета считаются в логических пикселях, поэтому при
масштабе экрана позиция умножается на scale factor. Иначе на HiDPI окно
уезжает в угол, но не в тот.
"""
from __future__ import annotations

import json
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HELPER_NAME = "org.cua.WinRects"
HELPER_PATH = "/org/cua/WinRects"
HELPER_IFACE = "org.cua.WinRects"

# Первая версия API, где есть Move. В 10 окно можно только активировать и снять.
API_WITH_MOVE = 11

# Вызовы идут из главного потока на показе окна, поэтому таймаут короткий:
# зависший хелпер не должен задерживать появление виджета.
CALL_TIMEOUT_MS = 300


def _session_bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _call(bus, method: str, args: GLib.Variant | None = None,
          reply: str | None = None):
    """Синхронный вызов хелпера. None, если его нет или он не ответил."""
    if bus is None:
        return None
    try:
        result = bus.call_sync(HELPER_NAME, HELPER_PATH, HELPER_IFACE, method,
                               args, GLib.VariantType.new(reply) if reply else None,
                               Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)
    except GLib.Error:
        return None
    return result


def helper_version(bus=None) -> int | None:
    """Версия API хелпера или None, если хелпер не отвечает."""
    result = _call(bus if bus is not None else _session_bus(),
                   "GetVersion", reply="(u)")
    if result is None:
        return None
    try:
        return int(result.unpack()[0])
    except (TypeError, IndexError, ValueError):
        return None


def available(bus=None) -> bool:
    """Можно ли двигать окно: хелпер есть и умеет Move."""
    version = helper_version(bus)
    return version is not None and version >= API_WITH_MOVE


def reason(bus=None) -> str:
    """Почему недоступно — для журнала запуска.

    Строки этой функции видны только в консоли запуска: пользователь их не
    переводит и не ищет, это диагностика для отчёта об ошибке.
    """
    version = helper_version(bus)
    # i18n: нет перевода — диагностика в журнале запуска
    if version is None:
        return ("нет хелпера оболочки (расширение winrects@cua не загружено: "
                "нужен вход в сессию после установки, в GNOME 50 он отключён)")
    # i18n: нет перевода — диагностика в журнале запуска
    if version < API_WITH_MOVE:
        return f"хелпер API {version}: метод Move появился в API {API_WITH_MOVE}"
    # i18n: нет перевода — диагностика в журнале запуска
    return f"поддерживается (API {version})"


def windows(bus=None) -> list[dict]:
    """Окна по данным хелпера: id, pid, title и рамка в пикселях сцены."""
    result = _call(bus if bus is not None else _session_bus(), "GetRects", reply="(s)")
    if result is None:
        return []
    try:
        return json.loads(result.unpack()[0])
    except (TypeError, ValueError):
        return []


def find_window(window_id: int | None = None, pid: int | None = None,
                title: str | None = None, size: tuple[int, int] | None = None,
                bus=None) -> int | None:
    """Номер окна в оболочке: по pid, затем по заголовку и размеру.

    Заголовок уточняет выбор, когда у приложения несколько окон, а размер —
    когда заголовок у них общий (окно плеера и диалог настроек). Размер
    сравнивается с допуском: композитор добавляет к нему свою рамку.
    """
    candidates = windows(bus)
    own_pid = os.getpid() if pid is None else pid
    mine = [entry for entry in candidates if entry.get("pid") == own_pid]
    if not mine:
        return None
    if title is not None:
        titled = [entry for entry in mine if entry.get("title") == title]
        if titled:
            mine = titled
    if size is not None and len(mine) > 1:
        width, height = size
        near = [entry for entry in mine
                if abs(entry.get("w", 0) - width) <= 32
                and abs(entry.get("h", 0) - height) <= 32]
        if near:
            mine = near
    return mine[0].get("id")


def corner_position(area_x: int, area_y: int, area_w: int, area_h: int,
                    width: int, height: int, margin: int, scale: int) -> tuple[int, int]:
    """Правый нижний угол рабочей области в пикселях сцены.

    Размеры окна и отступ — в логических пикселях, ``Move`` ждёт физические:
    при масштабе 2 окно шириной 500 занимает 1000 пикселей сцены, и позиция,
    посчитанная без множителя, окажется в середине экрана.
    """
    scale = max(1, int(scale))
    right = (area_x + area_w) * scale
    bottom = (area_y + area_h) * scale
    x = right - width * scale - margin * scale
    y = bottom - height * scale - margin * scale
    return max(0, x), max(0, y)


def move_window(window_id: int, x: int, y: int, bus=None) -> bool:
    """Поставить окно в точку (x, y). True — запрос принят композитором."""
    result = _call(bus if bus is not None else _session_bus(), "Move",
                   GLib.Variant("(uii)", (int(window_id), int(x), int(y))), "(b)")
    if result is None:
        return False
    try:
        return bool(result.unpack()[0])
    except (TypeError, IndexError):
        return False


def place_bottom_right(gtk_window, width: int, height: int, margin: int = 16,
                       monitor_index: int | None = None) -> str:
    """Прижать окно к правому нижнему углу через хелпер оболочки.

    Возвращает ``"dbus"``, если запрос принят, иначе ``"none"``: вызывающий
    по этому значению понимает, что позиция уже задана и трогать её не надо.
    Окно должно быть уже показано — до этого оболочка о нём не знает, и
    ``find_window`` не найдёт его в списке.
    """
    from . import layer_shell  # общий код выбора монитора

    monitor = layer_shell.monitor_at(gtk_window, monitor_index) \
        if monitor_index is not None else layer_shell.primary_monitor(gtk_window)
    if monitor is None:
        return "none"
    area = monitor.get_geometry()
    scale = monitor.get_scale_factor()
    # Окно ищется по логическому размеру: столько просит виджет, а физический
    # размер в списке оболочки равен ему, умноженному на масштаб.
    window_id = find_window(title=gtk_window.get_title(),
                            size=(width * max(1, scale), height * max(1, scale)))
    if window_id is None:
        return "none"
    x, y = corner_position(area.x, area.y, area.width, area.height,
                           width, height, margin, scale)
    return "dbus" if move_window(window_id, x, y) else "none"
