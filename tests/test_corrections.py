"""Проверки вкладки «Правки ударений»: разбор, проверка и запись файлов.

Набор держится на одном правиле: негодная правка не должна попасть в файл.
Файл читается целиком при запуске, и одна испорченная запись отключает ВСЕ
правки сразу — молча, без сообщения пользователю. Поэтому проверяется не
только удачный путь, но и каждый отказ, и то, что после отказа файл остался
прежним.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core.accent import (
    MODE_DICT,
    Accentor,
    load_corrections,
    load_overrides,
)
from silero_tts_reader.core.corrections import (
    BROKEN_FILE,
    EMPTY_FORM,
    EMPTY_WORD,
    LAYER_BUILTIN,
    LAYER_USER,
    LETTERS_DIFFER,
    MARK_POSITION,
    NO_MARK,
    NOT_A_WORD,
    READ_ONLY,
    WORD_HAS_MARK,
    CorrectionError,
    Corrections,
    load_file,
    normalize,
    save_file,
)

checks = 0
failures = 0

DATA = ROOT / "silero_tts_reader" / "assets" / "stress"


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks, failures
    checks += 1
    if condition:
        print(f"  ✓ {name}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"  ✗ {name}{(' — ' + detail) if detail else ''}")


def reason(word: str, form: str) -> str | None:
    """Код отказа или None, если правка годна."""
    try:
        normalize(word, form)
    except CorrectionError as exc:
        return exc.reason
    return None


print("1. Проверка пары «слово — ударение»")
check("верная правка проходит", reason("жалюзи", "жалюз+и") is None)
check("ё сохраняется", reason("щёлкать", "щ+ёлкать") is None)
check("регистр слова приводится", normalize("Жалюзи", "жалюз+и")[0] == "жалюзи")
check("пустое слово", reason("", "жалюз+и") == EMPTY_WORD)
check("пустое ударение", reason("жалюзи", "") == EMPTY_FORM)
check("знак в слове", reason("жалю+зи", "жалюз+и") == WORD_HAS_MARK)
check("слово с пробелом", reason("жалю зи", "жалюз+и") == NOT_A_WORD)
check("слово латиницей", reason("jaluzi", "жалюз+и") == NOT_A_WORD)
check("нет знака в ударении", reason("жалюзи", "жалюзи") == NO_MARK)
check("знак перед согласной", reason("звонят", "звоня+т") == MARK_POSITION)
check("знак в конце слова", reason("жалюзи", "жалюзи+") == MARK_POSITION)
check("два знака", reason("жалюзи", "жал+юз+и") == MARK_POSITION)
check("другая словоформа", reason("звонят", "звон+ит") == LETTERS_DIFFER)
check("другая длина", reason("жалюзи", "жалюз+ии") == LETTERS_DIFFER)

print("\n2. Поставляемый файл целиком, а не выборкой")
raw = json.loads((DATA / "corrections.json").read_text(encoding="utf-8"))
entries = {word: form for word, form in raw.items() if not word.startswith("_")}
bad = {word: reason(word, form) for word, form in entries.items() if reason(word, form)}
check("все записи годны", not bad, str(bad))
check("записей больше десяти", len(entries) > 10, str(len(entries)))
check("у каждой записи ровно один знак",
      all(form.count("+") == 1 for form in entries.values()))
check("комментарий в файле сохранён", "_comment" in raw)

print("\n3. Чтение и запись файла")
tmp = Path(tempfile.mkdtemp(prefix="str-corr-"))
source = tmp / "corrections.json"
source.write_text(
    json.dumps({"_comment": "пояснение", "жалюзи": "жалюз+и", "агент": "аг+ент"},
               ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
meta, loaded = load_file(source)
check("служебный ключ отделён от правок", meta == {"_comment": "пояснение"}, str(meta))
check("правки прочитаны", list(loaded) == ["жалюзи", "агент"], str(list(loaded)))
source.chmod(0o640)
save_file(source, meta, {**loaded, "замки": "замк+и"})
text = source.read_text(encoding="utf-8")
check("порядок записей сохранён",
      list(json.loads(text)) == ["_comment", "жалюзи", "агент", "замки"],
      str(list(json.loads(text))))
check("комментарий на месте", json.loads(text)["_comment"] == "пояснение")
check("права файла сохранены", source.stat().st_mode & 0o777 == 0o640,
      oct(source.stat().st_mode & 0o777))
check("в каталоге не осталось временных файлов",
      sorted(p.name for p in tmp.iterdir()) == ["corrections.json"],
      str(sorted(p.name for p in tmp.iterdir())))
check("файл читается словарём приложения",
      load_corrections(tmp).get("замки") == "замк+и", str(load_corrections(tmp)))

print("\n4. Два слоя: встроенные правки и свои")
tmp2 = Path(tempfile.mkdtemp(prefix="str-corr2-"))
builtin = tmp2 / "corrections.json"
user = tmp2 / "stress.json"
builtin.write_text(json.dumps({"жалюзи": "жалюз+и", "агент": "аг+ент"},
                             ensure_ascii=False), encoding="utf-8")
book = Corrections(builtin, user)
check("файл исходников доступен на запись", book.writable is True)
check("строк без своих правок",
      [(row.word, row.layer) for row in book.rows()] ==
      [("агент", LAYER_BUILTIN), ("жалюзи", LAYER_BUILTIN)],
      str([(row.word, row.layer) for row in book.rows()]))

saved = book.save("Жалюзи", "жалюз+и")
check("правка встроенного слова легла во встроенный файл",
      saved.layer == LAYER_BUILTIN and load_corrections(tmp2).get("жалюзи") == "жалюз+и",
      str(saved))
saved = book.save("бодуна", "бодун+а")
check("новое слово легло туда же", saved.layer == LAYER_BUILTIN, str(saved.layer))
check("своих правок не появилось", load_overrides(user) == {}, str(load_overrides(user)))

user.write_text(json.dumps({"агент": "а+гент"}, ensure_ascii=False), encoding="utf-8")
book.reload()
rows = {row.word: row for row in book.rows()}
check("своя правка перекрывает встроенную", rows["агент"].form == "а+гент",
      rows["агент"].form)
check("слой строки — свои правки", rows["агент"].layer == LAYER_USER)
check("строк стало три", len(rows) == 3, str(len(rows)))

check("свою правку удалить можно", book.can_remove("агент") is True)
removed = book.remove("агент")
check("удаление своей правки", removed == LAYER_USER, removed)
check("после удаления видна встроенная", book.layer_of("агент") == LAYER_BUILTIN)
check("файл своих правок пуст", load_overrides(user) == {}, str(load_overrides(user)))

print("\n5. Файл пакета только для чтения")
before = builtin.read_text(encoding="utf-8")
builtin.chmod(0o444)
book.reload()
check("запись запрещена", book.writable is False)
check("встроенную строку удалить нельзя", book.can_remove("агент") is False)
try:
    book.remove("агент")
    check("удаление встроенной строки отклонено", False, "исключения не было")
except CorrectionError as exc:
    check("удаление встроенной строки отклонено", exc.reason == READ_ONLY, exc.reason)

saved = book.save("жалюзи", "жалюз+и")
check("правка ушла в свои", saved.layer == LAYER_USER, str(saved.layer))
check("свои правки записаны", load_overrides(user).get("жалюзи") == "жалюз+и",
      str(load_overrides(user)))
check("встроенный файл не переписан", builtin.read_text(encoding="utf-8") == before)
builtin.chmod(0o644)

print("\n6. Испорченный файл открывается, но не перезаписывается")
broken = tmp2 / "broken.json"
broken.write_text('{"жалюзи": "жалюз+и",}', encoding="utf-8")
book = Corrections(broken, tmp2 / "none.json")
check("битый JSON виден как ошибка", book.broken == broken, str(book.broken))
check("строк нет, а не половина", book.rows() == [], str(book.rows()))
try:
    book.save("жалюзи", "жалюз+и")
    check("запись в испорченный файл отклонена", False, "исключения не было")
except CorrectionError as exc:
    check("запись в испорченный файл отклонена", exc.reason == BROKEN_FILE, exc.reason)
check("испорченный файл на месте", broken.read_text(encoding="utf-8").startswith('{"жалюзи"'))
broken.write_text('["жалюзи"]', encoding="utf-8")
book = Corrections(broken, tmp2 / "none.json")
check("список вместо объекта тоже ошибка", book.broken == broken, str(book.broken))
broken.unlink()
book = Corrections(broken, tmp2 / "none.json")
check("пропавший файл — не ошибка", book.broken is None, str(book.broken))

print("\n7. Акцентор перечитывает правки без перезагрузки словаря")
# Данные словаря копируются во временный каталог: без них акцентор падает на
# загрузке форм, и проверка перечитывания правок превращается в проверку
# наличия словаря. В CI Silero Stress не установлен, поэтому подмены источника
# нет — нужны настоящие файлы, а не пустой каталог.
DATA = ROOT / "silero_tts_reader" / "assets" / "stress"
for name in ("forms.txt.xz", "homographs.json.xz", "argo.txt.xz"):
    source = DATA / name
    if source.is_file():
        (tmp2 / name).write_bytes(source.read_bytes())
acc = Accentor(mode=MODE_DICT, directory=tmp2, source="dictionary")
builtin = tmp2 / "corrections.json"
book = Corrections(builtin, user)
book.save("висят", "вис+ят")
after = acc.accent_text("жалюзи висят на окне")
check("правка подхвачена без пересоздания акцентора", "вис+ят" in after, after)
check("словарь при этом жив", acc.accent_word("жалюзи") is not None,
      str(acc.accent_word("жалюзи")))
acc.reload_fixes({"висят": "в+исят"})
check("свои правки сильнее встроенных", "в+исят" in acc.accent_text("висят"))
acc.reload_fixes({})
check("снятие своих правок возвращает встроенные",
      "вис+ят" in acc.accent_text("висят"), acc.accent_text("висят"))

print("\n8. Движок перечитывает правки, не пересоздавая акцентор")
from silero_tts_reader.core.tts_engine import TTSEngine

engine = TTSEngine(accent_mode=MODE_DICT, stress_source="dictionary")
engine._accentor = acc
book = Corrections(builtin, user)
book.save("висят", "в+исят")
engine.reload_accents({})
check("правка из файла дошла до движка",
      "в+исят" in engine._apply_accents("висят"), engine._apply_accents("висят"))
check("акцентор тот же, словарь не перезагружен", engine._accentor is acc)
engine.reload_accents({"висят": "вис+ят"})
check("свои правки перекрывают файл в движке",
      "вис+ят" in engine._apply_accents("висят"), engine._apply_accents("висят"))
engine.reload_accents({})
check("сброс правок возвращает файл",
      "в+исят" in engine._apply_accents("висят"), engine._apply_accents("висят"))

print(f"\nИТОГ: успешно {checks - failures}, провалено {failures}")
sys.exit(0 if failures == 0 else 1)
