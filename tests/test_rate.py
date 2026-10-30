#!/usr/bin/env python3
"""Регулировка скорости: растяжение через rubberband-cli и шкала speech-dispatcher.

Проверяется то, что раньше молча не работало: скорость меняется без изменения
тона, длина звука соответствует множителю, а темп speech-dispatcher
(-100…+100) переводится в понятный множитель.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from silero_tts_reader.cli import _resolve_speed, _spd_percent_to_speed  # noqa: E402
from silero_tts_reader.core import rate  # noqa: E402

CHECKS = 0
SAMPLE_RATE = 48000


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def tone(seconds: float, freq: int = 440) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_available() -> None:
    print("\n1. Доступность rubberband-cli")
    check(rate.available() is True,
          "rubberband-cli найден (пакет в зависимостях silero-tts-reader)")
    check(rate.MIN_SPEED == 0.5 and rate.MAX_SPEED == 3.0, "пределы скорости 0.5–3.0")


def test_percent_mapping() -> None:
    print("\n2. Шкала speech-dispatcher → множитель")
    cases = {
        0: 1.0,      # без изменений
        100: 2.0,    # максимум
        -100: 0.5,   # минимум
        50: 1.5,
        -50: 0.75,
        200: 2.0,    # за верхней границей — обрезается
        -200: 0.5,   # за нижней — обрезается
        # speech-dispatcher подставляет $RATE как float: «0.00», «50.00»
        0.0: 1.0,
        50.0: 1.5,
        -50.0: 0.75,
        33.33: 1.33,     # округляется до 33 → 1.33
    }
    for percent, expected in cases.items():
        got = _spd_percent_to_speed(percent)
        check(abs(got - expected) < 1e-6, f"{percent:+g} → {got:.2f}×")


def test_speed_priority() -> None:
    print("\n3. Приоритет источников скорости")
    config = SimpleNamespace(speed=1.25)

    args = argparse.Namespace(rate=1.5, spd_rate=100)
    check(abs(_resolve_speed(args, config) - 1.5) < 1e-9, "--rate важнее --spd-rate")

    args = argparse.Namespace(rate=None, spd_rate=50)
    check(abs(_resolve_speed(args, config) - 1.5) < 1e-9, "--spd-rate важнее конфига")

    args = argparse.Namespace(rate=None, spd_rate=None)
    check(abs(_resolve_speed(args, config) - 1.25) < 1e-9, "иначе скорость из конфига")

    args = argparse.Namespace(rate=9.0, spd_rate=None)
    check(abs(_resolve_speed(args, config) - rate.MAX_SPEED) < 1e-9, "--rate обрезается сверху")

    args = argparse.Namespace(rate=0.1, spd_rate=None)
    check(abs(_resolve_speed(args, config) - rate.MIN_SPEED) < 1e-9, "--rate обрезается снизу")


def test_stretch() -> None:
    print("\n4. Растяжение звука")
    source = tone(2.0)
    check(rate.time_stretch(source, SAMPLE_RATE, 1.0).size == source.size,
          "скорость 1.0 → массив без изменений")
    check(rate.time_stretch(np.zeros(0, dtype=np.float32), SAMPLE_RATE, 1.5).size == 0,
          "пустой массив не ломает растяжение")

    for speed in (0.5, 0.75, 1.5, 2.0, 3.0):
        out = rate.time_stretch(source, SAMPLE_RATE, speed)
        expected = source.size / speed
        deviation = abs(out.size - expected) / expected
        check(deviation < 0.02,
              f"скорость {speed}× → {out.size / SAMPLE_RATE:.2f} с "
              f"(ждали {expected / SAMPLE_RATE:.2f} с)")
        check(out.dtype == np.float32, f"скорость {speed}× → float32")

    # Тон сохраняется: доминирующая частота остаётся 440 Гц
    stretched = rate.time_stretch(source, SAMPLE_RATE, 1.5)
    spectrum = np.abs(np.fft.rfft(stretched))
    peak_freq = np.fft.rfftfreq(stretched.size, 1 / SAMPLE_RATE)[int(np.argmax(spectrum))]
    check(abs(peak_freq - 440) < 15,
          f"высота тона не изменилась: пик {peak_freq:.0f} Гц при 440 Гц")

    # Амплитуда не улетает в клиппинг
    check(float(np.max(np.abs(stretched))) < 1.0, "амплитуда осталась в пределах [-1, 1]")


def test_failure_is_loud() -> None:
    print("\n5. Понятный отказ без rubberband")
    saved = rate._RUBBERBAND
    rate._RUBBERBAND = None
    try:
        try:
            rate.time_stretch(tone(0.2), SAMPLE_RATE, 1.5)
        except RuntimeError as exc:
            check("rubberband-cli" in str(exc), f"ошибка называет пакет: {exc}")
        else:
            raise AssertionError("без rubberband растяжение не упало")
    finally:
        rate._RUBBERBAND = saved


def main() -> int:
    print("Регулировка скорости речи")
    test_available()
    test_percent_mapping()
    test_speed_priority()
    test_stretch()
    test_failure_is_loud()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
