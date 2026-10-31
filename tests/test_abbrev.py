"""Проверки расшифровки сокращений.

Правило то же, что у ударений: неверная расшифровка хуже непрочитанного
сокращения. Поэтому проверяется и то, что разворачивается, и то, что обязано
остаться на месте.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core.abbrev import Abbreviations

DATA = ROOT / "silero_tts_reader" / "assets" / "speech"
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


print("1. Данные на месте")
path = DATA / "abbrev.json"
check("файл abbrev.json есть", path.is_file())
raw = json.loads(path.read_text(encoding="utf-8"))
check("расшифровки не пустые", len(raw.get("expansions") or {}) > 15,
      f"{len(raw.get('expansions') or {})}")
check("аббревиатуры по буквам не пустые", len(raw.get("spelled") or {}) > 10,
      f"{len(raw.get('spelled') or {})}")
check("словочитаемые не пустые", len(raw.get("as_word") or []) > 10,
      f"{len(raw.get('as_word') or [])}")

known = Abbreviations(generic=False)
everything = Abbreviations(generic=True)

print("2. Сокращения с точками")
for source, expected in (
    ("и т.д.", "и так далее"),
    ("и т.п.", "и тому подобное"),
    ("т.е. это", "то есть это"),
    ("т.к. было", "так как было"),
    ("в т.ч. он", "в том числе он"),
    ("см. стр. 15", "смотри страница 15"),
    ("рис. 3", "рисунок 3"),
    ("до н.э.", "до нашей эры"),
    ("тыс. рублей", "тысяч рублей"),
    ("№ 5", "номер 5"),
):
    got = known.expand(source)
    check(f"{source!r}", got == expected, got)

print("3. Длинная форма выигрывает у короткой")
got = known.expand("до н.э.")
check("«до н.э.» не распадается на «до нашей эры» и обрывок",
      got == "до нашей эры", got)

print("4. Известные аббревиатуры читаются по буквам")
for source, expected in (
    ("в РФ", "в эр эф"),
    ("из США", "из сэ шэ а"),
    ("СССР", "эс эс эс эр"),
    ("МГУ", "эм гэ у"),
    ("ООО «Ромашка»", "о о о «Ромашка»"),
    ("ФНС", "эф эн эс"),
):
    got = known.expand(source)
    check(f"{source!r}", got == expected, got)

print("5. Словочитаемые аббревиатуры не разбираются")
for word in ("вуз", "загс", "ГОСТ", "ТАСС", "МКАД", "ГЭС"):
    got = everything.expand(word)
    check(f"{word!r} остаётся словом", got == word, got)

print("6. Неизвестная аббревиатура: по буквам только в режиме all")
check("known не трогает незнакомое", known.expand("НЛО") == "НЛО", known.expand("НЛО"))
check("all разбирает по буквам", everything.expand("НЛО") == "эн эль о",
      everything.expand("НЛО"))

print("7. Неоднозначные сокращения не разворачиваются")
for source in ("в г. Москве", "с. 15", "пр. Мира", "м. метро"):
    got = everything.expand(source)
    check(f"{source!r} остаётся как есть", got == source, got)

print("8. Сокращение внутри слова не заменяется")
check("«итог» не превращается в «ит так далее»",
      known.expand("итог") == "итог", known.expand("итог"))
check("«строки» не превращаются в «страницаи»",
      known.expand("строки") == "строки", known.expand("строки"))

print("9. Правки пользователя сильнее встроенных")
custom = Abbreviations(generic=False, extra={
    "expansions": {"т.д.": "и так далее"},
    "spelled": {"НЛО": "эн эль о"},
    "as_word": ["ПВО"],
})
check("расшифровка перекрыта", custom.expand("т.д.") == "и так далее",
      custom.expand("т.д."))
check("аббревиатура добавлена", custom.expand("НЛО") == "эн эль о",
      custom.expand("НЛО"))
check("словочитаемость добавлена", custom.expand("ПВО") == "ПВО",
      custom.expand("ПВО"))

print("10. Пустой текст и текст без сокращений")
check("пустая строка", known.expand("") == "")
plain = "Обычный текст без сокращений."
check("обычный текст не меняется", known.expand(plain) == plain, known.expand(plain))

print("11. Регистр и знаки препинания")
check("заглавная в начале сохраняется по смыслу",
      known.expand("См. приложение") == "смотри приложение",
      known.expand("См. приложение"))
check("кавычки не страдают",
      known.expand("ООО «Ромашка» и РФ") == "о о о «Ромашка» и эр эф",
      known.expand("ООО «Ромашка» и РФ"))

print("12. Цепочка в движке: сокращения, затем ударения")
from silero_tts_reader.core.tts_engine import TTSEngine

engine = TTSEngine(accent_mode="dict", abbreviations="known")
prepared = engine._apply_accents(
    engine._expand_abbreviations("В РФ звонят, см. стр. 15 и т.д."))
check("сокращения развёрнуты", "эр эф" in prepared, prepared)
check("ударения расставлены", "звон+ят" in prepared, prepared)
# Знак ударения может стоять внутри слова, поэтому сравниваем без пометок.
check("порядок верный: страница, а не стр",
      "страница" in prepared.replace("+", ""), prepared)
silent = TTSEngine(accent_mode="off", abbreviations="off")
check("оба режима выключены — текст не тронут",
      silent._apply_accents(silent._expand_abbreviations("В РФ и т.д.")) == "В РФ и т.д.")

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
