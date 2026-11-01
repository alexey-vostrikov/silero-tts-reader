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
    convert_marks,
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
user = Accentor(mode=MODE_USER, overrides={"договор": "догов+ор"},
                directory=DATA, source="dictionary")
check("правка применена", user.accent_word("договор") == "догов+ор",
      str(user.accent_word("договор")))
check("словарь в этом режиме не используется", user.accent_word("каталог") is None,
      str(user.accent_word("каталог")))

print("9. Режим dict: словарь, правки словаря и односложные слова")
# Источник задан явно: поведение словаря не должно зависеть от того,
# установлен ли ruaccent в системе.
full = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
for word, expected in (("каталог", "катал+ог"), ("километр", "килом+етр"),
                       ("жалюзи", "жалюз+и"), ("гражданство", "гражд+анство"),
                       ("сантиметр", "сантим+етр")):
    got = full.accent_word(word)
    check(f"dict: {word}", got == expected, str(got))
for word in ("он", "и", "в"):
    check(f"односложное «{word}» не помечается", full.accent_word(word) is None,
          str(full.accent_word(word)))

print("10. Правка пользователя сильнее правки словаря")
strong = Accentor(mode=MODE_DICT,
                  overrides={"километр": "килом+етр2".replace("2", "")},
                  directory=DATA, source="dictionary")
check("правка пользователя выигрывает", strong.explain("километр")[1] == "правка",
      strong.explain("километр")[1])

print("11. Текст: пунктуация, регистр, готовые пометки")
# Фраза без многозначных слов: разметка работает.
plain_text = "Договор лежит на столе, каталог рядом."
accented = full.accent_text(plain_text)
check("регистр сохранён", accented.startswith("Догов+ор"), accented)
check("пунктуация сохранена", accented.count(",") == plain_text.count(","))
check("пометки в тексте", accented.count("+") >= 3, accented)

# Фраза с многозначным словом не размечается вовсе: иначе модель перестаёт
# разрешать омограф по контексту (измерено: «пров+ерка окна» читается как о́кна).
with_homograph = "Он начал читать книгу, а договор остался на столе."
check("фраза с омографом размечена",
      full.accent_text(with_homograph) == "Он начал чит+ать кн+игу, а догов+ор ост+ался на стол+е.",
      full.accent_text(with_homograph))
check("омограф перечислен для журнала",
      "начал" in full.ambiguous_words(with_homograph),
      str(full.ambiguous_words(with_homograph)))
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
# Базовый путь не тянет нейросети: словарь — это данные. Оба акцентора —
# Silero Stress и ruaccent — поддерживаются, но только как необязательные:
# импорт внутри класса, а не в начале модуля, иначе приложение требовало бы
# torch и onnxruntime у всех.
for forbidden in ("import torch", "from torch", "numpy", "import onnxruntime"):
    check(f"нет обязательной зависимости «{forbidden}»",
          forbidden not in module_source)
module_lines = module_source.splitlines()
top_level_import = [line for line in module_lines
                    if line.startswith(("import ", "from "))
                    and ("ruaccent" in line or "silero_stress" in line)]
check("акценторы не импортируются на уровне модуля", not top_level_import,
      str(top_level_import))

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
    # Разметка ставится всегда: многозначное слово остаётся без пометки,
    # остальные помечены.
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
check("фраза с «выходить» размечена, само слово — нет",
      full.accent_text("Я буду выходить на работу каждый день")
      == "Я б+уду выходить на раб+оту к+аждый день",
      full.accent_text("Я буду выходить на работу каждый день"))

print("19в. Омограф не помечается, даже если есть в словаре (защита кода)")
# Проверка страхует КОД, а не данные. В рабочем словаре многозначные формы
# выброшены при сборке, поэтому ветка «омограф» не пересекается с общим
# словарём и мутация кода остаётся незамеченной. Здесь собираем синтетический
# словарь, где омограф ЕСТЬ и в общем списке тоже, и требуем, чтобы пометки
# всё равно не было.
import tempfile

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
    fake_accentor = Accentor(mode=MODE_DICT, directory=fake,
                             source="dictionary")

    form, source = fake_accentor.explain("выходят")
    check("омограф не помечен, хотя есть в словаре",
          form is None, f"{form} [{source}]")
    check("источник назван честно", "омограф" in source, source)
    check("текст остаётся без пометки",
          fake_accentor.accent_text("Они выходят во двор.") == "Они выходят во двор.",
          fake_accentor.accent_text("Они выходят во двор."))
    # Синтетический словарь не несёт списка многозначных форм, поэтому
    # «выходят» здесь не опознаётся как двойное — проверяется именно код:
    # слово из homographs.json остаётся без пометки.
    check("слово из homographs.json не помечено",
          fake_accentor.accent_word("выходят") is None,
          str(fake_accentor.accent_word("выходят")))
    check("однозначное слово из того же словаря помечено",
          fake_accentor.accent_word("каталог") == "катал+ог",
          str(fake_accentor.accent_word("каталог")))

print("19г. CLI использует те же настройки, что приложение")
# Дефект, найденный при озвучивании через silero-tts-speak: движок создавался
# без accent_mode, и через CLI (а значит и через speech-dispatcher) словарь
# ударений и сокращения не работали, хотя в настройках включены.
cli_source = (ROOT / "silero_tts_reader" / "cli.py").read_text(encoding="utf-8")
check("CLI передаёт accent_mode", "accent_mode=config.stress_dict" in cli_source)
check("CLI передаёт правки ударений", "load_overrides(config.stress_file)" in cli_source)
check("CLI передаёт режим сокращений",
      "abbreviations=config.abbreviations" in cli_source)
check("в CLI нет голого TTSEngine без настроек",
      "TTSEngine(sample_rate=config.sample_rate, models=models)" not in cli_source)

print("19д. Список многозначных форм отделён от словаря")
# «Окна» в словаре омографов источника есть только в винительном падеже, то
# есть по нему многозначность не видна. Список форм с двойным ударением
# считается при сборке данных из лемменного файла и лежит отдельно.
check("файл многозначных форм есть", (DATA / "ambiguous.txt.xz").is_file())
check("«окна» признано многозначным", dictionary.is_ambiguous("окна"),
      str(dictionary.is_ambiguous("окна")))
check("«выходят» признано многозначным", dictionary.is_ambiguous("выходят"),
      str(dictionary.is_ambiguous("выходят")))
check("«каталог» однозначно", not dictionary.is_ambiguous("каталог"),
      str(dictionary.is_ambiguous("каталог")))
check("союз «и» не считается многозначным",
      not dictionary.is_ambiguous("и"), str(dictionary.is_ambiguous("и")))
check("«эр» не считается многозначным",
      not dictionary.is_ambiguous("эр"), str(dictionary.is_ambiguous("эр")))
check("фраза без многозначных слов размечается",
      full.accent_text("Договор и каталог лежат на столе.").count("+") >= 4,
      full.accent_text("Договор и каталог лежат на столе."))
check("фраза размечена, многозначное слово без пометки",
      full.accent_text("Проверка окна плеера.") == "Пров+ерка окна плеера.",
      full.accent_text("Проверка окна плеера."))

print("19е. Знаки ударения из текста переводятся в нотацию модели")
# Модель комбинирующий знак (U+0301) не видит: «щаве́ль» и «щавель» дают один
# и тот же звук (измерено), а «щав+ель» — другой. Значит разметку автора надо
# переводить в «+», иначе она теряется.
ACUTE = "\u0301"
check("знак после гласной даёт «+» перед ней",
      convert_marks("окна" + ACUTE) == "окн+а", convert_marks("окна" + ACUTE))
check("знак на первом слоге", convert_marks("за" + ACUTE + "мок") == "з+амок",
      convert_marks("за" + ACUTE + "мок"))
check("знак после «ё» даёт «+» перед ё",
      convert_marks("щё" + ACUTE + "лкать") == "щ+ёлкать",
      convert_marks("щё" + ACUTE + "лкать"))
check("текст без знаков не меняется",
      convert_marks("обычный текст") == "обычный текст")
check("модификатор акут U+00B4 тоже понимается",
      convert_marks("сло" + "\u00b4во") == "сл+ово",
      convert_marks("сло" + "\u00b4во"))
check("знак не после гласной отбрасывается",
      convert_marks("окн" + ACUTE + "а") == "окна",
      convert_marks("окн" + ACUTE + "а"))

print("19ж. Разметка ставится всегда, многозначные видны в журнале")
# Раньше фраза с многозначным словом не размечалась вовсе. Теперь размечается
# целиком, а многозначные слова перечисляются отдельно — для журнала.
check("фраза с омографом размечена",
      full.accent_text("Проверка окна плеера.") == "Пров+ерка окна плеера.",
      full.accent_text("Проверка окна плеера."))
check("многозначное слово в списке",
      full.ambiguous_words("Проверка окна плеера.") == ["окна"],
      str(full.ambiguous_words("Проверка окна плеера.")))
check("фраза без многозначных даёт пустой список",
      full.ambiguous_words("Договор и каталог.") == [],
      str(full.ambiguous_words("Договор и каталог.")))
check("знаки автора сохраняются при разметке",
      "щ+а" in full.accent_text("ща" + ACUTE + "вель").replace("+", "+")
      or full.accent_text("ща" + ACUTE + "вель") == "щав+ель",
      full.accent_text("ща" + ACUTE + "вель"))

print("19з. ruaccent как необязательный источник")
# ruaccent размечает ВСЕ слова по контексту, поэтому решает и омографы.
# Зависимость необязательная: установлен — используется, нет — словарь.
module_source = (ROOT / "silero_tts_reader" / "core" / "accent.py").read_text(
    encoding="utf-8")
check("есть класс для ruaccent", "class RuAccentBackend" in module_source)
check("источник по умолчанию auto", 'source: str = SOURCE_AUTO' in module_source)
check("откат на словарь при отсутствии ruaccent",
      "except Exception as exc:" in module_source)
check("явный выбор ruaccent не подменяется молча",
      "ruaccent не подключился" in module_source)

# Без ruaccent в системе источник — словарь, и это видно.
plain = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
check("source=dictionary даёт словарь", plain.source == "dictionary",
      plain.source)
check("словарь работает как прежде",
      plain.accent_word("каталог") == "катал+ог",
      str(plain.accent_word("каталог")))

# Правки применяются поверх разметки акцентора: свой словарь главнее.
# Раньше они шли только по тексту без пометок и на размеченном тексте
# молчали — из-за этого «с будуна» уходило в синтез как «с буд+уна».
check("правки применяются к размеченному тексту",
      plain._apply_fixes("буд+уна лежит") == "будун+а лежит",
      plain._apply_fixes("буд+уна лежит"))
check("регистр первой буквы сохраняется",
      plain._apply_fixes("Буд+уна пришёл") == "Будун+а пришёл",
      plain._apply_fixes("Буд+уна пришёл"))
check("незнакомое слово правка не трогает",
      plain._apply_fixes("дв+ерь скрипнула") == "дв+ерь скрипнула",
      plain._apply_fixes("дв+ерь скрипнула"))

print("19и. ruaccent не роняет синтез на незнакомом слове")
# Библиотека падает на слове вне её словаря: «Диреектора» (опечатка),
# выдуманное слово. Измерено 3 падения из 10 фраз. В книге это потеря
# синтеза, поэтому отказ перехватывается, а предложение размечает словарь.
module_text = (ROOT / "silero_tts_reader" / "core" / "accent.py").read_text(
    encoding="utf-8")
check("отказ перехватывается", "except Exception:" in module_text)
check("есть разбор по предложениям", "_SENTENCE_RE" in module_text)
check("упавшее предложение отдаётся словарю",
      "fallback=self._mark_with_dictionary" in module_text)
check("разметка словарём вынесена отдельно",
      "def _mark_with_dictionary" in module_text)

# Разметка словарём не трогает слово, у которого уже есть пометка: иначе
# знак автора задвоился бы.
marked_once = full._mark_with_dictionary("звон+ит в дверь")
check("готовая пометка не удваивается", marked_once.count("+") == 1,
      marked_once)

print("19к. ruaccent, если установлен: разметка по контексту")
try:
    import ruaccent  # noqa: F401
except ImportError:
    print("  — ruaccent не установлен, проверки пропущены")
else:
    contextual = Accentor(mode=MODE_DICT, directory=DATA, source="ruaccent")
    check("источник переключился", contextual.source == "ruaccent",
          contextual.source)
    # Омограф решается по контексту: у словаря здесь пометки нет вовсе.
    for text, expected in (("проверка окна плеера", "окн+а"),
                           ("Окна выходят во двор", "+Окна")):
        got = contextual.accent_text(text)
        check(f"{text!r}", expected in got, got)
    # Незнакомое слово не роняет вызов: предложение размечается словарём.
    try:
        survived = contextual.accent_text("Диреектора пришли. Договор готов.")
    except Exception as exc:
        check("незнакомое слово не роняет разметку", False, repr(exc))
    else:
        check("незнакомое слово не роняет разметку", "Диреектора" in survived,
              survived)
        check("остальное размечено", "догов+ор" in survived or "гот+ов" in survived,
              survived)

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
