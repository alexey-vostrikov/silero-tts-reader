"""Точка входа silero-tts-reader (GTK4)."""
import sys

from silero_tts_reader import build_info, instance, log
from silero_tts_reader.app import Application
from silero_tts_reader.i18n import _
from silero_tts_reader.run_options import (
    HELP_FLAGS,
    VERSION_FLAGS,
    OptionError,
    help_text,
    parse_run_options,
)

PROGRAM = "silero-tts-reader"


def main() -> None:
    info = build_info.current()

    # Справка и версия — до создания Gtk.Application и до проверки
    # единственного экземпляра: пользователь может спросить о ключах, когда
    # приложение уже запущено, и второй экземпляр тут ни при чём.
    if any(arg in HELP_FLAGS for arg in sys.argv[1:]):
        print(help_text(PROGRAM, info.describe()), flush=True)
        sys.exit(0)
    if any(arg in VERSION_FLAGS for arg in sys.argv[1:]):
        print(f"{PROGRAM} {info.describe()}", flush=True)
        sys.exit(0)

    # Ключи отладки вынимаются ДО создания Gtk.Application: GApplication
    # разбирает аргументы сам и на незнакомый ключ завершается с ошибкой.
    try:
        options, argv = parse_run_options(sys.argv)
    except OptionError as exc:
        print(f"  ✗ {exc}", file=sys.stderr, flush=True)
        print(_("  справка: {program} --help").format(program=PROGRAM),
              file=sys.stderr, flush=True)
        sys.exit(2)

    # Уровень журнала: ключ сильнее настройки, настройка сильнее умолчания.
    # Читается здесь, потому что приложение ещё не создано, а печатать уже есть
    # что (замена сборки ниже).
    log.set_level(options.log_level or _configured_level())

    # Если уже работает более старая сборка — закрываем её и занимаем место.
    # Проверка обязана быть до app.run(): GApplication передаёт второй запуск
    # первому процессу, и старый код продолжил бы работать.
    replaced = instance.replace_older(info)
    if replaced is not None:
        log.info(
            _("Заменяю работающую сборку {old} на {new}").format(
                old=replaced.version or _("старше"),
                new=info.describe(),
            )
        )

    app = Application(argv, options=options)
    if options.describe():
        log.info(options.describe())
    # Gtk.Application.run() сам разбирает аргументы и держит главный цикл GLib.
    sys.exit(app.run(argv))


def _configured_level() -> str:
    """Уровень из файла настроек.

    Читается напрямую, без ConfigManager: он поднимает миграции и создаёт
    каталоги, а здесь нужно одно значение до старта приложения. Битый или
    отсутствующий файл — обычный уровень.
    """
    import json
    import os
    from pathlib import Path

    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) \
        / "silero-tts-reader" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "normal"
    value = (data.get("ui") or {}).get("log_level", "normal")
    return value if value in ("quiet", "normal", "detail", "debug") else "normal"


if __name__ == "__main__":
    main()
