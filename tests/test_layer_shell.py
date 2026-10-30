#!/usr/bin/env python3
"""Позиционирование окна через layer-shell: решения без композитора и экрана.

Живой протокол здесь не проверить — для этого нужен композитор с поддержкой
wlr-layer-shell. Зато проверяемо всё, что решается до обращения к нему: выбор
монитора по номеру, отказ при отсутствии библиотеки или протокола и значения,
которые приходят прямо из спецификации.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.ui import layer_shell

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class FakeMonitor:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"<монитор {self.name}>"


class FakeMonitorList:
    """Список мониторов как в GDK4: объект-модель, а не пара методов."""

    def __init__(self, monitors) -> None:
        self._monitors = list(monitors)

    def get_n_items(self) -> int:
        return len(self._monitors)

    def get_item(self, index: int):
        return self._monitors[index]


class FakeDisplay:
    def __init__(self, count: int) -> None:
        self._monitors = FakeMonitorList(
            FakeMonitor(str(index)) for index in range(count))
        self._primary = self._monitors.get_item(0) if count else None

    def get_monitors(self):
        return self._monitors

    def get_primary_monitor(self):
        return self._primary


class FakeWindow:
    """Окно-заглушка: настоящему GTK нужен дисплей, а решению — только список."""

    def __init__(self, count: int) -> None:
        self._display = FakeDisplay(count)

    def get_display(self):
        return self._display


def test_protocol_constants() -> None:
    """Края и слои — значения протокола, а не наши: ошибка молча ставит не туда."""
    print("\n1. Значения из протокола wlr-layer-shell")
    check((layer_shell.EDGE_LEFT, layer_shell.EDGE_RIGHT,
           layer_shell.EDGE_TOP, layer_shell.EDGE_BOTTOM) == (0, 1, 2, 3),
          "порядок краёв LEFT, RIGHT, TOP, BOTTOM")
    check(layer_shell.LAYER_OVERLAY == 2 and layer_shell.LAYER_BOTTOM == 0,
          "слой overlay = 2, bottom = 0")
    check(layer_shell.KEYBOARD_MODE_ON_DEMAND == 2,
          "режим клавиатуры on-demand = 2")


def test_missing_library() -> None:
    """Нет библиотеки — нет и протокола, но приложение работает дальше."""
    print("\n2. Библиотека протокола отсутствует")
    saved_loaded, saved_lib = layer_shell._loaded, layer_shell._lib
    layer_shell._loaded, layer_shell._lib = True, None
    try:
        check(layer_shell.available() is False, "available() возвращает False")
        check("libgtk4-layer-shell" in layer_shell.reason(),
              f"reason() называет причину: {layer_shell.reason()}")
    finally:
        layer_shell._loaded, layer_shell._lib = saved_loaded, saved_lib


def test_unsupported_compositor() -> None:
    """Композитор без протокола: available() = False и без вызова GTK."""
    print("\n3. Композитор не реализует протокол")
    class FakeLib:
        @staticmethod
        def gtk_layer_is_supported() -> int:
            return 0

    saved_loaded, saved_lib = layer_shell._loaded, layer_shell._lib
    layer_shell._loaded, layer_shell._lib = True, FakeLib()
    try:
        check(layer_shell.available() is False, "available() = False")
        check("не реализует" in layer_shell.reason(),
              f"причина: {layer_shell.reason()}")
    finally:
        layer_shell._loaded, layer_shell._lib = saved_loaded, saved_lib


def test_monitor_by_index() -> None:
    """Выбор монитора по номеру: границы важнее середины.

    Номер приходит из настройки отладки, и «монитора с таким номером нет» —
    обычная ситуация во вложенной сессии: выходов бывает один или два.
    """
    print("\n4. Монитор по номеру")
    window = FakeWindow(2)
    first = layer_shell.monitor_at(window, 0)
    check(first is not None and first.name == "0", f"нулевой монитор: {first}")
    second = layer_shell.monitor_at(window, 1)
    check(second is not None and second.name == "1", f"второй монитор: {second}")
    check(layer_shell.monitor_at(window, 2) is None,
          "номер за пределами — None, а не исключение")
    check(layer_shell.monitor_at(window, -1) is None,
          "отрицательный номер — None")

    empty = FakeWindow(0)
    check(layer_shell.monitor_at(empty, 0) is None,
          "в сессии без мониторов — None")


def main() -> int:
    print("Позиционирование через layer-shell")
    test_protocol_constants()
    test_missing_library()
    test_unsupported_compositor()
    test_monitor_by_index()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
