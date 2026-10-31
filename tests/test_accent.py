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

print("6. Словарь омографов виден, но не применяется")
# Словарь омографов остаётся в данных: по нему можно понять, что слово
# многозначно, и объяснить в журнале, почему пометки нет.
check("«окна» известен как омограф",
      bool(dictionary.homograph_variants("окна")),
      str(list(dictionary.homograph_variants("окна"))[:2]))
check("«звонит» омографом не считается",
      not dictionary.homograph_variants("звонит"),
      str(dictionary.homograph_variants("звонит")))

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
                       ("сантиметр", "сантим+етр")):
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

print("18. Омографы не разрешаются словарём вовсе")
# Замер на модели показал: она различает омографы по контексту лучше, чем
# словарь с одним вариантом. Поэтому словарь их не трогает — и заодно не
# нужен pymorphy3, которого нет в архиве Ubuntu.
check("в данных нет заранее разрешённых омографов",
      not (DATA / "resolved.txt.xz").is_file())
for word in ("окна", "письма", "ноги", "полки"):
    check(f"«{word}» остаётся без пометки", full.accent_word(word) is None,
          str(full.accent_word(word)))

print("19. Омограф не помечается: решает акцентор модели")
# Модель разрешает омографы по контексту сама (put_stress_homo), и делает это
# лучше фиксированного словарного варианта. Замер на десяти контрастных фразах:
# модель верна 7 раз, словарь с одним вариантом — 5. Пример из жизни:
# «Окна выходят во двор» модель читает как и́менительный множественного, а
# «Проверка окна плеера» — как родительный единственного. Один словарный
# вариант на оба случая ошибается обязательно.
# Ожидание: омограф остаётся без пометки, остальные слова помечены словарём.
# «плеера» и «устали» в словаре отсутствуют либо тоже омографы — их тоже
# отдаём модели, поэтому в ожидании они без пометки.
context_cases = [
    ("Окна выходят во двор.", "Окна выходят во двор."),
    ("Проверка окна плеера.", "Пров+ерка окна плеера."),
    ("Ноги устали от дороги.", "Ноги устали от дороги."),
]
for source, expected in context_cases:
    got = full.accent_text(source)
    check(f"{source!r}", got == expected, got)
for word in ("окна", "ноги", "полки", "замок", "стоит"):
    form, source = full.explain(word)
    check(f"омограф «{word}» отдан модели",
          form is None and "омограф" in source, f"{form} [{source}]")

print("19а. Многозначные словоформы исключены из словаря")
# «выходят» в источнике помечено как вы́ходят (вынянчат), хотя в тексте
# почти всегда выхо́дят. Разные леммы дают разное ударение — словарь такое
# не трогает.
for word in ("выходят", "выходит", "выходить", "двери"):
    got = full.accent_word(word)
    check(f"«{word}» не помечается словарём", got is None, str(got))
check("однозначное слово по-прежнему помечается",
      full.accent_word("каталог") == "катал+ог", str(full.accent_word("каталог")))
# «боду́н»: знак стоит ПЕРЕД ударной гласной, поэтому «бод+ун».
check("«бодун» помечается верно", full.accent_word("бодун") == "бод+ун",
      str(full.accent_word("бодун")))
check("«бодуна» помечается верно", full.accent_word("бодуна") == "бодун+а",
      str(full.accent_word("бодуна")))

print("19б. Вид глагола задаёт смысл, а не форма слова")
# «Выходить» — вы́ходить (совершенный, вынянчить) и выходи́ть (несовершенный,
# идти). Морфология вид различает, но выбор зависит от фразы, поэтому обе
# формы остаются без пометки, а решает акцентор модели.
for word in ("выходить", "выходит", "выходят", "выходил"):
    check(f"«{word}» не помечается", full.accent_word(word) is None,
          str(full.accent_word(word)))
for text in ("Мама обещала выходить его до выздоровления",
             "Я буду выходить на работу каждый день"):
    got = full.accent_text(text)
    # Точная сверка: ни один из двух вариантов ударения не появился.
    check("ни «в+ыходить», ни «выход+ить» в тексте нет",
          "в+ыходить" not in got and "выход+ить" not in got, got)
check("остальные слова во фразе помечены",
      full.accent_text("Я буду выходить на работу каждый день").count("+") >= 3,
      full.accent_text("Я буду выходить на работу каждый день"))

print("19в. Омограф не помечается, даже если есть в словаре (защита кода)")
# Проверка страхует КОД, а не данные. В рабочем словаре многозначные формы
# выброшены при сборке, поэтому ветка «омограф» не пересекается с общим
# словарём и мутация кода остаётся незамеченной. Здесь собираем синтетический
# словарь, где омограф ЕСТЬ и в общем списке тоже, и требуем, чтобы пометки
# всё равно не было.
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as raw:
    fake = Path(raw)
    # Порядок строк — по слову без пометки, иначе двоичный поиск ничего не
    # найдёт и проверка пройдёт при любом коде. «выходить» < «выходят» (и < я).
    fake_forms = sorted(
        ["в+ыходят", "выход+ить", "катал+ог"],
        key=lambda form: (form.replace("+", ""), form))
    (fake / "forms.txt.xz").write_bytes(lzma.compress(
        ("\n".join(fake_forms) + "\n").encode("utf-8")))
    (fake / "homographs.json.xz").write_bytes(lzma.compress(
        json.dumps({"выходят": {"1": "в+ыходят", "2": "выход+ят"},
                    "выходить": {"1": "в+ыходить"}},
                   ensure_ascii=False).encode("utf-8")))
    fake_accentor = Accentor(mode=MODE_DICT, directory=fake)

    form, source = fake_accentor.explain("выходят")
    check("омограф не помечен, хотя есть в словаре",
          form is None, f"{form} [{source}]")
    check("источник назван честно", "омограф" in source, source)
    check("текст остаётся без пометки",
          fake_accentor.accent_text("Они выходят во двор.") == "Они выходят во двор.",
          fake_accentor.accent_text("Они выходят во двор."))
    check("однозначное слово из того же словаря помечено",
          fake_accentor.accent_word("каталог") == "катал+ог",
          str(fake_accentor.accent_word("каталог")))

print("20. Правки к словарю корректны по форме")
corrections = json.loads((DATA / "corrections.json").read_text(encoding="utf-8"))
plain = {word: form for word, form in corrections.items()
         if isinstance(form, str) and not word.startswith("_")}
bad = [word for word, form in plain.items()
       if not has_valid_stress(form) or form.replace("+", "").lower() != word.lower()]
check("ключ совпадает с формой и пометка одна", not bad, str(bad[:4]))
check("правок не меньше десяти", len(plain) >= 11, f"{len(plain)}")

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(1 if failures else 0)
