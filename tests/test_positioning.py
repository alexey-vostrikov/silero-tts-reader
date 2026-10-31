#!/usr/bin/env python3
"""Глобальное позиционирование окна: выключено по умолчанию.

Позиция окна верхнего уровня — единственное, чего Wayland-клиенту не даёт
система. Поэтому режим выключен по умолчанию, а включается на отдельной
вкладке настроек. Здесь проверяется именно это: что при выключенном режиме
окно не трогают ни протокол, ни расширение оболочки, и что способ из
настроек соблюдается.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.config.config_manager import ConfigManager

ROOT = Path(__file__).resolve().parent.parent
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def test_default_off() -> None:
    """Новый конфиг: режим выключен."""
    print("\n1. Значение по умолчанию")
    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigManager(path=Path(tmp) / "config.json")
        check(config.window_positioning == "off",
              f"по умолчанию off: {config.window_positioning!r}")


def test_values() -> None:
    """Допустимые значения принимаются, мусор не ломает настройку."""
    print("\n2. Допустимые значения")
    with tempfile.TemporaryDirectory() as tmp:
        config = ConfigManager(path=Path(tmp) / "config.json")
        for value in ("auto", "layer", "shell", "off"):
            config.window_positioning = value
            check(config.window_positioning == value, f"значение {value} сохраняется")
        config.window_positioning = "shell"
        config.window_positioning = "чепуха"
        check(config.window_positioning == "shell",
              f"недопустимое значение не меняет настройку: {config.window_positioning}")

        # Битое значение в файле читается как off: окно не должно «прыгать»
        # из-за опечатки в конфиге.
        config.set("ui", "window_positioning", "lаyer")   # с русской «а»
        config.save()
        again = ConfigManager(path=Path(tmp) / "config.json")
        check(again.window_positioning == "off",
              f"опечатка в файле читается как off: {again.window_positioning}")


def test_widget_off_by_default() -> None:
    """Виджет с режимом off не просит позицию ни у протокола, ни у оболочки."""
    print("\n3. Виджет не трогает позицию при off")
    os.environ.setdefault("GDK_BACKEND", "x11")
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    check(Gtk.init_check() is not False, "GTK инициализируется для проверки виджета")

    from silero_tts_reader.ui import playback_widget

    calls: list[str] = []
    saved = (playback_widget.layer_shell.place_bottom_right,
             playback_widget.window_placement.available)
    playback_widget.layer_shell.place_bottom_right = (
        lambda *a, **k: (calls.append("layer"), "layer")[1])
    playback_widget.window_placement.available = lambda *a, **k: (
        calls.append("dbus"), True)[1]
    try:
        widget = playback_widget.PlaybackWidget()
        check(widget._positioning == "off", "виджет по умолчанию в режиме off")
        widget._place_window()
        check(calls == [], f"при off позиция не запрашивается: {calls}")
        check(widget._position_mode == "none",
              f"режим позиции none: {widget._position_mode}")

        # Включили «только расширение оболочки»: протокол не должен зваться,
        # потому что он перекрыл бы панели слоем OVERLAY.
        calls.clear()
        widget.set_positioning("shell")
        check("layer" not in calls, f"в режиме shell протокол не используется: {calls}")
        check(widget._position_mode == "dbus-pending",
              f"позиция отложена до показа окна: {widget._position_mode}")

        # «Только протокол»: расширение не зовём.
        calls.clear()
        widget.set_positioning("layer")
        check(calls == ["layer"], f"в режиме layer зовётся только протокол: {calls}")

        calls.clear()
        widget.set_positioning("off")
        check(calls == [], f"возврат в off ничего не запрашивает: {calls}")
    finally:
        (playback_widget.layer_shell.place_bottom_right,
         playback_widget.window_placement.available) = saved


def test_run_preload_gate() -> None:
    """LD_PRELOAD для layer-shell поднимается только при включённом режиме."""
    print("\n4. Перезапуск с LD_PRELOAD только по включённому режиму")
    import json
    import runpy

    module = runpy.run_path(str(ROOT / "run.py"))

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CONFIG_HOME"] = tmp
        path = Path(tmp) / "silero-tts-reader" / "config.json"
        path.parent.mkdir(parents=True)

        check(module["_positioning_enabled"]() is False,
              "нет файла конфига — режим выключен")

        path.write_text(json.dumps({"ui": {"window_positioning": "off"}}), encoding="utf-8")
        check(module["_positioning_enabled"]() is False, "off — перезапуска нет")

        path.write_text(json.dumps({"ui": {"window_positioning": "shell"}}), encoding="utf-8")
        check(module["_positioning_enabled"]() is False,
              "shell не требует LD_PRELOAD: расширение работает по D-Bus")

        path.write_text(json.dumps({"ui": {"window_positioning": "auto"}}), encoding="utf-8")
        check(module["_positioning_enabled"]() is True, "auto — перезапуск нужен")

        path.write_text(json.dumps({"ui": {"window_positioning": "layer"}}), encoding="utf-8")
        check(module["_positioning_enabled"]() is True, "layer — перезапуск нужен")

        path.write_text("{ битый json", encoding="utf-8")
        check(module["_positioning_enabled"]() is False, "битый файл — режим выключен")
    os.environ.pop("XDG_CONFIG_HOME", None)


def main() -> int:
    print("Позиционирование окна: по умолчанию выключено")
    test_default_off()
    test_values()
    test_widget_off_by_default()
    test_run_preload_gate()
    print(f"\n  все проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
