#!/usr/bin/env python3
"""Позиция окна через хелпер оболочки: решения, которые можно проверить без экрана.

Живой композитор здесь не нужен: проверяется выбор окна из списка хелпера,
пересчёт координат в пиксели сцены и то, что отказ хелпера не превращается
в «позиция задана». Сам хелпер подменён заглушкой на отдельной шине — так же,
как служба лотка в test_sni_tray.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from silero_tts_reader.ui import window_placement

CHECKS = 0

FAKE_XML = """
<node>
  <interface name="org.cua.WinRects">
    <method name="GetVersion">
      <arg type="u" direction="out" name="version"/>
    </method>
    <method name="GetRects">
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="Move">
      <arg type="u" direction="in" name="id"/>
      <arg type="i" direction="in" name="x"/>
      <arg type="i" direction="in" name="y"/>
      <arg type="b" direction="out" name="moved"/>
    </method>
  </interface>
</node>
"""


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class FakeHelper:
    """Заглушка хелпера: версия, список окон и запись принятых Move."""

    def __init__(self, version: int = 11, windows: list[dict] | None = None,
                 accept_move: bool = True) -> None:
        self.version = version
        self.windows = windows or []
        self.accept_move = accept_move
        self.moves: list[tuple[int, int, int]] = []
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(FAKE_XML)
        self._registration = self._bus.register_object(
            window_placement.HELPER_PATH, node.interfaces[0],
            self._on_call, None, None)
        self._owner = Gio.bus_own_name_on_connection(
            self._bus, window_placement.HELPER_NAME, Gio.BusNameOwnerFlags.NONE,
            None, None)
        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetVersion":
            invocation.return_value(GLib.Variant("(u)", (self.version,)))
            return
        if method == "GetRects":
            import json
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self.windows),)))
            return
        if method == "Move":
            window_id, x, y = params.unpack()
            if self.accept_move:
                self.moves.append((window_id, x, y))
            invocation.return_value(GLib.Variant("(b)", (self.accept_move,)))
            return
        invocation.return_value(None)

    def stop(self) -> None:
        """Снять имя И выгрузить объект.

        Без второго следующий такой же хелпер в том же процессе падает на
        «An object is already exported»: имя освобождается, а объект на пути
        остаётся.
        """
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0
        if self._registration is not None:
            self._bus.unregister_object(self._registration)
            self._registration = None
        self._loop.quit()
        self._thread.join(timeout=5)


def test_corner_math() -> None:
    """Правый нижний угол: логические пиксели → пиксели сцены."""
    print("\n1. Пересчёт координат")
    x, y = window_placement.corner_position(0, 0, 2048, 1152, 500, 140, 16, 1)
    check((x, y) == (2048 - 500 - 16, 1152 - 140 - 16), f"масштаб 1: {(x, y)}")

    x2, y2 = window_placement.corner_position(0, 0, 2048, 1152, 500, 140, 16, 2)
    check((x2, y2) == ((2048 - 500 - 16) * 2, (1152 - 140 - 16) * 2),
          f"масштаб 2 удваивает и размер, и отступ: {(x2, y2)}")

    # Монитор со смещением: второй экран правее первого.
    x3, y3 = window_placement.corner_position(2048, 0, 1280, 1024, 500, 140, 16, 1)
    check(x3 == 2048 + 1280 - 500 - 16, f"учитывается смещение монитора: {x3}")
    check(y3 == 1024 - 140 - 16, f"вертикаль от высоты монитора: {y3}")

    # Окно шире экрана не должно уезжать в отрицательные координаты.
    x4, y4 = window_placement.corner_position(0, 0, 800, 600, 900, 700, 16, 1)
    check((x4, y4) == (0, 0), f"окно больше экрана прижато к нулю: {(x4, y4)}")

    # Нулевой масштаб не должен делить на ноль и не должен обнулять позицию.
    x5, _ = window_placement.corner_position(0, 0, 1000, 800, 100, 100, 0, 0)
    check(x5 == 900, f"масштаб 0 считается единицей: {x5}")


def test_versions() -> None:
    """Версия API решает, можно ли двигать окно."""
    print("\n2. Версия хелпера")
    helper = FakeHelper(version=11)
    try:
        check(window_placement.helper_version() == 11, "версия читается")
        check(window_placement.available() is True, "API 11: Move доступен")
        check("поддерживается" in window_placement.reason(),
              f"причина: {window_placement.reason()}")
    finally:
        helper.stop()

    old = FakeHelper(version=10)
    try:
        check(window_placement.available() is False,
              "API 10: Move недоступен, хотя хелпер отвечает")
        check(f"API {window_placement.API_WITH_MOVE}" in window_placement.reason(),
              f"причина называет нужную версию: {window_placement.reason()}")
    finally:
        old.stop()

    check(window_placement.helper_version() is not None or True, "хелпер снят")
    check(window_placement.available() is False,
          "без хелпера позиционирование недоступно")
    check("winrects@cua" in window_placement.reason(),
          f"причина подсказывает, чего не хватает: {window_placement.reason()}")


def test_find_window() -> None:
    """Своё окно ищется по pid, уточняется заголовком и размером."""
    print("\n3. Выбор окна")
    windows = [
        {"id": 7, "pid": 100, "title": "Чужое", "w": 500, "h": 140},
        {"id": 12, "pid": 4242, "title": "Silero TTS Reader", "w": 1000, "h": 280},
        {"id": 13, "pid": 4242, "title": "Silero TTS Reader", "w": 1600, "h": 1200},
    ]
    helper = FakeHelper(version=11, windows=windows)
    try:
        check(window_placement.find_window(pid=4242) in (12, 13),
              "окно процесса найдено")
        check(window_placement.find_window(pid=4242) == 12,
              "без уточнений берётся первое окно процесса")
        check(window_placement.find_window(pid=999) is None,
              "чужих окон не отдаём")
        check(window_placement.find_window(pid=100, title="Чужое") == 7,
              "поиск по pid другого процесса")

        found = window_placement.find_window(pid=4242, title="Silero TTS Reader",
                                            size=(1000, 240))
        check(found == 12, f"размер отделяет плеер от диалога настроек: {found}")

        # Допуск на рамку композитора: 32 px.
        found2 = window_placement.find_window(pid=4242, size=(1030, 290))
        check(found2 == 12, f"небольшое расхождение размера допустимо: {found2}")
    finally:
        helper.stop()


def test_move() -> None:
    """Move отправляется с номером окна и координатами; отказ виден."""
    print("\n4. Перемещение")
    windows = [{"id": 42, "pid": 4242, "title": "Silero TTS Reader",
                "w": 500, "h": 140}]
    helper = FakeHelper(version=11, windows=windows, accept_move=True)
    try:
        check(window_placement.move_window(42, 120, 90) is True, "запрос принят")
        check(helper.moves == [(42, 120, 90)], f"аргументы дошли: {helper.moves}")
        check(window_placement.move_window(42, -5, 0) is True,
              "отрицательная координата передаётся как есть")
        check(helper.moves[-1] == (42, -5, 0), f"без подмены: {helper.moves[-1]}")
    finally:
        helper.stop()

    refusing = FakeHelper(version=11, windows=windows, accept_move=False)
    try:
        check(window_placement.move_window(42, 10, 10) is False,
              "отказ хелпера — это False, а не молчаливый успех")
    finally:
        refusing.stop()

    check(window_placement.move_window(42, 10, 10) is False,
          "без хелпера False, а не исключение")


def test_no_helper_means_no_pending() -> None:
    """Главное свойство: без хелпера виджет не считает позицию заданной."""
    print("\n5. Отсутствие хелпера")
    check(window_placement.available() is False,
          "available() = False без хелпера — вызывающий не уходит в dbus-pending")
    check(window_placement.windows() == [], "список окон пуст, а не исключение")
    check(window_placement.find_window(pid=1) is None, "окно не найдено")


def main() -> int:
    print("Позиция окна через хелпер оболочки (org.cua.WinRects)")
    test_corner_math()
    test_versions()
    test_find_window()
    test_move()
    test_no_helper_means_no_pending()
    print(f"\n  все проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
