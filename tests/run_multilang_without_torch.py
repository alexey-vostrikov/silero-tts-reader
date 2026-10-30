#!/usr/bin/env python3
"""Запуск tests/test_multilang.py в системе БЕЗ torch.

В CI torch не устанавливается, а на машине разработчика он есть — из-за этого
ветки кода, где импорт torch идёт раньше проверки файла модели, ведут себя
по-разному. Тест скрывает torch, чтобы поведение совпадало с CI.
"""
import runpy
import sys
from pathlib import Path


class _BlockTorch:
    def find_module(self, name, path=None):  # достаточно для проверки импорта
        return self if name == "torch" or name.startswith("torch.") else None

    def load_module(self, name):
        raise ImportError("torch скрыт: имитация системы без python3-torch")


sys.meta_path.insert(0, _BlockTorch())
target = Path(__file__).resolve().parent / "test_multilang.py"
runpy.run_path(str(target), run_name="__main__")
