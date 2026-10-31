#!/usr/bin/env python3
"""Ключи отладки: --max-time и --exit-after-play.

Ограничение по времени и автоматический выход нужны для проверки длинных
текстов: книгу иначе читать часами. Здесь проверяется разбор ключей и то, что
приложение действительно останавливает чтение по сроку и выходит, когда
просили.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from silero_tts_reader.run_options import (
    EXIT_AFTER_PLAY_FLAG,
    MAX_TIME_FLAG,
    OptionError,
    RunOptions,
    parse_run_options,
)

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def expect_error(argv: list[str]) -> str:
    try:
        parse_run_options(argv)
    except OptionError as exc:
        return str(exc)
    raise AssertionError(f"ожидалась ошибка для {argv}")


def test_parse() -> None:
    """Обе формы ключа, сочетание, сохранение остальных аргументов."""
    print("\n1. Разбор ключей")
    options, rest = parse_run_options(["prog", "книга.fb2", MAX_TIME_FLAG, "120"])
    check(options.max_time == 120.0, f"раздельная форма: {options.max_time}")
    check(rest == ["prog", "книга.fb2"], f"файл и имя программы сохранены: {rest}")

    options, _ = parse_run_options([MAX_TIME_FLAG + "=1.5"])
    check(options.max_time == 1.5, f"форма со знаком равенства: {options.max_time}")

    options, rest = parse_run_options(["prog", EXIT_AFTER_PLAY_FLAG, "текст.txt"])
    check(options.exit_after_play is True, "выход после чтения включён")
    check(options.max_time is None, "ограничение по времени не задано")
    check(rest == ["prog", "текст.txt"], f"ключ вынут из аргументов: {rest}")

    options, rest = parse_run_options(
        ["prog", MAX_TIME_FLAG, "30", EXIT_AFTER_PLAY_FLAG, "книга.fb2"])
    check(options.max_time == 30.0 and options.exit_after_play,
          "сочетание оба ключа")
    check(rest == ["prog", "книга.fb2"], f"осталось только нужное: {rest}")

    options, rest = parse_run_options(["prog", "--print", "x"])
    check(options == RunOptions(), "без наших ключей настройки пустые")
    check(rest == ["prog", "--print", "x"], "чужие ключи не трогаем")


def test_errors() -> None:
    """Некорректный ключ останавливает запуск, а не читает всё подряд."""
    print("\n2. Ошибки разбора")
    message = expect_error([MAX_TIME_FLAG])
    check("секундах" in message, f"без значения: {message}")
    message = expect_error([MAX_TIME_FLAG, "abc"])
    check("число" in message, f"не число: {message}")
    message = expect_error([MAX_TIME_FLAG, "0"])
    check("больше нуля" in message, f"ноль: {message}")
    message = expect_error([MAX_TIME_FLAG, "-5"])
    check("больше нуля" in message, f"отрицательное: {message}")


def test_describe() -> None:
    """Строка журнала показывает, что ограничения включены."""
    print("\n3. Строка журнала")
    check(RunOptions().describe() == "", "без ключей — пусто")
    check("120" in RunOptions(max_time=120).describe(),
          f"срок в строке: {RunOptions(max_time=120).describe()}")
    check("выход" in RunOptions(exit_after_play=True).describe(),
          f"выход в строке: {RunOptions(exit_after_play=True).describe()}")


def test_app_stops_at_max_time() -> None:
    """Приложение останавливает чтение по сроку и выходит, если просили."""
    print("\n4. Приложение: срок и выход")
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    check(GLib is not None, "GLib доступен")

    from silero_tts_reader.app import Application

    app = Application(["prog"], options=RunOptions(max_time=0.05,
                                                   exit_after_play=True))
    stopped: list[str] = []
    quit_called: list[str] = []
    scheduled: list[tuple[int, object]] = []

    app._stop_playback_sync = lambda: stopped.append("stop")
    app._quit_after_play = lambda: (quit_called.append("quit"), False)[1]
    saved_add, saved_remove = GLib.timeout_add, GLib.source_remove

    def fake_add(msec, callback, *args):
        scheduled.append((msec, callback))
        return len(scheduled)

    removed: list[int] = []
    GLib.timeout_add = fake_add
    GLib.source_remove = lambda source: removed.append(source)
    try:
        app._on_playback_started()
        check(bool(scheduled) and scheduled[-1][0] == 50,
              f"таймер на 50 мс: {scheduled}")
        check(app._max_time_source is not None, "таймер запомнен")

        # Ограничение — на всё воспроизведение, а не на квант: прогресс по
        # сегментам не должен ставить новые таймеры.
        for value in (0.1, 0.2, 0.3, 0.5, 0.99):
            app._on_segment_progress(value)
        check(len(scheduled) == 1,
              f"на каждый квант таймер не ставится: {len(scheduled)}")

        # Повторный старт не должен ставить второй таймер.
        app._on_playback_started()
        check(len(scheduled) == 1, "второго таймера нет")

        app._on_max_time()
        check(stopped == ["stop"], f"чтение остановлено: {stopped}")
        check(len(scheduled) == 2 and scheduled[-1][1] == app._quit_after_play,
              "выход запланирован после остановки")
        check(app._max_time_source is None, "таймер снят")

        # Конец чтения раньше срока снимает ограничение.
        app._on_playback_started()
        app._on_playback_finished()
        check(bool(removed), f"таймер снят при завершении: {removed}")

        app._on_playback_started()
        app._on_playback_stopped()
        check(len(removed) >= 2, "таймер снят и при остановке")
    finally:
        GLib.timeout_add, GLib.source_remove = saved_add, saved_remove


def test_app_without_options() -> None:
    """Без ключей ничего не взводится и приложение не выходит само."""
    print("\n5. Приложение без ключей")
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    from silero_tts_reader.app import Application

    app = Application(["prog"])
    scheduled: list[object] = []
    saved_add = GLib.timeout_add
    GLib.timeout_add = lambda msec, cb, *a: (scheduled.append(cb), 1)[1]
    try:
        app._on_playback_started()
        check(scheduled == [], f"таймер не поставлен: {scheduled}")
        check(app._max_time_source is None, "срок не взведён")
        app._on_playback_finished()
        check(scheduled == [], "после чтения выход не планируется")
    finally:
        GLib.timeout_add = saved_add


def test_log_level_flags() -> None:
    """--log-level и --debug: перекрытие уровня, файл настроек не трогается."""
    print("\n6. Уровень журнала из ключей")
    options, rest = parse_run_options(["prog", "--log-level", "detail", "x.txt"])
    check(options.log_level == "detail", f"уровень из ключа: {options.log_level}")
    check(rest == ["prog", "x.txt"], f"ключ вынут: {rest}")

    options, _ = parse_run_options(["--log-level=debug"])
    check(options.log_level == "debug", "--log-level=debug")

    options, _ = parse_run_options(["--debug"])
    check(options.log_level == "debug", "--debug = уровень debug")

    message = expect_error(["--log-level", "чепуха"])
    check("неизвестный уровень" in message, f"неизвестный уровень: {message}")
    message = expect_error(["--log-level"])
    check("уровень" in message, f"без значения: {message}")

    # Ключ перекрывает настройку, но НЕ меняет её: в конфиге остаётся прежнее.
    import tempfile

    from silero_tts_reader.config.config_manager import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        config = ConfigManager(path=path)
        config.log_level = "quiet"
        config.save()
        options, _ = parse_run_options(["--log-level", "debug"])
        # приложение применило бы options.log_level к журналу, но не к конфигу
        again = ConfigManager(path=path)
        check(again.log_level == "quiet",
              f"настройка в файле не изменилась ключом: {again.log_level}")
        check(options.log_level == "debug", "ключ действовал на этот запуск")


def test_log_levels() -> None:
    """Уровни: что печатается на каждом."""
    print("\n7. Что печатается на уровне")
    import io

    from silero_tts_reader import log

    saved_level = log.current_level()
    try:
        for name, expected in (
            ("quiet", ["ERROR"]),
            ("normal", ["INFO", "ERROR"]),
            ("detail", ["INFO", "DETAIL", "ERROR"]),
            ("debug", ["INFO", "DETAIL", "DEBUG", "ERROR"]),
        ):
            stream = io.StringIO()
            log.set_stream(stream)
            log.set_level(name)
            log.info("INFO")
            log.detail("DETAIL")
            log.debug("DEBUG")
            log.error("ERROR")
            printed = [line.strip(" ✗") for line in stream.getvalue().split("\n") if line.strip()]
            check(printed == expected, f"{name}: {printed}")
    finally:
        log.set_stream(None)
        log.set_level(saved_level)


def main() -> int:
    print("Ключи отладки: --max-time, --exit-after-play, --log-level")
    test_parse()
    test_errors()
    test_describe()
    test_app_stops_at_max_time()
    test_app_without_options()
    test_log_level_flags()
    test_log_levels()
    print(f"\n  все проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
