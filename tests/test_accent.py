"""Проверки словаря ударений.

Набор проверок строится вокруг одного правила: неверная пометка хуже
отсутствующей. Модель умеет угадывать сама, но не спорит с поставленным
знаком, поэтому всё, что не подтверждено, обязано остаться без пометки.
"""

from __future__ import annotations

import json
import lzma
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core.accent import (
    MODE_DICT,
    MODE_OFF,
    MODE_USER,
    Accentor,
    StressDictionary,
    apply_mark,
    has_valid_stress,
    load_overrides,
)

DATA = ROOT / "silero_tts_reader" / "assets" / "stress"
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


print("1. Данные на месте и не пустые")
for filename in ("forms.txt.xz", "homographs.json.xz", "corrections.json"):
    path = DATA / filename
    if not path.is_file():
        check(f"файл {filename} есть", False, "нет файла")
        continue
    size = path.stat().st_size
    check(f"файл {filename} не пустой", size > 100, f"{size / 1e6:.1f} МБ"
          if filename.endswith(".xz") else f"{size} байт")

print("2. Формат данных: одна пометка перед гласной")
blob = lzma.decompress((DATA / "forms.txt.xz").read_bytes()).decode("utf-8")
forms = blob.split("\n")
if forms and forms[-1] == "":
    forms.pop()
check("словоформ загружено", len(forms) > 1_000_000, f"{len(forms):,}")
bad_forms = [form for form in forms[:200000] if not has_valid_stress(form)]
check("пометка стоит перед гласной", not bad_forms, f"проверено 200000, "
      f"негодных {len(bad_forms)} {bad_forms[:3]}")

print("3. Порядок строк: поиск требует сортировки по слову без пометки")
keys = [form.replace("+", "") for form in forms]
check("слова отсортированы", all(keys[i] <= keys[i + 1] for i in range(len(keys) - 1)),
      "нарушений порядка нет")

print("4. Словарь находит ударения")
dictionary = StressDictionary(DATA)
started = time.perf_counter()
dictionary.variants("проверка")
elapsed = time.perf_counter() - started
check("загрузка словаря укладывается в 3 с", elapsed < 3.0, f"{elapsed:.2f} с")
for word, expected in (("звонит", "звон+ит"), ("договор", "догов+ор"),
                       ("каталог", "катал+ог"), ("торты", "т+орты"),
                       ("свёкла", "св+ёкла"), ("щавель", "щав+ель")):
    found = dictionary.variants(word)
    check(f"словарь: {word}", found == (expected,), f"{found}")

print("5. Буква «ё», написанная через «е»")
check("свекла находится как свёкла",
      dictionary.variants("свекла") == ("св+ёкла",), str(dictionary.variants("свекла")))
check("поселок находится как посёлок",
      dictionary.variants("поселок") == ("пос+ёлок",), str(dictionary.variants("поселок")))

print("6. Омографы разрешаются морфологией, а не угадыванием")
cases = [
    ("ноги", "NOUN Animacy=Inan|Case=Gen|Gender=Fem|Number=Sing", "ног+и"),
    ("ноги", "NOUN Animacy=Inan|Case=Nom|Gender=Fem|Number=Plur", "н+оги"),
    ("полки", "NOUN Animacy=Inan|Case=Gen|Gender=Fem|Number=Sing", "п+олки"),
    # «полки́» — множественное число от «полк» (полк, а не полка).
    ("полки", "NOUN Animacy=Inan|Case=Nom|Gender=Masc|Number=Plur", "полк+и"),
    ("начал", "VERB Aspect=Perf|Gender=Masc|Mood=Ind|Number=Sing|Tense=Past|VerbForm=Fin",
     "н+ачал"),
]
for word, tag, expected in cases:
    got = dictionary.resolve_homograph(word, [tag])
    check(f"омограф {word} по разбору {tag.split()[1][:28]}…", got == expected, str(got))
check("неразрешимый омограф не выдумывает ударение",
      dictionary.resolve_homograph("замок", ["VERB X"]) is None)
check("омограф без разбора не разрешается",
      dictionary.resolve_homograph("ноги", []) is None)

print("7. Режим off ничего не меняет")
off = Accentor(mode=MODE_OFF)
source = "На столе лежит договор."
check("текст без изменений", off.accent_text(source) == source)

print("8. Режим user применяет только правки")
user = Accentor(mode=MODE_USER, overrides={"договор": "догов+ор"}, directory=DATA)
check("правка применена", user.accent_word("договор") == "догов+ор",
      str(user.accent_word("договор")))
check("словарь в этом режиме не используется", user.accent_word("каталог") is None,
      str(user.accent_word("каталог")))

print("9. Режим dict: словарь, правки словаря и односложные слова")
full = Accentor(mode=MODE_DICT, directory=DATA)
for word, expected in (("каталог", "катал+ог"), ("километр", "килом+етр"),
                       ("жалюзи", "жалюз+и"), ("гражданство", "гр+ажданство"),
                       ("двери", "дв+ери")):
    got = full.accent_word(word)
    check(f"dict: {word}", got == expected, str(got))
for word in ("он", "и", "в"):
    check(f"односложное «{word}» не помечается", full.accent_word(word) is None,
          str(full.accent_word(word)))

print("10. Правка пользователя сильнее правки словаря")
strong = Accentor(mode=MODE_DICT, overrides={"километр": "килом+етр2".replace("2", "")},
                  directory=DATA)
check("правка пользователя выигрывает", strong.explain("километр")[1] == "правка",
      strong.explain("километр")[1])

print("11. Текст: пунктуация, регистр, готовые пометки")
text = "Он начал читать книгу, а договор остался на столе."
accented = full.accent_text(text)
# «начал» — омограф: без pymorphy3 ударение не ставится, и это правильно.
# Регистр проверяем на слове, где разбор не нужен.
check("регистр сохранён", "на стол+е" in accented and accented.startswith("Он "),
      accented)
check("пунктуация сохранена", accented.count(",") == text.count(","))
check("пометки в тексте", accented.count("+") >= 3, accented)
loud = full.accent_text("ЗВОНЯТ в дверь")
check("капс сохранён", "ЗВОН+ЯТ" in loud, loud)
already = "звон+ит"
check("готовая пометка не переставляется",
      full.accent_text(already) == already, full.accent_text(already))

print("12. «е» вместо «ё» не подменяет буквы в тексте")
# «все» — одна гласная: помечать нечего, слово остаётся как есть.
check("односложное «все» не трогается", full.accent_text("все") == "все",
      full.accent_text("все"))
check("поселок помечается без добавления ё",
      full.accent_text("поселок") == "пос+елок", full.accent_text("поселок"))
check("свекла помечается без добавления ё",
      full.accent_text("свекла") == "св+екла", full.accent_text("свекла"))

print("13. Перенос пометки не портит слово")
check("apply_mark сохраняет буквы", apply_mark("свекла", "св+ёкла") == "св+екла",
      apply_mark("свекла", "св+ёкла"))
check("apply_mark сохраняет капс", apply_mark("ДОГОВОР", "догов+ор") == "ДОГОВ+ОР",
      apply_mark("ДОГОВОР", "догов+ор"))
check("apply_mark сохраняет первую заглавную",
      apply_mark("Договор", "догов+ор") == "Догов+ор",
      apply_mark("Договор", "догов+ор"))

print("14. Скорость: книга не должна ждать словаря")
words = ["договор", "квартал", "каталог", "звонят", "красивее", "средства", "эксперты"]
sample = " ".join(words * 500)
started = time.perf_counter()
full.accent_text(sample)
elapsed = time.perf_counter() - started
rate = len(sample.split()) / max(elapsed, 1e-6)
check("больше 20 000 слов в секунду", rate > 20_000, f"{rate:,.0f} слов/с")

print("15. Правки пользователя читаются и проверяются")
path = Path("/tmp/test-stress-overrides.json")
path.write_text(json.dumps({
    "договор": "догов+ор",
    "плохая": "плохая",
    "ещё": "ещ+ё зря",
}), encoding="utf-8")
parsed = load_overrides(path)
check("годная правка принята", "договор" in parsed, str(parsed))
check("правка без пометки отброшена", "плохая" not in parsed, str(parsed))
check("правка с лишним текстом отброшена", "ещё" not in parsed, str(parsed))
path.unlink()

print("16. В модуле нет нейросетей")
module_source = (ROOT / "silero_tts_reader" / "core" / "accent.py").read_text(
    encoding="utf-8")
for forbidden in ("import torch", "from torch", "silero_stress", "ruaccent",
                  "onnxruntime", "numpy"):
    check(f"нет зависимости «{forbidden}»", forbidden not in module_source)

print("17. Правки к словарю корректны по форме")
corrections = json.loads((DATA / "corrections.json").read_text(encoding="utf-8"))
bad = [word for word, form in corrections.items()
       if not word.startswith("_")
       and (not has_valid_stress(form) or form.replace("+", "").lower() != word.lower())]
check("ключ совпадает с формой и пометка одна", not bad, str(bad[:4]))
check("правок не меньше десяти", len(corrections) >= 11, f"{len(corrections) - 1}")

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
