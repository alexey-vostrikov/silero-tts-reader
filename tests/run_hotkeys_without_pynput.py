#!/usr/bin/env python3
"""Запуск tests/test_hotkeys.py в системе БЕЗ pynput.

Нужно, чтобы проверять ветку «ни evdev, ни pynput» и импорт модуля без
необязательной зависимости. pynput скрывается через meta_path, поэтому
тест идёт против того же кода, что и в чистой установке.
"""
import runpy
import sys
from pathlib import Path


class _BlockPynput:
    def find_module(self, name, path=None):  # устаревший протокол — достаточно для нас
        return self if name == "pynput" or name.startswith("pynput.") else None

    def load_module(self, name):
        raise ImportError("pynput скрыт: имитация системы без python3-pynput")


sys.meta_path.insert(0, _BlockPynput())
target = Path(__file__).resolve().parent / "test_hotkeys.py"
runpy.run_path(str(target), run_name="__main__")
