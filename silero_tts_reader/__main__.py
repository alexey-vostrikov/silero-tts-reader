"""Точка входа silero-tts-reader (GTK3)."""
import sys

from silero_tts_reader.app import Application


def main() -> None:
    app = Application(sys.argv)
    # Gtk.Application.run() сам разбирает аргументы и держит главный цикл GLib.
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
