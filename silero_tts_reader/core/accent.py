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
import os
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

# Откуда берутся ударения. ``auto`` — ruaccent, если он установлен, иначе
# словарь. Разница принципиальная: ruaccent размечает ВСЕ слова по контексту,
# поэтому решает и омографы; словарь омографы не трогает, а частичная разметка
# на этой модели портит их (знак «+» левее переключает модель).
SOURCE_AUTO = "auto"
SOURCE_DICTIONARY = "dictionary"
SOURCE_RUACCENT = "ruaccent"
# Silero Stress: та же компания, что и синтез. Отдельная зависимость не нужна
# там, где уже стоит torch; омографы решаются по контексту.
SOURCE_SILERO = "silero-stress"
SOURCES = (SOURCE_AUTO, SOURCE_DICTIONARY, SOURCE_RUACCENT, SOURCE_SILERO)

_WORD_RE = re.compile(r"[А-Яа-яЁё]+")
# Деление на предложения с сохранением знаков: split делит по захваченной
# группе, поэтому знаки остаются в списке и при склейке возвращаются на место.
_SENTENCE_RE = re.compile(r"([.!?…]+\s*)")
# Слово вместе с пометкой ударения — для чистки односложных.
_WORD_MARKED_RE = re.compile(r"[А-Яа-яЁё+]+")
_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

# Знаки ударения, которые ставят в текстах: комбинирующий акут (U+0301),
# комбинирующий тон (U+0341), модификатор «акут» (U+00B4) и ASCII-апостроф
# в роли знака (U+02CA). Модель их НЕ понимает — выбрасывает как небуквенные
# (проверено: «окн́а» и «о́кна» дают тот же звук, что текст без пометок), а знак
# «+» понимает. Поэтому знаки автора переводятся в «+».
_TEXT_STRESS = frozenset("\u0301\u0341\u00b4\u02ca")

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
        # Словоформы с двойным ударением. Хранятся отдельно от словаря: они в
        # него не входят, но по ним видно, можно ли размечать текст вовсе.
        self._ambiguous: frozenset[str] | None = None

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

    def _load_ambiguous(self) -> None:
        path = self._directory / "ambiguous.txt.xz"
        if not path.is_file():
            self._ambiguous = frozenset()
            return
        raw = lzma.decompress(path.read_bytes()).decode("utf-8")
        self._ambiguous = frozenset(line for line in raw.split("\n") if line)

    def is_ambiguous(self, word: str) -> bool:
        """Двойное ли ударение у словоформы (по списку из источника)."""
        if self._ambiguous is None:
            self._load_ambiguous()
        assert self._ambiguous is not None
        return word.lower() in self._ambiguous

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


class ArgoDictionary:
    """Ударения арготизмов и просторечия — по «Словарю русского арго».

    Источник: В. С. Елистратов, «Словарь русского арго» (ГРАМОТА.РУ, 2002),
    около 9 000 словарных статей. Ударение там указано автором, а не выведено
    моделью, поэтому слова вроде «буду́н», «акаде́м», «амбрэ́» получают верное
    ударение, тогда как акцентор на незнакомом слове часто ошибается на слог
    влево: «с будуна» он читал как «с бу́дуна».

    Зачем отдельный файл. Словарь арго — другой лексикон, а не дополнение к
    литературному: «буха́ть» у Елистратова значит «пить», а «бу́хать» в
    литературном языке — «ударять». Совпадение написания при разном значении
    означает, что брать его можно только там, где обычный словарь молчит.
    Проверено на собранных данных: из 5 810 слов 2 475 есть в основном
    словаре, и 55 из них расходятся по ударению — все это разные слова.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or data_dir()
        self._forms: dict[str, str] | None = None

    def _load(self) -> None:
        path = self._directory / "argo.txt.xz"
        if not path.is_file():
            # Файла может не быть: словарь арго необязателен, и его отсутствие
            # не должно ломать разметку остальным.
            self._forms = {}
            return
        raw = lzma.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        forms: dict[str, str] = {}
        for line in raw.splitlines():
            word, _, marked = line.partition("\t")
            if word and marked:
                forms[word] = marked
        self._forms = forms

    def lookup(self, word: str) -> str | None:
        """Ударённая форма слова или ``None``, если его нет в словаре."""
        if self._forms is None:
            self._load()
        assert self._forms is not None
        return self._forms.get(word.lower())

    def items(self):
        """Все пары «слово → ударённая форма» для общей карты правок."""
        if self._forms is None:
            self._load()
        assert self._forms is not None
        return self._forms.items()

    @property
    def size(self) -> int:
        if self._forms is None:
            self._load()
        assert self._forms is not None
        return len(self._forms)


class RuAccentBackend:
    """Разметка через ruaccent — библиотеку с моделью омографов.

    Отличие от словаря: размечаются ВСЕ слова, и выбор ударения омографа идёт
    по контексту фразы, а не по таблице. Замер на восьми фразах: 7 верных
    против 5 у словаря с фиксированным вариантом.

    Цена: сама библиотека тянет onnxruntime и transformers, модели занимают
    около 160 МБ. Поэтому зависимость необязательная: нет ruaccent — работает
    словарь, и это видно в журнале, а не замалчивается.
    """

    def __init__(self, models_dir: Path | None = None,
                 model_size: str = "tiny") -> None:
        RUAccent = _load_extra("ruaccent").RUAccent

        if models_dir is None:
            models_dir = (Path.home() / ".local" / "share"
                          / "silero-tts-reader" / "ruaccent")
        models_dir = Path(models_dir).expanduser()
        # Модели уже лежат на месте — значит, сеть не нужна: без этих
        # переменных ruaccent всё равно сходит на HuggingFace проверить
        # обновление и падает без сети.
        if (models_dir / "nn").is_dir():
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        else:
            models_dir.mkdir(parents=True, exist_ok=True)
        self._accent = RUAccent()
        self._accent.load(
            omograph_model_size=model_size,
            use_dictionary=True,
            workdir=str(models_dir),
        )

    def mark(self, text: str, fallback=None) -> str:
        """Разметить текст, переживая незнакомые слова.

        Библиотека падает на слове, которого нет в её словаре (опечатка,
        выдуманное имя): токенизатор отдаёт неполный вход, и onnxruntime
        поднимает ValueError. Проверено: 3 падения из 10 фраз, в том числе на
        «Диреектора» — одна опечатка роняла весь вызов, а в книге это значит
        потерю синтеза.

        Поэтому отказ перехватывается: сначала пробуем весь текст, при
        неудаче — по предложениям, и упавшее предложение остаётся как есть
        (модель прочтёт его сама). Молча терять разметку целой главы нельзя,
        терять её в одном предложении — можно.
        """
        try:
            return self._accent.process_all(text)
        except Exception:
            pass
        parts = _SENTENCE_RE.split(text)
        marked: list[str] = []
        for part in parts:
            if not part.strip():
                marked.append(part)
                continue
            try:
                marked.append(self._accent.process_all(part))
            except Exception:
                # Предложение с незнакомым словом: отдаём его словарю — он
                # разметит хотя бы известные слова, остальное прочтёт модель.
                marked.append(fallback(part) if fallback is not None else part)
        return "".join(marked)


def _load_extra(module: str):
    """Импорт пакета из окружения приложения, если он там есть.

    Приложение ставит то, чего нет в apt, в ~/.local/share/silero-tts-reader/venv
    (см. ``extras``). Графическое приложение подмешивает это окружение в
    launcher, а командная строка и модуль speech-dispatcher туда не заходят —
    и без этой строки акцентор молча не находился бы именно там, где он нужен
    для чтения книг. Подмешивание путей безопасно и повторяемо: повторный
    вызов ничего не меняет.
    """
    from . import extras

    extras.activate()
    return __import__(module, fromlist=["_"])


class SileroStressBackend:
    """Разметка через Silero Stress — модель ударений и омографов от Silero.

    Три отличия от ruaccent, важные на практике:

    * покрытие около 4 млн словоформ плюс 2,2 тыс. омографов с F1 0,92;
    * вес около 50 МБ и никаких зависимостей, кроме torch, который и так нужен
      синтезу: ни transformers, ни onnxruntime;
    * MIT и никакой сети — модели лежат внутри пакета.

    Замер на наших фразах: согласен со всеми двенадцатью правками словаря и
    решает три омографа, на которых словарь и ruaccent ошибаются («окн+а
    плеера», «замк+и», при том что «в+ыходит» он путает с совершенным видом).
    """

    def __init__(self) -> None:
        module = _load_extra("silero_stress")

        self._accentor = module.load_accentor()

    def mark(self, text: str, fallback=None) -> str:
        """Разметить текст, переживая отказ модели.

        Ударение на односложных не ставится: знак на них ничего не меняет, а
        лишний «+» левее по тексту переключает модель синтеза, и омограф после
        него читается неверно.
        """
        try:
            return self._accentor(text, stress_single_vowel=False)
        except Exception:
            pass
        parts = _SENTENCE_RE.split(text)
        marked: list[str] = []
        for part in parts:
            # Знаки препинания и пробелы модели не нужны: она на них тратит
            # вызов и ничего не возвращает.
            if not _WORD_RE.search(part):
                marked.append(part)
                continue
            try:
                marked.append(self._accentor(part, stress_single_vowel=False))
            except Exception:
                marked.append(fallback(part) if fallback is not None else part)
        return "".join(marked)


class Accentor:
    """Расставляет ударения в тексте.

    Источники: словарь (всегда), а поверх него нейросетевой акцентор, если он
    установлен, — Silero Stress или ruaccent. Словарь омографы не трогает: их
    различает акцентор модели. Оба акцентора размечают текст целиком, поэтому
    на них частичной разметки не возникает.

    Silero Stress идёт первым в ``auto``: он легче (50 МБ против 160 МБ у
    ruaccent) и не тянет transformers с onnxruntime — только torch, который
    синтезу и так нужен.
    """

    def __init__(
        self,
        mode: str = MODE_OFF,
        overrides: dict[str, str] | None = None,
        dictionary: StressDictionary | None = None,
        directory: Path | None = None,
        source: str = SOURCE_AUTO,
        ruaccent_models: Path | None = None,
    ) -> None:
        self._directory = directory
        self._source = source if source in SOURCES else SOURCE_AUTO
        self._ruaccent_models = ruaccent_models
        self._ruaccent: RuAccentBackend | None = None
        self._ruaccent_checked = False
        self._silero: SileroStressBackend | None = None
        self._silero_checked = False
        self.mode = mode if mode in MODES else MODE_OFF
        self._overrides = {
            _decode_stress(word).lower(): form
            for word, form in (overrides or {}).items()
        }
        self._corrections = load_corrections(self._data_dir())
        self._dictionary = dictionary or StressDictionary(self._data_dir())
        self._argo: ArgoDictionary | None = None
        self._fix_map_cache: dict[str, str] | None = None

    def _data_dir(self) -> Path:
        return self._directory or data_dir()

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
            # Омограф: решает акцентор модели, а не словарь. У модели есть
            # разбор по контексту (put_stress_homo), и на замере из десяти
            # контрастных фраз она верна 7 раз против 5 у словаря с одним
            # фиксированным вариантом. Словарная пометка перебила бы её выбор:
            # «Окна выходят во двор» получило бы «Окн+а».
            return None, "омограф: решает модель"

        variants = self._dictionary.variants(lowered)
        if len(variants) == 1:
            return variants[0], "словарь"
        if len(variants) > 1:
            # Угадывать между вариантами нечем: разбора нет, а словарь даёт
            # несколько ударений. Пусть решает акцентор модели.
            return None, "словарь: вариантов несколько"

        # Основной словарь слова не знает — вот здесь и нужен словарь арго:
        # это ровно те слова, на которых акцентор ошибается. Где основной
        # словарь отвечает, арго не применяется никогда: это другой лексикон
        # («буха́ть» пить против «бу́хать» ударять).
        if self._argo is None:
            self._argo = ArgoDictionary(self._data_dir())
        argo = self._argo.lookup(lowered)
        if argo:
            return argo, "арго"
        return None, "нет"

    def accent_word(self, word: str) -> str | None:
        """Ударённая форма слова или ``None``, если слова нет в словаре."""
        return self.explain(word)[0]

    # ── Текст ────────────────────────────────────────────────────────────────

    def accent_text(self, text: str) -> str:
        """Разметить ударения в тексте, сохранив пунктуацию и регистр.

        Если во фрагменте есть многозначное слово, фрагмент не размечается
        вовсе. Причина измерена: наличие хотя бы одного знака «+» переключает
        модель в режим, где она доверяет пометкам целиком, и слова БЕЗ пометки
        в том же тексте она читает уже не по контексту. «проверка окна плеера»
        без пометок читается верно (окна́), а «пров+ерка окна плеера» — как
        о́кна. Поэтому частичная разметка омографы портит.
        """
        if self.mode == MODE_OFF or not text:
            return text
        # Знаки автора переводятся всегда, в любом режиме: это не наша догадка,
        # а прямая подсказка из текста.
        text = convert_marks(text)

        silero = self._silero_backend()
        if silero is not None:
            # Акцентор размечает всё по контексту, поэтому правки пользователя
            # идут после него: свой словарь главнее любой модели.
            marked = silero.mark(text, fallback=self._mark_with_dictionary)
            return self._apply_fixes(_drop_redundant_marks(marked))

        ruaccent = self._ruaccent_backend()
        if ruaccent is not None:
            # ruaccent размечает всё по контексту, поэтому правки пользователя
            # применяются после него: свой словарь главнее любой модели.
            marked = ruaccent.mark(text, fallback=self._mark_with_dictionary)
            return self._apply_fixes(_drop_redundant_marks(marked))

        return self._apply_fixes(self._mark_with_dictionary(text))

    def _mark_with_dictionary(self, text: str) -> str:
        """Разметка словарём: помечаются слова, у которых ударение одно."""
        def replace(match: re.Match) -> str:
            word = match.group(0)
            if STRESS_MARK in word:
                return word
            accented = self.accent_word(word)
            if not accented:
                return word
            return apply_mark(word, accented)

        return _WORD_RE.sub(replace, text)

    def _silero_backend(self) -> SileroStressBackend | None:
        """Silero Stress, если он запрошен и установлен. Иначе None."""
        if self._source in (SOURCE_DICTIONARY, SOURCE_RUACCENT):
            return None
        if not self._silero_checked:
            self._silero_checked = True
            try:
                self._silero = SileroStressBackend()
            except Exception as exc:
                if self._source == SOURCE_SILERO:
                    # Выбор сделан явно: молчаливая подмена была бы обманом.
                    # i18n: нет перевода — строка журнала в консоли
                    print(f"  ✗ silero-stress не подключился: "
                          f"{type(exc).__name__}: {exc}", flush=True)
                self._silero = None
        return self._silero

    def _ruaccent_backend(self) -> RuAccentBackend | None:
        """ruaccent, если он запрошен и доступен. Иначе None и словарь."""
        if self._source in (SOURCE_DICTIONARY, SOURCE_SILERO):
            return None
        if not self._ruaccent_checked:
            self._ruaccent_checked = True
            try:
                self._ruaccent = RuAccentBackend(self._ruaccent_models)
            except Exception as exc:
                if self._source == SOURCE_RUACCENT:
                    # Выбор сделан явно: молчаливая подмена была бы обманом.
                    # i18n: нет перевода — строка журнала в консоли
                    print(f"  ✗ ruaccent не подключился: {type(exc).__name__}: "
                          f"{exc}", flush=True)
                self._ruaccent = None
        return self._ruaccent

    def _fix_map(self) -> dict[str, str]:
        """Что и чем исправлять поверх разметки: арго, встроенные правки, свои.

        Порядок силы: правки пользователя сильнее встроенных, встроенные
        сильнее арго.

        Арго отбирается по одному правилу: слово берётся только там, где
        основной словарь молчит. Иначе «буха́ть» (пить, арго) перебило бы
        «бу́хать» (ударять, литературный язык) — это разные слова с одинаковым
        написанием. Отбор делается один раз и запоминается: 5 810 проверок по
        бинарному поиску на каждую фразу были бы расточительны.
        """
        if self._fix_map_cache is not None:
            return self._fix_map_cache

        merged: dict[str, str] = {}
        if self._argo is None:
            self._argo = ArgoDictionary(self._data_dir())
        if self._argo.size:
            for word, marked in self._argo.items():
                # Омограф не берём ни при каких условиях: его читают по
                # контексту, а арго даёт одно значение. В арго «ноги» — это
                # «уйти», и жёсткая пометка ломала бы «ноги устали».
                if (self._dictionary.variants(word)
                        or self._dictionary.homograph_variants(word)):
                    continue
                merged[word] = marked
        merged.update(self._corrections)
        merged.update(self._overrides)
        self._fix_map_cache = merged
        return merged

    def _apply_fixes(self, text: str) -> str:
        """Поставить правки поверх разметки: сначала встроенные, потом свои.

        Зачем поверх модели. Акцентор размечает текст целиком и ошибается на
        редких словах: в книге «с будуна» он поставил «буд+уна». Правка в
        данных при этом не срабатывала вовсе, потому что применялась только к
        тексту без пометок. Ошибка была не в словаре, а в порядке шагов.

        Порядок: встроенные правки из ``corrections.json``, затем правки
        пользователя — своё всегда главнее.

        Слово сравнивается без знаков ударения: к этому моменту акцентор уже
        мог поставить свой, и поиск по «чистому» слову его не нашёл бы.
        """
        if not text:
            return text
        merged = self._fix_map()
        if not merged:
            return text

        def replace(match: re.Match) -> str:
            token = match.group(0)
            form = merged.get(token.replace(STRESS_MARK, "").lower())
            if form is None:
                return token
            # Регистр первой буквы сохраняется: правка не должна портить
            # начало фразы и имена собственные.
            if token[:1].isupper():
                return form[:1].upper() + form[1:]
            return form

        return _WORD_MARKED_RE.sub(replace, text)

    @property
    def source(self) -> str:
        """Действующий источник: ``silero-stress``, ``ruaccent`` или ``dictionary``."""
        if self._silero_backend() is not None:
            return SOURCE_SILERO
        if self._ruaccent_backend() is not None:
            return SOURCE_RUACCENT
        return "dictionary"

    def ambiguous_words(self, text: str) -> list[str]:
        """Многозначные слова во фрагменте — для журнала, а не для пропуска.

        Разметка ставится всегда, но у таких слов пометки нет: модель читает
        их по контексту. Знак левее по тексту переключает её, и тогда выбор
        может стать неверным — это видно в журнале уровня detail.
        """
        found: list[str] = []
        for match in _WORD_RE.finditer(convert_marks(text)):
            word = match.group(0).lower()
            if self.count_vowels(word) < 2:
                continue
            if self._dictionary.is_ambiguous(word):
                found.append(word)
        return found

    def count_vowels(self, word: str) -> int:
        """Сколько гласных в слове — для проверки правильности пометки."""
        return sum(1 for letter in word if letter in _VOWELS)


def _drop_redundant_marks(text: str) -> str:
    """Убрать пометку с односложных слов.

    ruaccent ставит знак всем словам, включая «он», «и», «в». Там гласная одна,
    помечать нечего, а лишние знаки засоряют текст и мешают читать журнал.
    """
    def replace(match: re.Match) -> str:
        word = match.group(0)
        if word.count(STRESS_MARK) and sum(
                1 for letter in word if letter in _VOWELS) < 2:
            return word.replace(STRESS_MARK, "")
        return word

    return _WORD_MARKED_RE.sub(replace, text)


def convert_marks(text: str) -> str:
    """Перевести знаки ударения из текста в нотацию модели.

    «окн́а» и «о́кна» становятся «окн+а». Модель комбинирующий знак не видит
    вовсе, поэтому без этой замены разметка автора теряется: в книгах и
    статьях ударение часто проставлено, и это готовая подсказка.
    """
    if not text or not any(char in _TEXT_STRESS for char in text):
        return text
    result: list[str] = []
    for char in text:
        if char in _TEXT_STRESS:
            # Знак относится к предыдущей гласной; «+» ставится перед ней.
            if result and result[-1] in _VOWELS:
                result.insert(len(result) - 1, STRESS_MARK)
            continue
        result.append(char)
    return "".join(result)


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
