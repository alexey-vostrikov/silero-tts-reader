"""Расшифровка сокращений для синтеза речи.

Модель читает то, что видит: «т.д.» она произнесёт как «тэ дэ», «РФ» — как
одно невнятное слово, а «стр.» — как «стр». Поэтому текст для синтеза
разворачивается в то, что человек сказал бы вслух.

Три вида правил, от безопасного к рискованному:

1. Явные расшифровки с точками (``т.д.``, ``т.е.``, ``стр.``) — замена словом.
2. Известные аббревиатуры, которые читаются по буквам (``РФ``, ``США``).
3. Незнакомое слово из заглавных букв — по буквам, если оно не в списке
   читаемых как слово (``вуз``, ``загс``, ``ГОСТ``).

Третий вид — единственный, где можно ошибиться: аббревиатура, которую
произносят как слово, но которой нет в списке, будет прочитана по буквам.
Поэтому список исключений пополняемый, а не «и так сойдёт».

Сокращения с неоднозначным смыслом (``г.`` — год, город или грамм; ``с.`` —
страница или село) намеренно не разворачиваются: неверная расшифровка хуже
непрочитанного сокращения.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Имена букв: как их произносят при чтении аббревиатуры по буквам.
LETTER_NAMES = {
    "а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е", "ё": "ё",
    "ж": "жэ", "з": "зэ", "и": "и", "й": "и краткое", "к": "ка", "л": "эль",
    "м": "эм", "н": "эн", "о": "о", "п": "пэ", "р": "эр", "с": "эс", "т": "тэ",
    "у": "у", "ф": "эф", "х": "ха", "ц": "цэ", "ч": "че", "ш": "ша", "щ": "ща",
    "ъ": "твёрдый знак", "ы": "ы", "ь": "мягкий знак", "э": "э", "ю": "ю",
    "я": "я",
}

# Слово из заглавных букв: от двух до шести знаков.
_CAPS_RE = re.compile(r"(?<![А-ЯЁA-Zа-яё])([А-ЯЁ]{2,6})(?![А-ЯЁA-Zа-яё])")
# Пробелы и знаки, которые не должны склеиваться с соседними словами.
_SPACE = " "


def data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "speech"


class Abbreviations:
    """Правила расшифровки сокращений.

    ``extra`` — правки пользователя: расшифровки и словочитаемые аббревиатуры.
    Они сильнее встроенных, как и в словаре ударений.
    """

    def __init__(self, directory: Path | None = None,
                 extra: dict | None = None, generic: bool = True) -> None:
        # generic=True — разбирать по буквам любое слово из заглавных, а не
        # только известные. Ошибка здесь возможна (аббревиатура, которую
        # произносят как слово), поэтому режим включается отдельно.
        self._generic = generic
        self._directory = directory or data_dir()
        self._data = self._load()
        if extra:
            self._merge(extra)
        self._patterns = self._build_patterns()

    def _load(self) -> dict:
        path = self._directory / "abbrev.json"
        if not path.is_file():
            return {"expansions": {}, "spelled": {}, "as_word": []}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"expansions": {}, "spelled": {}, "as_word": []}
        return {
            "expansions": dict(raw.get("expansions") or {}),
            "spelled": dict(raw.get("spelled") or {}),
            "as_word": list(raw.get("as_word") or []),
        }

    def _merge(self, extra: dict) -> None:
        for key in ("expansions", "spelled"):
            values = extra.get(key) or {}
            if isinstance(values, dict):
                self._data[key].update(
                    {str(k): str(v) for k, v in values.items()})
        words = extra.get("as_word") or []
        if isinstance(words, (list, tuple)):
            self._data["as_word"].extend(str(w) for w in words)

    def _build_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Шаблоны замен, от длинных к коротким.

        Длинные первыми: иначе «до н.э.» совпадёт как «н.э.», и останется
        «до нашей эры» с лишним словом.
        """
        items = [(key, value) for key, value in self._data["expansions"].items()]
        items.sort(key=lambda item: len(item[0]), reverse=True)
        patterns: list[tuple[re.Pattern, str]] = []
        for key, value in items:
            body = re.escape(key)
            # Сокращение с точками должно стоять отдельным словом: иначе
            # «ит.д.» превратилось бы в «ит так далее».
            pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
            patterns.append((pattern, value))
        return patterns

    # ── Расшифровка ──────────────────────────────────────────────────────────

    def expand(self, text: str) -> str:
        """Развернуть сокращения в текст, пригодный для чтения вслух."""
        if not text:
            return text
        result = text
        for pattern, replacement in self._patterns:
            result = pattern.sub(replacement, result)

        spelled = self._data["spelled"]
        as_word = {word.upper() for word in self._data["as_word"]}

        def replace_caps(match: re.Match) -> str:
            token = match.group(1)
            # Список «читается словом» сильнее списка по буквам: это явное
            # исключение, и правка пользователя должна его дополнять, а не
            # упираться во встроенную расшифровку.
            if token in as_word:
                return token
            known = spelled.get(token) or spelled.get(token.lower())
            if known:
                return known
            if len(token) < 2 or not self._generic:
                return token
            letters: list[str] = []
            for letter in token:
                name = LETTER_NAMES.get(letter.lower())
                letters.append(name if name is not None else letter)
            return _SPACE.join(letters)

        return _CAPS_RE.sub(replace_caps, result)
