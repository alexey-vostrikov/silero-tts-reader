"""Проверки выбора источника ударений: словарь, Silero Stress, ruaccent.

Набор не требует установленных моделей: оба акцентора подменяются заглушками,
поэтому проверяется именно маршрутизация — кто получил текст, в каком порядке
применяются правки и что происходит при отказе модели.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from silero_tts_reader.core import accent as accent_module
from silero_tts_reader.core.accent import (
    MODE_DICT,
    SOURCE_SILERO,
    Accentor,
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


class FakeModel:
    """Заглушка самой модели: помечает слова и помнит, как её позвали."""

    calls: ClassVar[list[tuple[str, bool]]] = []
    fail_on: ClassVar[str | None] = None

    def __call__(self, text: str, stress_single_vowel: bool = True) -> str:
        FakeModel.calls.append((text, stress_single_vowel))
        if FakeModel.fail_on and FakeModel.fail_on in text:
            raise ValueError("заглушка не осилила")
        vowels = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
        words = []
        for token in text.split(" "):
            if not token[:1].isalpha():
                words.append(token)
                continue
            # Знак «+» ставим перед второй гласной — так видно, кто размечал,
            # и остаётся слово, к которому можно приложить правку.
            seen = 0
            out = []
            for letter in token:
                if letter in vowels:
                    seen += 1
                    if seen == 2:
                        out.append("+")
                out.append(letter)
            words.append("".join(out))
        return " ".join(words)


class FakeBackend:
    """Заглушка бэкенда: тот же интерфейс, что у Silero Stress."""

    def __init__(self, *args, **kwargs) -> None:
        self._model = FakeModel()

    def mark(self, text: str, fallback=None) -> str:
        # Повторяет поведение настоящего: сначала весь текст, при отказе —
        # по предложениям, а упавшее предложение отдаётся словарю.
        try:
            return self._model(text, stress_single_vowel=False)
        except Exception:
            pass
        parts = re.split(r"([.!?…]+\s*)", text)
        marked: list[str] = []
        for part in parts:
            # Как и настоящий бэкенд: знаки препинания модели не отдаются.
            if not re.search(r"[А-Яа-яЁё]", part):
                marked.append(part)
                continue
            try:
                marked.append(self._model(part, stress_single_vowel=False))
            except Exception:
                marked.append(fallback(part) if fallback is not None else part)
        return "".join(marked)


def install(fail_on: str | None = None) -> None:
    """Подменить Silero Stress заглушкой, чтобы не требовать пакета."""
    FakeModel.calls = []
    FakeModel.fail_on = fail_on
    accent_module.SileroStressBackend = FakeBackend   # type: ignore[assignment]


def make(source: str) -> Accentor:
    return Accentor(mode=MODE_DICT, directory=DATA, source=source)


def test_auto_prefers_silero() -> None:
    """auto берёт Silero Stress, когда он доступен."""
    print("\n1. auto: Silero Stress первым")
    install()
    acc = make("auto")
    out = acc.accent_text("Проверка окна плеера.")
    check(acc.source == SOURCE_SILERO, f"источник: {acc.source}")
    check(out.count("+") >= 3, f"текст ушёл акцентору: {out}")
    got_text = FakeModel.calls[0][0] if FakeModel.calls else ""
    check(got_text == "Проверка окна плеера.",
          f"акцентор получил исходный текст: {got_text!r}")


def test_single_vowel_flag() -> None:
    """Ударение на односложных акцентор не ставит: знак на них бесполезен."""
    print("\n2. Флаг односложных")
    install()
    make("auto").accent_text("Он шёл в дом.")
    check(all(call[1] is False for call in FakeModel.calls),
          f"stress_single_vowel=False: {FakeModel.calls}")


def test_dictionary_ignores_models() -> None:
    """Явный словарь не трогает ни одну модель."""
    print("\n3. dictionary: модели не вызываются")
    install()
    acc = make("dictionary")
    out = acc.accent_text("Проверка окна плеера.")
    check(not FakeModel.calls, f"заглушку не звали: {FakeModel.calls}")
    check(out.count("+") == 1, f"разметил только словарь: {out}")
    check("пров" in out.lower(), f"знак словаря на месте: {out}")


def test_explicit_ruaccent_skips_silero() -> None:
    """Явный ruaccent не подменяется Silero Stress даже при его наличии."""
    print("\n4. ruaccent: Silero Stress не используется")
    install()
    acc = make("ruaccent")
    # ruaccent не установлен в тестовой среде — источник откажется честно.
    check(acc._silero_backend() is None, "Silero Stress не запрошен")
    check(not FakeModel.calls, f"заглушку не звали: {FakeModel.calls}")


def test_overrides_win() -> None:
    """Правка пользователя сильнее модели: она применяется после разметки."""
    print("\n5. Правки применяются после акцентора")
    install()
    acc = Accentor(mode=MODE_DICT, directory=DATA, source="auto",
                   overrides={"дверь": "дв+ерь"})
    out = acc.accent_text("Открой дверь.")
    check("дв+ерь" in out, f"правка победила: {out}")


def test_fallback_per_sentence() -> None:
    """Отказ модели на одной фразе не теряет разметку остальных."""
    print("\n6. Отказ модели: разметка по предложениям")
    install(fail_on="БЕДА")
    acc = make("auto")
    out = acc.accent_text("Первая фраза. БЕДА пришла. Третья фраза.")
    check(out.count("+") >= 2, f"остальные фразы размечены: {out}")
    # Один вызов на весь текст, затем по предложению: путь отказа пройден.
    check(len(FakeModel.calls) == 4,
          f"попыток: {len(FakeModel.calls)} (весь текст и три фразы)")
    # Фразу с отказом разметил словарь, а не осталась она без разметки.
    by_dict = Accentor(mode=MODE_DICT, directory=DATA,
                       source="dictionary").accent_text("БЕДА пришла.")
    check(by_dict in out, f"упавшая фраза размечена словарём: {out}")


def main() -> int:
    test_auto_prefers_silero()
    test_single_vowel_flag()
    test_dictionary_ignores_models()
    test_explicit_ruaccent_skips_silero()
    test_overrides_win()
    test_fallback_per_sentence()
    print(f"\nвсе проверки пройдены: {CHECKS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
