"""Проверки GTK4-диалогов: каждый показываемый диалог должен появляться.

В GTK4 у диалогов нет ``run()``, а ``Gtk.FileChooserNative`` и
``Gtk.AboutDialog`` живут, пока на них есть ссылка из Python. Оба случая уже
приводили к «ничего не открывается, ошибок нет». Проверки ловят возврат к
старому коду и потерю ссылки.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "silero_tts_reader"

checks = 0
failures = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks, failures
    checks += 1
    if condition:
        print(f"  ✓ {name}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"  ✗ {name}{(' — ' + detail) if detail else ''}")


def source(relative: str) -> str:
    return (SOURCE / relative).read_text(encoding="utf-8")


print("1. Ни один диалог не вызывает удалённый run()")
files = sorted(p for p in SOURCE.rglob("*.py"))
offenders = []
for path in files:
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"^\s*(\w+)\.run\(\)", text, re.MULTILINE):
        line = text[:match.start()].count("\n") + 1
        # Gtk.Application.run() и argparse остаются законными.
        if match.group(1) in {"app", "self", "application", "parser"}:
            continue
        offenders.append(f"{path.relative_to(ROOT)}:{line} {match.group(1)}.run()")
check("нет вызовов dialog.run()", not offenders, "; ".join(offenders) or "чисто")

print("2. Окно «О программе» держит ссылку")
app_source = source("app.py")
check("поле _about_dialog объявлено", "_about_dialog = None" in app_source)
check("диалог сохраняется в поле", "self._about_dialog = dialog" in app_source)
check("показ через present(), а не run()",
      "dialog.present()" in app_source and "dialog.run()" not in app_source)

print("3. Диалог выбора файла держит ссылку")
check("поле _file_chooser объявлено", "_file_chooser = None" in app_source)
check("диалог сохраняется в поле", "self._file_chooser = chooser" in app_source)
check("после ответа ссылка снимается", "self._file_chooser = None" in app_source)
check("повторное нажатие поднимает открытый диалог",
      "if self._file_chooser is not None:" in app_source)

print("4. Папка диалога передаётся как Gio.File")
check("set_current_folder получает Gio.File",
      "set_current_folder(Gio.File.new_for_path(" in app_source)
check("строки в set_current_folder нет",
      not re.search(r"set_current_folder\(\s*(?!Gio\.)\w+\s*\)", app_source))

print("5. Логотип — Paintable, а не Pixbuf")
# В GTK4 set_logo ждёт Gdk.Paintable: Pixbuf даёт TypeError, и значок молча
# исчезает — исключение глушится suppress'ом.
check("логотип из Gdk.Texture", "Gdk.Texture.new_from_filename(" in app_source)
check("Pixbuf для логотипа не используется",
      "GdkPixbuf" not in app_source.replace("GdkPixbuf.Pixbuf даёт", ""))
check("Gdk импортирован", "from gi.repository import Gdk," in app_source)

print("6. Диалоги в настройках показываются асинхронно")
settings_source = source("ui/settings_window.py")
open_handlers = settings_source.count('connect("response"')
check("ответ обрабатывается сигналом", open_handlers >= 3,
      f"{open_handlers} обработчиков")
check("нет вызова run() в настройках", "dialog.run()" not in settings_source)

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
