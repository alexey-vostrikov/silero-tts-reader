"""Проверки правок сокращений: файл, слияние, редактор.

Правило, вокруг которого построен набор: встроенные данные пакета не
меняются, правки живут отдельным файлом и сильнее встроенных. Выключенная
встроенная запись остаётся в пакете и возвращается одной строкой.

Окно проверяется без показа: собирается и опрашивается напрямую, поэтому
набор идёт в CI без экрана.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core.abbrev import (
    Abbreviations,
    load_extra,
    save_extra,
    spell_letters,
)

DATA = ROOT / "silero_tts_reader" / "assets" / "speech"
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ✓ {message}")
    else:
        raise AssertionError(message)


def test_spelling() -> None:
    """Буквы называются так же, как при чтении вслух."""
    print("\n1. Чтение по буквам")
    check(spell_letters("ВДВ") == "вэ дэ вэ", f"ВДВ: {spell_letters('ВДВ')}")
    check(spell_letters("МВД") == "эм вэ дэ", f"МВД: {spell_letters('МВД')}")
    check(spell_letters("США") == "эс ша а", f"США: {spell_letters('США')}")
    check(spell_letters("Й") == "и краткое", f"Й: {spell_letters('Й')}")
    # У неизвестной буквы остаётся она сама: лучше прочитать как есть.
    check(spell_letters("В2") == "вэ 2", f"цифра: {spell_letters('В2')}")


def test_user_rule_wins() -> None:
    """Правка пользователя сильнее встроенной записи."""
    print("\n2. Своё чтение сильнее встроенного")
    # ВКС — аббревиатура, которой нет во встроенных данных: именно такие
    # пользователь и добавляет через редактор.
    plain = Abbreviations(directory=DATA, generic=False)
    check(plain.expand("Служил в ВКС.") == "Служил в ВКС.",
          "без правки ВКС не разворачивается")

    mine = Abbreviations(directory=DATA, generic=False,
                         extra={"spelled": {"ВКС": "вэ ка эс"}})
    check(mine.expand("Служил в ВКС.") == "Служил в вэ ка эс.",
          f"с правкой: {mine.expand('Служил в ВКС.')}")

    # Своё чтение перебивает и встроенное: у МВД в данных «эм вэ дэ».
    override = Abbreviations(directory=DATA, generic=False,
                             extra={"spelled": {"МВД": "эм вэ дэ (министерство)"}})
    check(override.expand("Пришёл из МВД.") == "Пришёл из эм вэ дэ (министерство).",
          "правка перебила встроенную расшифровку")


def test_disable_builtin() -> None:
    """Встроенную запись можно выключить, не удаляя файл пакета."""
    print("\n3. Выключение встроенной записи")
    acc = Abbreviations(directory=DATA, generic=False,
                        extra={"removed": ["МВД"]})
    check(acc.expand("Пришёл из МВД.") == "Пришёл из МВД.",
          f"выключено: {acc.expand('Пришёл из МВД.')}")

    expansion = Abbreviations(directory=DATA, generic=False,
                              extra={"removed": ["т.д."]})
    check("так далее" not in expansion.expand("и т.д."),
          "выключенная расшифровка не применяется")

    # Файл данных пакета при этом не тронут.
    data = json.loads((DATA / "abbrev.json").read_text(encoding="utf-8"))
    check("МВД" in data["spelled"], "встроенные данные на месте")


def test_files() -> None:
    """Файл правок читается, пишется и переживает мусор."""
    print("\n4. Файл правок")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "abbrev.json"
        check(load_extra(path) == {"expansions": {}, "spelled": {},
                                  "as_word": [], "removed": []},
              "нет файла — нет правок")

        save_extra(path, {
            "expansions": {"т.к.": "так как"},
            "spelled": {"ВДВ": "вэ дэ вэ"},
            "as_word": ["ВУЗ"],
            "removed": ["МГУ"],
        })
        back = load_extra(path)
        check(back["spelled"] == {"ВДВ": "вэ дэ вэ"}, f"прочитано: {back['spelled']}")
        check(back["removed"] == ["МГУ"], f"выключения: {back['removed']}")

        # Мусор в файле не роняет чтение книги.
        path.write_text("{ битый json", encoding="utf-8")
        check(load_extra(path)["spelled"] == {}, "битый файл — пустые правки")

        # Запись идёт через временный файл: половины не остаётся.
        save_extra(path, {"spelled": {"ВДВ": "вэ дэ вэ"}})
        check(path.is_file() and not path.with_suffix(".json.tmp").exists(),
              "временный файл убран")


def test_window() -> None:
    """Окно редактора собирается и показывает действующие записи."""
    print("\n5. Окно редактора сокращений")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        if not Gtk.init_check():
            raise RuntimeError("нет дисплея")
    except Exception as exc:
        print(f"  — пропущено: {exc}")
        return

    from silero_tts_reader.ui.abbrev_window import KIND_SPELLED, AbbrevWindow

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "abbrev.json"
        window = AbbrevWindow(None, path, builtin_dir=DATA)

        rows = window._effective()
        kinds = {row[0] for row in rows}
        check(KIND_SPELLED in kinds, "в списке есть аббревиатуры по буквам")
        check(any(row[1] == "МВД" for row in rows), "МВД из встроенных данных виден")

        # Добавление ВДВ: по буквам, без своего текста.
        window._entry_short.set_text("ВДВ")
        window._kind.set_selected(1)
        window._entry_reading.set_text("")
        window._on_add(None)
        check(window._extra["spelled"].get("ВДВ") == "вэ дэ вэ",
              f"ВДВ добавлен: {window._extra['spelled'].get('ВДВ')}")
        check(any(row[1] == "ВДВ" for row in window._effective()),
              "новая запись появилась в списке")

        # Проверка чтения показывает результат с правкой.
        window._entry_test.set_text("Служил в ВДВ.")
        window._update_preview()
        check("вэ дэ вэ" in window._preview.get_text(),
              f"предпросмотр: {window._preview.get_text()!r}")

        # Сохранение пишет файл и применяет правила.
        window._on_response(None, Gtk.ResponseType.OK)
        saved = load_extra(path)
        check(saved["spelled"].get("ВДВ") == "вэ дэ вэ", f"на диске: {saved}")
        applied = Abbreviations(directory=DATA, generic=False, extra=saved)
        check(applied.expand("Служил в ВДВ.") == "Служил в вэ дэ вэ.",
              "после сохранения ВДВ читается по буквам")


def main() -> int:
    test_spelling()
    test_user_rule_wins()
    test_disable_builtin()
    test_files()
    test_window()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
