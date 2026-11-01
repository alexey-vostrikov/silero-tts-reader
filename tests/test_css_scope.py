"""Проверки ограничителя CSS: правила одного окна не должны красить другое."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.ui.css_scope import scope_css

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


print("1. Простые селекторы получают область")
result = scope_css("button {\n    color: red;\n}\n", ".settings-window")
check("button ограничен окном", ".settings-window button {" in result, result.splitlines()[0])

print("2. Селектор самого окна не префиксуется")
result = scope_css("window.playback-window {\n    background-color: transparent;\n}\n",
                   ".playback-window")
check("window.* остаётся как есть",
      result.splitlines()[0] == "window.playback-window {", result.splitlines()[0])

print("3. Группа селекторов через запятую")
result = scope_css("textview, textview text { background: blue; }\n", ".settings-window")
check("оба селектора ограничены",
      result.startswith(".settings-window textview, .settings-window textview text"),
      result.splitlines()[0])

print("4. Комбинаторы и псевдоклассы")
result = scope_css("dropdown > button { color: red; }\nbutton:hover { color: blue; }\n",
                   ".settings-window")
lines = result.splitlines()
check("комбинатор сохраняется",
      lines[0] == ".settings-window dropdown > button { color: red; }", lines[0])
check("псевдокласс сохраняется",
      lines[1] == ".settings-window button:hover { color: blue; }", lines[1])

print("4a. Селектор самого окна области: :scope")
result = scope_css(":scope { background-color: #101010; }\n:scope label { color: #ffffff; }\n",
                   ".settings-window")
lines = result.splitlines()
check(":scope превращается в класс окна",
      lines[0] == ".settings-window { background-color: #101010; }", lines[0])
check(":scope с потомком тоже",
      lines[1] == ".settings-window label { color: #ffffff; }", lines[1])

print("4b. Узла dialog в GTK4 нет — проверка, что такой селектор не остаётся")
result = scope_css("dialog { background: red; }\n", ".settings-window")
check("dialog получает область, но такого узла в GTK4 нет",
      result.startswith(".settings-window dialog"), result.splitlines()[0])

print("5. Объявления внутри блока не трогаются")
result = scope_css("button {\n    background-image: none;\n}\n", ".settings-window")
check("объявление не превращается в селектор",
      "background-image: none;" in result
      and ".settings-window background-image" not in result, result.replace("\n", " "))

print("6. Ат-правила не трогаются")
result = scope_css("@keyframes fade {\n}\n", ".settings-window")
check("@keyframes остаётся", result.startswith("@keyframes"), result.splitlines()[0])

print("7. Настоящий CSS настроек действительно ограничен")
settings_source = (ROOT / "silero_tts_reader" / "ui" / "settings_window.py").read_text(encoding="utf-8")
check("настройки применяют scope_css", "scope_css(" in settings_source)
check("окно настроек получает класс области",
      'add_css_class("settings-window")' in settings_source)

print("8. Настоящий CSS плеера ограничен, прозрачность — только своё окно")
player_source = (ROOT / "silero_tts_reader" / "ui" / "playback_widget.py").read_text(encoding="utf-8")
check("плеер применяет scope_css", "scope_css(" in player_source)
check("окно плеера получает класс области",
      'add_css_class("playback-window")' in player_source)
check("прозрачность привязана к классу окна",
      "window.playback-window" in player_source)
check("нет правила на все окна подряд",
      not any(line.strip() == "window {{" for line in player_source.splitlines()),
      "только window.playback-window")

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
