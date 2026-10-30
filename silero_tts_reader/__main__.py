"""Точка входа silero-tts-reader (GTK3)."""
import sys

from silero_tts_reader import build_info, instance
from silero_tts_reader.app import Application
from silero_tts_reader.i18n import _


def main() -> None:
    info = build_info.current()

    # Если уже работает более старая сборка — закрываем её и занимаем место.
    # Проверка обязана быть до app.run(): GApplication передаёт второй запуск
    # первому процессу, и старый код продолжил бы работать.
    replaced = instance.replace_older(info)
    if replaced is not None:
        print(
            "  "
            + _("Заменяю работающую сборку {old} на {new}").format(
                old=replaced.version or _("старше"),
                new=info.describe(),
            ),
            flush=True,
        )

    app = Application(sys.argv)
    # Gtk.Application.run() сам разбирает аргументы и держит главный цикл GLib.
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
