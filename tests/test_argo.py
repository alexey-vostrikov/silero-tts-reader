"""Проверки словаря арго как источника ударений.

Два правила, вокруг которых построен набор:

* арго заполняет только пробелы основного словаря — где тот молчит, а
  акцентор ошибается на слог влево («с будуна» → «с бу́дуна»);
* там, где основной словарь отвечает, арго не применяется никогда: это другой
  лексикон, и «буха́ть» (пить) не должно перебить «бу́хать» (ударять).

Настоящий файл данных небольшой (десятки килобайт), поэтому проверки идут на
нём, а не на заглушке: важно, чтобы работал именно формат на диске.
"""

from __future__ import annotations

import lzma
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core.accent import (
    MODE_DICT,
    Accentor,
    ArgoDictionary,
)

DATA = ROOT / "silero_tts_reader" / "assets" / "stress"
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ✓ {message}")
    else:
        raise AssertionError(message)


def make_argo(directory: Path, rows: dict[str, str]) -> None:
    """Собрать файл данных в том же формате, что у настоящего словаря."""
    payload = "\n".join(f"{w}\t{m}" for w, m in rows.items()) + "\n"
    (directory / "argo.txt.xz").write_bytes(
        lzma.compress(payload.encode("utf-8"), preset=9))


def test_format() -> None:
    """Файл данных читается, и в нём есть проверенные слова."""
    print("\n1. Файл данных арго")
    path = DATA / "argo.txt.xz"
    check(path.is_file(), f"файл на месте: {path.name}")
    argo = ArgoDictionary(DATA)
    check(argo.size > 1000, f"слов в словаре: {argo.size}")
    # Ударение в источнике стоит перед гласной — иначе синтез его не поймёт.
    bad = [w for w, m in argo.items()
           if "+" not in m or m.replace("+", "") != w]
    check(not bad, f"все записи в нотации синтеза, ошибок: {len(bad)}")
    vowels = set("аеёиоуыэюя")
    wrong_place = [m for _w, m in argo.items()
                   if m.index("+") + 1 >= len(m)
                   or m[m.index("+") + 1] not in vowels]
    check(not wrong_place,
          f"знак стоит перед гласной везде, нарушений: {len(wrong_place)}")


def test_fills_gap() -> None:
    """Арго отвечает там, где основной словарь молчит."""
    print("\n2. Арго закрывает пробел основного словаря")
    dictionary = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
    argo = ArgoDictionary(DATA)

    # Слова, проверенные по статье: ударение авторское, модель ошибалась.
    for word in ("академ", "алиас", "амбрэ"):
        check(not dictionary._dictionary.variants(word),
              f"«{word}» нет в основном словаре")
        form, who = dictionary.explain(word)
        check(form == argo.lookup(word) and who == "арго",
              f"«{word}» → {form!r} ({who})")


def test_dictionary_wins() -> None:
    """Где основной словарь отвечает, арго не применяется."""
    print("\n3. Основной словарь сильнее арго")
    dictionary = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
    # «бухать» в арго — «пить» (буха́ть), в литературном — «ударять» (бу́хать).
    for word in ("бухать", "врезать", "километр"):
        form, who = dictionary.explain(word)
        check(who != "арго",
              f"«{word}» взят из {who}, а не из арго: {form!r}")
    form, _ = dictionary.explain("бухать")
    check(form == "б+ухать", f"литературное ударение сохранено: {form}")


def test_applied_over_model() -> None:
    """Правка арго ставится поверх разметки акцентора."""
    print("\n4. Арго применяется к размеченному тексту")
    plain = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
    argo = ArgoDictionary(DATA)
    marked = argo.lookup("академ")
    check(marked is not None, f"слово есть в словаре: {marked}")
    out = plain._apply_fixes("ак+адем закрыли")
    check(out == f"{marked} закрыли", f"разметка акцентора исправлена: {out}")

    # Правки пользователя сильнее арго.
    custom = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary",
                      overrides={"академ": "ак+адем"})
    check(custom._apply_fixes("акад+ем закрыли") == "ак+адем закрыли",
          "правка пользователя перебивает арго")


def test_map_is_built_once() -> None:
    """Карта правок строится один раз, а не на каждую фразу."""
    print("\n5. Карта правок запоминается")
    acc = Accentor(mode=MODE_DICT, directory=DATA, source="dictionary")
    first = acc._fix_map()
    check(acc._fix_map() is first, "повторный вызов отдаёт ту же карту")
    check(len(first) > 100, f"в карте записей: {len(first)}")


def test_missing_file() -> None:
    """Нет файла арго — разметка продолжает работать словарём."""
    print("\n6. Без файла арго всё работает")
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        # Копируем только основной словарь и необязательные данные.
        for name in ("forms.txt.xz", "homographs.json.xz",
                     "ambiguous.txt.xz", "corrections.json"):
            source = DATA / name
            if source.is_file():
                (directory / name).write_bytes(source.read_bytes())
        acc = Accentor(mode=MODE_DICT, directory=directory, source="dictionary")
        form, who = acc.explain("километр")
        check(form == "килом+етр" and who != "арго",
              f"словарь работает: {form!r} ({who})")
        check(acc.explain("академ")[1] == "нет",
              "арго молчит, если файла нет")


def main() -> int:
    test_format()
    test_fills_gap()
    test_dictionary_wins()
    test_applied_over_model()
    test_map_is_built_once()
    test_missing_file()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
