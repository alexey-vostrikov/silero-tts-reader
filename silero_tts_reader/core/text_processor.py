"""Text processing and transliteration engine — LLM API & offline rule-based phonetization."""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.config_manager import ConfigManager


# Common English IT words/brands dictionary for instant accurate offline transliteration
DICTIONARY: dict[str, str] = {
    "python": "па+йтон",
    "javascript": "джаваскри+пт",
    "typescript": "та+йпскрипт",
    "github": "гитха+б",
    "google": "гу+гл",
    "windows": "ви+ндовс",
    "linux": "ли+нукс",
    "ubuntu": "убу+нту",
    "arch": "а+рч",
    "youtube": "юту+б",
    "telegram": "телегра+м",
    "docker": "до+кер",
    "rust": "ра+ст",
    "c++": "си плюс плюс",
    "c#": "си шарп",
    "java": "джа+ва",
    "php": "пи аш пи",
    "html": "аш тэ эм эль",
    "css": "си эс эс",
    "json": "дже+йсон",
    "api": "э пэ и",
    "tts": "ти ти эс",
    "qt": "кью ти",
    "pyqt": "пай кью ти",
    "wayland": "вэ+йланд",
    "x11": "икс оди+ннадцать",
    "deepmind": "дипма+нд",
    "openai": "о+упен эй ай",
    "ollama": "олла+ма",
}

# Phonetic character mapping for Latin to Cyrillic offline fallback
LATIN_TO_CYRILLIC: dict[str, str] = {
    "sh": "ш", "ch": "ч", "sch": "щ", "th": "т", "ph": "ф", "kh": "х", "zh": "ж",
    "ts": "ц", "ck": "к", "ee": "и", "oo": "у", "ea": "и", "qu": "кв",
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з",
}


UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
UNITS_FEM = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
         "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _num_to_ru_phrase(val: int) -> str:
    if val == 0:
        return "ноль"

    parts = []

    def _triplet(n: int, is_fem: bool = False) -> str:
        if n == 0:
            return ""
        res = []
        h = n // 100
        t = (n % 100) // 10
        u = n % 10

        if h > 0:
            res.append(HUNDREDS[h])
        if t == 1:
            res.append(TEENS[u])
        else:
            if t > 1:
                res.append(TENS[t])
            if u > 0:
                res.append(UNITS_FEM[u] if is_fem else UNITS[u])
        return " ".join(res)

    # Millions
    m = (val // 1_000_000) % 1000
    if m > 0:
        m_words = _triplet(m)
        last = m % 100
        if 11 <= last <= 19:
            suff = "миллионов"
        elif last % 10 == 1:
            suff = "миллион"
        elif 2 <= last % 10 <= 4:
            suff = "миллиона"
        else:
            suff = "миллионов"
        parts.append(f"{m_words} {suff}")

    # Thousands
    k = (val // 1_000) % 1000
    if k > 0:
        k_words = _triplet(k, is_fem=True)
        last = k % 100
        if 11 <= last <= 19:
            suff = "тысяч"
        elif last % 10 == 1:
            suff = "тысяча"
        elif 2 <= last % 10 <= 4:
            suff = "тысячи"
        else:
            suff = "тысяч"
        parts.append(f"{k_words} {suff}")

    # Hundreds/Tens/Units
    r = val % 1000
    if r > 0 or not parts:
        r_words = _triplet(r)
        if r_words:
            parts.append(r_words)

    return " ".join(parts).strip()


class TextProcessor:
    """Handles text preprocessing, LLM API phonetization, and rule-based transliteration."""

    @staticmethod
    def convert_numbers_to_words(text: str) -> str:
        """Convert all numbers, percentages, and decimals into Russian words offline."""
        if not text or not re.search(r"\d", text):
            return text

        # Replace percentages (e.g. 100%, 5%)
        def _replace_pct(m: re.Match) -> str:
            num_str = m.group(1)
            try:
                val = int(num_str)
                phrase = _num_to_ru_phrase(val)
                last = val % 100
                if 11 <= last <= 19:
                    suff = "процентов"
                elif last % 10 == 1:
                    suff = "процент"
                elif 2 <= last % 10 <= 4:
                    suff = "процента"
                else:
                    suff = "процентов"
                return f"{phrase} {suff}"
            except Exception:
                return m.group(0)

        res = re.sub(r"(\d+)%", _replace_pct, text)

        # Replace decimals (e.g. 3.14 or 3,14)
        def _replace_float(m: re.Match) -> str:
            try:
                v1 = int(m.group(1))
                v2 = int(m.group(2))
                return f"{_num_to_ru_phrase(v1)} точка {_num_to_ru_phrase(v2)}"
            except Exception:
                return m.group(0)

        res = re.sub(r"(\d+)[.,](\d+)", _replace_float, res)

        # Replace remaining integer numbers
        def _replace_int(m: re.Match) -> str:
            try:
                val = int(m.group(0))
                return _num_to_ru_phrase(val)
            except Exception:
                return m.group(0)

        return re.sub(r"\d+", _replace_int, res)

    @staticmethod
    def has_non_cyrillic(text: str) -> bool:
        """Check if text contains Latin alphabetic characters."""
        return bool(re.search(r"[a-zA-Z]", text))

    @staticmethod
    def digits_to_words_en(text: str) -> str:
        """Развернуть числа в английские слова — для нерусских моделей.

        Английская v3 читает латиницу сама, но цифры ей нужно подавать словами.
        Дробные числа и разряды поддерживаются до миллионов: этого достаточно
        для озвучки текста, а не для бухгалтерии.
        """
        if not text or not re.search(r"\d", text):
            return text

        ones = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve", "thirteen",
                "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                "nineteen")
        tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty",
                "seventy", "eighty", "ninety")

        def under_thousand(value: int) -> str:
            words: list[str] = []
            if value >= 100:
                words.append(f"{ones[value // 100]} hundred")
                value %= 100
            if value >= 20:
                words.append(tens[value // 10] + (f"-{ones[value % 10]}" if value % 10 else ""))
            elif value:
                words.append(ones[value])
            return " ".join(words)

        def integer_words(value: int) -> str:
            if value == 0:
                return ones[0]
            parts: list[str] = []
            for divisor, name in ((1_000_000_000, "billion"), (1_000_000, "million"),
                                  (1_000, "thousand")):
                if value >= divisor:
                    parts.append(f"{under_thousand(value // divisor)} {name}")
                    value %= divisor
            if value:
                parts.append(under_thousand(value))
            return " ".join(parts)

        def replace_number(match: re.Match) -> str:
            raw = match.group(0).replace(",", "")
            try:
                if "." in raw:
                    whole, _, frac = raw.partition(".")
                    spoken = f"{integer_words(int(whole))} point " + " ".join(
                        ones[int(digit)] for digit in frac)
                    return spoken
                return integer_words(int(raw))
            except (ValueError, IndexError):
                return match.group(0)

        return re.sub(r"\d[\d,]*(?:\.\d+)?", replace_number, text)

    @staticmethod
    def transliterate_offline(text: str) -> str:
        """Fast offline rule- and dictionary-based transliteration of Latin words."""
        def _replace_word(match: re.Match) -> str:
            word = match.group(0)
            lower = word.lower()
            if lower in DICTIONARY:
                res = DICTIONARY[lower]
            else:
                # Rule-based phonetization
                res = lower
                for lat, cyr in LATIN_TO_CYRILLIC.items():
                    res = res.replace(lat, cyr)

            if word.isupper():
                return res.upper()
            elif word[0].isupper():
                return res.capitalize()
            return res

        # Match Latin words
        return re.sub(r"[a-zA-Z]+", _replace_word, text)

    @staticmethod
    def _clean_llm_response(result: str, original_text: str) -> str:
        """Clean up LLM output: strip codeblocks, conversational preamble, leftover Latin in parens."""
        if not result:
            return TextProcessor.transliterate_offline(original_text)

        # 1. Remove markdown code blocks ```...```
        result = re.sub(r"```[a-zA-Z]*\n?", "", result)
        result = result.replace("```", "")

        # 2. Remove common LLM conversational preambles
        preambles = [
            r"^Вот\s+переписанный\s+текст(?:\s+для\s+TTS)?:\s*",
            r"^Вот\s+текст:\s*",
            r"^Результат:\s*",
            r"^Перевод:\s*",
            r"^Текст:\s*",
            r"^Ответ:\s*",
        ]
        for pattern in preambles:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE)

        # 3. Strip outer quotes if LLM wrapped response in quotes
        result = result.strip()
        if (result.startswith('"') and result.endswith('"')) or (result.startswith('«') and result.endswith('»')):
            result = result[1:-1].strip()

        # 4. Remove original Latin words in parentheses like "Па+йтон (Python)" -> "Па+йтон"
        result = re.sub(r"\s*\([a-zA-Z0-9\s\-_]+\)", "", result)

        # 5. Final safety pass: convert any remaining Latin words via offline transliterator!
        if TextProcessor.has_non_cyrillic(result):
            print(f"[TextProcessor] Внимание: в ответе ИИ остались латинские буквы ({result!r}). Выполняю контрольный офлайн-фолбэк...", flush=True)
            result = TextProcessor.transliterate_offline(result)

        cleaned = result.strip()
        print(f"[TextProcessor] Очищенный результат для TTS: {cleaned!r}", flush=True)
        return cleaned

    @staticmethod
    def transliterate_llm(
        text: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 3.0,
        system_prompt: str | None = None,
    ) -> str:
        """Call an OpenAI-compatible Chat Completion API to rewrite Latin words to Cyrillic pronunciation."""
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key or 'ollama'}",
        }

        prompt = system_prompt or (
            "Ты — генератор текста для синтеза речи (Silero TTS).\n"
            "Твоя СТРОГАЯ задача: заменять ВСЕ иностранные, латинские слова, термины и бренды "
            "на их точную русскую фонетическую транскрипцию кириллицей и ОБЯЗАТЕЛЬНО ставить знак плюс '+' "
            "ПОСЛЕ ударной гласной буквы в транслитерированных словах (например: 'Python' -> 'Па+йтон', 'Google' -> 'Гу+гл', 'JavaScript' -> 'Джаваскри+пт', 'Windows' -> 'Ви+ндовс', 'GitHub' -> 'Гитха+б').\n"
            "СТРОГИЕ ПРАВИЛА:\n"
            "1. НЕ пиши никаких вступлений, пояснений или разметки (ЗАПРЕЩЕНО писать 'Вот текст:', 'Результат:').\n"
            "2. НЕ оставляй оригинальные латинские слова в скобках.\n"
            "3. В итоговом ответе НЕ ДОЛЖНО БЫТЬ ни одной латинской буквы (a-z, A-Z).\n"
            "4. Выдавай ТОЛЬКО чистый итоговый русский текст."
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }

        print(f"[TextProcessor] Отправка запроса к ИИ API ({url}, модель={model})...", flush=True)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            raw_result = body["choices"][0]["message"]["content"].strip()
            print(f"[TextProcessor] Сырой ответ от ИИ API: {raw_result!r}", flush=True)
            return TextProcessor._clean_llm_response(raw_result, text)

    @staticmethod
    def test_llm_connection(
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 4.0,
        system_prompt: str | None = None,
    ) -> tuple[bool, str]:
        """Test API connection to specified LLM provider."""
        try:
            res = TextProcessor.transliterate_llm(
                text="Hello Python",
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=timeout,
                system_prompt=system_prompt,
            )
            return True, f"Соединение успешно! Ответ ИИ: '{res}'"
        except Exception as exc:
            return False, f"Ошибка подключения: {exc}"

    URL_PATTERN = re.compile(
        r"(?:https?://|ftps?://|www\.)[^\s<>'\"\)]+|[a-zA-Z0-9.\-]+\.(?:com|org|net|ru|io|dev|ai|app|gov|edu|me|info|site|online|tech|xyz|co|by|ua|kz)[^\s<>'\"\)]*",
        re.IGNORECASE,
    )

    @classmethod
    def strip_urls(cls, text: str) -> str:
        """Remove URLs and web links from text."""
        if not text:
            return text
        cleaned = cls.URL_PATTERN.sub("", text)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned

    @classmethod
    def process_text(cls, text: str, config: ConfigManager) -> str:
        """Main text processing entry point."""
        print(f"[TextProcessor] Входной текст: {text!r}", flush=True)
        if not text:
            return text

        # 0. Strip URLs if enabled in config
        if getattr(config, "ignore_urls", True):
            stripped = cls.strip_urls(text)
            if stripped != text:
                print(f"[TextProcessor] Удалены ссылки из текста: {stripped!r}", flush=True)
                text = stripped
                if not text:
                    return ""

        # 1. 100% Standalone offline number transliteration first
        text_num_converted = cls.convert_numbers_to_words(text)
        if text_num_converted != text:
            print(f"[TextProcessor] Автономный численный транслитератор обработал числа: {text_num_converted!r}", flush=True)
            text = text_num_converted

        is_active = config.transliteration_enabled or config.transliteration_use_llm
        if not is_active:
            print("[TextProcessor] Транслитерация латинских слов отключена в настройках.", flush=True)
            return text

        has_latin = cls.has_non_cyrillic(text)
        print(f"[TextProcessor] Содержит латиницу: {has_latin} (use_llm={config.transliteration_use_llm})", flush=True)
        if not has_latin:
            return text

        if config.transliteration_use_llm:
            try:
                res = cls.transliterate_llm(
                    text=text,
                    base_url=config.transliteration_base_url,
                    api_key=config.transliteration_api_key,
                    model=config.transliteration_model,
                    timeout=config.transliteration_timeout,
                    system_prompt=config.transliteration_system_prompt,
                )
                print(f"[TextProcessor] Итоговый обработанный текст через ИИ: {res!r}", flush=True)
                return res
            except Exception as exc:
                print(f"[TextProcessor] ИИ API недоступен ({exc}), переключаюсь на офлайн-фолбэк...", flush=True)

        res_off = cls.transliterate_offline(text)
        print(f"[TextProcessor] Итоговый обработанный текст через офлайн-фолбэк: {res_off!r}", flush=True)
        return res_off
