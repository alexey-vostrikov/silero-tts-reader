"""Ключи командной строки для отладки.

Читаются до создания `Gtk.Application`: `GApplication` разбирает свои
аргументы сам и на незнакомый ключ отвечает ошибкой, поэтому наши ключи
вынимаются из argv заранее.

Зачем это нужно: длинный текст (книга) читается часами, и проверить поведение
на середине или в конце по-другому неудобно. С ключами воспроизведение можно
ограничить по времени, завершить приложение сразу после прочтения и поднять
подробность журнала.

    silero-tts-reader книга.fb2 --max-time 120        # 2 минуты и стоп
    silero-tts-reader книга.fb2 --exit-after-play     # выйти после прочтения
    silero-tts-reader книга.fb2 --log-level detail    # подробнее в журнале
    silero-tts-reader книга.fb2 --debug               # то же, что debug
"""
from __future__ import annotations

from dataclasses import dataclass

from .log import LEVELS, LevelError, level_name, parse_level

MAX_TIME_FLAG = "--max-time"
EXIT_AFTER_PLAY_FLAG = "--exit-after-play"
LOG_LEVEL_FLAG = "--log-level"
DEBUG_FLAG = "--debug"
HELP_FLAGS = ("--help", "-h")
VERSION_FLAGS = ("--version", "-V")


def help_text(program: str = "silero-tts-reader", version: str = "") -> str:
    """Справка по нашим ключам.

    Печатается вместо справки Gtk: та показывает ``__main__.py [ПАРАМЕТР…]`` и
    ничего не знает про отладочные ключи. Пользователь спрашивает про
    приложение, значит и ответ должен быть про него.
    """
    title = f"{program} {version}".strip()
    # i18n: нет перевода — справка командной строки
    return f"""{title} — чтение текста и книг вслух (Silero TTS).

Использование:
  {program} [ФАЙЛ…] [КЛЮЧИ]

Файлы:
  .txt, .fb2, .fb2.zip, .fb2.xz, .fb2.lzma, а также сжатые .gz/.bz2/.zst.
  Без файла приложение запускается в трее и ждёт горячей клавиши.

Ключи отладки (действуют только на этот запуск, настройки не меняют):
  -h, --help              эта справка
  -V, --version           версия и метка сборки
      --max-time СЕК      остановить воспроизведение через СЕК секунд
                          (ограничение на всё чтение, не на фрагмент)
      --exit-after-play   выйти, когда текст прочитан
      --log-level УРОВЕНЬ  quiet | normal | detail | debug
      --debug             то же, что --log-level debug

Примеры:
  {program} книга.fb2 --max-time 120     # две минуты и стоп
  {program} книга.fb2 --exit-after-play  # выйти после прочтения
  {program} глава.txt --debug            # подробный журнал в консоль

Журнал печатается в консоль, из которой запущено приложение: в systemd-сессии
сообщения оболочки в journald не попадают. Уровень по умолчанию — normal; его
можно поменять и в настройках (вкладка «Позиционирование окна», раздел журнала).
"""


@dataclass(frozen=True)
class RunOptions:
    """Настройки, действующие только на этот запуск.

    ``max_time`` — сколько секунд воспроизведения проигрывать, затем стоп.
    ``exit_after_play`` — завершить приложение, когда чтение закончилось.
    ``log_level`` — подробность журнала в консоли (``None`` — из настроек).

    Ни одно из значений не записывается в файл настроек: это перекрытие на
    время запуска, а не изменение конфигурации.
    """

    max_time: float | None = None
    exit_after_play: bool = False
    log_level: str | None = None

    def describe(self) -> str:
        """Строка для журнала запуска: видно, что ограничения включены."""
        parts = []
        # i18n: нет перевода — строка журнала в консоли
        if self.max_time is not None:
            parts.append(f"не дольше {self.max_time:g} с")
        if self.exit_after_play:
            parts.append("выход после прочтения")
        # i18n: нет перевода — строка журнала в консоли
        if self.log_level is not None:
            parts.append(f"журнал {self.log_level}")
        # i18n: нет перевода — строка журнала в консоли
        return "Отладка: " + ", ".join(parts) if parts else ""


class OptionError(ValueError):
    """Ключ задан неверно: лучше остановиться, чем читать непонятно сколько."""


def _take_value(argv: list[str], index: int, flag: str, hint: str) -> tuple[str, int]:
    """Значение ключа: через пробел или через знак равенства.

    Возвращает (значение, новый индекс). Отдельная функция, потому что таких
    ключей два и разбор не должен разъезжаться между ними.
    """
    arg = argv[index]
    if arg == flag:
        if index + 1 >= len(argv):
            # i18n: нет перевода — диагностика командной строки
            raise OptionError(f"{flag} требует {hint}")
        return argv[index + 1], index + 2
    return arg.split("=", 1)[1], index + 1


def parse_run_options(argv: list[str]) -> tuple[RunOptions, list[str]]:
    """Вынуть наши ключи из argv. Возвращает (настройки, остальные аргументы).

    Некорректное значение — исключение, а не молчаливое «без ограничения»:
    иначе отладочный запуск выглядел бы рабочим, а читал бы всё подряд.
    """
    max_time: float | None = None
    exit_after_play = False
    log_level: str | None = None
    rest: list[str] = []

    index = 0
    while index < len(argv):
        arg = argv[index]

        if arg == EXIT_AFTER_PLAY_FLAG:
            exit_after_play = True
            index += 1
            continue

        if arg == DEBUG_FLAG:
            log_level = "debug"
            index += 1
            continue

        if arg == LOG_LEVEL_FLAG or arg.startswith(f"{LOG_LEVEL_FLAG}="):
            # i18n: нет перевода — подсказка в диагностике командной строки
            raw_level, index = _take_value(
                argv, index, LOG_LEVEL_FLAG, f"уровень: {', '.join(LEVELS)}")
            try:
                log_level = level_name(parse_level(raw_level))
            except LevelError as exc:
                raise OptionError(str(exc)) from exc
            continue

        if arg == MAX_TIME_FLAG or arg.startswith(f"{MAX_TIME_FLAG}="):
            raw, index = _take_value(argv, index, MAX_TIME_FLAG, "значение в секундах")
            try:
                value = float(raw)
            except ValueError as exc:
                # i18n: нет перевода — диагностика командной строки
                raise OptionError(
                    f"{MAX_TIME_FLAG}: ожидается число секунд, получено {raw!r}") from exc
            if value <= 0:
                # i18n: нет перевода — диагностика командной строки
                raise OptionError(f"{MAX_TIME_FLAG}: значение должно быть больше нуля")
            max_time = value
            continue

        rest.append(arg)
        index += 1

    return RunOptions(max_time=max_time, exit_after_play=exit_after_play,
                      log_level=log_level), rest
