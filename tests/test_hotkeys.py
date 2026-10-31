#!/usr/bin/env python3
"""Регрессия на разбор глобальных хоткеев.

Баг был в pynput-ветке: `set_hotkeys` складывала результат разбора
``(dict модификаторов, клавиша)`` в ключ словаря, а dict нехешируем —
приложение падало с ``TypeError: cannot use 'tuple' as a dict key`` сразу после
старта. GTK-тесты этого не видели, потому что pynput не установлен и ветка не
выполнялась, а живой запуск падал.

Тест подставляет пустышку вместо настоящего слушателя, поэтому работает
и без python3-pynput.
"""
from __future__ import annotations

import contextlib
import io
import sys
import types
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.core import hotkey_listener as hl

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


class _StubListener:
    """Пустышка вместо настоящего pynput.Listener."""

    instances: ClassVar[list[_StubListener]] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        _StubListener.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _stub_pynput() -> None:
    listener = types.SimpleNamespace(Listener=_StubListener)
    hl.pynput_keyboard = listener
    hl.HAS_PYNPUT = True


def test_parse() -> None:
    parsed = hl.PynputHotkeyListener._parse_pynput_hotkey("<ctrl>+<alt>+r")
    check(parsed is not None, "хоткей <ctrl>+<alt>+r разбирается")
    mods, trigger = parsed  # type: ignore[misc]
    check(trigger == "r", "триггер распознан как r")
    check(
        mods == {"ctrl": True, "alt": True, "shift": False, "super": False},
        "модификаторы распознаны",
    )

    single = hl.PynputHotkeyListener._parse_pynput_hotkey("<ctrl>")
    check(single is not None and single[1] == "ctrl", "одиночный модификатор разбирается")

    check(
        hl.PynputHotkeyListener._parse_pynput_hotkey("") is None,
        "пустая строка даёт None, а не падение",
    )


def test_set_hotkeys() -> None:
    _stub_pynput()
    calls: list[str] = []
    listener = hl.PynputHotkeyListener()
    listener.set_hotkeys({
        "<ctrl>+<alt>+r": lambda: calls.append("speak"),
        "<ctrl>+<alt>+c": lambda: calls.append("clipboard"),
        "<ctrl>+<alt>+s": lambda: calls.append("stop"),
    })
    # Раньше здесь был TypeError: dict модификаторов использовался как ключ.
    check(len(listener._hotkeys) == 3, "три хоткея попали в список без TypeError")
    check(
        all(isinstance(item, tuple) and len(item) == 3 for item in listener._hotkeys),
        "элементы списка — тройки (модификаторы, клавиша, callback)",
    )
    check(
        {trigger for _, trigger, _ in listener._hotkeys} == {"r", "c", "s"},
        "триггеры на месте",
    )
    check(
        all(cb.__class__.__name__ == "function" for *_, cb in listener._hotkeys),
        "callbacks сохранены",
    )

    # Повторная установка заменяет набор, а не копит его
    listener.set_hotkeys({"<ctrl>+<alt>+r": lambda: calls.append("speak2")})
    check(len(listener._hotkeys) == 1, "повторный set_hotkeys заменяет набор")

    check(bool(_StubListener.instances), "слушатель создан через подставленный Listener")
    check(_StubListener.instances[-1].started, "слушатель запущен")
    listener.stop_listening()


def test_hotkeys_usable_without_pynput() -> None:
    """Модуль импортируется и сообщает о проблеме, а не падает на импорте."""
    hl.pynput_keyboard = None
    hl.HAS_PYNPUT = False
    facade = hl.HotkeyListener()
    facade.set_hotkeys(
        speak_selection="<ctrl>+<alt>+r",
        speak_clipboard="<ctrl>+<alt>+c",
        stop="<ctrl>+<alt>+s",
        on_speak_selection=lambda: None,
        on_speak_clipboard=lambda: None,
        on_stop=lambda: None,
    )
    check(facade._backend in {"evdev", "pynput", "none"}, f"бэкенд определён: {facade._backend}")


def test_advice_uses_input_group() -> None:
    """Совет про доступ к /dev/input — это группа input, а не chmod.

    ``chmod 666 /dev/input/event*`` открывает клавиатуру всем в системе и
    слетает при перезагрузке или переподключении устройства, поэтому такой
    совет не должен вернуться ни в одну из веток. Проверяем живой вывод обеих
    веток, а не константу: важно, что видит пользователь.
    """
    print("\n3. Совет про доступ к устройствам ввода")
    hint = hl.INPUT_GROUP_HINT
    check("usermod -aG input" in hint, f"совет: {hint.strip()}")
    check("chmod" not in hint, "в совете только группа input")
    check("перелогин" in hint, "сказано про перелогин: без него группа не применится")

    # Ветка «ни evdev, ни pynput»: совет должен дойти до вывода целиком.
    # evdev принудительно выключается: на машине с доступом к /dev/input он
    # нашёл бы клавиатуры и увел бы выполнение в другую ветку, где совета нет
    # и не должно быть.
    hl.pynput_keyboard = None
    hl.HAS_PYNPUT = False
    saved_evdev = hl.HAS_EVDEV
    hl.HAS_EVDEV = False
    facade = hl.HotkeyListener()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        facade.set_hotkeys(
            speak_selection="<ctrl>+<alt>+r",
            speak_clipboard="<ctrl>+<alt>+c",
            stop="<ctrl>+<alt>+s",
            on_speak_selection=lambda: None,
            on_speak_clipboard=lambda: None,
            on_stop=lambda: None,
        )
    text = output.getvalue()
    hl.HAS_EVDEV = saved_evdev
    check(hint in text, "ветка без бэкенда печатает совет про группу input")
    check("chmod 666" not in text, "вывод предлагает только группу input")

    # Наоборот: когда evdev доступен, хоткеи работают и совета быть не должно —
    # иначе пользователь с рабочей машиной будет чинить то, что не сломано.
    # Подменяется сам класс целиком: get_keyboards — статический метод, и
    # лабораторная подмена одного атрибута ломает вызов из set_hotkeys.
    class _StubEvdev:
        @staticmethod
        def get_keyboards():
            return [object()]

        def set_hotkeys(self, _map) -> None:
            pass

        def is_alive(self) -> bool:
            return False

        def start(self) -> None:
            pass

    hl.HAS_EVDEV = True
    saved_listener = hl.EvdevHotkeyListener
    hl.EvdevHotkeyListener = _StubEvdev  # type: ignore[assignment]
    try:
        facade = hl.HotkeyListener()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            facade.set_hotkeys(
                speak_selection="ctrl+alt+r",
                speak_clipboard="ctrl+alt+c",
                stop="ctrl+alt+s",
                on_speak_selection=lambda: None,
                on_speak_clipboard=lambda: None,
                on_stop=lambda: None,
            )
        check(hint not in output.getvalue(),
              "evdev работает — совета про группу input нет")
    finally:
        hl.EvdevHotkeyListener = saved_listener  # type: ignore[assignment]
        hl.HAS_EVDEV = saved_evdev


def test_advice_in_pynput_branch() -> None:
    """Та же строка в ветке pynput: советы не должны разъезжаться."""
    print("\n4. Совет в ветке pynput")

    class _StubFacade:
        def set_hotkeys(self, _map) -> None:
            pass

    hl.pynput_keyboard = types.SimpleNamespace(Listener=_StubListener)
    hl.HAS_PYNPUT = True
    saved = hl.PynputHotkeyListener
    hl.PynputHotkeyListener = _StubFacade  # type: ignore[assignment]
    try:
        facade = hl.HotkeyListener()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            facade.set_hotkeys(
                speak_selection="<ctrl>+<alt>+r",
                speak_clipboard="<ctrl>+<alt>+c",
                stop="<ctrl>+<alt>+s",
                on_speak_selection=lambda: None,
                on_speak_clipboard=lambda: None,
                on_stop=lambda: None,
            )
        # На машине с доступом к /dev/input ветка evdev, и совета не будет.
        if facade._backend == "pynput":
            check(hl.INPUT_GROUP_HINT in output.getvalue(),
                  "ветка pynput печатает тот же совет")
        else:
            check(facade._backend == "evdev",
                  f"evdev доступен — совет не нужен: {facade._backend}")
    finally:
        hl.PynputHotkeyListener = saved  # type: ignore[assignment]


def main() -> int:
    print("Хоткеи: разбор и установка")
    test_parse()
    test_set_hotkeys()
    test_advice_uses_input_group()
    test_advice_in_pynput_branch()
    test_hotkeys_usable_without_pynput()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
