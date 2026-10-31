"""Расстановка ударений по словарю, без нейросетей.

Зачем отдельный слой, если у Silero TTS есть свой акцентор: модели ошибаются
на словах, где ударение не выводится из формы (звоня́т, догово́р, ща́вель), и на
омографах. Свой знак ``+`` перед гласной модель уважает и не переставляет,
поэтому достаточно разметить текст до синтеза.

Источники ударений — три, в порядке силы:

1. Правки пользователя (``~/.config/silero-tts-reader/stress.json``) — всегда
   сильнее всего остального, включая словарь.
2. Словарь омографов с морфологическими ключами: ударение выбирается по
   разбору слова, а не по догадке. ``ноги`` в родительном падеже и ``но́ги`` в
   именительном — разные записи.
3. Общий словарь на 1,5 млн словоформ.

Если слова нет нигде, возвращается ``None``: пусть решает акцентор модели.
Подставлять «как получится» нельзя — неверное ударение хуже отсутствующего,
потому что модель уже умеет угадывать, но не умеет спорить с поставленным
знаком.

Данные взяты из проекта russian_g2p (НГУ, MIT): см. assets/stress/README.md.
"""

from __future__ import annotations

import itertools
import json
import lzma
import re
from array import array
from pathlib import Path

# Пометка ударения в формате Silero TTS: знак перед ударной гласной.
STRESS_MARK = "+"

# Режимы работы. ``off`` — текст не трогается вовсе; ``user`` — только правки
# пользователя (безопасно, ничего не меняет само по себе); ``dict`` — правки,
# омографы и общий словарь.
MODE_OFF = "off"
MODE_USER = "user"
MODE_DICT = "dict"
MODES = (MODE_OFF, MODE_USER, MODE_DICT)

_WORD_RE = re.compile(r"[А-Яа-яЁё]+")
_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

# Признаки pymorphy3 -> имена граммем в словаре омографов.
# Ключи словаря записаны в алфавитном порядке признаков, поэтому при сборке
# тега признаки сортируются: иначе строка не совпадёт с ключом.
_GRAMMEMES = {
    "anim": "Animacy=Anim",
    "inan": "Animacy=Inan",
    "perf": "Aspect=Perf",
    "impf": "Aspect=Imp",
    "nomn": "Case=Nom",
    "gent": "Case=Gen",
    "datv": "Case=Dat",
    "accs": "Case=Acc",
    "ablt": "Case=Ins",
    "loct": "Case=Loc",
    "voct": "Case=Voc",
    "masc": "Gender=Masc",
    "femn": "Gender=Fem",
    "neut": "Gender=Neut",
    "ms-f": "Gender=Masc",
    "indc": "Mood=Ind",
    "impr": "Mood=Imp",
    "sing": "Number=Sing",
    "plur": "Number=Plur",
    "1per": "Person=1",
    "2per": "Person=2",
    "3per": "Person=3",
    "pres": "Tense=Pres",
    "past": "Tense=Past",
    "futr": "Tense=Fut",
    "brev": "Variant=Brev",
    "fin": "VerbForm=Fin",
    "infn": "VerbForm=Inf",
    "partcp": "VerbForm=Part",
    "ger": "VerbForm=Ger",
}


def data_dir() -> Path:
    """Каталог со словарными данными: рядом с пакетом или в установленном виде."""
    here = Path(__file__).resolve().parent.parent / "assets" / "stress"
    return here


def _decode_stress(form: str) -> str:
    """Убрать пометку ударения: ``звон+ит`` -> ``звонит``."""
    return form.replace(STRESS_MARK, "")


class StressDictionary:
    """Словарь ударённых словоформ с двоичным поиском.

    Файл на 36 МБ в распакованном виде грузится один раз и держится в памяти
    как одни байты плюс массив смещений строк. Список отдельных строк занял бы
    втрое больше: на 1,5 млн записей накладные расходы Python сравнимы с самими
    данными.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or data_dir()
        self._blob: bytes | None = None
        self._offsets: array | None = None
        self._homographs: dict[str, dict[str, str]] | None = None

    # ── Загрузка ─────────────────────────────────────────────────────────────

    def _load_forms(self) -> None:
        path = self._directory / "forms.txt.xz"
        if not path.is_file():
            # i18n: нет перевода — исключение для журнала
            raise FileNotFoundError(f"нет словаря ударений: {path}")
        blob = lzma.decompress(path.read_bytes())
        offsets = array("I")
        position = 0
        append = offsets.append
        while True:
            newline = blob.find(b"\n", position)
            if newline < 0:
                break
            append(position)
            position = newline + 1
        self._blob = blob
        self._offsets = offsets

    def _load_homographs(self) -> None:
        path = self._directory / "homographs.json.xz"
        if not path.is_file():
            self._homographs = {}
            return
        raw = lzma.decompress(path.read_bytes())
        self._homographs = json.loads(raw.decode("utf-8"))

    @property
    def loaded(self) -> bool:
        return self._blob is not None

    def load(self) -> None:
        """Загрузить данные. Повторный вызов ничего не делает."""
        if self._blob is None:
            self._load_forms()
        if self._homographs is None:
            self._load_homographs()

    def unload(self) -> None:
        """Освободить память: словарь занимает около 40 МБ."""
        self._blob = None
        self._offsets = None
        self._homographs = None

    # ── Поиск ────────────────────────────────────────────────────────────────

    def _line(self, index: int) -> bytes:
        assert self._blob is not None and self._offsets is not None
        start = self._offsets[index]
        end = self._blob.find(b"\n", start)
        return self._blob[start:end]

    def variants(self, word: str) -> tuple[str, ...]:
        """Все ударённые варианты слова из общего словаря.

        Строки отсортированы по слову без пометки, поэтому одинаковые слова (а
        это и есть омографы) лежат подряд: собираем всю серию.

        Букву «ё» в книгах пишут через «е» гораздо чаще, чем через «ё»
        (свекла, а не свёкла), а словарь хранит ё. Поэтому при промахе
        пробуем замены «е» на «ё»: их немного, перебор дешевле, чем хранение
        второго индекса на 36 МБ.
        """
        for candidate in (word, *self._yo_candidates(word)):
            found = self._variants_exact(candidate)
            if found:
                return found
        return ()

    @staticmethod
    def _yo_candidates(word: str) -> list[str]:
        """Слова с «е», заменённым на «ё», — от одной замены к большему числу."""
        positions = [index for index, letter in enumerate(word) if letter == "е"]
        result: list[str] = []
        # Ограничение в четыре замены: в словах русского языка их больше не
        # встречается, а перебор растёт как 2^n.
        for count in range(1, min(len(positions), 4) + 1):
            for combination in itertools.combinations(positions, count):
                letters = list(word)
                for index in combination:
                    letters[index] = "ё"
                result.append("".join(letters))
        return result

    def _variants_exact(self, word: str) -> tuple[str, ...]:
        self.load()
        assert self._offsets is not None
        needle = word.lower().encode("utf-8")
        low, high = 0, len(self._offsets)
        while low < high:
            middle = (low + high) // 2
            if self._line(middle).replace(b"+", b"") < needle:
                low = middle + 1
            else:
                high = middle
        found: list[str] = []
        index = low
        while index < len(self._offsets):
            line = self._line(index)
            if line.replace(b"+", b"") != needle:
                break
            found.append(line.decode("utf-8"))
            index += 1
        return tuple(found)

    def homograph_variants(self, word: str) -> dict[str, str]:
        """Записи словаря омографов: ключ — разбор, значение — ударённая форма."""
        self.load()
        assert self._homographs is not None
        return self._homographs.get(word.lower(), {})

    def tag_for(self, parse) -> str:
        """Разбор pymorphy3 в строку того вида, что в словаре омографов.

        Вид: ``NOUN Animacy=Inan|Case=Gen|Gender=Fem|Number=Sing``. Признаки
        отсортированы — так записаны ключи в словаре.
        """
        names = set(parse.tag.grammemes)
        grammemes = sorted(
            _GRAMMEMES[name] for name in names if name in _GRAMMEMES
        )
        # У pymorphy личные формы прошедшего времени не помечены как конечные
        # (нет «fin»), а словарь омографов такие формы различает. Восстанавливаем
        # признак сами: наклонение есть, а инфинитива, причастия и деепричастия
        # нет — значит, форма конечная.
        if (parse.tag.POS == "VERB"
                and ("indc" in names or "impr" in names)
                and not names & {"infn", "partcp", "ger"}):
            grammemes = sorted(set(grammemes) | {"VerbForm=Fin"})
        part_of_speech = parse.tag.POS or "X"
        return f"{part_of_speech} {'|'.join(grammemes)}" if grammemes else part_of_speech

    def resolve_homograph(self, word: str, tags: list[str]) -> str | None:
        """Выбрать ударение омографа по готовым разборам.

        Если ни один разбор не совпал с ключами словаря, возвращается ``None``:
        значит, различить варианты нечем, и решать должна модель.
        """
        entries = self.homograph_variants(word)
        if not entries:
            return None
        for tag in tags:
            form = entries.get(tag)
            if form:
                return form
        return None


class Accentor:
    """Расставляет ударения в тексте по словарю.

    Порядок источников задан в описании модуля. Разбор слова берётся из
    pymorphy3, если он установлен; без него омографы не разрешаются, и это
    видно в журнале, а не замалчивается.
    """

    def __init__(
        self,
        mode: str = MODE_OFF,
        overrides: dict[str, str] | None = None,
        dictionary: StressDictionary | None = None,
        directory: Path | None = None,
    ) -> None:
        self._directory = directory
        self.mode = mode if mode in MODES else MODE_OFF
        self._overrides = {
            _decode_stress(word).lower(): form
            for word, form in (overrides or {}).items()
        }
        self._corrections = load_corrections(self._data_dir())
        self._dictionary = dictionary or StressDictionary(self._data_dir())
        self._morphology = None
        self._morphology_checked = False

    def _data_dir(self) -> Path:
        return self._directory or data_dir()

    # ── Морфология ───────────────────────────────────────────────────────────

    def _parses(self, word: str) -> list[str]:
        """Разборы слова в формате словаря омографов, пустой список без pymorphy."""
        if not self._morphology_checked:
            self._morphology_checked = True
            try:
                import pymorphy3
            except ImportError:
                self._morphology = None
            else:
                self._morphology = pymorphy3.MorphAnalyzer()
        if self._morphology is None:
            return []
        return [self._dictionary.tag_for(p) for p in self._morphology.parse(word)]

    @property
    def morphology_available(self) -> bool:
        self._parses("проверка")
        return self._morphology is not None

    # ── Одно слово ───────────────────────────────────────────────────────────

    def explain(self, word: str) -> tuple[str | None, str]:
        """Ударённая форма и источник: ``правка``, ``омограф``, ``словарь`` или ``нет``."""
        if self.mode == MODE_OFF:
            return None, "нет"
        lowered = word.lower()
        if STRESS_MARK in word:
            return word, "готовая пометка"
        override = self._overrides.get(lowered)
        if override:
            return override, "правка"
        if self.mode == MODE_USER:
            return None, "нет"
        # Односложное слово: гласная одна, помечать нечего, а лишний знак
        # только засоряет текст перед синтезом.
        if self.count_vowels(lowered) < 2:
            return None, "одна гласная"

        fixed = self._corrections.get(lowered)
        if fixed:
            return fixed, "правка словаря"

        if self._dictionary.homograph_variants(lowered):
            resolved = self._dictionary.resolve_homograph(lowered, self._parses(lowered))
            if resolved:
                return resolved, "омограф"
            # Омограф, но разрешить нечем — пусть решает модель.
            return None, "омограф неразрешён"

        variants = self._dictionary.variants(lowered)
        if len(variants) == 1:
            return variants[0], "словарь"
        if len(variants) > 1:
            # Угадывать между вариантами нечем: разбора нет, а словарь даёт
            # несколько ударений. Пусть решает акцентор модели.
            return None, "словарь: вариантов несколько"
        return None, "нет"

    def accent_word(self, word: str) -> str | None:
        """Ударённая форма слова или ``None``, если слова нет в словаре."""
        return self.explain(word)[0]

    # ── Текст ────────────────────────────────────────────────────────────────

    def accent_text(self, text: str) -> str:
        """Разметить ударения в тексте, сохранив пунктуацию и регистр."""
        if self.mode == MODE_OFF or not text:
            return text

        def replace(match: re.Match) -> str:
            word = match.group(0)
            accented = self.accent_word(word)
            if not accented:
                return word
            return apply_mark(word, accented)

        return _WORD_RE.sub(replace, text)

    def count_vowels(self, word: str) -> int:
        """Сколько гласных в слове — для проверки правильности пометки."""
        return sum(1 for letter in word if letter in _VOWELS)


def apply_mark(word: str, form: str) -> str:
    """Перенести пометку из словарной формы на слово из текста.

    Берётся только позиция ударной гласной, а буквы остаются исходными.
    Иначе поиск, терпимый к «е» вместо «ё», подменял бы слова: «все» стало бы
    «всё», а это уже другое слово. Словарная форма и слово совпадают по длине,
    потому что различаются только буквой «е»/«ё».

    Регистр сохраняется: словарь хранит строчные формы, а слово в тексте может
    начинать предложение или быть написано капсом.
    """
    position = form.index(STRESS_MARK)
    letters = word.replace(STRESS_MARK, "")
    if position > len(letters):
        return word
    marked = letters[:position] + STRESS_MARK + letters[position:]
    if word.isupper() and len(word) > 1:
        return marked.upper()
    if word[:1].isupper():
        return marked[:1].upper() + marked[1:]
    return marked


def has_valid_stress(form: str) -> bool:
    """Ровно одна пометка и она стоит перед гласной."""
    if form.count(STRESS_MARK) != 1:
        return False
    position = form.index(STRESS_MARK)
    return position + 1 < len(form) and form[position + 1] in _VOWELS


def load_corrections(directory: Path) -> dict[str, str]:
    """Правки к словарю, поставляемые вместе с пакетом.

    Нужны там, где источник ошибается (киломе+тр) или слова нет вовсе
    (жалюзи). Формат тот же, что у правок пользователя, но правит их не он.
    """
    path = directory / "corrections.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        word: form
        for word, form in raw.items()
        if isinstance(word, str) and isinstance(form, str)
        and not word.startswith("_")
    }


def load_overrides(path: Path) -> dict[str, str]:
    """Прочитать правки пользователя, отбросив некорректные записи.

    Некорректная правка не должна ломать чтение книги: она пропускается с
    сообщением, остальные работают.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for word, form in raw.items():
        if not isinstance(word, str) or not isinstance(form, str):
            continue
        if not _WORD_RE.fullmatch(_decode_stress(form)):
            # i18n: нет перевода — строка журнала в консоли
            print(f"  ⚠ правка ударения пропущена: {word!r} -> {form!r}", flush=True)
            continue
        if not has_valid_stress(form):
            # i18n: нет перевода — строка журнала в консоли
            print(f"  ⚠ правка ударения пропущена (нет одной пометки перед "
                  f"гласной): {word!r} -> {form!r}", flush=True)
            continue
        result[word] = form
    return result
